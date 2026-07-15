"""Security reference lists used by yearly tax classification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

AIX_API_URL = "https://market-backend.aixkz.com/api/table/mw-main-records?&is_etf_etn=true"
AIX_COLUMNS = (
    "year",
    "snapshot_type",
    "isin",
    "secCode",
    "shortName",
    "issuer",
    "instrument",
    "assetClass",
    "securityGroup",
    "currency",
    "state",
    "listingDate",
)
DEFAULT_AIX_INSTRUMENTS_PATH = Path("data/aix_instruments.xlsx")
DEFAULT_OFFSHORE_LIST_PATH = Path("data/offshore_list.xlsx")


def ensure_aix_instruments_current(path: Path = DEFAULT_AIX_INSTRUMENTS_PATH, today: date | None = None) -> bool:
    """Ensure the local AIX instrument snapshot was refreshed for the previous year."""

    check_date = today or date.today()
    required_year = check_date.year - 1
    current = read_aix_instruments_dataframe(path) if path.exists() else _empty_aix_dataframe()
    existing_years = set(_existing_years(current))
    is_full_snapshot = current["snapshot_type"].eq("full").all()
    if existing_years == {required_year} and is_full_snapshot:
        return False

    updated = fetch_aix_instruments(required_year)
    path.parent.mkdir(parents=True, exist_ok=True)
    updated.to_excel(path, index=False)
    if not _contains_year(updated, required_year):
        raise RuntimeError(f"AIX instrument snapshot for {required_year} is missing after update attempt.")
    return True


def read_aix_instruments_dataframe(path: Path) -> Any:
    pd = _pandas()
    df = pd.read_excel(path, engine="openpyxl")
    required_columns = {"year", "isin", "listingDate"}
    missing_columns = required_columns - set(df.columns)
    if missing_columns:
        raise ValueError(f"AIX instruments workbook {path} is missing required columns: {sorted(missing_columns)}")
    return _normalize_aix_dataframe(df)


def fetch_aix_instruments(snapshot_year: int) -> Any:
    """Download one complete AIX instrument snapshot and mark it with its refresh year."""

    pd = _pandas()
    requests = _requests()
    response = requests.get(AIX_API_URL, timeout=30)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("AIX returned an empty or invalid instrument snapshot.")
    frame = pd.DataFrame(rows)
    frame["year"] = snapshot_year
    frame["snapshot_type"] = "full"
    return _sort_aix_dataframe(frame)


@dataclass(frozen=True, slots=True)
class AixInstrumentProvider:
    listing_dates: Mapping[str, date]

    @classmethod
    def from_xlsx(cls, path: Path = DEFAULT_AIX_INSTRUMENTS_PATH) -> "AixInstrumentProvider":
        if not path.exists():
            return cls({})
        df = read_aix_instruments_dataframe(path)
        listing_dates: dict[str, date] = {}
        for record in df.to_dict(orient="records"):
            isin = _normalize_isin(record.get("isin"))
            listing_date = _date_or_none(record.get("listingDate"))
            if isin is None or listing_date is None:
                continue
            previous_date = listing_dates.get(isin)
            if previous_date is None or listing_date < previous_date:
                listing_dates[isin] = listing_date
        return cls(listing_dates)

    def is_listed(self, isin: str | None, capital_gain_date: Any) -> bool:
        normalized = _normalize_isin(isin)
        gain_date = _date_or_none(capital_gain_date)
        listing_date = self.listing_dates.get(normalized) if normalized is not None else None
        if listing_date is None or gain_date is None:
            return False
        return gain_date >= listing_date


@dataclass(frozen=True, slots=True)
class OffshoreJurisdictionProvider:
    isin_prefixes: frozenset[str]

    @classmethod
    def from_xlsx(cls, path: Path = DEFAULT_OFFSHORE_LIST_PATH) -> "OffshoreJurisdictionProvider":
        if not path.exists():
            return cls(frozenset())
        pd = _pandas()
        prefixes: set[str] = set()
        excel = pd.ExcelFile(path)
        try:
            for sheet_name in excel.sheet_names:
                df = pd.read_excel(excel, sheet_name=sheet_name, dtype=object)
                prefixes.update(_offshore_prefixes_from_dataframe(df))
        finally:
            excel.close()
        return cls(frozenset(prefixes))

    def is_offshore_isin(self, isin: str | None) -> bool:
        normalized = _normalize_isin(isin)
        return bool(normalized and normalized[:2] in self.isin_prefixes)


def _normalize_aix_dataframe(df: Any) -> Any:
    pd = _pandas()
    if df is None or len(df) == 0:
        return _empty_aix_dataframe()
    result = df.copy()
    for column in AIX_COLUMNS:
        if column not in result.columns:
            result[column] = None
    result = result[list(AIX_COLUMNS)]
    result["year"] = pd.to_numeric(result["year"], errors="coerce").astype("Int64")
    result["isin"] = result["isin"].astype("string").str.strip().str.upper()
    result = result.dropna(subset=["year", "isin"])
    result["year"] = result["year"].astype(int)
    return result.drop_duplicates(subset=["year", "isin"], keep="last")


def _date_or_none(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "nat"}:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _sort_aix_dataframe(df: Any) -> Any:
    return _normalize_aix_dataframe(df).sort_values(by=["year", "isin"], ascending=[False, True])


def _empty_aix_dataframe() -> Any:
    pd = _pandas()
    return pd.DataFrame(columns=list(AIX_COLUMNS))


def _contains_year(df: Any, year: int) -> bool:
    if df is None or len(df) == 0 or "year" not in df:
        return False
    return year in set(int(value) for value in df["year"].dropna().unique())


def _existing_years(df: Any) -> list[int]:
    if df is None or len(df) == 0 or "year" not in df:
        return []
    return [int(value) for value in df["year"].dropna().unique()]


def _offshore_prefixes_from_dataframe(df: Any) -> set[str]:
    prefixes: set[str] = set()
    normalized_columns = {_normalize_header(column): column for column in df.columns}
    alpha2_column = normalized_columns.get("iso alpha-2") or normalized_columns.get("alpha-2")
    detection_column = normalized_columns.get("isin-only detection") or normalized_columns.get("isin prefix sufficient?")
    if alpha2_column is None:
        return prefixes
    for row in df.to_dict(orient="records"):
        if detection_column is not None and not _is_yes(row.get(detection_column)):
            continue
        prefix = str(row.get(alpha2_column) or "").strip().upper()
        if len(prefix) == 2 and prefix.isalpha():
            prefixes.add(prefix)
    return prefixes


def _normalize_header(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_yes(value: Any) -> bool:
    return str(value or "").strip().lower() in {"yes", "y", "true", "1", "\u0434\u0430"}


def _normalize_isin(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text if len(text) >= 2 else None


def _pandas() -> Any:
    try:
        import pandas as pd  # type: ignore
    except Exception as exc:
        raise RuntimeError("Security reference processing requires pandas and openpyxl.") from exc
    return pd


def _requests() -> Any:
    try:
        import requests  # type: ignore
    except Exception as exc:
        raise RuntimeError("Updating AIX instruments requires requests.") from exc
    return requests
