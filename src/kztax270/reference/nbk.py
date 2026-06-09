"""NBK average annual FX rate updater and import utilities."""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

from .repositories import ReferenceDataStore

NBK_MAIN_URL = "https://nationalbank.kz"
NBK_RATES_NEWS_URL = f"{NBK_MAIN_URL}/ru/news/oficialnye-kursy?page={{page}}"
NBK_RATE_COLUMNS = ("Des_Currency", "Currency", "Annual", "Year")


def ensure_nbk_rates_current(path: Path, today: date | None = None) -> bool:
    """Ensure `path` contains NBK average annual rates for the previous year.

    Returns True when the workbook was updated.
    """

    check_date = today or date.today()
    required_year = check_date.year - 1
    current = read_nbk_rates_dataframe(path) if path.exists() else _empty_rates_dataframe()
    if _contains_year(current, required_year):
        return False

    updated = fetch_nbk_average_annual_rates(current, required_year)
    path.parent.mkdir(parents=True, exist_ok=True)
    updated.to_excel(path, index=False)
    if not _contains_year(updated, required_year):
        raise RuntimeError(f"NBK average annual FX rates for {required_year} are missing after update attempt.")
    return True


def read_nbk_rates_dataframe(path: Path) -> Any:
    """Read local NBK rates workbook from `data/nb_rates.xlsx` shape."""

    pd = _pandas()
    df = pd.read_excel(path, engine="openpyxl")
    missing = set(NBK_RATE_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"NBK rates workbook {path} is missing columns: {sorted(missing)}")
    return _normalize_rates_dataframe(df[list(NBK_RATE_COLUMNS)])


def fetch_nbk_average_annual_rates(existing: Any, required_year: int) -> Any:
    """Fetch NBK average annual rates pages until `required_year` is present."""

    pd = _pandas()
    requests, bs = _html_dependencies()
    full_data = _normalize_rates_dataframe(existing)

    for page in range(1, 100):
        response = requests.get(NBK_RATES_NEWS_URL.format(page=page), timeout=30)
        response.raise_for_status()
        soup = bs(response.content, "lxml")
        links = soup.find_all("div", attrs={"class": "posts-files__title"})
        if not links:
            return _sort_rates_dataframe(full_data)

        for link in links:
            if not getattr(link, "a", None) or not link.a.get("href"):
                continue
            year_text = link.text.strip()[:4]
            if year_text == "2014":
                return _sort_rates_dataframe(full_data)

            file_url = f"{NBK_MAIN_URL}{link.a['href']}"
            try:
                table = _read_nbk_excel_table(file_url)
            except BadZipFile:
                table = _read_nbk_pdf_table(file_url)

            if table is None:
                continue
            full_data = pd.concat([full_data, table], ignore_index=True)
            full_data = _deduplicate_rates_dataframe(full_data)

            if _contains_year(full_data, required_year):
                return _sort_rates_dataframe(full_data)

    return _sort_rates_dataframe(full_data)


def read_nbk_average_annual_rates_xlsx(path: Path) -> list[dict[str, object]]:
    """Read local NBK average annual rates workbook into reference rows."""

    df = read_nbk_rates_dataframe(path)
    rows: list[dict[str, object]] = []
    source_date = date.fromtimestamp(path.stat().st_mtime).isoformat()
    for record in df.to_dict(orient="records"):
        year = int(record["Year"])
        rows.append(
            {
                "year": year,
                "currency": str(record["Currency"]).upper(),
                "average_annual_rate": _decimal_text(record["Annual"]),
                "source": f"NBK average annual official rates: {path}",
                "source_date": source_date,
                "valid_from": f"{year}-01-01",
                "valid_to": f"{year}-12-31",
            }
        )
    return rows


def upsert_nbk_average_annual_rates_xlsx(path: Path, store: ReferenceDataStore) -> int:
    """Import NBK average annual FX rates from `data/nb_rates.xlsx` into reference CSV."""

    return store.fx_rates.upsert_many(read_nbk_average_annual_rates_xlsx(path))


