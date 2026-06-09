"""Helpers for turning legacy parser tables into canonical tables."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .schema import AccountMetadata, CanonicalDataset
from .workbook_schema import CANONICAL_REQUIRED_COLUMNS

LEGACY_SHEET_ALIASES: dict[str, str] = {
    "FinInfo": "Instruments",
    "CorpActions": "CorporateActions",
    "Corporate_Actions": "CorporateActions",
    "Dividend": "Dividends",
    "Dividends": "Dividends",
    "Transfers": "Transfers",
    "MoneyTrans": "Transfers",
    "OtherMoneyTrans": "Transfers",
    "Trades": "Trades",
    "Fifo": "Fifo",
    "Positions": "Positions",
    "Interest": "Interest",
    "Deposits": "Interest",
    "Coupons": "Coupons",
    "Cash": "CashBalances",
    "Account": "CashBalances",
    "Years_Results": "Years_Results",
}

LEGACY_COLUMN_ALIASES: dict[str, str] = {
    "Symbol": "symbol",
    "Asset": "asset_type",
    "Asset_Category": "asset_type",
    "Asset Category": "asset_type",
    "Security_ID": "isin",
    "Security ID": "security_id",
    "Isin": "isin",
    "ISIN": "isin",
    "Currency": "currency",
    "Country": "country",
    "Exchange": "exchange",
    "Listing Exch": "listing_exchange",
    "Date_Time": "date_time",
    "Date": "date",
    "Quantity": "quantity",
    "Qty": "quantity",
    "T._Price": "price",
    "Comm_Fee": "commission",
    "Commission": "allocated_commission",
    "PnL": "pnl",
    "PnL_KZT": "pnl_kzt",
    "Amount": "gross_amount",
    "Withhold": "withholding_tax",
    "KZT": "kzt_rate",
    "Amount_KZT": "gross_amount_kzt",
    "Withhold_KZT": "withholding_tax_kzt",
    "Value": "ending_cash",
    "Year": "year",
    "Description": "description",
    "Conid": "conid",
    "FIGI": "figi",
    "CUSIP": "cusip",
}


def _records_from_table(table: Any) -> list[dict[str, Any]]:
    """Convert pandas-like or plain table objects to dictionaries."""

    if table is None:
        return []
    if isinstance(table, list):
        return [dict(row) for row in table if isinstance(row, Mapping)]
    if isinstance(table, tuple):
        return [dict(row) for row in table if isinstance(row, Mapping)]
    if isinstance(table, Mapping):
        return _records_from_nested_mapping(table)
    to_dict = getattr(table, "to_dict", None)
    if callable(to_dict):
        try:
            records = to_dict("records")
        except TypeError:
            records = to_dict()
        if isinstance(records, list):
            return [dict(row) for row in records if isinstance(row, Mapping)]
        if isinstance(records, Mapping):
            return _records_from_nested_mapping(records)
    return []


def _records_from_nested_mapping(mapping: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for name, value in mapping.items():
        child_records = _records_from_table(value)
        for record in child_records:
            record.setdefault("summary_name", name)
            records.append(record)
    return records


def normalize_record(record: Mapping[str, Any], source_sheet: str) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in record.items():
        canonical_key = LEGACY_COLUMN_ALIASES.get(str(key), str(key).strip().lower().replace(" ", "_"))
        normalized[canonical_key] = value
    normalized.setdefault("legacy_source_sheet", source_sheet)
    return normalized


def normalize_legacy_tables(
    broker: str,
    account_id: str,
    legacy_tables: Mapping[str, Any],
) -> CanonicalDataset:
    """Map legacy workbook-like tables to the canonical sheet contract.

    This is intentionally conservative. Broker-specific canonicalizers can add
    richer transformations later; this function gives a stable bridge that lets
    the new pipeline consume the existing scripts immediately.
    """

    dataset = CanonicalDataset(metadata=AccountMetadata(broker=broker, account_id=account_id))
    for legacy_name, table in legacy_tables.items():
        canonical_name = LEGACY_SHEET_ALIASES.get(legacy_name)
        if canonical_name is None:
            dataset.warnings.append(f"Skipped unmapped legacy sheet: {legacy_name}")
            continue
        records = [normalize_record(row, legacy_name) for row in _records_from_table(table)]
        if not records and canonical_name in CANONICAL_REQUIRED_COLUMNS:
            dataset.tables.setdefault(canonical_name, [])
            continue
        dataset.add_records(canonical_name, records)
    return dataset
