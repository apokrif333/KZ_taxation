"""Native Exante CSV parser."""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from kztax270.canonical.schema import AccountMetadata, CanonicalDataset, RawReportTotals
from kztax270.reconciliation.models import ReconciliationMetric
from kztax270.reference.fx import AnnualFxRateProvider
from kztax270.transfers import TransferInFifoResolver

from .base import BrokerReport, ParseResult
from .discovery import DiscoveryRule, discover_raw_reports
from .ib import (
    ISIN_RE,
    _amount_kzt,
    _annual_rate,
    _build_broker_trade_realized_pl,
    _build_fifo_and_positions,
    _build_fx_fifo_rows,
    _build_unprocessed_rows,
    _build_years_results,
    _canonical_trade_rows,
    _canonical_transfer_rows,
    _decimal as _ib_decimal,
    _decimal_text,
    _effective_transaction_multiplier,
    _instrument_identity_key_from_values,
    _instrument_symbol_history,
    _is_fx_trade,
    _money_text,
    _multiplier_text,
    _parse_datetime as _ib_parse_datetime,
    _sort_trades_by_datetime,
    _string_or_none,
)

EXANTE_SECTION_TRADES = "Trades"
EXANTE_SECTION_TRANSACTIONS = "Transactions"
EXANTE_SECTION_CASH_BALANCES = "CashBalancesRaw"
EXANTE_SECTION_OPEN_POSITIONS = "OpenPositionsRaw"

TRADE_REQUIRED_COLUMNS = {
    "Time",
    "Account ID",
    "Side",
    "Symbol ID",
    "ISIN",
    "Type",
    "Price",
    "Currency",
    "Quantity",
    "Commission",
    "P&L",
    "Traded Volume",
}
TRANSACTION_REQUIRED_COLUMNS = {
    "Transaction ID",
    "Account ID",
    "Symbol ID",
    "ISIN",
    "Operation type",
    "When",
    "Sum",
    "Asset",
}
HANDLED_TRANSACTION_OPERATION_TYPES = {
    "TRADE",
    "DIVIDEND",
    "US TAX",
    "TAX",
    "COMMISSION",
    "SECURITY TRANSFER",
    "FUNDING/WITHDRAWAL",
    "ELECTRONIC TRANSFER",
    "BANK CHARGE",
    "AUTOCONVERSION",
    "ROLLOVER",
    "INTEREST",
    "COUPON",
    "CORPORATE ACTION",
    "STOCK SPLIT",
}

