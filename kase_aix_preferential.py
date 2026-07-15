from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests
from kztax270.reference.securities import ensure_aix_instruments_current

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - tqdm is only a progress-display dependency
    def tqdm(iterable: Iterable[Any], **_: Any) -> Iterable[Any]:
        return iterable
    

    
    
KASE_INSTRUMENT_URL = "https://kase.kz/api/instruments/{endpoint}/{ticker}"
KASE_PREF_PATH = Path("data/kase_pref.xlsx")
AIX_PREF_PATH = Path("data/aix_pref.xlsx")
AIX_INSTRUMENTS_PATH = Path("data/aix_instruments.xlsx")
KASE_AIX_PREF_PATH = Path("data/kase_aix_pref.xlsx")
KASE_AIX_YEARLY_PATH = Path("data/kase_aix_pref_yearly.xlsx")
ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
SECTOR_ENDPOINTS = {
    "акции": "shares",
    "kase global": "shares",
    "производные ценные бумаги": "depts",
    "ценные бумаги инвестиционных фондов": "mifs",
}
ALL_ENDPOINTS = ("shares", "depts", "mifs")


def update_aix_pref_data(
    path: Path = AIX_PREF_PATH,
    instruments_path: Path = AIX_INSTRUMENTS_PATH,
) -> dict[str, int]:
    """Append missing AIX monthly tax-statistics rows and fill their ISINs."""

    total_df = pd.read_excel(path)
    if "period" not in total_df.columns:
        raise ValueError(f"{path} is missing required column: period")

    periods = pd.to_datetime(total_df["period"], format="%Y-%m", errors="coerce")
    if periods.isna().all():
        raise ValueError(f"{path} has no valid period values")

    current = datetime.datetime.now()
    date_start = periods.max() + pd.DateOffset(months=1)
    url = "https://market-backend.aixkz.com/api/aix/income-tax"
    headers = {"User-Agent": "Mozilla/5.0"}
    session = requests.Session()
    added = 0
    while (date_start.year, date_start.month) != (current.year, current.month):
        payload = {"asset": "EQTY", "period": f"{date_start.year}-{date_start.month:02d}"}
        response = session.get(
            url,
            params={"incomeTaxFilter": json.dumps(payload)},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        rows = response.json()
        if isinstance(rows, list) and rows:
            total_df = pd.concat([total_df, pd.DataFrame(rows)], ignore_index=True)
            added += len(rows)
        date_start += pd.DateOffset(months=1)

    total_df, fetched = update_aix_isin(total_df, instruments_path=instruments_path, session=session)
    try:
        total_df.to_excel(path, index=False)
    except PermissionError as exc:
        raise PermissionError(f"Cannot write {path}; close the workbook in Excel and retry.") from exc
    summary = {"added": added, "isin_fetched": fetched}
    print(summary)
    return summary


def update_aix_isin(
    total_df: pd.DataFrame,
    *,
    instruments_path: Path = AIX_INSTRUMENTS_PATH,
    session: requests.Session | None = None,
) -> tuple[pd.DataFrame, int]:
    """Fill missing AIX ISINs from the local instrument list and AIX profiles."""

    if "secCode" not in total_df.columns:
        raise ValueError("AIX monthly data is missing required column: secCode")
    if "isin" not in total_df.columns:
        total_df["isin"] = pd.NA
    total_df["isin"] = total_df["isin"].map(_normalise_isin)

    if instruments_path.exists():
        instruments = pd.read_excel(instruments_path)
        if {"secCode", "isin"}.issubset(instruments.columns):
            isin_by_code: dict[str, str] = {}
            for row in instruments[["secCode", "isin"]].to_dict(orient="records"):
                raw_code = row.get("secCode")
                code = "" if raw_code is None or pd.isna(raw_code) else str(raw_code).strip()
                isin = _normalise_isin(row.get("isin"))
                if code and isin is not None:
                    isin_by_code[code] = isin
            missing = total_df["isin"].isna()
            total_df.loc[missing, "isin"] = total_df.loc[missing, "secCode"].map(
                lambda value: isin_by_code.get(str(value).strip())
            )

    session = session or requests.Session()
    headers = {"User-Agent": "Mozilla/5.0"}
    fetched = 0
    missing_codes = total_df.loc[total_df["isin"].isna(), "secCode"].dropna().astype(str).str.strip().unique()
    for code in missing_codes:
        if not code:
            continue
        try:
            response = session.get(f"https://market-backend.aixkz.com/api/profile/{code}", headers=headers, timeout=30)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            continue
        isin = _normalise_isin(payload.get("isin") if isinstance(payload, dict) else None)
        if isin is None:
            continue
        total_df.loc[total_df["secCode"].astype(str).str.strip() == code, "isin"] = isin
        fetched += 1
    return total_df, fetched


def update_kase_pref_data():
    total_df = pd.read_excel(KASE_PREF_PATH)

    cur_date = datetime.datetime.now()
    months = pd.to_datetime(total_df["month"], format="%m_%Y", errors="coerce")
    if months.isna().all():
        raise ValueError(f"{KASE_PREF_PATH} has no valid month values")
    date_start = months.max() + pd.DateOffset(months=1)
    while True:
        if (date_start.month == cur_date.month) and (date_start.year == cur_date.year):
            break

        base_url = "https://kase.kz/ru/app-gateway/shares-taxes-stats-file"
        add_url = (f"?start_month={date_start.month}&start_year={date_start.year}"
                   f"&end_month={date_start.month}&end_year={date_start.year}")
        df = pd.read_excel(base_url + add_url, header=1)
        df['month'] = str(date_start.month) + '_' + str(date_start.year)
        total_df = pd.concat([total_df, df], ignore_index=True)

        date_start += pd.DateOffset(months=1)

    total_df.to_excel(KASE_PREF_PATH, index=False)


def update_isin(path: Path = KASE_PREF_PATH) -> dict[str, int]:
    """Fill and normalise up to three ISINs per KASE ticker.

    The API is queried once per ticker.  For a depositary receipt KASE returns
    its local ``KZ...`` identifier as well as the underlying foreign ISINs;
    only the latter are saved, in ``ISIN``, ``isin2`` and ``isin3``.  Missing
    identifiers, legacy receipt rows and a one-time secondary-ISIN backfill
    are retried on subsequent runs.
    """

    total_df = pd.read_excel(path)
    required_columns = {"Код", "Сектор"}
    missing_columns = required_columns - set(total_df.columns)
    if missing_columns:
        raise ValueError(f"{path} is missing required columns: {', '.join(sorted(missing_columns))}")

    needs_secondary_backfill = "isin2" not in total_df.columns or "isin3" not in total_df.columns
    for column in ("ISIN", "isin2", "isin3"):
        if column not in total_df.columns:
            total_df[column] = pd.NA

    ticker_column = total_df["Код"].astype("string").str.strip()
    total_df["Код"] = ticker_column
    for column in ("ISIN", "isin2", "isin3"):
        total_df[column] = total_df[column].map(_normalise_isin)

    tickers = total_df[["Код", "Сектор"]].drop_duplicates(subset=["Код"], keep="first")
    isins_by_ticker: dict[str, tuple[str | None, str | None, str | None]] = {}
    tickers_to_fetch: list[dict[str, Any]] = []
    for row in tickers.to_dict(orient="records"):
        if pd.isna(row["Код"]):
            continue
        ticker = str(row["Код"])
        ticker_rows = total_df.loc[total_df["Код"] == ticker, ["ISIN", "isin2", "isin3"]]
        current = tuple(
            next(
                (
                    isin
                    for value in ticker_rows[column]
                    if (isin := _normalise_isin(value)) is not None
                ),
                None,
            )
            for column in ("ISIN", "isin2", "isin3")
        )
        isins_by_ticker[ticker] = current

        sector = str(row.get("Сектор") or "").strip().casefold()
        has_legacy_depositary_isin = sector == "производные ценные бумаги" and str(current[0] or "").startswith("KZ")
        if current[0] is None or needs_secondary_backfill or has_legacy_depositary_isin:
            tickers_to_fetch.append(row)

    fetched = 0
    failed = 0
    session = requests.Session()
    for row in tqdm(tickers_to_fetch, total=len(tickers_to_fetch), desc="KASE ISIN"):
        ticker = str(row["Код"])
        endpoints = _candidate_endpoints(row.get("Сектор"))
        if not endpoints:
            print(f"Unknown KASE sector for ticker {ticker!r}: {row.get('Сектор')!r}")
            failed += 1
            continue

        isins: list[str] = []
        for endpoint in endpoints:
            url = KASE_INSTRUMENT_URL.format(endpoint=endpoint, ticker=ticker)
            try:
                response = session.get(url, timeout=30)
                if response.status_code == 404:
                    continue
                response.raise_for_status()
                isins = _extract_isins(response.json())
            except (requests.RequestException, ValueError):
                continue
            if isins:
                break

        if not isins:
            print(f"No ISIN in KASE response for {ticker}; tried: {', '.join(endpoints)}")
            failed += 1
            continue

        isins_by_ticker[ticker] = tuple((isins + [None, None, None])[:3])
        fetched += 1

    for ticker, isins in isins_by_ticker.items():
        ticker_mask = total_df["Код"] == ticker
        for column, isin in zip(("ISIN", "isin2", "isin3"), isins, strict=False):
            total_df.loc[ticker_mask, column] = isin

    columns = [column for column in total_df.columns if column not in {"isin2", "isin3"}]
    isin_index = columns.index("ISIN") + 1
    columns[isin_index:isin_index] = ["isin2", "isin3"]
    total_df = total_df[columns]

    try:
        total_df.to_excel(path, index=False)
    except PermissionError as exc:
        raise PermissionError(f"Cannot write {path}; close the workbook in Excel and retry.") from exc
    unresolved = sum(
        1
        for ticker in total_df["Код"].dropna().unique()
        if not total_df.loc[total_df["Код"] == ticker, "ISIN"].map(_normalise_isin).notna().any()
    )
    summary = {"tickers": len(tickers), "fetched": fetched, "failed": failed, "unresolved": unresolved}
    print(summary)
    return summary


def _endpoint_for_sector(sector: Any) -> str | None:
    if sector is None or pd.isna(sector):
        return None
    return SECTOR_ENDPOINTS.get(str(sector).strip().casefold())


def _candidate_endpoints(sector: Any) -> tuple[str, ...]:
    """Return the sector endpoint first, then KASE's fallback endpoints.

    KASE Global instruments are often funds/ETFs even though the monthly
    statistics label them as ``KASE Global`` or ``акции``.  Likewise, a
    derivative can occasionally be exposed by another instrument endpoint.
    """

    preferred = _endpoint_for_sector(sector)
    if preferred is None:
        return ()
    return (preferred, *(endpoint for endpoint in ALL_ENDPOINTS if endpoint != preferred))


def _extract_isins(payload: Any) -> list[str]:
    """Extract up to three distinct identifiers, preferring a DR's underlying ISINs.

    KASE stores a local ``KZ...`` identifier in ``ticker.nin`` for a
    depositary receipt.  When the response supplies non-KZ ISINs in
    ``nind``/``characteristics.isin``, those identify the actual underlying
    securities and must be used instead of the local KASE identifier.
    """

    if not isinstance(payload, dict):
        return []

    ticker = payload.get("ticker") if isinstance(payload.get("ticker"), dict) else {}
    characteristics = payload.get("characteristics") if isinstance(payload.get("characteristics"), dict) else {}
    candidates = [
        characteristics.get("isin"),
        characteristics.get("isin2"),
        characteristics.get("isin3"),
        ticker.get("nind"),
        ticker.get("nind2"),
        payload.get("isin"),
        payload.get("isin2"),
        ticker.get("nin"),
        ticker.get("nin2"),
    ]
    isins: list[str] = []
    for value in candidates:
        isin = _normalise_isin(value)
        if isin and isin not in isins:
            isins.append(isin)

    non_kz_isins = [isin for isin in isins if not isin.startswith("KZ")]
    return (non_kz_isins or isins)[:3]


def _normalise_isin(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().upper()
    if text in {"", "NONE", "NAN", "-"} or not ISIN_PATTERN.fullmatch(text):
        return None
    return text


def create_kase_aix_checks(
    kase_path: Path = KASE_PREF_PATH,
    aix_path: Path = AIX_PREF_PATH,
    output_path: Path = KASE_AIX_PREF_PATH,
    yearly_output_path: Path = KASE_AIX_YEARLY_PATH,
) -> pd.DataFrame:
    """Combine KASE and AIX monthly liquidity statistics and calculate annual checks.

    Volumes in the output are expressed in millions of KZT.  AIX reports
    ``value`` in KZT, whereas KASE already provides millions of KZT.  Rows
    with the same ISIN and month are summed before applying the liquidity
    thresholds.  AIX has no IPO/free-float criterion, so its presence makes
    ``ipo_ff_check`` pass for that month.
    """

    combined = _prepare_kase_months(kase_path).merge(
        _prepare_aix_months(aix_path),
        on=["ISIN", "month", "year", "month_num"],
        how="outer",
    )
    if combined.empty:
        raise ValueError("KASE and AIX monthly data contain no rows with valid ISINs")
    combined["is_fund"] = combined.pop("is_fund_x").fillna(False) | combined.pop("is_fund_y").fillna(False)

    for column in ("kase_vol", "aix_vol", "kase_trades", "aix_trades", "Free Float %"):
        combined[column] = pd.to_numeric(combined[column], errors="coerce").fillna(0)
    for column in ("has_kase", "has_aix", "is_fund", "kase_eligible", "aix_eligible", "IPO/SPO"):
        combined[column] = combined[column].fillna(False).astype(bool)
    for column in ("isin2", "isin3", "kase_code", "aix_code", "kase_company", "aix_company", "kase_sector", "aix_sector"):
        combined[column] = combined[column].where(combined[column].notna(), pd.NA)

    combined["vol"] = combined["kase_vol"] + combined["aix_vol"]
    combined["trades"] = combined["kase_trades"] + combined["aix_trades"]
    combined["eligible"] = combined["kase_eligible"] | combined["aix_eligible"]
    combined["Код"] = combined.apply(lambda row: _join_values(row["kase_code"], row["aix_code"]), axis=1)
    combined["Компания"] = combined.apply(
        lambda row: _join_values(row["kase_company"], row["aix_company"]), axis=1
    )
    combined["Сектор"] = combined.apply(
        lambda row: _join_values(row["kase_sector"], row["aix_sector"]), axis=1
    )

    volume_threshold = combined["is_fund"].map({True: 20, False: 25}).astype(int)
    trades_threshold = combined["is_fund"].map({True: 10, False: 50}).astype(int)
    combined["vol_check"] = (combined["vol"] >= volume_threshold).astype(int)
    combined["trades_check"] = (combined["trades"] >= trades_threshold).astype(int)
    combined["ipo_ff_check"] = (
        combined["year"].lt(2026)
        | combined["has_aix"]
        | combined["is_fund"]
        | combined["IPO/SPO"]
        | combined["Free Float %"].ge(10)
    ).astype(int)
    combined["month_check"] = combined[["vol_check", "trades_check", "ipo_ff_check"]].min(axis=1).astype(int)
    combined["year_check"] = 0

    eligible_rows = combined[combined["eligible"]].copy()
    annual = (
        eligible_rows.groupby(["ISIN", "year"], as_index=False)
        .agg(month_check=("month_check", "min"), month_count=("month_num", "nunique"))
    )
    annual["year_check"] = ((annual["month_check"] == 1) & (annual["month_count"] == 12)).astype(int)
    combined = combined.drop(columns=["year_check"]).merge(
        annual[["ISIN", "year", "year_check"]], on=["ISIN", "year"], how="left"
    )
    combined["year_check"] = combined["year_check"].fillna(0).astype(int)

    monthly_columns = [
        "Код", "Компания", "Сектор", "ISIN", "isin2", "isin3", "month", "year",
        "kase_code", "aix_code", "kase_vol", "aix_vol", "vol",
        "kase_trades", "aix_trades", "trades", "Free Float %", "IPO/SPO",
        "has_kase", "has_aix", "is_fund", "eligible",
        "vol_check", "trades_check", "ipo_ff_check", "month_check", "year_check",
    ]
    combined = combined[monthly_columns].sort_values(["year", "month", "ISIN"], kind="stable")
    try:
        combined.to_excel(output_path, index=False)
    except PermissionError as exc:
        raise PermissionError(f"Cannot write {output_path}; close the workbook in Excel and retry.") from exc

    yearly = (
        combined[["Код", "Компания", "Сектор", "ISIN", "isin2", "isin3", "year", "year_check"]]
        .rename(columns={"year": "Год"})
        .drop_duplicates(subset=["ISIN", "Год"], keep="last")
        .sort_values(["Год", "Код"], kind="stable")
    )
    try:
        yearly.to_excel(yearly_output_path, index=False)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot write {yearly_output_path}; close the workbook in Excel and retry."
        ) from exc
    return combined


def build_kase_aix_preferential() -> pd.DataFrame:
    """Update both exchanges' source data, enrich ISINs, and write combined checks."""

    update_kase_pref_data()
    update_isin()
    ensure_aix_instruments_current(AIX_INSTRUMENTS_PATH)
    update_aix_pref_data()
    return create_kase_aix_checks()


def _prepare_kase_months(path: Path) -> pd.DataFrame:
    total_df = pd.read_excel(path)
    required = {"Код", "Компания", "Сектор", "ISIN", "month", "Объём, млн KZT", "Количество сделок"}
    missing = required - set(total_df.columns)
    if missing:
        raise ValueError(f"{path} is missing required columns: {', '.join(sorted(missing))}")

    month_parts = total_df["month"].astype("string").str.extract(r"^(?P<month>\d{1,2})_(?P<year>\d{4})$")
    total_df["year"] = pd.to_numeric(month_parts["year"], errors="coerce").astype("Int64")
    total_df["month_num"] = pd.to_numeric(month_parts["month"], errors="coerce").astype("Int64")
    if total_df[["year", "month_num"]].isna().any().any():
        raise ValueError(f"{path} contains invalid month values")

    sector = total_df["Сектор"].astype("string").str.strip().str.casefold()
    total_df["ISIN"] = total_df["ISIN"].map(_normalise_isin)
    total_df["isin2"] = total_df.get("isin2", pd.Series(pd.NA, index=total_df.index)).map(_normalise_isin)
    total_df["isin3"] = total_df.get("isin3", pd.Series(pd.NA, index=total_df.index)).map(_normalise_isin)
    total_df["kase_vol"] = pd.to_numeric(total_df["Объём, млн KZT"], errors="coerce").fillna(0)
    total_df["kase_trades"] = pd.to_numeric(total_df["Количество сделок"], errors="coerce").fillna(0)
    total_df["Free Float %"] = pd.to_numeric(total_df.get("Free Float %"), errors="coerce")
    total_df["IPO/SPO"] = total_df.get("IPO/SPO", pd.Series(pd.NA, index=total_df.index)).map(_as_bool)
    total_df["is_fund"] = sector.eq("ценные бумаги инвестиционных фондов")
    total_df["kase_eligible"] = sector.isin(
        {"акции", "kase global", "производные ценные бумаги", "ценные бумаги инвестиционных фондов"}
    )
    total_df = total_df.dropna(subset=["ISIN"])
    return (
        total_df.groupby(["ISIN", "month", "year", "month_num"], as_index=False)
        .agg(
            kase_code=("Код", _join_values),
            kase_company=("Компания", _join_values),
            kase_sector=("Сектор", _join_values),
            isin2=("isin2", _first_value),
            isin3=("isin3", _first_value),
            kase_vol=("kase_vol", "sum"),
            kase_trades=("kase_trades", "sum"),
            **{"Free Float %": ("Free Float %", "max")},
            **{"IPO/SPO": ("IPO/SPO", "max")},
            is_fund=("is_fund", "max"),
            kase_eligible=("kase_eligible", "max"),
            has_kase=("ISIN", "size"),
        )
        .assign(has_kase=lambda frame: frame["has_kase"].gt(0))
    )


def _prepare_aix_months(path: Path) -> pd.DataFrame:
    total_df = pd.read_excel(path)
    required = {"period", "secCode", "name", "assetClass", "numberOfTrades", "value", "isin"}
    missing = required - set(total_df.columns)
    if missing:
        raise ValueError(f"{path} is missing required columns: {', '.join(sorted(missing))}")

    period = pd.to_datetime(total_df["period"], format="%Y-%m", errors="coerce")
    if period.isna().any():
        raise ValueError(f"{path} contains invalid period values")
    total_df["ISIN"] = total_df["isin"].map(_normalise_isin)
    total_df["month"] = period.dt.month.astype(str) + "_" + period.dt.year.astype(str)
    total_df["year"] = period.dt.year.astype("Int64")
    total_df["month_num"] = period.dt.month.astype("Int64")
    total_df["aix_vol"] = pd.to_numeric(total_df["value"], errors="coerce").fillna(0) / 1_000_000
    total_df["aix_trades"] = pd.to_numeric(total_df["numberOfTrades"], errors="coerce").fillna(0)
    total_df["is_fund"] = total_df.get("isEtfEtn", pd.Series(False, index=total_df.index)).map(_as_bool)
    total_df["aix_eligible"] = total_df["assetClass"].astype("string").str.strip().str.upper().eq("EQTY")
    total_df = total_df.dropna(subset=["ISIN"])
    return (
        total_df.groupby(["ISIN", "month", "year", "month_num"], as_index=False)
        .agg(
            aix_code=("secCode", _join_values),
            aix_company=("name", _join_values),
            aix_sector=("assetClass", _join_values),
            aix_vol=("aix_vol", "sum"),
            aix_trades=("aix_trades", "sum"),
            is_fund=("is_fund", "max"),
            aix_eligible=("aix_eligible", "max"),
            has_aix=("ISIN", "size"),
        )
        .assign(has_aix=lambda frame: frame["has_aix"].gt(0))
    )


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or pd.isna(value):
        return False
    return str(value).strip().casefold() in {"+", "да", "yes", "y", "true", "1"}


def _join_values(*values: Any) -> str | None:
    if len(values) == 1 and isinstance(values[0], pd.Series):
        values = tuple(values[0].tolist())
    result: list[str] = []
    for value in values:
        if value is None or pd.isna(value):
            continue
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return " / ".join(result) if result else None


def _first_value(values: pd.Series) -> str | None:
    for value in values:
        if value is not None and not pd.isna(value):
            return str(value)
    return None


def create_yearly_check(
    path: Path = KASE_PREF_PATH,
    output_path: Path | None = None,
    yearly_output_path: Path | None = None,
) -> pd.DataFrame:
    """Legacy KASE-only check retained for compatibility.

    New runs use :func:`create_kase_aix_checks`, which combines both exchanges.

    The source volume is expressed in millions of KZT, so the thresholds are
    compared with ``25`` and ``20`` rather than with values in tenge.  The
    yearly result is the minimum monthly result for each ``ISIN``/year group:
    one failed month makes the yearly result zero.  The IPO/SPO or free-float
    criterion applies from 2026 onward; before 2026 only volume and trade-count
    criteria are used.
    """

    total_df = pd.read_excel(path)
    old_free_float = "Количество акций в свободном обращении по расчету Эмитента (%%)"
    if "Free Float %" not in total_df.columns and old_free_float in total_df.columns:
        total_df = total_df.rename(columns={old_free_float: "Free Float %"})
    elif "Free Float %" in total_df.columns and old_free_float in total_df.columns:
        total_df = total_df.drop(columns=[old_free_float])

    old_check_columns = {
        "Проверка объёма",
        "Проверка количества сделок",
        "Проверка IPO/SPO или free float",
        "Проверка за месяц",
        "Проверка за год",
        "month_number",
    }
    total_df = total_df.drop(columns=[column for column in old_check_columns if column in total_df.columns])

    required_columns = {
        "ISIN",
        "month",
        "Сектор",
        "Объём, млн KZT",
        "Количество сделок",
        "Free Float %",
        "IPO/SPO",
    }
    missing_columns = required_columns - set(total_df.columns)
    if missing_columns:
        raise ValueError(f"{path} is missing required columns: {', '.join(sorted(missing_columns))}")

    month_parts = total_df["month"].astype("string").str.extract(r"^(?P<month>\d{1,2})_(?P<year>\d{4})$")
    total_df["year"] = pd.to_numeric(month_parts["year"], errors="coerce").astype("Int64")
    month_number = pd.to_numeric(month_parts["month"], errors="coerce").astype("Int64")
    if total_df["year"].isna().any() or month_number.isna().any():
        invalid_months = total_df.loc[
            total_df["year"].isna() | month_number.isna(), "month"
        ].drop_duplicates().tolist()
        raise ValueError(f"Invalid month values in {path}: {invalid_months}")

    sector = total_df["Сектор"].astype("string").str.strip().str.casefold()
    is_fund = sector.eq("ценные бумаги инвестиционных фондов")
    eligible = sector.isin(
        {"акции", "kase global", "производные ценные бумаги", "ценные бумаги инвестиционных фондов"}
    )
    volume = pd.to_numeric(total_df["Объём, млн KZT"], errors="coerce")
    trades = pd.to_numeric(total_df["Количество сделок"], errors="coerce")
    free_float = pd.to_numeric(total_df["Free Float %"], errors="coerce")
    ipo_spo = total_df["IPO/SPO"].astype("string").str.strip().str.casefold().isin({"+", "да", "yes", "y", "true", "1"})
    pre_2026 = total_df["year"].lt(2026)
    volume_threshold = is_fund.map({True: 20, False: 25}).fillna(25)
    trades_threshold = is_fund.map({True: 10, False: 50}).fillna(50)

    total_df["vol_check"] = (volume >= volume_threshold).astype(int)
    total_df["trades_check"] = (trades >= trades_threshold).astype(int)
    total_df["ipo_ff_check"] = (is_fund | pre_2026 | ipo_spo | free_float.ge(10)).astype(int)
    total_df["month_check"] = (
        total_df[["vol_check", "trades_check", "ipo_ff_check"]].min(axis=1)
    ).astype(int)

    total_df["year_check"] = 0
    valid_isin = total_df["ISIN"].map(_normalise_isin).notna() & eligible
    yearly_checks = (
        total_df.loc[valid_isin]
        .groupby(["ISIN", "year"], dropna=False)["month_check"]
        .min()
    )
    yearly_checks = yearly_checks.astype(int)
    valid_rows = total_df.loc[valid_isin, ["ISIN", "year"]]
    total_df.loc[valid_isin, "year_check"] = [
        int(yearly_checks.get((isin, year), 0))
        for isin, year in zip(valid_rows["ISIN"], valid_rows["year"], strict=False)
    ]

    for column in ("isin2", "isin3"):
        if column not in total_df.columns:
            total_df[column] = pd.NA

    destination = output_path or path
    try:
        total_df.to_excel(destination, index=False)
    except PermissionError as exc:
        raise PermissionError(f"Cannot write {destination}; close the workbook in Excel and retry.") from exc

    yearly_columns = ["Код", "Компания", "Сектор", "ISIN", "isin2", "isin3", "year", "year_check"]
    yearly_df = (
        total_df[yearly_columns]
        .rename(columns={"year": "Год"})
        .drop_duplicates(subset=["Код", "Год"], keep="last")
        .sort_values(["Год", "Код"], kind="stable")
    )
    yearly_destination = yearly_output_path or Path(destination).with_name("kase_pref_yearly.xlsx")
    try:
        yearly_df.to_excel(yearly_destination, index=False)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot write {yearly_destination}; close the workbook in Excel and retry."
        ) from exc
    return total_df


if __name__ == '__main__':
    build_kase_aix_preferential()
