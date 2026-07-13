"""Security reference lists used by yearly tax classification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

AIX_API_URL = (
    "https://market-backend.aixkz.com/api/table/mw-main-records?"
    "search=&instrument=&listing_between_start={year}-01-01&listing_between_end={year}-12-31&is_etf_etn=true"
)
AIX_COLUMNS = (
    "year",
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
    """Ensure the local AIX instrument workbook contains previous-year listings."""

    check_date = today or date.today()
    required_year = check_date.year - 1
    current = read_aix_instruments_dataframe(path) if path.exists() else _empty_aix_dataframe()
    if _contains_year(current, required_year):
        return False

    start_year = min([2023, required_year, *_existing_years(current)])
    updated = fetch_aix_instruments(current, start_year, required_year)
    path.parent.mkdir(parents=True, exist_ok=True)
    updated.to_excel(path, index=False)
    if not _contains_year(updated, required_year):
        raise RuntimeError(f"AIX instrument list for {required_year} is missing after update attempt.")
    return True


def read_aix_instruments_dataframe(path: Path) -> Any:
    pd = _pandas()
    df = pd.read_excel(path, engine="openpyxl")
    if "year" not in df.columns or "isin" not in df.columns:
        raise ValueError(f"AIX instruments workbook {path} is missing required columns: ['year', 'isin']")
    return _normalize_aix_dataframe(df)


def fetch_aix_instruments(existing: Any, start_year: int, end_year: int) -> Any:
    pd = _pandas()
    requests = _requests()
    frames = [_normalize_aix_dataframe(existing)]
    for year in range(start_year, end_year + 1):
        response = requests.get(AIX_API_URL.format(year=year), timeout=30)
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            continue
        frame = pd.DataFrame(rows)
        if frame.empty:
            continue
        frame["year"] = year
        frames.append(_normalize_aix_dataframe(frame))
    return _sort_aix_dataframe(pd.concat(frames, ignore_index=True))


@dataclass(frozen=True, slots=True)
class AixInstrumentProvider:
    listed_by_year: Mapping[int, frozenset[str]]

    @classmethod
    def from_xlsx(cls, path: Path = DEFAULT_AIX_INSTRUMENTS_PATH) -> "AixInstrumentProvider":
        if not path.exists():
            return cls({})
        df = read_aix_instruments_dataframe(path)
        listed: dict[int, set[str]] = {}
        for record in df.to_dict(orient="records"):
            isin = _normalize_isin(record.get("isin"))
            year = _int_or_none(record.get("year"))
            if isin is None or year is None:
                continue
            listed.setdefault(year, set()).add(isin)
        return cls({year: frozenset(values) for year, values in listed.items()})

    def is_listed(self, isin: str | None, year: int | None) -> bool:
        normalized = _normalize_isin(isin)
        if normalized is None or year is None:
            return False
        return any(normalized in values for listing_year, values in self.listed_by_year.items() if listing_year <= year)


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


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


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