DIVIDEND_AMOUNT_RE = re.compile(
    r"\bdividend\s+(?P<symbol>\S+)\s+(?P<amount>-?[\d.,]+)\s+(?P<currency>[A-Z]{3})\b",
    re.IGNORECASE,
)
DIVIDEND_TAX_RE = re.compile(r"\btax\s+(?P<tax>-?[\d.,]+)\s+(?P<currency>[A-Z]{3})\b", re.IGNORECASE)
DIVIDEND_COUNTRY_RE = re.compile(r"\bDivCntry\s+(?P<country>[A-Z]{2})\b", re.IGNORECASE)
DIVIDEND_PAY_DATE_RE = re.compile(r"\bPD\s+(?P<date>\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
PERIOD_RE = re.compile(r"(?P<start>\d{4}-\d{2}-\d{2})\s+-\s+(?P<end>\d{4}-\d{2}-\d{2})")
CASH_BALANCE_SECTION_RE = re.compile(r"^Cash Balance .*?, (?P<date>\d{4}-\d{2}-\d{2})$")
OPEN_POSITIONS_SECTION_RE = re.compile(r"^Stocks & ETFs .*?, (?P<date>\d{4}-\d{2}-\d{2})\)?$")
EXANTE_OPTION_SYMBOL_RE = re.compile(
    r"^(?P<underlying>[A-Z0-9_/-]+)\.(?P<exchange>[A-Z]+)\.\d{1,2}[A-Z]\d{4}\.[CP]\d+(?:\.\d+)?$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ParsedExanteReport:
    path: Path
    account_id: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    base_currency: str | None = "USD"
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    fields: dict[str, str] = field(default_factory=dict)


class ExanteParser:
    broker_code = "exante"

    def __init__(
        self,
        fx_provider: AnnualFxRateProvider | None = None,
        transfer_in_resolver: TransferInFifoResolver | None = None,
    ) -> None:
        self.fx_provider = fx_provider or AnnualFxRateProvider({})
        self.transfer_in_resolver = transfer_in_resolver

    def discover_reports(self, raw_root: Path, account_id: str) -> list[BrokerReport]:
        return discover_raw_reports(raw_root, DiscoveryRule(broker="exante", account_id=account_id))

    def parse_reports(self, reports: Sequence[BrokerReport], account_id: str) -> ParseResult:
        parsed_reports = [parse_exante_csv_report(report.path) for report in reports]
        dataset = build_canonical_dataset(
            parsed_reports,
            account_id,
            self.fx_provider,
            transfer_in_resolver=self.transfer_in_resolver,
        )
        dataset.raw_totals.source_reports = [str(report.path) for report in reports]
        return ParseResult(
            broker=self.broker_code,
            account_id=account_id,
            reports=reports,
            dataset=dataset,
            raw_totals=dataset.raw_totals,
        )


def parse_exante_csv_report(path: Path) -> ParsedExanteReport:
    parsed = ParsedExanteReport(path=path)
    with path.open("r", newline="", encoding="utf-16") as handle:
        rows = [[value.strip() for value in row] for row in csv.reader(handle, delimiter="\t")]

    for row in rows[:50]:
        if not row:
            continue
        if row[0].startswith("Costs and Charges Report"):
            period_match = PERIOD_RE.search(row[0])
            if period_match:
                parsed.period_start = date.fromisoformat(period_match.group("start"))
                parsed.period_end = date.fromisoformat(period_match.group("end"))
        elif row[0] == "Account" and len(row) > 1:
            parsed.account_id = row[1]

    _parse_snapshot_sections(parsed, rows, path)

    trade_header_idx = _find_header(rows, "Time", required_columns=TRADE_REQUIRED_COLUMNS)
    if trade_header_idx is not None:
        trade_header = rows[trade_header_idx]
        for row in rows[trade_header_idx + 1 :]:
            if not row or not any(row):
                continue
            if row[0] == "Transaction ID":
                break
            if len(row) < len(trade_header):
                continue
            if not _is_trade_data_row(trade_header, row):
                continue
            parsed.rows[EXANTE_SECTION_TRADES].append(_row_to_record(trade_header, row, path))

    transaction_header_idx = _find_header(rows, "Transaction ID", required_columns=TRANSACTION_REQUIRED_COLUMNS)
    if transaction_header_idx is not None:
        transaction_header = rows[transaction_header_idx]
        for row in rows[transaction_header_idx + 1 :]:
            if not row or not any(row):
                continue
            if len(row) < len(transaction_header):
                continue
            if not _is_transaction_data_row(transaction_header, row):
                continue
            parsed.rows[EXANTE_SECTION_TRANSACTIONS].append(_row_to_record(transaction_header, row, path))

    if parsed.account_id is None:
        parsed.account_id = _first_account_id(parsed)
    return parsed


def _parse_snapshot_sections(parsed: ParsedExanteReport, rows: Sequence[Sequence[str]], path: Path) -> None:
    for idx, row in enumerate(rows):
        if not row:
            continue
        title = row[0]
        cash_match = CASH_BALANCE_SECTION_RE.match(title)
        if cash_match:
            _append_snapshot_rows(
                parsed,
                rows,
                idx,
                path,
                section=EXANTE_SECTION_CASH_BALANCES,
                snapshot_date=cash_match.group("date"),
                required_columns={"Instrument", "ISO", "Value"},
            )
            continue

        positions_match = OPEN_POSITIONS_SECTION_RE.match(title)
        if positions_match:
            _append_snapshot_rows(
                parsed,
                rows,
                idx,
                path,
                section=EXANTE_SECTION_OPEN_POSITIONS,
                snapshot_date=positions_match.group("date"),
                required_columns={"Instrument", "QTY", "Currency", "ISIN"},
            )


def _append_snapshot_rows(
    parsed: ParsedExanteReport,
    rows: Sequence[Sequence[str]],
    section_idx: int,
    path: Path,
    *,
    section: str,
    snapshot_date: str,
    required_columns: set[str],
) -> None:
    header_idx = section_idx + 1
    if header_idx >= len(rows):
        return
    header = list(rows[header_idx])
    if not required_columns.issubset(set(header)):
        return

    for row in rows[header_idx + 1 :]:
        if not row or not any(row):
            break
        if _looks_like_section_title(row):
            break
        record = _row_to_record(header, row, path)
        record["snapshot_date"] = snapshot_date
        parsed.rows[section].append(record)


def _looks_like_section_title(row: Sequence[str]) -> bool:
    first_cell = row[0] if row else ""
    return bool(CASH_BALANCE_SECTION_RE.match(first_cell) or OPEN_POSITIONS_SECTION_RE.match(first_cell))


def build_canonical_dataset(
    reports: Sequence[ParsedExanteReport],
    account_id: str,
    fx_provider: AnnualFxRateProvider,
    *,
    transfer_in_resolver: TransferInFifoResolver | None = None,
) -> CanonicalDataset:
    base_currency = next((report.base_currency for report in reports if report.base_currency), "USD") or "USD"
    dataset = CanonicalDataset(metadata=AccountMetadata(broker="exante", account_id=account_id, base_currency=base_currency))

    instruments = _build_instruments(reports, account_id)
    instrument_lookup = _instrument_lookup(instruments)
    symbol_history = _instrument_symbol_history(instruments)
    dataset.tables["Instruments"] = instruments
    dataset.tables["CorporateActions"] = _build_corporate_actions(reports, instrument_lookup)
    dataset.tables["Dividends"] = _build_dividends(reports, instrument_lookup, fx_provider, dataset.warnings)

    transfers, transfer_totals_by_currency = _build_transfers(reports, instrument_lookup)
    internal_trades = _sort_trades_by_datetime(_build_trades(reports, instrument_lookup))
    fifo_input_trades = [trade for trade in internal_trades if not _is_fx_trade(trade)]
    dataset.tables["Trades"] = _canonical_trade_rows(internal_trades)
    dataset.tables["_BrokerTradeRealizedPL"] = _build_broker_trade_realized_pl(internal_trades)

    fifo_rows, fifo_positions, transfer_rows = _build_fifo_and_positions(
        fifo_input_trades,
        transfers=transfers,
        initial_lots=[],
        max_year=_max_report_year(reports, fifo_input_trades, transfers),
        fx_provider=fx_provider,
        warnings=dataset.warnings,
        symbol_history=symbol_history,
        transfer_in_resolver=transfer_in_resolver,
    )
    fifo_rows.extend(_build_fx_fifo_rows(internal_trades, fx_provider, dataset.warnings))
    dataset.tables["Fifo"] = fifo_rows
    dataset.tables["Unprocessed"] = [
        *_build_unprocessed_rows(dataset.tables["Trades"], fifo_rows),
        *_build_unprocessed_trade_rows(reports),
        *_build_unprocessed_transaction_rows(reports),
    ]
    dataset.tables["Positions"] = fifo_positions
    dataset.tables["Transfers"] = _canonical_transfer_rows(transfer_rows)
    interest, coupons = _build_interest_and_coupons(reports, instrument_lookup, fx_provider, dataset.warnings)
    dataset.tables["Interest"] = interest
    dataset.tables["Coupons"] = coupons
    dataset.tables["CashBalances"] = _build_cash_balances(reports, fx_provider, dataset.warnings)
    dataset.tables["Years_Results"] = _build_years_results(dataset)

    _populate_raw_totals(
        dataset.raw_totals,
        reports,
        internal_trades,
        dataset.tables["Dividends"],
        transfer_totals_by_currency,
        instrument_lookup,
    )
    return dataset


def _find_header(
    rows: Sequence[Sequence[str]],
    first_cell: str,
    *,
    required_columns: set[str] | None = None,
) -> int | None:
    for idx, row in enumerate(rows):
        if not row or row[0] != first_cell:
            continue
        if required_columns is not None and not required_columns.issubset(set(row)):
            continue
        return idx
    return None


def _is_trade_data_row(header: Sequence[str], row: Sequence[str]) -> bool:
    record = {header[idx]: row[idx] for idx in range(min(len(header), len(row)))}
    if not _string_or_none(record.get("Symbol ID")) or not _string_or_none(record.get("Type")):
        return False
    if _parse_datetime(record.get("Time")) is None:
        return False
    try:
        _decimal(record.get("Price"))
        _decimal(record.get("Quantity"))
        _decimal(record.get("Traded Volume"))
    except Exception:
        return False
    return True


def _is_transaction_data_row(header: Sequence[str], row: Sequence[str]) -> bool:
    record = {header[idx]: row[idx] for idx in range(min(len(header), len(row)))}
    operation_type = str(record.get("Operation type") or "").strip().upper()
    if not operation_type:
        return False
    if not _string_or_none(record.get("Account ID")):
        return False
    if _parse_datetime(record.get("When")) is None:
        return False
    try:
        _decimal(record.get("Sum"))
    except Exception:
        return False
    return True


def _row_to_record(header: Sequence[str], row: Sequence[str], path: Path) -> dict[str, Any]:
    record = {header[idx]: row[idx] for idx in range(min(len(header), len(row)))}
    record["source_report"] = str(path)
    return record


def _first_account_id(parsed: ParsedExanteReport) -> str | None:
    for section in (EXANTE_SECTION_TRADES, EXANTE_SECTION_TRANSACTIONS):
        for row in parsed.rows.get(section, []):
            account_id = _string_or_none(row.get("Account ID"))
            if account_id and account_id != "Account ID":
                return account_id
    return None


def _parse_datetime(value: Any) -> datetime | None:
    parsed = _ib_parse_datetime(value)
    if parsed is not None:
        return parsed
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "nat"}:
        return None
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "nat", "n/a", "null"}:
        return Decimal("0")
    return _ib_decimal(text)


