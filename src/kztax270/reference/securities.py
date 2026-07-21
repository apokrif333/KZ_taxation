"""Security reference lists used by yearly tax classification."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

AIX_API_URL = "https://market-backend.aixkz.com/api/table/mw-main-records?&is_etf_etn=true"
AIX_PROFILE_API_URL = "https://market-backend.aixkz.com/api/profile/{ticker}"
AIX_PROFILE_PAGE_URL = "https://market.aixkz.com/details/{ticker}/profile"
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
DEFAULT_TABYS_INSTRUMENTS_PATH = Path("data/tabys_instruments.xlsx")
DEFAULT_OFFSHORE_LIST_PATH = Path("data/offshore_list.xlsx")
TABYS_PROFILE_COLUMNS = (
    *AIX_COLUMNS,
    "country",
    "securityName",
    "maturityDate",
    "faceValue",
    "couponRate",
    "couponFreq",
)


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


def read_tabys_instruments_dataframe(path: Path) -> Any:
    """Read AIX profiles cached specifically for Tabys.

    This workbook is deliberately separate from the yearly AIX listing snapshot:
    a profile can be available for a delisted security and must not affect tax
    classification based on ``aix_instruments.xlsx``.
    """

    pd = _pandas()
    return _normalize_tabys_instruments_dataframe(pd.read_excel(path, engine="openpyxl"))


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


@dataclass(slots=True)
class AixInstrumentResolver:
    """Resolve AIX tickers from the yearly snapshot, Tabys cache, then AIX profile."""

    aix_path: Path = DEFAULT_AIX_INSTRUMENTS_PATH
    profile_cache_path: Path = DEFAULT_TABYS_INSTRUMENTS_PATH
    timeout: float = 30
    persist_profiles: bool = True
    _aix_records_by_ticker: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _cached_records_by_ticker: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _loaded: bool = field(default=False, init=False, repr=False)

    def resolve(self, ticker: str, *, snapshot_year: int | None = None) -> dict[str, Any]:
        normalized_ticker = str(ticker or "").strip().upper()
        if not normalized_ticker:
            raise ValueError("AIX instrument ticker is empty.")

        self._load_local_records()
        local = self._aix_records_by_ticker.get(normalized_ticker)
        if local is not None:
            return _aix_reference(local, source=str(self.aix_path))
        cached = self._cached_records_by_ticker.get(normalized_ticker)
        if cached is not None:
            return _aix_reference(cached, source=str(self.profile_cache_path))

        profile = fetch_aix_instrument_profile(normalized_ticker, timeout=self.timeout)
        reference = _aix_reference(
            profile,
            source=AIX_PROFILE_PAGE_URL.format(ticker=normalized_ticker),
        )
        self._cached_records_by_ticker[normalized_ticker] = profile
        if self.persist_profiles:
            self._persist_profile(profile, snapshot_year=snapshot_year)
        return reference

    def _load_local_records(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.aix_path.exists():
            frame = read_aix_instruments_dataframe(self.aix_path)
            records = sorted(
                frame.to_dict(orient="records"),
                key=lambda row: int(row.get("year") or 0),
            )
            for record in records:
                ticker = _normalize_ticker(record.get("secCode"))
                if ticker is not None:
                    self._aix_records_by_ticker[ticker] = record
        if not self.profile_cache_path.exists():
            return
        frame = read_tabys_instruments_dataframe(self.profile_cache_path)
        for record in frame.to_dict(orient="records"):
            ticker = _normalize_ticker(record.get("secCode"))
            if ticker is not None:
                self._cached_records_by_ticker[ticker] = record

    def _persist_profile(self, profile: Mapping[str, Any], *, snapshot_year: int | None) -> None:
        pd = _pandas()
        current = (
            read_tabys_instruments_dataframe(self.profile_cache_path)
            if self.profile_cache_path.exists()
            else _empty_tabys_instruments_dataframe()
        )
        row = {column: profile.get(column) for column in TABYS_PROFILE_COLUMNS}
        row["year"] = snapshot_year or date.today().year
        row["snapshot_type"] = "profile"
        updated = _normalize_tabys_instruments_dataframe(pd.concat([current, pd.DataFrame([row])], ignore_index=True))
        self.profile_cache_path.parent.mkdir(parents=True, exist_ok=True)
        updated.to_excel(self.profile_cache_path, index=False)


def fetch_aix_instrument_profile(ticker: str, *, timeout: float = 30) -> dict[str, Any]:
    """Fetch the data used by ``market.aixkz.com/details/{ticker}/profile``."""

    normalized_ticker = str(ticker or "").strip().upper()
    requests = _requests()
    response = requests.get(AIX_PROFILE_API_URL.format(ticker=normalized_ticker), timeout=timeout)
    response.raise_for_status()
    profile = response.json()
    if not isinstance(profile, dict) or not _normalize_isin(profile.get("isin")):
        raise RuntimeError(f"AIX returned an invalid profile for {normalized_ticker}.")
    profile["secCode"] = _normalize_ticker(profile.get("secCode")) or normalized_ticker
    return profile


@dataclass(frozen=True, slots=True)
class AixInstrumentProvider:
    listing_dates: Mapping[str, date]

    @classmethod
    def from_xlsx(cls, path: Path = DEFAULT_AIX_INSTRUMENTS_PATH) -> AixInstrumentProvider:
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
    def from_xlsx(cls, path: Path = DEFAULT_OFFSHORE_LIST_PATH) -> OffshoreJurisdictionProvider:
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


def _normalize_tabys_instruments_dataframe(df: Any) -> Any:
    if df is None or len(df) == 0:
        return _empty_tabys_instruments_dataframe()
    result = df.copy()
    for column in TABYS_PROFILE_COLUMNS:
        if column not in result.columns:
            result[column] = None
    result = result[list(TABYS_PROFILE_COLUMNS)]
    result["secCode"] = result["secCode"].astype("string").str.strip().str.upper()
    result["isin"] = result["isin"].astype("string").str.strip().str.upper()
    result = result.dropna(subset=["secCode", "isin"])
    return result.drop_duplicates(subset=["secCode"], keep="last").sort_values(by=["secCode"])


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


def _empty_tabys_instruments_dataframe() -> Any:
    pd = _pandas()
    return pd.DataFrame(columns=list(TABYS_PROFILE_COLUMNS))


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


def _normalize_ticker(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text or None


def _aix_reference(record: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    isin = _normalize_isin(record.get("isin"))
    country = _aix_country_code(record.get("country"), isin)
    return {
        "symbol": _normalize_ticker(record.get("secCode")),
        "description": _first_text(record, "securityName", "shortName", "secCode"),
        "isin": isin,
        "type": _aix_asset_type(record),
        "issuer": _first_text(record, "issuer"),
        "country": country,
        "currency": _first_text(record, "currency", "faceCurrency"),
        "maturity": _date_or_none(record.get("maturityDate")),
        "face_value": _first_text(record, "faceValue"),
        "coupon_rate": _first_text(record, "couponRate"),
        "coupon_frequency": _first_text(record, "couponFreq"),
        "source": source,
    }


def _aix_asset_type(record: Mapping[str, Any]) -> str | None:
    values = [
        _first_text(record, "instrument"),
        _first_text(record, "assetClass"),
        _first_text(record, "securityGroup"),
    ]
    combined = " ".join(value.casefold() for value in values if value)
    if any(token in combined for token in ("debt", "bond", "облигац")):
        return "Bonds"
    if "etn" in combined:
        return "ETN"
    if any(token in combined for token in ("equity", "share", "stock", "etf", "fund", "акци")):
        return "Stocks"
    return values[0] or values[1] or values[2]


def _aix_country_code(value: Any, isin: str | None) -> str | None:
    text = str(value or "").strip()
    if len(text) == 2:
        return text.upper()
    known = {
        "kazakhstan": "KZ",
        "қазақстан": "KZ",
        "казахстан": "KZ",
    }
    if text.casefold() in known:
        return known[text.casefold()]
    if isin:
        return "BE" if isin.startswith("XS") else isin[:2]
    return None


def _first_text(record: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text.casefold() not in {"nan", "nat", "none", "null"}:
            return text
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