def _read_nbk_excel_table(file_url: str) -> Any | None:
    pd = _pandas()
    table = pd.read_excel(file_url, engine="openpyxl", skiprows=2, header=0).iloc[:-4, 1:]
    table = table.rename(columns={"Unnamed: 1": "Des_Currency", "Unnamed: 2": "Currency"})
    if len(table.columns) != 19:
        return None
    table["Year"] = int(str(table.columns[-1])[:4])
    table = table.rename(columns={table.columns[-2]: "Annual"})
    return _normalize_rates_dataframe(table[list(NBK_RATE_COLUMNS)])


def _read_nbk_pdf_table(file_url: str) -> Any:
    pd = _pandas()
    requests, _ = _html_dependencies()
    pdfplumber = _pdf_dependency()
    response = requests.get(file_url, timeout=30)
    response.raise_for_status()

    tables = []
    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                tables.append(pd.DataFrame(table[1:], columns=table[0]))

    if not tables:
        return _empty_rates_dataframe()
    table = pd.concat(tables, ignore_index=True)
    columns = list(table.columns)
    columns[0] = "Des_Currency"
    columns[1] = "Currency"
    table.columns = columns
    numeric_columns = list(table.columns)[2:]
    table[numeric_columns] = table[numeric_columns].replace(",", ".", regex=True)
    table[numeric_columns] = table[numeric_columns].astype(float)
    table["Year"] = int(str(table.columns[-1])[:4])
    table = table.rename(columns={table.columns[-2]: "Annual"})
    return _normalize_rates_dataframe(table[list(NBK_RATE_COLUMNS)])


def _normalize_rates_dataframe(df: Any) -> Any:
    pd = _pandas()
    if df is None or len(df) == 0:
        return _empty_rates_dataframe()
    result = df.copy()
    result = result[list(NBK_RATE_COLUMNS)]
    result["Currency"] = result["Currency"].astype("string").str.strip().str.upper()
    result["Des_Currency"] = result["Des_Currency"].astype("string").str.strip()
    result["Annual"] = pd.to_numeric(result["Annual"], errors="coerce")
    result["Year"] = pd.to_numeric(result["Year"], errors="coerce").astype("Int64")
    result = result.dropna(subset=["Currency", "Annual", "Year"])
    result["Year"] = result["Year"].astype(int)
    return _deduplicate_rates_dataframe(result)


def _deduplicate_rates_dataframe(df: Any) -> Any:
    return df.drop_duplicates(subset=["Year", "Currency"], keep="last")


def _sort_rates_dataframe(df: Any) -> Any:
    return _deduplicate_rates_dataframe(df).sort_values(by=["Year", "Currency"], ascending=[False, True])


def _contains_year(df: Any, year: int) -> bool:
    if df is None or len(df) == 0 or "Year" not in df:
        return False
    return year in set(int(value) for value in df["Year"].dropna().unique())


def _empty_rates_dataframe() -> Any:
    pd = _pandas()
    return pd.DataFrame(columns=list(NBK_RATE_COLUMNS))


def _decimal_text(value: Any) -> str:
    return format(Decimal(str(value)), "f")


def _pandas() -> Any:
    try:
        import pandas as pd  # type: ignore
    except Exception as exc:
        raise RuntimeError("NBK rate processing requires pandas and openpyxl.") from exc
    return pd


def _html_dependencies() -> tuple[Any, Any]:
    try:
        import requests  # type: ignore
        from bs4 import BeautifulSoup as bs  # type: ignore
    except Exception as exc:
        raise RuntimeError("Updating NBK rates from nationalbank.kz requires requests and beautifulsoup4.") from exc
    return requests, bs


def _pdf_dependency() -> Any:
    try:
        import pdfplumber  # type: ignore
    except Exception as exc:
        raise RuntimeError("Parsing NBK PDF rate files requires pdfplumber.") from exc
    return pdfplumber