def _build_instruments(reports: Sequence[ParsedExanteReport], account_id: str) -> list[dict[str, Any]]:
    instruments: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None, int | None]] = set()

    for report in reports:
        report_year = _year_for_report(report)
        for row in report.rows.get(EXANTE_SECTION_TRADES, []):
            trade_dt = _parse_datetime(row.get("Time"))
            symbol_id = _string_or_none(row.get("Symbol ID"))
            symbol, exchange = _split_symbol_id(symbol_id)
            isin = _normalize_isin(row.get("ISIN"))
            year = trade_dt.year if trade_dt else report_year
            record = _instrument_record(
                symbol=symbol,
                symbol_id=symbol_id,
                isin=isin,
                exchange=exchange,
                asset_type=_asset_type(row.get("Type"), symbol_id),
                currency=_string_or_none(row.get("Currency")),
                year=year,
                source_report=_string_or_none(row.get("source_report")),
                account_id=account_id,
            )
            _append_instrument(instruments, seen, record)

        for row in report.rows.get(EXANTE_SECTION_TRANSACTIONS, []):
            if not _transaction_should_seed_instrument(row):
                continue
            symbol_id = _string_or_none(row.get("Symbol ID"))
            if not symbol_id or symbol_id == "None":
                continue
            symbol, exchange = _split_symbol_id(symbol_id)
            isin = _normalize_isin(row.get("ISIN"))
            operation_type = _string_or_none(row.get("Operation type"))
            transaction_dt = _parse_datetime(row.get("When"))
            year = transaction_dt.year if transaction_dt else report_year
            country = _dividend_country_from_comment(row.get("Comment")) if operation_type in {"DIVIDEND", "US TAX", "TAX"} else None
            currency = _transaction_currency(row, symbol_id, exchange)
            record = _instrument_record(
                symbol=symbol,
                symbol_id=symbol_id,
                isin=isin,
                exchange=exchange,
                asset_type=_asset_type(None, symbol_id) or "Stocks",
                currency=currency,
                year=year,
                source_report=_string_or_none(row.get("source_report")),
                account_id=account_id,
                country_override=country,
            )
            _append_instrument(instruments, seen, record)

        for row in report.rows.get(EXANTE_SECTION_OPEN_POSITIONS, []):
            snapshot_dt = _parse_datetime(row.get("snapshot_date"))
            symbol_id = _string_or_none(row.get("Instrument"))
            symbol, exchange = _split_symbol_id(symbol_id)
            isin = _normalize_isin(row.get("ISIN"))
            year = snapshot_dt.year if snapshot_dt else report_year
            record = _instrument_record(
                symbol=symbol,
                symbol_id=symbol_id,
                isin=isin,
                exchange=exchange,
                asset_type=_asset_type(None, symbol_id) or "Stocks",
                currency=_string_or_none(row.get("Currency")),
                year=year,
                source_report=_string_or_none(row.get("source_report")),
                account_id=account_id,
            )
            _append_instrument(instruments, seen, record)
    _backfill_instrument_identity(instruments)
    return instruments


def _append_instrument(
    instruments: list[dict[str, Any]],
    seen: set[tuple[str | None, str | None, int | None]],
    record: dict[str, Any],
) -> None:
    key = (_string_or_none(record.get("symbol")), _string_or_none(record.get("isin")), _int_or_none(record.get("year")))
    if key[1] is None and any(
        _string_or_none(existing.get("symbol")) == key[0]
        and _int_or_none(existing.get("year")) == key[2]
        and _string_or_none(existing.get("isin")) is not None
        for existing in instruments
    ):
        return
    if key in seen:
        return
    seen.add(key)
    instruments.append(record)


def _backfill_instrument_identity(instruments: list[dict[str, Any]]) -> None:
    by_symbol_id: dict[str, dict[str, Any]] = {}
    by_symbol: dict[str, dict[str, Any]] = {}
    for record in instruments:
        isin = _string_or_none(record.get("isin"))
        if not isin:
            continue
        symbol_id = _string_or_none(record.get("_exante_symbol_id"))
        symbol = _string_or_none(record.get("symbol"))
        if symbol_id:
            by_symbol_id.setdefault(symbol_id, record)
        if symbol:
            by_symbol.setdefault(symbol, record)

    for record in instruments:
        if _string_or_none(record.get("isin")) is not None:
            continue
        source = by_symbol_id.get(str(record.get("_exante_symbol_id") or "")) or by_symbol.get(str(record.get("symbol") or ""))
        if source is None:
            continue
        for field_name in ("isin", "security_id", "cusip"):
            value = _string_or_none(source.get(field_name))
            if value and not _string_or_none(record.get(field_name)):
                record[field_name] = value
        if not _string_or_none(record.get("country")):
            record["country"] = source.get("country")
        if not _string_or_none(record.get("issuer_country")):
            record["issuer_country"] = source.get("issuer_country")
        if record.get("issuer_outside_kz_flag") is None:
            record["issuer_outside_kz_flag"] = source.get("issuer_outside_kz_flag")


def _instrument_record(
    *,
    symbol: str | None,
    symbol_id: str | None,
    isin: str | None,
    exchange: str | None,
    asset_type: str | None,
    currency: str | None,
    year: int | None,
    source_report: str | None,
    account_id: str,
    country_override: str | None = None,
) -> dict[str, Any]:
    country = country_override or _country_from_isin_or_exchange(isin, exchange)
    multiplier = "100" if asset_type == "Equity and Index Options" else "1"
    return {
        "symbol": symbol,
        "description": symbol_id,
        "conid": None,
        "security_id": isin,
        "underlying": None,
        "listing_exchange": exchange,
        "multiplier": multiplier,
        "type": asset_type,
        "code": None,
        "year": year,
        "expiry": None,
        "delivery_month": None,
        "strike": None,
        "issuer": None,
        "maturity": None,
        "cusip": isin[2:11] if isin and ISIN_RE.fullmatch(isin) else None,
        "country": country,
        "isin": isin,
        "figi": None,
        "issuer_country": country,
        "offshore_flag": None,
        "issuer_outside_kz_flag": None if country is None else country != "KZ",
        "preferential_tax_flag": None,
        "source_broker": "exante",
        "source_account": account_id,
        "source_report": source_report,
        "as_of_date": None,
        "asset_type": asset_type,
        "_exante_symbol_id": symbol_id,
        "_currency": currency,
    }


def _instrument_lookup(instruments: Sequence[Mapping[str, Any]]) -> dict[tuple[str, int | None], dict[str, Any]]:
    lookup: dict[tuple[str, int | None], dict[str, Any]] = {}
    for row in instruments:
        year = _int_or_none(row.get("year"))
        keys = (
            row.get("symbol"),
            row.get("isin"),
            row.get("security_id"),
            row.get("_exante_symbol_id"),
            row.get("description"),
        )
        for value in keys:
            if not value:
                continue
            _set_lookup_record(lookup, (str(value), year), row)
            _set_lookup_record(lookup, (str(value), None), row)
    return lookup


def _set_lookup_record(
    lookup: dict[tuple[str, int | None], dict[str, Any]],
    key: tuple[str, int | None],
    row: Mapping[str, Any],
) -> None:
    existing = lookup.get(key)
    if existing is None or _record_has_better_identity(row, existing):
        lookup[key] = dict(row)


