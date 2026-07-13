from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests

pd.set_option('display.max_rows', 500)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', None)

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - tqdm is only a progress-display dependency
    def tqdm(iterable: Iterable[Any], **_: Any) -> Iterable[Any]:
        return iterable
    

    
    
KASE_INSTRUMENT_URL = "https://kase.kz/api/instruments/{endpoint}/{ticker}"
KASE_PREF_PATH = Path("data/kase_pref.xlsx")
ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
SECTOR_ENDPOINTS = {
    "акции": "shares",
    "kase global": "shares",
    "производные ценные бумаги": "depts",
    "ценные бумаги инвестиционных фондов": "mifs",
}
ALL_ENDPOINTS = ("shares", "depts", "mifs")


def update_kase_pref_data():
    total_df = pd.read_excel(KASE_PREF_PATH)

    cur_date = datetime.datetime.now()
    date_start = datetime.datetime.strptime(total_df.iloc[-1]['month'], "%m_%Y") + pd.DateOffset(months=1)
    while True:
        print(date_start)
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


def create_yearly_check(
    path: Path = KASE_PREF_PATH,
    output_path: Path | None = None,
    yearly_output_path: Path | None = None,
) -> pd.DataFrame:
    """Add monthly and yearly dividend-exemption checks to the KASE table.

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
    # update_kase_pref_data()
    # update_isin()
    create_yearly_check()