def _record_has_better_identity(candidate: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    if _string_or_none(current.get("isin")) is None and _string_or_none(candidate.get("isin")) is not None:
        return True
    if _string_or_none(current.get("security_id")) is None and _string_or_none(candidate.get("security_id")) is not None:
        return True
    return False


def _lookup_instrument(
    lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    *,
    symbol: str | None = None,
    symbol_id: str | None = None,
    isin: str | None = None,
    year: int | None = None,
) -> dict[str, Any] | None:
    for key in (isin, symbol_id, symbol):
        if key and ((str(key), year) in lookup or (str(key), None) in lookup):
            return lookup.get((str(key), year)) or lookup.get((str(key), None))
    return None


def _build_trades(
    reports: Sequence[ParsedExanteReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for report in reports:
        expiration_keys = {
            key
            for row in report.rows.get(EXANTE_SECTION_TRANSACTIONS, [])
            if (key := _option_expiration_transaction_key(row)) is not None
        }
        seen_expiration_keys: set[tuple[str, datetime, Decimal]] = set()
        for idx, row in enumerate(report.rows.get(EXANTE_SECTION_TRADES, []), start=1):
            trade_dt = _parse_datetime(row.get("Time"))
            symbol_id = _string_or_none(row.get("Symbol ID"))
            symbol, exchange = _split_symbol_id(symbol_id)
            isin = _normalize_isin(row.get("ISIN"))
            year = trade_dt.year if trade_dt else _year_for_report(report)
            instrument = _lookup_instrument(instrument_lookup, symbol=symbol, symbol_id=symbol_id, isin=isin, year=year) or {}
            raw_quantity = abs(_decimal(row.get("Quantity")))
            side_sign = _trade_side_sign(row)
            if side_sign is None:
                continue
            quantity = raw_quantity * side_sign
            price = _decimal(row.get("Price"))
            traded_volume = abs(_decimal(row.get("Traded Volume")))
            instrument_multiplier = _decimal(instrument.get("multiplier") or "1") or Decimal("1")
            multiplier = _effective_transaction_multiplier(instrument_multiplier, quantity, price, traded_volume)
            amount = traded_volume if traded_volume else abs(quantity * price * multiplier)
            commission = abs(_decimal(row.get("Commission")))
            currency = _string_or_none(row.get("Currency")) or _string_or_none(instrument.get("_currency"))
            country = _string_or_none(instrument.get("country")) or _country_from_isin_or_exchange(isin, exchange)
            broker_realized_pl = _decimal(row.get("P&L")) if row.get("P&L") not in (None, "", "0", "0.0") else None
            expiration_key = _option_expiration_key(symbol_id, trade_dt, quantity)
            is_option_expiration = expiration_key in expiration_keys
            if is_option_expiration and expiration_key is not None:
                seen_expiration_keys.add(expiration_key)
            trades.append(
                {
                    "date_time": trade_dt.isoformat(sep=" ") if trade_dt else None,
                    "trade_id": _exante_trade_id(report, idx, row),
                    "trade_type": "option_expiration" if is_option_expiration else "trade",
                    "symbol": symbol,
                    "isin": isin or _string_or_none(instrument.get("isin")),
                    "asset_type": _string_or_none(instrument.get("asset_type")) or _asset_type(row.get("Type"), symbol_id),
                    "quantity": str(quantity),
                    "calculation_quantity": str(quantity),
                    "price": str(price),
                    "calculation_price": str(price),
                    "multiplier": _multiplier_text(multiplier),
                    "_calculation_multiplier": str(multiplier),
                    "amount": str(amount),
                    "commission": str(commission),
                    "amount_with_commission": str(amount + commission),
                    "currency": currency,
                    "exchange": exchange or _string_or_none(instrument.get("listing_exchange")),
                    "country": country,
                    "source_report": f"option_expiration:{report.path}" if is_option_expiration else str(report.path),
                    "_instrument_identity_key": _instrument_identity_key_from_values(
                        isin=isin or _string_or_none(instrument.get("isin")),
                        symbol=symbol,
                    ),
                    "_broker_realized_pl": str(broker_realized_pl) if broker_realized_pl is not None else None,
                    "_broker_realized_pl_includes_commissions": False,
                    "_broker_code": "C" if is_option_expiration else None,
                }
            )
        for row in report.rows.get(EXANTE_SECTION_TRANSACTIONS, []):
            expiration_key = _option_expiration_transaction_key(row)
            if expiration_key is not None and expiration_key in seen_expiration_keys:
                continue
            expiration_trade = _option_expiration_trade_row(report, row, instrument_lookup)
            if expiration_trade is not None:
                trades.append(expiration_trade)
    return trades


def _trade_side_sign(row: Mapping[str, Any]) -> Decimal | None:
    side = str(row.get("Side") or "").strip().lower()
    if side == "buy":
        return Decimal("1")
    if side == "sell":
        return Decimal("-1")
    return None


def _option_expiration_trade_row(
    report: ParsedExanteReport,
    row: Mapping[str, Any],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> dict[str, Any] | None:
    if not _is_option_expiration_security_leg(row):
        return None
    event_dt = _parse_datetime(row.get("When"))
    symbol_id = _string_or_none(row.get("Symbol ID"))
    symbol, exchange = _split_symbol_id(symbol_id)
    raw_quantity = _decimal(row.get("Sum"))
    if raw_quantity == 0:
        return None
    year = event_dt.year if event_dt else _year_for_report(report)
    isin = _normalize_isin(row.get("ISIN"))
    instrument = _lookup_instrument(instrument_lookup, symbol=symbol, symbol_id=symbol_id, isin=isin, year=year) or {}
    multiplier = _decimal(instrument.get("multiplier") or "100") or Decimal("100")
    currency = _string_or_none(instrument.get("_currency")) or _currency_from_exchange(exchange)
    country = _string_or_none(instrument.get("country")) or _country_from_isin_or_exchange(isin, exchange)
    source_report = str(report.path)
    return {
        "date_time": event_dt.isoformat(sep=" ") if event_dt else None,
        "trade_id": f"{report.path.name}:option_expiration:{_string_or_none(row.get('Transaction ID')) or symbol_id}",
        "trade_type": "option_expiration",
        "symbol": symbol,
        "isin": isin or _string_or_none(instrument.get("isin")),
        "asset_type": "Equity and Index Options",
        "quantity": str(raw_quantity),
        "calculation_quantity": str(raw_quantity),
        "price": "0",
        "calculation_price": "0",
        "multiplier": _multiplier_text(multiplier),
        "_calculation_multiplier": str(multiplier),
        "amount": "0",
        "commission": "0",
        "amount_with_commission": "0",
        "currency": currency,
        "exchange": exchange or _string_or_none(instrument.get("listing_exchange")),
        "country": country,
        "source_report": f"option_expiration:{source_report}",
        "_instrument_identity_key": _instrument_identity_key_from_values(
            isin=isin or _string_or_none(instrument.get("isin")),
            symbol=symbol,
        ),
        "_broker_realized_pl": None,
        "_broker_code": "C",
    }


def _is_option_expiration_transaction(row: Mapping[str, Any]) -> bool:
    if _string_or_none(row.get("Operation type")) != "EXERCISE":
        return False
    symbol_id = _string_or_none(row.get("Symbol ID"))
    if not _is_option_symbol_id(symbol_id):
        return False
    return "expiration" in ((_none_text(row.get("Comment")) or "").lower())


def _is_option_expiration_security_leg(row: Mapping[str, Any]) -> bool:
    if not _is_option_expiration_transaction(row):
        return False
    symbol_id = _string_or_none(row.get("Symbol ID"))
    if _string_or_none(row.get("Asset")) != symbol_id:
        return False
    if _decimal(row.get("Sum")) == 0:
        return False
    return True


def _option_expiration_transaction_key(row: Mapping[str, Any]) -> tuple[str, datetime, Decimal] | None:
    if not _is_option_expiration_security_leg(row):
        return None
    return _option_expiration_key(
        _string_or_none(row.get("Symbol ID")),
        _parse_datetime(row.get("When")),
        _decimal(row.get("Sum")),
    )


def _option_expiration_key(
    symbol_id: str | None,
    event_dt: datetime | None,
    quantity: Decimal,
) -> tuple[str, datetime, Decimal] | None:
    if not symbol_id or event_dt is None or quantity == 0:
        return None
    return symbol_id, event_dt.replace(microsecond=0), quantity


def _build_transfers(
    reports: Sequence[ParsedExanteReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Decimal]]:
    transfers: list[dict[str, Any]] = []
    cash_totals_by_currency: dict[str, Decimal] = defaultdict(Decimal)
    for report in reports:
        for row in report.rows.get(EXANTE_SECTION_TRANSACTIONS, []):
            operation_type = _string_or_none(row.get("Operation type"))
            if _is_security_transfer_transaction(row):
                transfer = _security_transfer_row(report, row, instrument_lookup)
                if transfer is not None:
                    transfers.append(transfer)
                continue
            if operation_type in {"FUNDING/WITHDRAWAL", "ELECTRONIC TRANSFER", "BANK CHARGE"}:
                transfer = _cash_transfer_row(report, row)
                if transfer is None:
                    continue
                transfers.append(transfer)
                currency = _string_or_none(transfer.get("currency"))
                if currency:
                    cash_totals_by_currency[currency] += _decimal(transfer.get("amount"))
    return transfers, dict(cash_totals_by_currency)


def _is_security_transfer_transaction(row: Mapping[str, Any]) -> bool:
    operation_type = _string_or_none(row.get("Operation type"))
    if operation_type == "SECURITY TRANSFER":
        return True
    if operation_type != "FUNDING/WITHDRAWAL":
        return False
    symbol_id = _string_or_none(row.get("Symbol ID"))
    asset = _string_or_none(row.get("Asset"))
    comment = (_none_text(row.get("Comment")) or "").lower()
    return bool(symbol_id and symbol_id != "None" and asset == symbol_id and "securities transfer" in comment)


def _transaction_should_seed_instrument(row: Mapping[str, Any]) -> bool:
    if _is_security_transfer_transaction(row):
        return True
    operation_type = _string_or_none(row.get("Operation type"))
    return operation_type in {"DIVIDEND", "US TAX", "TAX", "COUPON", "CORPORATE ACTION", "STOCK SPLIT"}


def _security_transfer_row(
    report: ParsedExanteReport,
    row: Mapping[str, Any],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> dict[str, Any] | None:
    transfer_dt = _parse_datetime(row.get("When"))
    symbol_id = _string_or_none(row.get("Symbol ID"))
    symbol, exchange = _split_symbol_id(symbol_id)
    raw_quantity = _decimal(row.get("Sum"))
    if raw_quantity == 0:
        return None
    isin = _normalize_isin(row.get("ISIN"))
    year = transfer_dt.year if transfer_dt else _year_for_report(report)
    instrument = _lookup_instrument(instrument_lookup, symbol=symbol, symbol_id=symbol_id, isin=isin, year=year) or {}
    currency = _string_or_none(instrument.get("_currency")) or _currency_from_exchange(exchange)
    country = _string_or_none(instrument.get("country")) or _country_from_isin_or_exchange(isin, exchange)
    return {
        "date": _date_to_iso(transfer_dt),
        "transfer_type": "security",
        "direction": "in" if raw_quantity > 0 else "out",
        "asset_type": _string_or_none(instrument.get("asset_type")) or "Stocks",
        "symbol": symbol,
        "isin": isin or _string_or_none(instrument.get("isin")),
        "currency": currency,
        "country": country,
        "quantity": _decimal_text(abs(raw_quantity)),
        "price": None,
        "enter_date": None,
        "amount": None,
        "broker_comment": _none_text(row.get("Comment")),
        "counterparty": None,
        "source_report": str(report.path),
        "_raw_quantity": str(raw_quantity),
        "_transfer_id": _string_or_none(row.get("Transaction ID")),
        "_instrument_identity_key": _instrument_identity_key_from_values(
            isin=isin or _string_or_none(instrument.get("isin")),
            symbol=symbol,
        ),
        "_multiplier": _string_or_none(instrument.get("multiplier")) or "1",
    }


def _cash_transfer_row(report: ParsedExanteReport, row: Mapping[str, Any]) -> dict[str, Any] | None:
    amount = _decimal(row.get("Sum"))
    if amount == 0:
        return None
    transfer_dt = _parse_datetime(row.get("When"))
    operation_type = _string_or_none(row.get("Operation type"))
    broker_comment = _none_text(row.get("Comment"))
    if operation_type == "BANK CHARGE":
        broker_comment = f"BANK CHARGE: {broker_comment}" if broker_comment else "BANK CHARGE"
    return {
        "date": _date_to_iso(transfer_dt),
        "transfer_type": "cash",
        "direction": "in" if amount > 0 else "out",
        "asset_type": None,
        "symbol": None,
        "isin": None,
        "currency": _string_or_none(row.get("Asset")),
        "quantity": None,
        "price": None,
        "enter_date": None,
        "amount": str(amount),
        "broker_comment": broker_comment,
        "counterparty": None,
        "source_report": str(report.path),
    }


def _build_dividends(
    reports: Sequence[ParsedExanteReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        for row in report.rows.get(EXANTE_SECTION_TRANSACTIONS, []):
            operation_type = _string_or_none(row.get("Operation type"))
            if operation_type == "DIVIDEND":
                dividend_row = _dividend_row_from_transaction(report, row, instrument_lookup, fx_provider, warnings)
                if dividend_row is not None:
                    rows.append(dividend_row)
                continue

            if operation_type in {"US TAX", "TAX"}:
                reversal_row = _dividend_withholding_reversal_row(report, row, instrument_lookup, fx_provider, warnings)
                if reversal_row is not None:
                    rows.append(reversal_row)
    return rows


def _dividend_row_from_transaction(
    report: ParsedExanteReport,
    row: Mapping[str, Any],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> dict[str, Any] | None:
    dividend_dt = _parse_datetime(row.get("When"))
    pay_date = _dividend_pay_date(row.get("Comment")) or dividend_dt
    year = pay_date.year if pay_date else _year_for_report(report)
    symbol_id = _string_or_none(row.get("Symbol ID"))
    symbol, exchange = _split_symbol_id(symbol_id)
    isin = _normalize_isin(row.get("ISIN"))
    instrument = _lookup_instrument(instrument_lookup, symbol=symbol, symbol_id=symbol_id, isin=isin, year=year) or {}
    currency = _dividend_currency(row) or _string_or_none(row.get("Asset")) or _string_or_none(instrument.get("_currency"))
    gross_amount = _dividend_gross_amount(row)
    withholding_tax = _dividend_withholding_tax(row)
    return _dividend_canonical_row(
        report,
        pay_date=pay_date,
        symbol=symbol,
        isin=isin or _string_or_none(instrument.get("isin")),
        country=_dividend_country_from_comment(row.get("Comment"))
        or _string_or_none(instrument.get("country"))
        or _country_from_isin_or_exchange(isin, exchange),
        currency=currency,
        gross_amount=gross_amount,
        withholding_tax=withholding_tax,
        fx_provider=fx_provider,
        warnings=warnings,
    )


def _dividend_withholding_reversal_row(
    report: ParsedExanteReport,
    row: Mapping[str, Any],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> dict[str, Any] | None:
    withholding_tax = _decimal(row.get("Sum"))
    if withholding_tax <= 0:
        return None

    adjustment_dt = _parse_datetime(row.get("When"))
    pay_date = _dividend_pay_date(row.get("Comment")) or adjustment_dt
    year = pay_date.year if pay_date else _year_for_report(report)
    symbol_id = _string_or_none(row.get("Symbol ID"))
    symbol, exchange = _split_symbol_id(symbol_id)
    isin = _normalize_isin(row.get("ISIN"))
    instrument = _lookup_instrument(instrument_lookup, symbol=symbol, symbol_id=symbol_id, isin=isin, year=year) or {}
    currency = _dividend_currency(row) or _string_or_none(row.get("Asset")) or _string_or_none(instrument.get("_currency"))
    return _dividend_canonical_row(
        report,
        pay_date=pay_date,
        symbol=symbol,
        isin=isin or _string_or_none(instrument.get("isin")),
        country=_dividend_country_from_comment(row.get("Comment"))
        or _string_or_none(instrument.get("country"))
        or _country_from_isin_or_exchange(isin, exchange),
        currency=currency,
        gross_amount=Decimal("0"),
        withholding_tax=withholding_tax,
        fx_provider=fx_provider,
        warnings=warnings,
    )


def _dividend_canonical_row(
    report: ParsedExanteReport,
    *,
    pay_date: datetime | None,
    symbol: str | None,
    isin: str | None,
    country: str | None,
    currency: str | None,
    gross_amount: Decimal,
    withholding_tax: Decimal,
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> dict[str, Any]:
    year = pay_date.year if pay_date else _year_for_report(report)
    net_amount = gross_amount + withholding_tax
    rate = _annual_rate(fx_provider, year, currency, warnings)
    tax = gross_amount * Decimal("0.10")
    return {
        "date": _date_to_iso(pay_date),
        "pay_date": _date_to_iso(pay_date),
        "symbol": symbol,
        "isin": isin,
        "country": country,
        "currency": currency,
        "gross_amount": _money_text(gross_amount),
        "withholding_tax": _money_text(withholding_tax),
        "net_amount": _money_text(net_amount),
        "kzt_rate": str(rate) if rate is not None else None,
        "gross_amount_kzt": _amount_kzt(gross_amount, rate),
        "tax": str(tax),
        "tax_kzt": _amount_kzt(tax, rate),
        "offshore_flag": None,
        "kase_aix_preferential_flag": None,
        "source_report": str(report.path),
    }


def _build_interest_and_coupons(
    reports: Sequence[ParsedExanteReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    interest_rows: list[dict[str, Any]] = []
    coupon_rows: list[dict[str, Any]] = []
    for report in reports:
        for row in report.rows.get(EXANTE_SECTION_TRANSACTIONS, []):
            operation_type = _string_or_none(row.get("Operation type"))
            if operation_type not in {"INTEREST", "COUPON"}:
                continue
            event_dt = _parse_datetime(row.get("When"))
            year = event_dt.year if event_dt else _year_for_report(report)
            currency = _string_or_none(row.get("Asset"))
            amount = _decimal(row.get("Sum"))
            rate = _annual_rate(fx_provider, year, currency, warnings)
            base = {
                "date": _date_to_iso(event_dt),
                "currency": currency,
                "gross_amount": str(amount),
                "withholding_tax": "0",
                "net_amount": str(amount),
                "kzt_rate": str(rate) if rate is not None else None,
                "gross_amount_kzt": _amount_kzt(amount, rate),
                "withholding_tax_kzt": "0",
                "net_amount_kzt": _amount_kzt(amount, rate),
                "source_report": str(report.path),
            }
            if operation_type == "INTEREST":
                interest_rows.append({"description": _none_text(row.get("Comment")), **base})
            else:
                symbol_id = _string_or_none(row.get("Symbol ID"))
                symbol, exchange = _split_symbol_id(symbol_id)
                isin = _normalize_isin(row.get("ISIN"))
                instrument = _lookup_instrument(instrument_lookup, symbol=symbol, symbol_id=symbol_id, isin=isin, year=year) or {}
                country = _string_or_none(instrument.get("country")) or _country_from_isin_or_exchange(isin, exchange)
                coupon_rows.append(
                    {
                        "symbol": symbol,
                        "isin": isin or _string_or_none(instrument.get("isin")),
                        "country": country,
                        "offshore_flag": None,
                        **base,
                    }
                )
    return interest_rows, coupon_rows


def _build_cash_balances(
    reports: Sequence[ParsedExanteReport],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    snapshot_dates = _latest_snapshot_dates_by_year(reports, EXANTE_SECTION_CASH_BALANCES)
    for report in reports:
        for row in report.rows.get(EXANTE_SECTION_CASH_BALANCES, []):
            snapshot_dt = _parse_datetime(row.get("snapshot_date"))
            if snapshot_dt is None or snapshot_dt.date() not in snapshot_dates:
                continue
            currency = _string_or_none(row.get("ISO"))
            ending_cash = _decimal(row.get("Value"))
            rate = _annual_rate(fx_provider, snapshot_dt.year, currency, warnings)
            rows.append(
                {
                    "year": snapshot_dt.year,
                    "date": snapshot_dt.date().isoformat(),
                    "currency": currency,
                    "ending_cash": str(ending_cash),
                    "ending_cash_kzt": _amount_kzt(ending_cash, rate),
                    "source_report": str(report.path),
                }
            )
    return rows


def _build_corporate_actions(
    reports: Sequence[ParsedExanteReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        for row in report.rows.get(EXANTE_SECTION_TRANSACTIONS, []):
            operation_type = _string_or_none(row.get("Operation type"))
            if operation_type not in {"CORPORATE ACTION", "STOCK SPLIT"}:
                continue
            action_dt = _parse_datetime(row.get("When"))
            symbol_id = _string_or_none(row.get("Symbol ID"))
            symbol, exchange = _split_symbol_id(symbol_id)
            isin = _normalize_isin(row.get("ISIN"))
            instrument = _lookup_instrument(
                instrument_lookup,
                symbol=symbol,
                symbol_id=symbol_id,
                isin=isin,
                year=action_dt.year if action_dt else _year_for_report(report),
            ) or {}
            rows.append(
                {
                    "date": _date_to_iso(action_dt),
                    "symbol": symbol,
                    "isin": isin or _string_or_none(instrument.get("isin")),
                    "action_type": "split" if operation_type == "STOCK SPLIT" else "corporate_action",
                    "description": _none_text(row.get("Comment")) or operation_type,
                    "quantity": str(_decimal(row.get("Sum"))),
                    "proceeds": "0",
                    "value": "0",
                    "currency": _string_or_none(instrument.get("_currency")) or _currency_from_exchange(exchange),
                    "realized_pl": "0",
                    "source_report": str(report.path),
                }
            )
    return rows


def _build_unprocessed_trade_rows(reports: Sequence[ParsedExanteReport]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        for idx, row in enumerate(report.rows.get(EXANTE_SECTION_TRADES, []), start=1):
            if _trade_side_sign(row) is not None:
                continue
            event_dt = _parse_datetime(row.get("Time"))
            symbol_id = _string_or_none(row.get("Symbol ID"))
            symbol, _exchange = _split_symbol_id(symbol_id)
            side = str(row.get("Side") or "").strip()
            rows.append(
                {
                    "severity": "error",
                    "reason": "unsupported_exante_trade_side",
                    "details": f"Unsupported Exante trade side: {side or '<blank>'}",
                    "source_sheet": "Trades",
                    "source_report": str(report.path),
                    "trade_id": _exante_trade_id(report, idx, row),
                    "date_time": event_dt.isoformat(sep=" ") if event_dt else None,
                    "symbol": symbol,
                    "isin": _normalize_isin(row.get("ISIN")),
                    "asset_type": _asset_type(row.get("Type"), symbol_id),
                    "currency": _string_or_none(row.get("Currency")),
                    "quantity": str(_decimal(row.get("Quantity"))),
                    "price": str(_decimal(row.get("Price"))),
                    "amount": str(abs(_decimal(row.get("Traded Volume")))),
                    "commission": str(abs(_decimal(row.get("Commission")))),
                }
            )
    return rows


def _build_unprocessed_transaction_rows(reports: Sequence[ParsedExanteReport]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        for row in report.rows.get(EXANTE_SECTION_TRANSACTIONS, []):
            operation_type = str(row.get("Operation type") or "").strip().upper()
            if _is_option_expiration_transaction(row):
                continue
            if operation_type in HANDLED_TRANSACTION_OPERATION_TYPES:
                continue
            event_dt = _parse_datetime(row.get("When"))
            symbol_id = _string_or_none(row.get("Symbol ID"))
            symbol, _exchange = _split_symbol_id(symbol_id)
            rows.append(
                {
                    "severity": "warning",
                    "reason": "unhandled_exante_transaction",
                    "details": f"Unhandled Exante operation type: {operation_type}",
                    "source_sheet": "Transactions",
                    "source_report": str(report.path),
                    "trade_id": _string_or_none(row.get("Transaction ID")),
                    "date_time": event_dt.isoformat(sep=" ") if event_dt else None,
                    "symbol": symbol,
                    "isin": _normalize_isin(row.get("ISIN")),
                    "asset_type": None,
                    "currency": _string_or_none(row.get("Asset")),
                    "quantity": None,
                    "price": None,
                    "amount": str(_decimal(row.get("Sum"))),
                    "commission": None,
                }
            )
    return rows


def _populate_raw_totals(
    totals: RawReportTotals,
    reports: Sequence[ParsedExanteReport],
    trades: Sequence[Mapping[str, Any]],
    dividends: Sequence[Mapping[str, Any]],
    transfer_totals_by_currency: Mapping[str, Decimal],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> None:
    gross_trades = Decimal("0")
    commissions = Decimal("0")
    realized_pl = Decimal("0")
    interest = Decimal("0")
    coupons = Decimal("0")
    ending_positions: dict[tuple[int | None, str | None], Decimal] = defaultdict(Decimal)
    use_snapshot_positions = _has_position_snapshots(reports)

    for trade in trades:
        trade_dt = _parse_datetime(trade.get("date_time"))
        year = trade_dt.year if trade_dt else None
        currency = _string_or_none(trade.get("currency"))
        instrument_key = _string_or_none(trade.get("isin") or trade.get("symbol"))
        position_instrument_key = _string_or_none(trade.get("isin") or trade.get("symbol"))
        amount = _decimal(trade.get("amount"))
        commission = _decimal(trade.get("commission"))
        gross_trades += amount
        commissions += commission
        if _is_fx_trade(trade):
            realized_pl += _decimal(trade.get("_broker_realized_pl")) - commission
        elif trade.get("_broker_realized_pl") not in (None, ""):
            realized_pl += _decimal(trade.get("_broker_realized_pl"))
        turnover_key = _dimension_key(
            metric=ReconciliationMetric.TRADE_GROSS_AMOUNT_BY_INSTRUMENT.value,
            year=year,
            currency=currency,
            instrument_key=instrument_key,
        )
        totals.totals_by_metric_currency[turnover_key] = (
            totals.totals_by_metric_currency.get(turnover_key, Decimal("0")) + amount
        )
        if not use_snapshot_positions:
            ending_positions[(year, position_instrument_key)] += _decimal(trade.get("quantity"))

    if use_snapshot_positions:
        _populate_snapshot_positions(totals, reports, instrument_lookup)
    else:
        for report in reports:
            for row in report.rows.get(EXANTE_SECTION_TRANSACTIONS, []):
                if not _is_security_transfer_transaction(row):
                    continue
                event_dt = _parse_datetime(row.get("When"))
                year = event_dt.year if event_dt else _year_for_report(report)
                symbol_id = _string_or_none(row.get("Symbol ID"))
                symbol, exchange = _split_symbol_id(symbol_id)
                currency = _currency_from_exchange(exchange)
                instrument = _lookup_instrument(instrument_lookup, symbol=symbol, symbol_id=symbol_id, isin=_normalize_isin(row.get("ISIN")), year=year) or {}
                instrument_key = _string_or_none(instrument.get("isin")) or _normalize_isin(row.get("ISIN")) or symbol
                if not instrument_key:
                    continue
                ending_positions[(year, instrument_key)] += _decimal(row.get("Sum"))

        for (year, instrument_key), quantity in ending_positions.items():
            if abs(quantity) <= Decimal("0.0001"):
                continue
            totals.positions_by_key[_dimension_key(year=year, instrument_key=instrument_key)] = quantity

    _populate_snapshot_cash_balances(totals, reports)

    for report in reports:
        for row in report.rows.get(EXANTE_SECTION_TRANSACTIONS, []):
            operation_type = _string_or_none(row.get("Operation type"))
            if operation_type == "INTEREST":
                interest += _decimal(row.get("Sum"))
            elif operation_type == "COUPON":
                coupons += _decimal(row.get("Sum"))

    dividends_gross = sum((_decimal(row.get("gross_amount")) for row in dividends), Decimal("0"))
    dividends_tax = sum((_decimal(row.get("withholding_tax")) for row in dividends), Decimal("0"))
    cash_transfer_total = sum(transfer_totals_by_currency.values(), Decimal("0"))
    totals.scalar_totals.update(
        {
            ReconciliationMetric.TOTAL_TRADES_GROSS_AMOUNT.value: gross_trades,
            ReconciliationMetric.TOTAL_COMMISSIONS.value: commissions,
            ReconciliationMetric.TOTAL_DIVIDENDS_GROSS.value: dividends_gross,
            ReconciliationMetric.TOTAL_DIVIDENDS_TAX.value: dividends_tax,
            ReconciliationMetric.TOTAL_DIVIDENDS_NET.value: dividends_gross + dividends_tax,
            ReconciliationMetric.TOTAL_INTEREST.value: interest,
            ReconciliationMetric.TOTAL_COUPONS.value: coupons,
            ReconciliationMetric.TOTAL_DEPOSITS_WITHDRAWALS_TRANSFERS.value: cash_transfer_total,
            ReconciliationMetric.REALIZED_PL.value: realized_pl,
        }
    )
    for currency, amount in transfer_totals_by_currency.items():
        totals.totals_by_metric_currency[
            _dimension_key(metric=ReconciliationMetric.TOTAL_DEPOSITS_WITHDRAWALS_TRANSFERS.value, currency=currency)
        ] = amount


def _has_position_snapshots(reports: Sequence[ParsedExanteReport]) -> bool:
    return any(report.rows.get(EXANTE_SECTION_OPEN_POSITIONS) for report in reports)


def _populate_snapshot_positions(
    totals: RawReportTotals,
    reports: Sequence[ParsedExanteReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> None:
    snapshot_dates = _latest_snapshot_dates_by_year(reports, EXANTE_SECTION_OPEN_POSITIONS)
    for report in reports:
        for row in report.rows.get(EXANTE_SECTION_OPEN_POSITIONS, []):
            snapshot_dt = _parse_datetime(row.get("snapshot_date"))
            if snapshot_dt is None or snapshot_dt.date() not in snapshot_dates:
                continue
            quantity = _decimal(row.get("QTY"))
            symbol_id = _string_or_none(row.get("Instrument"))
            symbol, _exchange = _split_symbol_id(symbol_id)
            instrument = _lookup_instrument(instrument_lookup, symbol=symbol, symbol_id=symbol_id, isin=_normalize_isin(row.get("ISIN")), year=snapshot_dt.year) or {}
            instrument_key = _string_or_none(instrument.get("isin")) or _normalize_isin(row.get("ISIN")) or symbol
            if not instrument_key:
                continue
            key = _dimension_key(
                year=snapshot_dt.year,
                instrument_key=instrument_key,
            )
            totals.positions_by_key[key] = totals.positions_by_key.get(key, Decimal("0")) + quantity


def _populate_snapshot_cash_balances(totals: RawReportTotals, reports: Sequence[ParsedExanteReport]) -> None:
    snapshot_dates = _latest_snapshot_dates_by_year(reports, EXANTE_SECTION_CASH_BALANCES)
    for report in reports:
        for row in report.rows.get(EXANTE_SECTION_CASH_BALANCES, []):
            snapshot_dt = _parse_datetime(row.get("snapshot_date"))
            if snapshot_dt is None or snapshot_dt.date() not in snapshot_dates:
                continue
            key = _dimension_key(year=snapshot_dt.year, currency=_string_or_none(row.get("ISO")))
            totals.cash_by_currency[key] = totals.cash_by_currency.get(key, Decimal("0")) + _decimal(row.get("Value"))


def _latest_snapshot_dates_by_year(reports: Sequence[ParsedExanteReport], section: str) -> set[date]:
    latest_by_year: dict[int, date] = {}
    for report in reports:
        for row in report.rows.get(section, []):
            snapshot_dt = _parse_datetime(row.get("snapshot_date"))
            if snapshot_dt is None:
                continue
            snapshot_date = snapshot_dt.date()
            current = latest_by_year.get(snapshot_date.year)
            if current is None or snapshot_date > current:
                latest_by_year[snapshot_date.year] = snapshot_date
    return set(latest_by_year.values())


def _max_report_year(
    reports: Sequence[ParsedExanteReport],
    trades: Sequence[Mapping[str, Any]],
    transfers: Sequence[Mapping[str, Any]],
) -> int | None:
    years: list[int] = []
    for report in reports:
        if report.period_end:
            years.append(report.period_end.year)
    for row in (*trades, *transfers):
        parsed = _parse_datetime(row.get("date_time") or row.get("date"))
        if parsed:
            years.append(parsed.year)
    return max(years) if years else None


def _year_for_report(report: ParsedExanteReport) -> int | None:
    return report.period_end.year if report.period_end else None


def _exante_trade_id(report: ParsedExanteReport, idx: int, row: Mapping[str, Any]) -> str:
    order_id = _string_or_none(row.get("Order Id"))
    order_pos = _string_or_none(row.get("Order pos"))
    if order_id:
        return f"{report.path.name}:{order_id}:{order_pos or idx}"
    return f"{report.path.name}:trade:{idx}"


def _split_symbol_id(symbol_id: str | None) -> tuple[str | None, str | None]:
    if not symbol_id or symbol_id == "None":
        return None, None
    option_match = EXANTE_OPTION_SYMBOL_RE.fullmatch(symbol_id)
    if option_match:
        return symbol_id, option_match.group("exchange").upper()
    if "." not in symbol_id:
        return symbol_id, None
    symbol, exchange = symbol_id.split(".", 1)
    return symbol or None, exchange or None


def _is_option_symbol_id(symbol_id: str | None) -> bool:
    return bool(symbol_id and EXANTE_OPTION_SYMBOL_RE.fullmatch(symbol_id))


def _asset_type(value: Any, symbol_id: str | None = None) -> str | None:
    if _is_option_symbol_id(symbol_id):
        return "Equity and Index Options"
    text = _string_or_none(value)
    if text is None:
        return None
    normalized = text.strip().upper()
    if normalized in {"STOCK", "ETF", "EQUITY"}:
        return "Stocks"
    if normalized in {"OPTION", "OPTIONS"}:
        return "Equity and Index Options"
    if normalized in {"FUTURE", "FUTURES"}:
        return "Futures"
    if normalized in {"FOREX", "FX"}:
        return "Forex"
    if normalized in {"BOND", "BONDS"}:
        return "Bonds"
    return text


def _normalize_isin(value: Any) -> str | None:
    text = _string_or_none(value)
    if text is None or text == "None":
        return None
    if ISIN_RE.fullmatch(text):
        return text
    return None


def _country_from_isin_or_exchange(isin: str | None, exchange: str | None) -> str | None:
    if isin:
        country = isin[:2]
        return "BE" if country == "XS" else country
    exchange_country = {
        "ARCA": "US",
        "NASDAQ": "US",
        "NYSE": "US",
        "AMEX": "US",
        "CBOE": "US",
        "MOEX": "RU",
        "SPB": "RU",
        "LSE": "GB",
        "XETRA": "DE",
        "FWB": "DE",
        "HKEX": "HK",
        "TSE": "JP",
        "KASE": "KZ",
        "AIX": "KZ",
    }
    return exchange_country.get(str(exchange or "").upper())


def _currency_from_exchange(exchange: str | None) -> str | None:
    exchange_currency = {
        "ARCA": "USD",
        "NASDAQ": "USD",
        "NYSE": "USD",
        "AMEX": "USD",
        "CBOE": "USD",
        "MOEX": "RUB",
        "SPB": "RUB",
        "LSE": "USD",
        "XETRA": "EUR",
        "FWB": "EUR",
        "HKEX": "HKD",
        "TSE": "JPY",
        "KASE": "KZT",
        "AIX": "USD",
    }
    return exchange_currency.get(str(exchange or "").upper())


def _transaction_currency(row: Mapping[str, Any], symbol_id: str | None, exchange: str | None) -> str | None:
    asset = _string_or_none(row.get("Asset"))
    if asset and asset != "None" and asset != symbol_id and len(asset) == 3:
        return asset
    return _currency_from_exchange(exchange)


def _dividend_gross_amount(row: Mapping[str, Any]) -> Decimal:
    comment = _none_text(row.get("Comment")) or ""
    match = DIVIDEND_AMOUNT_RE.search(comment)
    if match:
        return _decimal(match.group("amount"))
    return _decimal(row.get("Sum"))


def _dividend_currency(row: Mapping[str, Any]) -> str | None:
    comment = _none_text(row.get("Comment")) or ""
    match = DIVIDEND_AMOUNT_RE.search(comment)
    return match.group("currency") if match else None


def _dividend_withholding_tax(row: Mapping[str, Any]) -> Decimal:
    comment = _none_text(row.get("Comment")) or ""
    match = DIVIDEND_TAX_RE.search(comment)
    return _decimal(match.group("tax")) if match else Decimal("0")


def _dividend_pay_date(value: Any) -> datetime | None:
    comment = _none_text(value) or ""
    match = DIVIDEND_PAY_DATE_RE.search(comment)
    return _parse_datetime(match.group("date")) if match else None


def _dividend_country_from_comment(value: Any) -> str | None:
    comment = _none_text(value) or ""
    match = DIVIDEND_COUNTRY_RE.search(comment)
    return match.group("country").upper() if match else None


def _none_text(value: Any) -> str | None:
    text = _string_or_none(value)
    if text is None or text == "None":
        return None
    return text


def _date_to_iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()


def _is_year_end(value: date | datetime) -> bool:
    return value.month == 12 and value.day == 31


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _dimension_key(
    *,
    metric: str | None = None,
    year: int | None = None,
    currency: str | None = None,
    instrument_key: str | None = None,
) -> str:
    return "|".join("" if value is None else str(value) for value in (metric, year, currency, instrument_key))
