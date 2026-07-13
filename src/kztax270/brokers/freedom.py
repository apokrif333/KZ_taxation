"""Native Freedom XLSX parser."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
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
    _apply_broker_country_to_forex_trades,
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
    _normalize_multiplier,
    _sort_trades_by_datetime,
    _string_or_none,
)

FREEDOM_BASE_CURRENCY = "USD"
BROKER_CODE = "freedom"

SECTION_TRADES = "Trades"
SECTION_COMMISSIONS = "Commissions"
SECTION_CORPACTIONS = "Corpactions"
SECTION_CASH_IN_OUT = "Cash In Out"
SECTION_CASH_FLOWS = "Cash Flows"
SECTION_SECURITIES = "Securities"
SECTION_SEC_IN_OUT = "Sec In Out"

COL_TICKER = "Тикер"
COL_ISIN = "ISIN"
COL_MARKET = "Рынок"
COL_OPERATION = "Операция"
COL_QTY = "Количество"
COL_PRICE = "Цена"
COL_CURRENCY = "Валюта"
COL_AMOUNT = "Сумма"
COL_REALIZED_PL = "P/L по закрытым сделкам"
COL_COMMISSION = "Комиссия"
COL_TRADE_DATE = "Дата сделки"
COL_ORDER_ID = "Id/OrderId"
COL_TYPE = "Тип"
COL_DATE = "Дата"
COL_ASSET = "Актив"
COL_PER_ONE = "На 1"
COL_RECORD_QTY = "Бумаг на дату фиксации"
COL_SOURCE_TAX = "Налог у источника"
COL_BROKER_TAX = "Налог у брокера"
COL_COMMENT = "Комментарий"
COL_ACCOUNT = "Счет"
COL_ASSET_TYPE = "Тип актива"
COL_START_QTY = "На начало"
COL_END_QTY = "На конец"
COL_END_CASH = "Остаток на конец периода"

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    COL_TICKER: ("Ticker", "Symbol", "Symbol ID"),
    COL_ISIN: ("Security ID", "Security_ID"),
    COL_MARKET: ("Market", "Exchange"),
    COL_OPERATION: ("Operation",),
    COL_QTY: ("Quantity", "Qty"),
    COL_PRICE: ("Price",),
    COL_CURRENCY: ("Currency",),
    COL_AMOUNT: ("Amount", "Sum"),
    COL_REALIZED_PL: ("Прибыль", "Realized P/L", "P/L", "P/L on closed trades", "P/L on Closed Trades"),
    COL_COMMISSION: ("Commission",),
    COL_TRADE_DATE: ("Дата", "Trade Date", "Date_Time", "Date Time"),
    COL_ORDER_ID: ("Order ID", "OrderId", "Order Id", "Id/OrderId"),
    COL_TYPE: ("Type",),
    COL_DATE: ("Date",),
    COL_ASSET: ("Asset",),
    COL_PER_ONE: ("Per 1", "Per security", "Per Security"),
    COL_RECORD_QTY: ("Balance on record date", "Quantity on record date"),
    COL_SOURCE_TAX: ("Source tax", "Withholding tax", "Withholding Tax"),
    COL_BROKER_TAX: ("Broker tax", "Broker Tax"),
    COL_COMMENT: ("Comment", "Description", "Broker Comment", "Broker_Comment"),
    COL_ACCOUNT: ("Account",),
    COL_ASSET_TYPE: ("Asset Type", "Asset_Category", "Asset Category"),
    COL_START_QTY: ("Beginning Quantity", "Start Quantity", "Start Qty", "Prior Quantity"),
    COL_END_QTY: ("Ending Quantity", "End Quantity", "End Qty"),
    COL_END_CASH: ("Ending Cash", "End Cash", "Ending balance"),
}


@dataclass(slots=True)
class ParsedFreedomReport:
    path: Path
    account_id: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    base_currency: str | None = FREEDOM_BASE_CURRENCY
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))


class FreedomParser:
    broker_code = BROKER_CODE

    def __init__(
        self,
        fx_provider: AnnualFxRateProvider | None = None,
        transfer_in_resolver: TransferInFifoResolver | None = None,
    ) -> None:
        self.fx_provider = fx_provider or AnnualFxRateProvider({})
        self.transfer_in_resolver = transfer_in_resolver

    def discover_reports(self, raw_root: Path, account_id: str) -> list[BrokerReport]:
        reports = discover_raw_reports(raw_root, DiscoveryRule(broker=BROKER_CODE, account_id=account_id, extensions=frozenset({".xlsx", ".xls"})))
        result: list[BrokerReport] = []
        for report in reports:
            if _is_ignored_freedom_report_path(report.path):
                continue
            period_start, period_end = _period_from_filename(report.path.name)
            result.append(replace(report, period_start=period_start, period_end=period_end))
        return result

    def parse_reports(self, reports: Sequence[BrokerReport], account_id: str) -> ParseResult:
        parsed_reports = [parse_freedom_report(report.path, account_id=account_id) for report in reports]
        dataset = build_canonical_dataset(
            parsed_reports,
            account_id,
            self.fx_provider,
            transfer_in_resolver=self.transfer_in_resolver,
        )
        dataset.raw_totals.source_reports = [str(report.path) for report in reports]
        return ParseResult(broker=self.broker_code, account_id=account_id, reports=reports, dataset=dataset, raw_totals=dataset.raw_totals)


def parse_freedom_report(path: Path, *, account_id: str | None = None) -> ParsedFreedomReport:
    import pandas as pd  # type: ignore

    parsed = ParsedFreedomReport(path=path, account_id=account_id)
    start_from_name, end_from_name = _period_from_filename(path.name)
    parsed.period_start = start_from_name
    parsed.period_end = end_from_name

    workbook = pd.ExcelFile(path)
    try:
        sheet_names = list(workbook.sheet_names)
    finally:
        workbook.close()
    for sheet_name in sheet_names:
        if sheet_name == "Worksheet":
            continue
        section = _section_name(sheet_name)
        if section.startswith("Account at "):
            report_date = _parse_yyyymmdd(section.rsplit(" ", 1)[-1])
            if report_date is not None:
                parsed.period_start = min(parsed.period_start, report_date) if parsed.period_start else report_date
                parsed.period_end = max(parsed.period_end, report_date) if parsed.period_end else report_date
            continue

        df = pd.read_excel(path, sheet_name=sheet_name)
        if df.empty:
            parsed.rows.setdefault(section, [])
            continue
        df.columns = [str(column).strip() for column in df.columns]
        for row in df.dropna(how="all").to_dict("records"):
            _clean_record(row)
            row["source_report"] = str(path)
            row["source_sheet"] = sheet_name
            parsed.rows[section].append(row)
    return parsed


def build_canonical_dataset(
    reports: Sequence[ParsedFreedomReport],
    account_id: str,
    fx_provider: AnnualFxRateProvider,
    *,
    transfer_in_resolver: TransferInFifoResolver | None = None,
) -> CanonicalDataset:
    dataset = CanonicalDataset(metadata=AccountMetadata(broker=BROKER_CODE, account_id=account_id, base_currency=FREEDOM_BASE_CURRENCY))

    instruments = _build_instruments(reports, account_id)
    instrument_lookup = _instrument_lookup(instruments)
    symbol_history = _instrument_symbol_history(instruments)
    dataset.tables["Instruments"] = instruments

    corporate_actions = _build_corporate_actions(reports, instrument_lookup)
    dataset.tables["CorporateActions"] = _canonical_corporate_actions(corporate_actions)

    trades = _build_trades(reports, instrument_lookup)
    trades = _apply_identity_changes_to_trades(trades, corporate_actions, instrument_lookup)
    synthetic_trades = _build_corporate_action_trades(corporate_actions, instrument_lookup)
    internal_trades = _sort_trades_by_datetime([*trades, *synthetic_trades])
    _apply_broker_country_to_forex_trades(internal_trades, BROKER_CODE)
    dataset.tables["Trades"] = _canonical_trade_rows([trade for trade in internal_trades if trade.get("_event_type") != "split"])

    transfers, transfer_totals_by_currency = _build_transfers(reports, instrument_lookup, internal_trades)
    fifo_transfers = [row for row in transfers if not row.get("_exclude_from_fifo")]
    fifo_input_trades = [trade for trade in internal_trades if not _is_fx_trade(trade)]
    fifo_rows, fifo_positions, transfer_rows = _build_fifo_and_positions(
        fifo_input_trades,
        transfers=fifo_transfers,
        initial_lots=[],
        max_year=_max_report_year(reports, internal_trades, transfers),
        fx_provider=fx_provider,
        warnings=dataset.warnings,
        symbol_history=symbol_history,
        transfer_in_resolver=transfer_in_resolver,
        broker_cost_basis_method="average",
    )
    fifo_rows.extend(_build_fx_fifo_rows(internal_trades, fx_provider, dataset.warnings))
    fifo_source_trade_ids = _fifo_source_trade_ids(fifo_rows)
    dataset.tables["_BrokerTradeRealizedPL"] = _build_broker_trade_realized_pl(
        [trade for trade in internal_trades if _none_text(trade.get("trade_id")) in fifo_source_trade_ids]
    )
    fifo_positions = _append_missing_raw_positions(fifo_positions, reports, instrument_lookup, fx_provider, dataset.warnings, corporate_actions)
    audit_transfer_rows = [row for row in transfers if row.get("_exclude_from_fifo")]
    dataset.tables["Fifo"] = fifo_rows
    dataset.tables["Positions"] = fifo_positions
    dataset.tables["Transfers"] = _canonical_transfer_rows([*transfer_rows, *audit_transfer_rows])

    dataset.tables["Dividends"] = _apply_ticker_changes_to_records(
        _build_dividends(reports, instrument_lookup, fx_provider, dataset.warnings),
        corporate_actions,
    )
    dataset.tables["Interest"] = _build_interest(reports, fx_provider, dataset.warnings)
    dataset.tables["Coupons"] = _apply_ticker_changes_to_records(
        _build_coupons(reports, instrument_lookup, fx_provider, dataset.warnings),
        corporate_actions,
    )
    dataset.tables["_TradeWithholdingTax"] = []
    dataset.tables["CashBalances"] = _build_cash_balances(reports, fx_provider, dataset.warnings)
    dataset.tables["Unprocessed"] = _build_unprocessed_rows(dataset.tables["Trades"], fifo_rows)
    dataset.tables["Years_Results"] = _build_years_results(dataset)

    _populate_raw_totals(
        dataset.raw_totals,
        reports,
        [trade for trade in internal_trades if trade.get("_event_type") != "split"],
        dataset.tables["Dividends"],
        dataset.tables["Interest"],
        dataset.tables["Coupons"],
        transfer_totals_by_currency,
        fifo_source_trade_ids,
        fifo_rows,
        dataset.tables["Positions"],
    )
    return dataset


def _section_name(sheet_name: str) -> str:
    if sheet_name.startswith("Account at "):
        return sheet_name.strip()
    return re.sub(r"\s+\d{8}\s+-\s+\d{8}$", "", sheet_name).strip()


def _period_from_filename(name: str) -> tuple[date | None, date | None]:
    matches = re.findall(r"(\d{4}-\d{2}-\d{2})", name)
    if len(matches) >= 2:
        return date.fromisoformat(matches[0]), date.fromisoformat(matches[1])
    compact_matches = re.findall(r"(\d{4})_(\d{2})_(\d{2})", name)
    if len(compact_matches) >= 2:
        start = date(*(int(part) for part in compact_matches[0]))
        end = date(*(int(part) for part in compact_matches[1]))
        return start, end
    return None, None


def _is_legacy_trading_report_path(path: Path) -> bool:
    return "trading report" in path.name.lower()


def _is_office_temp_file(path: Path) -> bool:
    return path.name.startswith("~$")


def _is_ignored_freedom_report_path(path: Path) -> bool:
    return _is_office_temp_file(path) or _is_legacy_trading_report_path(path)


def _parse_yyyymmdd(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value), "%Y%m%d").date()
    except ValueError:
        return None


def _build_instruments(reports: Sequence[ParsedFreedomReport], account_id: str) -> list[dict[str, Any]]:
    instruments: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None, int | None]] = set()

    def add(
        *,
        report: ParsedFreedomReport,
        symbol: str | None,
        isin: str | None,
        currency: str | None = None,
        exchange: str | None = None,
        asset_type: str | None = None,
        year: int | None = None,
        description: str | None = None,
    ) -> None:
        isin_norm = _normalize_isin(isin)
        symbol_norm = _clean_symbol(symbol) or isin_norm
        if not symbol_norm and not isin_norm:
            return
        instrument_year = year if year is not None else _year_for_report(report)
        key = (symbol_norm, isin_norm, instrument_year)
        if key in seen:
            return
        seen.add(key)
        country = _country_from_isin(isin_norm) or _country_from_symbol(symbol_norm)
        canonical_type = _asset_type(asset_type, symbol_norm)
        instruments.append(
            {
                "symbol": symbol_norm,
                "description": _none_text(description) or symbol_norm or isin_norm,
                "conid": None,
                "security_id": isin_norm,
                "underlying": None,
                "listing_exchange": _normalize_exchange(exchange),
                "multiplier": "1",
                "type": canonical_type,
                "asset_type": canonical_type,
                "code": None,
                "year": instrument_year,
                "expiry": None,
                "delivery_month": None,
                "strike": None,
                "issuer": None,
                "maturity": None,
                "cusip": isin_norm[2:11] if isin_norm and ISIN_RE.fullmatch(isin_norm) else None,
                "country": country,
                "isin": isin_norm,
                "figi": None,
                "issuer_country": country,
                "offshore_flag": None,
                "issuer_outside_kz_flag": None if country is None else country != "KZ",
                "preferential_tax_flag": None,
                "source_broker": BROKER_CODE,
                "source_account": account_id,
                "source_report": str(report.path),
                "as_of_date": report.period_end.isoformat() if report.period_end else None,
                "_currency": _normalize_currency(currency) or FREEDOM_BASE_CURRENCY,
            }
        )

    for report in reports:
        report_year = _year_for_report(report)
        for row in report.rows.get(SECTION_SECURITIES, []):
            add(
                report=report,
                symbol=_cell(row, COL_TICKER),
                isin=_cell(row, COL_ISIN),
                currency=_cell(row, COL_CURRENCY),
                asset_type=_cell(row, COL_ASSET_TYPE),
                year=report_year,
            )
        for row in report.rows.get(SECTION_TRADES, []):
            trade_dt = _parse_datetime(_cell(row, COL_TRADE_DATE))
            symbol = _clean_symbol(_cell(row, COL_TICKER))
            add(
                report=report,
                symbol=symbol,
                isin=_cell(row, COL_ISIN),
                currency=_cell(row, COL_CURRENCY),
                exchange=_cell(row, COL_MARKET),
                asset_type="Forex" if _is_currency_pair_symbol(symbol) else "Stocks",
                year=trade_dt.year if trade_dt else report_year,
            )
        for row in report.rows.get(SECTION_CORPACTIONS, []):
            action_dt = _parse_datetime(_cell(row, COL_DATE))
            add(
                report=report,
                symbol=_cell(row, COL_TICKER),
                isin=_cell(row, COL_ISIN),
                currency=_cell(row, COL_CURRENCY),
                asset_type="Stocks",
                year=action_dt.year if action_dt else report_year,
            )
        for row in report.rows.get(SECTION_SEC_IN_OUT, []):
            transfer_dt = _parse_datetime(_cell(row, COL_DATE))
            add(
                report=report,
                symbol=_cell(row, COL_TICKER),
                isin=_cell(row, COL_ISIN),
                asset_type="Stocks",
                year=transfer_dt.year if transfer_dt else report_year,
            )
    return _dedupe_instruments(instruments)


def _dedupe_instruments(instruments: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None, int | None]] = set()
    for row in sorted(instruments, key=lambda item: (item.get("year") or 0, str(item.get("symbol") or ""), 0 if item.get("isin") else 1)):
        key = (_none_text(row.get("symbol")), _none_text(row.get("isin")), _int_or_none(row.get("year")))
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(row))
    return result


def _instrument_lookup(instruments: Sequence[Mapping[str, Any]]) -> dict[tuple[str, int | None], dict[str, Any]]:
    lookup: dict[tuple[str, int | None], dict[str, Any]] = {}

    def assign(key_value: str | None, year: int | None, instrument: Mapping[str, Any]) -> None:
        if not key_value:
            return
        key = (key_value, year)
        existing = lookup.get(key)
        if existing is None or _prefer_instrument(instrument, existing):
            lookup[key] = dict(instrument)

    for instrument in instruments:
        year = _int_or_none(instrument.get("year"))
        symbol = _none_text(instrument.get("symbol"))
        isin = _none_text(instrument.get("isin"))
        if symbol:
            assign(symbol, year, instrument)
            assign(symbol, None, instrument)
            assign(_symbol_base(symbol), year, instrument)
            assign(_symbol_base(symbol), None, instrument)
        if isin:
            assign(isin, year, instrument)
            assign(isin, None, instrument)
            assign(isin[2:11], year, instrument)
            assign(isin[2:11], None, instrument)
    return lookup


def _prefer_instrument(candidate: Mapping[str, Any], existing: Mapping[str, Any]) -> bool:
    candidate_type = _none_text(candidate.get("type")) or _none_text(candidate.get("asset_type")) or "Stocks"
    existing_type = _none_text(existing.get("type")) or _none_text(existing.get("asset_type")) or "Stocks"
    if candidate_type != existing_type:
        if candidate_type != "Stocks" and existing_type == "Stocks":
            return True
        if candidate_type == "Stocks" and existing_type != "Stocks":
            return False
    candidate_exchange = _none_text(candidate.get("listing_exchange"))
    existing_exchange = _none_text(existing.get("listing_exchange"))
    if candidate_exchange and not existing_exchange:
        return True
    return False


def _lookup_instrument(
    lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    *,
    symbol: str | None = None,
    isin: str | None = None,
    year: int | None = None,
) -> dict[str, Any] | None:
    for key_value in (isin, symbol, _symbol_base(symbol)):
        key_value = _none_text(key_value)
        if not key_value:
            continue
        best: dict[str, Any] | None = None
        for key in ((key_value, year), (key_value, None)):
            candidate = lookup.get(key)
            if candidate is None:
                continue
            if best is None or _prefer_instrument(candidate, best):
                best = dict(candidate)
        if best is not None:
            return best
    return None


def _build_trades(reports: Sequence[ParsedFreedomReport], instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]]) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for report in reports:
        for idx, row in enumerate(report.rows.get(SECTION_TRADES, []), start=1):
            if _is_financing_operation(str(_cell(row, COL_OPERATION) or "")):
                continue
            trade = _new_trade_row(report, idx, row, instrument_lookup)
            if trade is not None:
                trades.append(trade)
    return trades


def _new_trade_row(
    report: ParsedFreedomReport,
    idx: int,
    row: Mapping[str, Any],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> dict[str, Any] | None:
    operation = str(_cell(row, COL_OPERATION) or "").strip()
    if not operation:
        return None
    trade_dt = _parse_datetime(_cell(row, COL_TRADE_DATE))
    year = trade_dt.year if trade_dt else _year_for_report(report)
    raw_symbol = _clean_symbol(_cell(row, COL_TICKER))
    raw_isin = _normalize_isin(_cell(row, COL_ISIN))
    instrument = _lookup_instrument(instrument_lookup, symbol=raw_symbol, isin=raw_isin, year=year) or {}
    symbol = _none_text(instrument.get("symbol")) or raw_symbol or raw_isin
    isin = _none_text(instrument.get("isin")) or raw_isin
    asset_type = _none_text(instrument.get("type")) or "Stocks"
    quantity_abs = abs(_decimal(_cell(row, COL_QTY)))
    if quantity_abs == 0:
        return None
    signed_quantity = quantity_abs if _is_buy_operation(operation) else -quantity_abs
    price = _decimal(_cell(row, COL_PRICE))
    broker_amount = abs(_decimal(_cell(row, COL_AMOUNT)))
    instrument_multiplier = _decimal(instrument.get("multiplier") or "1") or Decimal("1")
    multiplier = (
        _normalize_multiplier(instrument_multiplier)
        if asset_type == "Bonds"
        else _effective_transaction_multiplier(instrument_multiplier, signed_quantity, price, broker_amount)
    )
    amount = broker_amount if broker_amount else abs(quantity_abs * price * multiplier)
    commission = abs(_decimal(_cell(row, COL_COMMISSION)))
    currency = _normalize_currency(_cell(row, COL_CURRENCY)) or _normalize_currency(instrument.get("_currency")) or FREEDOM_BASE_CURRENCY
    identity_key = _instrument_identity_key_from_values(isin=isin, symbol=symbol)
    return {
        "date_time": trade_dt.isoformat(sep=" ") if trade_dt else None,
        "trade_id": _trade_id(report, idx, row),
        "trade_type": "trade",
        "symbol": symbol,
        "isin": isin,
        "asset_type": asset_type,
        "quantity": str(signed_quantity),
        "calculation_quantity": str(signed_quantity),
        "price": str(price),
        "calculation_price": str(price),
        "multiplier": _multiplier_text(multiplier),
        "_calculation_multiplier": str(multiplier),
        "amount": str(amount),
        "commission": str(commission),
        "amount_with_commission": str(amount + commission),
        "currency": currency,
        "exchange": _normalize_exchange(_cell(row, COL_MARKET) or instrument.get("listing_exchange")),
        "country": _country_from_isin(isin) or _none_text(instrument.get("country")),
        "source_report": str(report.path),
        "_instrument_identity_key": identity_key,
        "_broker_realized_pl": str(_decimal(_cell(row, COL_REALIZED_PL))),
        "_broker_realized_pl_includes_commissions": False,
    }


def _build_corporate_actions(
    reports: Sequence[ParsedFreedomReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    redemption_proceeds = _redemption_proceeds_by_key(reports)
    for report in reports:
        for idx, row in enumerate(report.rows.get(SECTION_CORPACTIONS, []), start=1):
            action_type = _corporate_action_type(_cell(row, COL_TYPE), _cell(row, COL_ASSET))
            if action_type is None:
                continue
            event_dt = _parse_datetime(_cell(row, COL_DATE))
            isin = _normalize_isin(_cell(row, COL_ISIN))
            symbol = _clean_symbol(_cell(row, COL_TICKER)) or isin
            asset = str(_cell(row, COL_ASSET) or "").strip()
            quantity = _decimal(_cell(row, COL_AMOUNT)) if _is_security_asset(asset) else Decimal("0")
            proceeds = Decimal("0")
            if action_type == "redemption" and _is_security_asset(asset):
                proceeds = redemption_proceeds.get((_date_key(event_dt), isin), Decimal("0"))
            elif action_type in {"conversion_compensation", "spinoff_compensation"} and _is_money_asset(asset):
                proceeds = abs(_decimal(_cell(row, COL_AMOUNT)))
            actions.append(
                {
                    "date": event_dt.date().isoformat() if event_dt else None,
                    "date_time": event_dt.isoformat(sep=" ") if event_dt else None,
                    "symbol": symbol,
                    "isin": isin,
                    "action_type": action_type,
                    "description": _none_text(_cell(row, COL_COMMENT)),
                    "quantity": str(quantity),
                    "proceeds": str(proceeds),
                    "value": str(_decimal(_cell(row, COL_AMOUNT))),
                    "currency": _normalize_currency(_cell(row, COL_CURRENCY)) or FREEDOM_BASE_CURRENCY,
                    "realized_pl": "0",
                    "source_report": str(report.path),
                    "_asset": asset,
                    "_amount_per_one": str(_decimal(_cell(row, COL_PER_ONE))),
                    "_record_qty": str(_decimal(_cell(row, COL_RECORD_QTY))),
                    "_source_index": idx,
                }
            )
        actions.extend(_internal_ticker_change_actions(report, instrument_lookup))
        actions.extend(_internal_depository_change_actions(report, instrument_lookup))
    return actions


def _canonical_corporate_actions(actions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    columns = ("date", "symbol", "isin", "action_type", "description", "quantity", "proceeds", "value", "currency", "realized_pl", "source_report")
    return [{column: action.get(column) for column in columns} for action in actions]


def _internal_ticker_change_actions(
    report: ParsedFreedomReport,
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str | None, str | None, Decimal, str], dict[str, Any]] = {}
    for idx, row in enumerate(report.rows.get(SECTION_SEC_IN_OUT, []), start=1):
        transfer_type = str(_cell(row, COL_TYPE) or "").strip()
        broker_comment = _none_text(_cell(row, COL_COMMENT) or transfer_type)
        if not _is_detected_internal_ticker_change_transfer(transfer_type, broker_comment):
            continue
        event_dt = _parse_datetime(_cell(row, COL_DATE))
        quantity = _decimal(_cell(row, COL_QTY))
        if event_dt is None or quantity == 0:
            continue
        symbol = _clean_symbol(_cell(row, COL_TICKER))
        isin = _normalize_isin(_cell(row, COL_ISIN))
        comment_key = _normalized_comment_key(broker_comment)
        key = (event_dt.isoformat(sep=" "), isin, abs(quantity), comment_key)
        bucket = grouped.setdefault(
            key,
            {
                "event_dt": event_dt,
                "isin": isin,
                "quantity": abs(quantity),
                "comment": comment_key,
                "source_report": str(report.path),
                "rows": [],
            },
        )
        bucket["rows"].append((idx, symbol, quantity))

    actions: list[dict[str, Any]] = []
    for bucket in grouped.values():
        year = bucket["event_dt"].year
        outgoing = sorted((item for item in bucket["rows"] if item[2] < 0), key=lambda item: item[0])
        incoming = sorted((item for item in bucket["rows"] if item[2] > 0), key=lambda item: item[0])
        pair_count = min(len(outgoing), len(incoming))
        for pair_idx in range(pair_count):
            _, old_symbol, _ = outgoing[pair_idx]
            _, new_symbol, _ = incoming[pair_idx]
            instrument = _lookup_instrument(instrument_lookup, symbol=new_symbol or old_symbol, isin=bucket["isin"], year=year) or {}
            description = f"Ticker change {old_symbol or bucket['isin']} -> {new_symbol or bucket['isin']}"
            actions.append(
                {
                    "date": bucket["event_dt"].date().isoformat(),
                    "date_time": bucket["event_dt"].isoformat(sep=" "),
                    "symbol": old_symbol or new_symbol or bucket["isin"],
                    "isin": bucket["isin"],
                    "action_type": "ticker_change",
                    "description": description,
                    "quantity": str(bucket["quantity"]),
                    "proceeds": "0",
                    "value": "0",
                    "currency": _normalize_currency(instrument.get("_currency")) or FREEDOM_BASE_CURRENCY,
                    "realized_pl": "0",
                    "source_report": str(bucket["source_report"]),
                }
            )
    return actions


def _internal_depository_change_actions(
    report: ParsedFreedomReport,
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str | None, str | None, Decimal, str], dict[str, Any]] = {}
    for idx, row in enumerate(report.rows.get(SECTION_SEC_IN_OUT, []), start=1):
        transfer_type = str(_cell(row, COL_TYPE) or "").strip()
        broker_comment = _none_text(_cell(row, COL_COMMENT) or transfer_type)
        if not _is_detected_internal_depository_change_transfer(transfer_type, broker_comment):
            continue
        event_dt = _parse_datetime(_cell(row, COL_DATE))
        quantity = _decimal(_cell(row, COL_QTY))
        if event_dt is None or quantity == 0:
            continue
        symbol = _clean_symbol(_cell(row, COL_TICKER))
        isin = _normalize_isin(_cell(row, COL_ISIN))
        comment_key = _normalized_comment_key(broker_comment)
        key = (event_dt.isoformat(sep=" "), isin, abs(quantity), comment_key)
        bucket = grouped.setdefault(
            key,
            {
                "event_dt": event_dt,
                "isin": isin,
                "quantity": abs(quantity),
                "comment": comment_key,
                "source_report": str(report.path),
                "rows": [],
            },
        )
        bucket["rows"].append((idx, symbol, quantity))

    actions: list[dict[str, Any]] = []
    for bucket in grouped.values():
        year = bucket["event_dt"].year
        outgoing = sorted((item for item in bucket["rows"] if item[2] < 0), key=lambda item: item[0])
        incoming = sorted((item for item in bucket["rows"] if item[2] > 0), key=lambda item: item[0])
        pair_count = min(len(outgoing), len(incoming))
        for pair_idx in range(pair_count):
            _, old_symbol, _ = outgoing[pair_idx]
            _, new_symbol, _ = incoming[pair_idx]
            symbol = old_symbol or new_symbol
            instrument = _lookup_instrument(instrument_lookup, symbol=symbol, isin=bucket["isin"], year=year) or {}
            actions.append(
                {
                    "date": bucket["event_dt"].date().isoformat(),
                    "date_time": bucket["event_dt"].isoformat(sep=" "),
                    "symbol": symbol or bucket["isin"],
                    "isin": bucket["isin"],
                    "action_type": "depository_change",
                    "description": f"Depository change {symbol or bucket['isin']}",
                    "quantity": str(bucket["quantity"]),
                    "proceeds": "0",
                    "value": "0",
                    "currency": _normalize_currency(instrument.get("_currency")) or FREEDOM_BASE_CURRENCY,
                    "realized_pl": "0",
                    "source_report": str(bucket["source_report"]),
                }
            )
    return actions


def _redemption_proceeds_by_key(reports: Sequence[ParsedFreedomReport]) -> dict[tuple[str | None, str | None], Decimal]:
    proceeds: dict[tuple[str | None, str | None], Decimal] = defaultdict(Decimal)
    for report in reports:
        for row in report.rows.get(SECTION_CORPACTIONS, []):
            if not _is_redemption_type(_cell(row, COL_TYPE)) or not _is_money_asset(_cell(row, COL_ASSET)):
                continue
            proceeds[(_date_key(_parse_datetime(_cell(row, COL_DATE))), _normalize_isin(_cell(row, COL_ISIN)))] += abs(_decimal(_cell(row, COL_AMOUNT)))
    return proceeds


def _corporate_action_type(action_value: Any, asset_value: Any) -> str | None:
    if _is_redemption_type(action_value):
        return "redemption"
    if _is_conversion_type(action_value):
        return "conversion_compensation" if _is_money_asset(asset_value) else "conversion"
    if _text_in(action_value, {"сплит", "split"}):
        return "split"
    if _text_in(action_value, {"спин-офф", "спинофф", "spin-off", "spinoff"}):
        return "spinoff"
    if _text_in(action_value, {"зачисление прав", "rights issue", "rights"}):
        return "rights"
    if _text_in(action_value, {"компенсация по корпоративному действию", "corporate action compensation"}):
        return "spinoff_compensation"
    return None


def _build_corporate_action_trades(
    actions: Sequence[Mapping[str, Any]],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for action in _net_corporate_actions_for_synthetic_trades(actions):
        action_type = _none_text(action.get("action_type"))
        if action_type == "redemption":
            trade = _synthetic_exit_trade(action, instrument_lookup, action_type)
        elif action_type in {"spinoff", "rights"}:
            trade = _synthetic_zero_cost_trade(action, instrument_lookup, action_type)
        elif action_type in {"conversion_compensation", "spinoff_compensation"}:
            trade = _synthetic_compensation_trade(action, instrument_lookup, action_type)
        elif action_type == "split":
            trade = _synthetic_split_event(action)
        else:
            trade = None
        if trade is not None:
            trades.append(trade)
    return trades


def _net_corporate_actions_for_synthetic_trades(actions: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    grouped: dict[tuple[str | None, str | None, str | None, str | None], list[Mapping[str, Any]]] = defaultdict(list)
    expected_spinoff_quantity_by_symbol: dict[str, Decimal] = {}
    for action in actions:
        if action.get("action_type") != "spinoff_compensation":
            continue
        symbol = _compensation_symbol(str(action.get("description") or ""))
        expected_quantity = _compensation_expected_quantity(action)
        if symbol and expected_quantity > 0:
            expected_spinoff_quantity_by_symbol[symbol] = max(expected_spinoff_quantity_by_symbol.get(symbol, Decimal("0")), expected_quantity)
    for action in actions:
        action_type = _none_text(action.get("action_type"))
        if action_type in {"spinoff", "rights"}:
            description = str(action.get("description") or "").replace("Reverted:", "").strip()
            grouped[(action_type, _none_text(action.get("isin")), _none_text(action.get("symbol")), description)].append(action)
        else:
            result.append(action)
    for group in grouped.values():
        quantity = sum((_decimal(action.get("quantity")) for action in group), Decimal("0"))
        if quantity <= 0:
            continue
        source = dict(group[-1])
        symbol = _none_text(source.get("symbol"))
        if source.get("action_type") == "spinoff" and symbol in expected_spinoff_quantity_by_symbol:
            quantity = max(quantity, expected_spinoff_quantity_by_symbol[symbol])
        source["quantity"] = str(quantity)
        result.append(source)
    return result


def _synthetic_exit_trade(action: Mapping[str, Any], instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]], action_type: str) -> dict[str, Any] | None:
    quantity = _decimal(action.get("quantity"))
    proceeds = _decimal(action.get("proceeds"))
    if quantity >= 0 or proceeds <= 0:
        return None
    action_dt = _parse_datetime(action.get("date_time") or action.get("date"))
    isin = _normalize_isin(action.get("isin"))
    if action_dt is None or not isin:
        return None
    instrument = _lookup_instrument(instrument_lookup, symbol=_none_text(action.get("symbol")), isin=isin, year=action_dt.year) or {}
    price = abs(proceeds / quantity)
    symbol = _none_text(instrument.get("symbol")) or _none_text(action.get("symbol")) or isin
    return _synthetic_trade_dict(action, action_dt, symbol, isin, quantity, price, proceeds, instrument, action_type)


def _synthetic_zero_cost_trade(action: Mapping[str, Any], instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]], action_type: str) -> dict[str, Any] | None:
    quantity = _decimal(action.get("quantity"))
    if quantity <= 0:
        return None
    action_dt = _parse_datetime(action.get("date_time") or action.get("date"))
    isin = _normalize_isin(action.get("isin"))
    if action_dt is None or not isin:
        return None
    instrument = _lookup_instrument(instrument_lookup, symbol=_none_text(action.get("symbol")), isin=isin, year=action_dt.year) or {}
    symbol = _none_text(instrument.get("symbol")) or _none_text(action.get("symbol")) or isin
    return _synthetic_trade_dict(action, action_dt, symbol, isin, quantity, Decimal("0"), Decimal("0"), instrument, action_type)


def _synthetic_compensation_trade(action: Mapping[str, Any], instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]], action_type: str) -> dict[str, Any] | None:
    proceeds = _decimal(action.get("proceeds"))
    if proceeds <= 0:
        return None
    action_dt = _parse_datetime(action.get("date_time") or action.get("date"))
    if action_dt is None:
        return None
    description = str(action.get("description") or "")
    symbol = _compensation_symbol(description) or _none_text(action.get("symbol"))
    instrument = _lookup_instrument(instrument_lookup, symbol=symbol, year=action_dt.year) or {}
    isin = _none_text(instrument.get("isin")) or _normalize_isin(action.get("isin"))
    if not isin:
        return None
    quantity = _compensation_quantity(action)
    if quantity == 0:
        price_hint = _decimal(action.get("_amount_per_one"))
        quantity = -(proceeds / price_hint) if price_hint else Decimal("0")
    if quantity >= 0:
        quantity = -abs(quantity)
    price = abs(proceeds / quantity) if quantity else _decimal(action.get("_amount_per_one"))
    symbol = _none_text(instrument.get("symbol")) or symbol or isin
    return _synthetic_trade_dict(action, action_dt, symbol, isin, quantity, price, proceeds, instrument, action_type)


def _synthetic_split_event(action: Mapping[str, Any]) -> dict[str, Any] | None:
    action_dt = _parse_datetime(action.get("date_time") or action.get("date"))
    isin = _normalize_isin(action.get("isin"))
    ratio = _split_ratio(str(action.get("description") or ""))
    if action_dt is None or not isin or ratio == 0:
        return None
    return {
        "date_time": action_dt.isoformat(sep=" "),
        "trade_id": f"CA:{action.get('source_report')}:{action_dt.isoformat()}:{isin}:split",
        "trade_type": "corporate_action:split",
        "symbol": _none_text(action.get("symbol")) or isin,
        "isin": isin,
        "asset_type": "Stocks",
        "quantity": "0",
        "calculation_quantity": "0",
        "price": "0",
        "calculation_price": "0",
        "multiplier": "1",
        "_calculation_multiplier": "1",
        "amount": "0",
        "commission": "0",
        "amount_with_commission": "0",
        "currency": _normalize_currency(action.get("currency")) or FREEDOM_BASE_CURRENCY,
        "exchange": None,
        "country": _country_from_isin(isin),
        "source_report": f"corporate_action:{action.get('source_report')}",
        "_instrument_identity_key": _instrument_identity_key_from_values(isin=isin, symbol=_none_text(action.get("symbol"))),
        "_event_type": "split",
        "_split_ratio": str(ratio),
        "_synthetic_source": "corporate_action",
        "_corporate_action_type": "split",
    }


def _synthetic_trade_dict(
    action: Mapping[str, Any],
    action_dt: datetime,
    symbol: str,
    isin: str,
    quantity: Decimal,
    price: Decimal,
    amount: Decimal,
    instrument: Mapping[str, Any],
    action_type: str,
) -> dict[str, Any]:
    multiplier = _effective_transaction_multiplier(Decimal("1"), quantity, price, amount)
    return {
        "date_time": action_dt.isoformat(sep=" "),
        "trade_id": f"CA:{action.get('source_report')}:{action_dt.isoformat()}:{isin}:{action_type}",
        "trade_type": f"corporate_action:{action_type}",
        "symbol": symbol,
        "isin": isin,
        "asset_type": _none_text(instrument.get("type")) or "Stocks",
        "quantity": str(quantity),
        "calculation_quantity": str(quantity),
        "price": str(price),
        "calculation_price": str(price),
        "multiplier": _multiplier_text(multiplier),
        "_calculation_multiplier": str(multiplier),
        "amount": str(abs(amount)),
        "commission": "0",
        "amount_with_commission": str(abs(amount)),
        "currency": _normalize_currency(action.get("currency")) or _normalize_currency(instrument.get("_currency")) or FREEDOM_BASE_CURRENCY,
        "exchange": _normalize_exchange(instrument.get("listing_exchange")),
        "country": _country_from_isin(isin) or _none_text(instrument.get("country")),
        "source_report": f"corporate_action:{action.get('source_report')}",
        "_instrument_identity_key": _instrument_identity_key_from_values(isin=isin, symbol=symbol),
        "_broker_realized_pl": str(abs(amount)) if action_type in {"conversion_compensation", "spinoff_compensation"} else "0",
        "_synthetic_source": "corporate_action",
        "_corporate_action_type": action_type,
        "corporate_action_adjustment": action.get("description"),
    }


def _apply_identity_changes_to_trades(
    trades: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> list[dict[str, Any]]:
    result = [dict(trade) for trade in trades]
    ticker_changes = _ticker_change_aliases(actions)
    for trade in result:
        isin = _none_text(trade.get("isin"))
        symbol = _none_text(trade.get("symbol"))
        new_symbol = ticker_changes.get((isin, symbol))
        if not new_symbol:
            continue
        trade["symbol"] = new_symbol
        trade["_instrument_identity_key"] = _instrument_identity_key_from_values(isin=isin, symbol=new_symbol)
        trade["corporate_action_adjustment"] = f"Ticker change {symbol} -> {new_symbol}"
    for action in actions:
        if action.get("action_type") != "conversion":
            continue
        description = _none_text(action.get("description")) or ""
        old_symbol, old_isin, new_symbol, new_isin = _conversion_identities(description)
        action_dt = _parse_datetime(action.get("date_time") or action.get("date"))
        ratio = _conversion_ratio(description)
        if action_dt is None or not old_isin or not new_isin:
            continue
        instrument = _lookup_instrument(instrument_lookup, symbol=new_symbol, isin=new_isin, year=action_dt.year) or {}
        canonical_symbol = _none_text(instrument.get("symbol")) or new_symbol or new_isin
        for trade in result:
            trade_dt = _parse_datetime(trade.get("date_time"))
            if trade_dt is None or trade_dt >= action_dt:
                continue
            if trade.get("isin") != old_isin and trade.get("symbol") != old_symbol:
                continue
            trade["symbol"] = canonical_symbol
            trade["isin"] = new_isin
            trade["_instrument_identity_key"] = _instrument_identity_key_from_values(isin=new_isin, symbol=canonical_symbol)
            if ratio not in (Decimal("0"), Decimal("1")):
                qty = _decimal(trade.get("calculation_quantity") or trade.get("quantity"))
                price = _decimal(trade.get("calculation_price") or trade.get("price"))
                trade["calculation_quantity"] = str(qty / ratio)
                trade["calculation_price"] = str(price * ratio)
                trade["quantity"] = str(qty / ratio)
                trade["price"] = str(price * ratio)
            trade["corporate_action_adjustment"] = description
    return result


def _apply_ticker_changes_to_records(
    rows: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    ticker_changes = _ticker_change_aliases(actions)
    if not ticker_changes:
        return [dict(row) for row in rows]
    result: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        isin = _none_text(record.get("isin"))
        symbol = _none_text(record.get("symbol"))
        new_symbol = ticker_changes.get((isin, symbol))
        if new_symbol:
            record["symbol"] = new_symbol
        result.append(record)
    return result


def _ticker_change_aliases(actions: Sequence[Mapping[str, Any]]) -> dict[tuple[str | None, str | None], str]:
    aliases: dict[tuple[str | None, str | None], str] = {}
    for action in actions:
        if action.get("action_type") != "ticker_change":
            continue
        description = _none_text(action.get("description")) or ""
        match = re.search(r"Ticker change\s+(.+?)\s+->\s+(.+)$", description, flags=re.IGNORECASE)
        if not match:
            continue
        old_symbol = _clean_symbol(match.group(1))
        new_symbol = _clean_symbol(match.group(2))
        isin = _normalize_isin(action.get("isin"))
        if old_symbol and new_symbol:
            aliases[(isin, old_symbol)] = new_symbol
    return aliases


def _build_transfers(
    reports: Sequence[ParsedFreedomReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    internal_trades: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Decimal]]:
    transfers: list[dict[str, Any]] = _starting_position_transfer_rows(reports, instrument_lookup)
    cash_totals_by_currency: dict[str, Decimal] = defaultdict(Decimal)
    for report in reports:
        for idx, row in enumerate(report.rows.get(SECTION_CASH_IN_OUT, []), start=1):
            transfer = _cash_transfer_row(report, idx, row)
            if transfer is None:
                continue
            transfers.append(transfer)
            cash_totals_by_currency[str(transfer["currency"])] += _decimal(transfer.get("amount"))
        for idx, row in enumerate(report.rows.get(SECTION_SEC_IN_OUT, []), start=1):
            transfer = _security_transfer_row(report, idx, row, instrument_lookup, internal_trades)
            if transfer is not None:
                transfers.append(transfer)
    transfers = _collapse_retry_security_transfers(transfers)
    _annotate_transfer_in_identity_changes(transfers, instrument_lookup)
    return transfers, dict(cash_totals_by_currency)


def _collapse_retry_security_transfers(transfers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse Freedom block/withdraw/reclaim retry sagas into one net transfer.

    Freedom's "Sec In Out" sheet logs every blocking step, withdrawal attempt and its
    cancellation/reclaim as separate rows ("Блокировка", "Вывод в другой депозитарий",
    "Перевод из другого депозитария", ...). A single real transfer out can therefore
    surface as a dozen rows that individually look like alternating transfers in and out,
    which makes the parser request a transfer-out cost-basis file for the spurious "in"
    legs. Replace each such saga with a single net transfer so both FIFO and the audit
    Transfers sheet show only the real movement instead of the cancelling duplicates.
    """

    groups: dict[tuple[str | None, str | None], list[dict[str, Any]]] = defaultdict(list)
    for transfer in transfers:
        if not _is_collapsible_security_transfer(transfer):
            continue
        key = (_none_text(transfer.get("source_report")), _none_text(transfer.get("_instrument_identity_key")))
        groups[key].append(transfer)

    collapsed_ids: set[int] = set()
    synthetic: list[dict[str, Any]] = []
    for group in groups.values():
        if len(group) <= 1 or not _is_retry_transfer_saga(group):
            continue
        collapsed_ids.update(id(row) for row in group)
        net = sum((_decimal(row.get("_raw_quantity")) for row in group), Decimal("0"))
        if abs(net) <= Decimal("0.0001"):
            continue
        synthetic.append(_net_security_transfer(group, net))

    if not collapsed_ids:
        return transfers
    remaining = [transfer for transfer in transfers if id(transfer) not in collapsed_ids]
    remaining.extend(synthetic)
    return remaining


def _is_collapsible_security_transfer(transfer: Mapping[str, Any]) -> bool:
    return (
        transfer.get("transfer_type") == "security"
        and not transfer.get("_exclude_from_fifo")
        and not transfer.get("_synthetic_starting_position")
        and ":sec:" in (_none_text(transfer.get("_transfer_id")) or "")
    )


def _is_retry_transfer_saga(group: Sequence[Mapping[str, Any]]) -> bool:
    """A retry/cancel saga repeats the same quantity with both in and out legs."""

    raw_quantities = [_decimal(row.get("_raw_quantity")) for row in group]
    has_incoming = any(quantity > 0 for quantity in raw_quantities)
    has_outgoing = any(quantity < 0 for quantity in raw_quantities)
    magnitudes = {abs(quantity) for quantity in raw_quantities}
    return has_incoming and has_outgoing and len(magnitudes) == 1


def _net_security_transfer(group: Sequence[Mapping[str, Any]], net: Decimal) -> dict[str, Any]:
    template = max(group, key=lambda row: _none_text(row.get("date")) or "")
    transfer = dict(template)
    transfer["direction"] = "in" if net > 0 else "out"
    transfer["quantity"] = _decimal_text(abs(net))
    transfer["_raw_quantity"] = _decimal_text(net)
    transfer["_transfer_id"] = f"{_none_text(template.get('_transfer_id'))}:net"
    transfer["broker_comment"] = "Net of repeated transfer attempts (block / withdraw / reclaim)"
    transfer.pop("_exclude_from_fifo", None)
    return transfer


def _starting_position_transfer_rows(
    reports: Sequence[ParsedFreedomReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in _earliest_freedom_reports(reports):
        event_dt = _report_start_datetime(report)
        if event_dt is None:
            continue
        year = event_dt.year
        for idx, row in enumerate(report.rows.get(SECTION_SECURITIES, []), start=1):
            quantity = _decimal(_cell(row, COL_START_QTY))
            if abs(quantity) <= Decimal("0.0001"):
                continue
            raw_symbol = _clean_symbol(_cell(row, COL_TICKER))
            raw_isin = _normalize_isin(_cell(row, COL_ISIN))
            instrument = _lookup_instrument(instrument_lookup, symbol=raw_symbol, isin=raw_isin, year=year) or {}
            symbol = _none_text(instrument.get("symbol")) or raw_symbol or raw_isin
            isin = _none_text(instrument.get("isin")) or raw_isin
            identity_key = _instrument_identity_key_from_values(isin=isin, symbol=symbol)
            if not identity_key:
                continue
            rows.append(
                {
                    "date": event_dt.date().isoformat(),
                    "transfer_type": "security",
                    "direction": "in" if quantity > 0 else "out",
                    "asset_type": _none_text(instrument.get("type")) or _asset_type(_cell(row, COL_ASSET_TYPE), symbol),
                    "symbol": symbol,
                    "isin": isin,
                    "currency": _normalize_currency(_cell(row, COL_CURRENCY)) or _normalize_currency(instrument.get("_currency")) or FREEDOM_BASE_CURRENCY,
                    "quantity": _decimal_text(abs(quantity)),
                    "price": None,
                    "enter_date": None,
                    "amount": None,
                    "broker_comment": "Starting position from earliest Securities sheet",
                    "counterparty": None,
                    "source_report": str(report.path),
                    "country": _country_from_isin(isin) or _none_text(instrument.get("country")),
                    "_raw_quantity": _decimal_text(quantity),
                    "_transfer_id": f"{report.path.name}:starting_position:{idx}",
                    "_instrument_identity_key": identity_key,
                    "_multiplier": "1",
                    "_transfer_cost_basis_status": "pending_transfer_out_fifo_cost_basis",
                    "_synthetic_starting_position": True,
                }
            )
    return rows


def _cash_transfer_row(report: ParsedFreedomReport, idx: int, row: Mapping[str, Any]) -> dict[str, Any] | None:
    transfer_type = str(_cell(row, COL_TYPE) or "").strip()
    if _is_non_transfer_cash_type(transfer_type):
        return None
    amount = _decimal(_cell(row, COL_AMOUNT))
    if amount == 0:
        return None
    event_dt = _parse_datetime(_cell(row, COL_DATE))
    currency = _normalize_currency(_cell(row, COL_CURRENCY)) or FREEDOM_BASE_CURRENCY
    return {
        "date": event_dt.date().isoformat() if event_dt else None,
        "transfer_type": "cash",
        "direction": "in" if amount > 0 else "out",
        "asset_type": "Cash",
        "symbol": None,
        "isin": None,
        "currency": currency,
        "quantity": None,
        "price": None,
        "enter_date": None,
        "amount": str(amount),
        "broker_comment": _none_text(_cell(row, COL_COMMENT) or transfer_type),
        "counterparty": None,
        "source_report": str(report.path),
        "_transfer_id": f"{report.path.name}:cash:{idx}",
    }


def _security_transfer_row(
    report: ParsedFreedomReport,
    idx: int,
    row: Mapping[str, Any],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    internal_trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    quantity = _decimal(_cell(row, COL_QTY))
    if quantity == 0:
        return None
    event_dt = _parse_datetime(_cell(row, COL_DATE))
    year = event_dt.year if event_dt else _year_for_report(report)
    raw_symbol = _clean_symbol(_cell(row, COL_TICKER))
    raw_isin = _normalize_isin(_cell(row, COL_ISIN))
    instrument = _lookup_instrument(instrument_lookup, symbol=raw_symbol, isin=raw_isin, year=year) or {}
    symbol = _none_text(instrument.get("symbol")) or raw_symbol or raw_isin
    isin = _none_text(instrument.get("isin")) or raw_isin
    identity_key = _instrument_identity_key_from_values(isin=isin, symbol=symbol)
    transfer_type = str(_cell(row, COL_TYPE) or "").strip()
    broker_comment = _none_text(_cell(row, COL_COMMENT) or transfer_type)
    if _is_detected_internal_ticker_change_transfer(transfer_type, broker_comment) or _is_detected_internal_depository_change_transfer(transfer_type, broker_comment):
        return None
    transfer = {
        "date": event_dt.date().isoformat() if event_dt else None,
        "transfer_type": "security",
        "direction": "in" if quantity > 0 else "out",
        "asset_type": _none_text(instrument.get("type")) or "Stocks",
        "symbol": symbol,
        "isin": isin,
        "currency": _security_transfer_currency(symbol, isin, event_dt, instrument, internal_trades),
        "quantity": str(abs(quantity)),
        "price": None,
        "enter_date": None,
        "amount": None,
        "broker_comment": broker_comment,
        "counterparty": None,
        "source_report": str(report.path),
        "country": _country_from_isin(isin) or _none_text(instrument.get("country")),
        "_raw_quantity": str(quantity),
        "_transfer_id": f"{report.path.name}:sec:{idx}",
        "_instrument_identity_key": identity_key,
        "_multiplier": "1",
        "_transfer_cost_basis_status": "pending_transfer_out_fifo_cost_basis",
    }
    if _security_transfer_should_be_audit_only(transfer, transfer_type, event_dt, internal_trades):
        transfer["_exclude_from_fifo"] = True
    return transfer


def _security_transfer_should_be_audit_only(
    transfer: Mapping[str, Any],
    transfer_type: str,
    event_dt: datetime | None,
    internal_trades: Sequence[Mapping[str, Any]],
) -> bool:
    if _is_detected_internal_ticker_change_transfer(transfer_type, _none_text(transfer.get("broker_comment"))):
        return True
    if _is_detected_internal_depository_change_transfer(transfer_type, _none_text(transfer.get("broker_comment"))):
        return True
    if _is_audit_only_security_transfer_type(transfer_type):
        return True
    return False


def _is_internal_ticker_change_transfer(transfer_type: str, broker_comment: str | None) -> bool:
    return _text_in(transfer_type, {"перевод внутри компании", "internal transfer", "internal company transfer"}) and _text_in(
        broker_comment,
        {"смена тикера", "cмена тикера", "ticker change", "symbol change"},
    )


def _is_internal_depository_change_transfer(transfer_type: str, broker_comment: str | None) -> bool:
    return _text_in(transfer_type, {"РїРµСЂРµРІРѕРґ РІРЅСѓС‚СЂРё РєРѕРјРїР°РЅРёРё", "internal transfer", "internal company transfer"}) and _text_in(
        broker_comment,
        {"СЃРјРµРЅР° РјРµСЃС‚Р° С…СЂР°РЅРµРЅРёСЏ", "СЃРјРµРЅР° РјРµСЃС‚Р° С…СЂР°РЅРµРЅРёСЏ С†Р±", "depository change", "custody change", "change of custody"},
    )


def _is_detected_internal_ticker_change_transfer(transfer_type: str, broker_comment: str | None) -> bool:
    transfer_text = _text_key(transfer_type)
    comment_text = _text_key(broker_comment)
    if ("internal transfer" in transfer_text or "внутри" in transfer_text) and (
        "ticker change" in comment_text or "symbol change" in comment_text or "смена тикер" in comment_text
    ):
        return True
    return _is_internal_ticker_change_transfer(transfer_type, broker_comment)


def _is_detected_internal_depository_change_transfer(transfer_type: str, broker_comment: str | None) -> bool:
    transfer_text = _text_key(transfer_type)
    comment_text = _text_key(broker_comment)
    if ("internal transfer" in transfer_text or "внутри" in transfer_text) and (
        "depository change" in comment_text or "custody change" in comment_text or "change of custody" in comment_text or "места хранения" in comment_text
    ):
        return True
    return _is_internal_depository_change_transfer(transfer_type, broker_comment)


def _normalized_comment_key(value: str | None) -> str:
    return " ".join(_text_key(value).split())


def _annotate_transfer_in_identity_changes(
    transfers: list[dict[str, Any]],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> None:
    conversions: list[tuple[datetime, str | None, str | None, str | None, str | None, Decimal]] = []
    for transfer in transfers:
        description = _none_text(transfer.get("broker_comment")) or ""
        old_symbol, old_isin, new_symbol, new_isin = _conversion_identities(description)
        if not old_isin or not new_isin:
            continue
        event_dt = _parse_datetime(transfer.get("date"))
        if event_dt is None:
            continue
        conversions.append((event_dt, old_symbol, old_isin, new_symbol, new_isin, _conversion_ratio(description)))

    for transfer in transfers:
        if transfer.get("transfer_type") != "security" or str(transfer.get("direction") or "").lower() != "in":
            continue
        transfer_dt = _parse_datetime(transfer.get("date"))
        if transfer_dt is None:
            continue
        transfer_symbol = _none_text(transfer.get("symbol"))
        transfer_isin = _none_text(transfer.get("isin"))
        candidates = [
            conversion
            for conversion in conversions
            if conversion[0] > transfer_dt
            and ((conversion[2] and conversion[2] == transfer_isin) or (conversion[1] and conversion[1] == transfer_symbol))
        ]
        if not candidates:
            continue
        conversion_dt, _, _, new_symbol, new_isin, ratio = min(candidates, key=lambda item: item[0])
        if not new_isin or ratio == 0:
            continue
        instrument = _lookup_instrument(instrument_lookup, symbol=new_symbol, isin=new_isin, year=conversion_dt.year) or {}
        canonical_symbol = _none_text(instrument.get("symbol")) or new_symbol or new_isin
        transfer["_converted_symbol"] = canonical_symbol
        transfer["_converted_isin"] = new_isin
        transfer["_converted_country"] = _country_from_isin(new_isin) or _none_text(instrument.get("country"))
        transfer["_converted_identity_key"] = _instrument_identity_key_from_values(isin=new_isin, symbol=canonical_symbol)
        transfer["_converted_ratio"] = str(ratio)


def _security_transfer_currency(
    symbol: str | None,
    isin: str | None,
    event_dt: datetime | None,
    instrument: Mapping[str, Any],
    internal_trades: Sequence[Mapping[str, Any]],
) -> str:
    normalized_symbol = _clean_symbol(symbol)
    if normalized_symbol and (normalized_symbol.endswith(".SPB") or "_RUR" in normalized_symbol):
        return "RUB"
    trade_currency = _matching_trade_currency(symbol, isin, event_dt, internal_trades)
    if trade_currency:
        return trade_currency
    return _normalize_currency(instrument.get("_currency")) or FREEDOM_BASE_CURRENCY


def _matching_trade_currency(
    symbol: str | None,
    isin: str | None,
    event_dt: datetime | None,
    internal_trades: Sequence[Mapping[str, Any]],
) -> str | None:
    identity = _instrument_identity_key_from_values(isin=isin, symbol=symbol)
    if identity is None:
        return None
    candidates: list[tuple[int, Decimal, str]] = []
    for idx, trade in enumerate(internal_trades):
        trade_identity = _instrument_identity_key_from_values(
            isin=_none_text(trade.get("isin")),
            symbol=_none_text(trade.get("symbol")),
        )
        if trade_identity != identity:
            continue
        currency = _normalize_currency(trade.get("currency"))
        if not currency:
            continue
        trade_dt = _parse_datetime(trade.get("date_time"))
        if event_dt is not None and trade_dt is not None:
            distance = Decimal(str(abs((trade_dt - event_dt).total_seconds())))
        else:
            distance = Decimal(idx)
        candidates.append((0 if trade_dt and event_dt and trade_dt >= event_dt else 1, distance, currency))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _build_dividends(
    reports: Sequence[ParsedFreedomReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pending_taxes: list[tuple[datetime | None, str | None, str | None, str, Decimal, bool]] = []
    for report in reports:
        for row in report.rows.get(SECTION_CASH_IN_OUT, []):
            transfer_type = str(_cell(row, COL_TYPE) or "").strip()
            if not (_is_dividend_type(transfer_type) or _is_tax_type(transfer_type)):
                continue
            event_dt = _parse_datetime(_cell(row, COL_DATE))
            year = event_dt.year if event_dt else _year_for_report(report)
            description = _none_text(_cell(row, COL_COMMENT)) or ""
            symbol, isin = _income_identity_from_description(description, year, instrument_lookup)
            instrument = _lookup_instrument(instrument_lookup, symbol=symbol, isin=isin, year=year) or {}
            currency = _normalize_currency(_cell(row, COL_CURRENCY)) or _normalize_currency(instrument.get("_currency")) or FREEDOM_BASE_CURRENCY
            amount = _decimal(_cell(row, COL_AMOUNT))
            is_revert = _is_reverted_description(description)
            if _is_tax_type(transfer_type):
                pending_taxes.append((event_dt, symbol, isin, currency, amount, is_revert))
                continue
            rate = _annual_rate(fx_provider, year, currency, warnings)
            record = _income_record(report, event_dt, symbol, isin, currency, amount, Decimal("0"), rate, instrument, include_tax=True)
            record["_is_revert"] = is_revert
            rows.append(record)
    for tax_dt, tax_symbol, tax_isin, tax_currency, tax_amount, tax_is_revert in pending_taxes:
        tax_date = tax_dt.date().isoformat() if tax_dt else None
        for record in rows:
            if record.get("date") != tax_date or record.get("currency") != tax_currency:
                continue
            if tax_isin and record.get("isin") != tax_isin:
                continue
            if not tax_isin and tax_symbol and record.get("symbol") != tax_symbol:
                continue
            if bool(record.get("_is_revert")) != tax_is_revert:
                continue
            withholding = _decimal(record.get("withholding_tax")) + tax_amount
            gross_amount = _decimal(record.get("gross_amount"))
            rate = _decimal(record.get("kzt_rate")) if record.get("kzt_rate") not in (None, "") else None
            record["withholding_tax"] = _money_text(withholding)
            record["net_amount"] = _money_text(gross_amount + withholding)
            record["withholding_tax_kzt"] = _amount_kzt(withholding, rate)
            record["net_amount_kzt"] = _amount_kzt(gross_amount + withholding, rate)
            break
    for record in rows:
        record.pop("_is_revert", None)
    return rows


def _income_identity_from_description(
    description: str,
    year: int | None,
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> tuple[str | None, str | None]:
    isins = re.findall(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b", description, flags=re.IGNORECASE)
    isin = _normalize_isin(isins[-1]) if isins else None
    if isin:
        instrument = _lookup_instrument(instrument_lookup, isin=isin, year=year) or {}
        if instrument:
            return _none_text(instrument.get("symbol")), _none_text(instrument.get("isin")) or isin
    symbol_matches = re.findall(r"\b((?=[A-Z0-9_.]*[A-Z])[A-Z0-9_]+(?:\.[A-Z0-9_]+)+)\b", description, flags=re.IGNORECASE)
    for raw_symbol in reversed(symbol_matches):
        symbol = _clean_symbol(raw_symbol)
        instrument = _lookup_instrument(instrument_lookup, symbol=symbol, year=year) or {}
        if instrument:
            return _none_text(instrument.get("symbol")) or symbol, _none_text(instrument.get("isin")) or isin
    symbol = _clean_symbol(symbol_matches[-1]) if symbol_matches else None
    return symbol, isin


def _is_reverted_description(description: str) -> bool:
    return description.strip().lower().startswith("reverted")


def _build_coupons(
    reports: Sequence[ParsedFreedomReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        for row in report.rows.get(SECTION_CASH_IN_OUT, []):
            if not _is_coupon_type(_cell(row, COL_TYPE)):
                continue
            event_dt = _parse_datetime(_cell(row, COL_DATE))
            year = event_dt.year if event_dt else _year_for_report(report)
            description = _none_text(_cell(row, COL_COMMENT)) or ""
            symbol, isin = _income_identity_from_description(description, year, instrument_lookup)
            instrument = _lookup_instrument(instrument_lookup, symbol=symbol, isin=isin, year=year) or {}
            currency = _normalize_currency(_cell(row, COL_CURRENCY)) or _normalize_currency(instrument.get("_currency")) or FREEDOM_BASE_CURRENCY
            withholding = Decimal("0")
            net_amount = _decimal(_cell(row, COL_AMOUNT))
            gross_amount = net_amount - withholding
            rate = _annual_rate(fx_provider, year, currency, warnings)
            rows.append(
                {
                    "date": event_dt.date().isoformat() if event_dt else None,
                    "symbol": _none_text(instrument.get("symbol")) or symbol or isin,
                    "isin": _none_text(instrument.get("isin")) or isin,
                    "country": _country_from_isin(isin) or _none_text(instrument.get("country")),
                    "currency": currency,
                    "gross_amount": _money_text(gross_amount),
                    "withholding_tax": _money_text(withholding),
                    "net_amount": _money_text(net_amount),
                    "kzt_rate": str(rate) if rate is not None else None,
                    "gross_amount_kzt": _amount_kzt(gross_amount, rate),
                    "withholding_tax_kzt": _amount_kzt(withholding, rate),
                    "net_amount_kzt": _amount_kzt(net_amount, rate),
                    "offshore_flag": None,
                    "source_report": str(report.path),
                }
            )
    return rows


def _income_record(
    report: ParsedFreedomReport,
    event_dt: datetime | None,
    symbol: str | None,
    isin: str | None,
    currency: str,
    gross_amount: Decimal,
    withholding: Decimal,
    rate: Decimal | None,
    instrument: Mapping[str, Any],
    *,
    include_tax: bool,
) -> dict[str, Any]:
    tax = gross_amount * Decimal("0.10")
    canonical_symbol = _none_text(instrument.get("symbol")) or symbol or isin
    canonical_isin = _none_text(instrument.get("isin")) or isin
    country = _country_from_isin(canonical_isin) or _none_text(instrument.get("country"))
    is_kz_issuer = country == "KZ"
    return {
        "date": event_dt.date().isoformat() if event_dt else None,
        "pay_date": event_dt.date().isoformat() if event_dt else None,
        "symbol": canonical_symbol,
        "isin": canonical_isin,
        "country": country,
        "currency": currency,
        "gross_amount": _money_text(gross_amount),
        "withholding_tax": _money_text(withholding),
        "net_amount": _money_text(gross_amount + withholding),
        "kzt_rate": str(rate) if rate is not None else None,
        "gross_amount_kzt": "0.00" if is_kz_issuer else _amount_kzt(gross_amount, rate),
        "withholding_tax_kzt": "0.00" if is_kz_issuer else _amount_kzt(withholding, rate),
        "net_amount_kzt": "0.00" if is_kz_issuer else _amount_kzt(gross_amount + withholding, rate),
        "tax": "0.00" if is_kz_issuer or not include_tax else str(tax),
        "tax_kzt": "0.00" if is_kz_issuer or not include_tax else _amount_kzt(tax, rate),
        "offshore_flag": None,
        "kase_aix_preferential_flag": None,
        "source_report": str(report.path),
    }


def _build_interest(reports: Sequence[ParsedFreedomReport], fx_provider: AnnualFxRateProvider, warnings: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    financing_groups: dict[tuple[str, str], list[tuple[ParsedFreedomReport, int, Mapping[str, Any]]]] = defaultdict(list)
    for report in reports:
        for idx, row in enumerate(report.rows.get(SECTION_TRADES, []), start=1):
            operation = str(_cell(row, COL_OPERATION) or "").strip()
            if not _is_financing_operation(operation):
                continue
            group_id = _financing_group_id(report, idx, row)
            financing_groups[(str(report.path), group_id)].append((report, idx, row))

    for group_rows in financing_groups.values():
        row = _financing_interest_row(group_rows, fx_provider, warnings)
        if row is not None:
            rows.append(row)
    return rows


def _build_cash_balances(reports: Sequence[ParsedFreedomReport], fx_provider: AnnualFxRateProvider, warnings: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        year = _year_for_report(report)
        if year is None:
            continue
        for row in report.rows.get(SECTION_CASH_FLOWS, []):
            currency = _normalize_currency(_cell(row, COL_CURRENCY))
            if not currency:
                continue
            ending_cash = _decimal(_cell(row, COL_END_CASH))
            rate = _annual_rate(fx_provider, year, currency, warnings)
            rows.append(
                {
                    "year": year,
                    "date": report.period_end.isoformat() if report.period_end else None,
                    "currency": currency,
                    "ending_cash": str(ending_cash),
                    "ending_cash_kzt": _amount_kzt(ending_cash, rate),
                    "source_report": str(report.path),
                }
            )
    return rows


def _append_missing_raw_positions(
    position_rows: Sequence[Mapping[str, Any]],
    reports: Sequence[ParsedFreedomReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
    actions: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    result = _apply_ticker_changes_to_records(position_rows, actions or [])
    canonical_by_key: dict[tuple[int | None, str | None], Decimal] = defaultdict(Decimal)
    for row in result:
        key = (_int_or_none(row.get("year")), _position_identity_key(row))
        canonical_by_key[key] += _decimal(row.get("quantity"))
    for raw in _apply_ticker_changes_to_records(_raw_position_records(reports), actions or []):
        year = raw["year"]
        currency = raw["currency"]
        symbol = raw["symbol"]
        key = (year, _position_identity_key(raw))
        missing = raw["quantity"] - canonical_by_key.get(key, Decimal("0"))
        if abs(missing) <= Decimal("0.0001"):
            continue
        instrument = _lookup_instrument(instrument_lookup, symbol=symbol, isin=raw["isin"], year=year) or {}
        transfer_year = _first_transfer_year(reports, symbol, raw["isin"]) or year
        for snapshot_year in range(transfer_year, year + 1):
            rate = _annual_rate(fx_provider, snapshot_year, currency, warnings)
            result.append(
                {
                    "year": snapshot_year,
                    "date": date(snapshot_year, 12, 31).isoformat(),
                    "asset_type": _none_text(instrument.get("type")) or raw["asset_type"] or "Stocks",
                    "symbol": symbol,
                    "isin": raw["isin"],
                    "currency": currency,
                    "country": _country_from_isin(raw["isin"]) or _none_text(instrument.get("country")),
                    "quantity": _decimal_text(missing),
                    "price": None,
                    "multiplier": "1",
                    "amount": None,
                    "kzt_rate": str(rate) if rate is not None else None,
                    "amount_kzt": None,
                    "_position_cost_basis_status": "missing_transfer_in_fifo_source",
                }
            )
    return result


def _position_identity_key(row: Mapping[str, Any]) -> str | None:
    return _none_text(row.get("isin")) or _none_text(row.get("symbol"))


def _raw_position_records(reports: Sequence[ParsedFreedomReport], *, include_zero: bool = False) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for report in _latest_reports_by_year(reports).values():
        year = _year_for_report(report)
        if year is None:
            continue
        for row in report.rows.get(SECTION_SECURITIES, []):
            quantity = _decimal(_cell(row, COL_END_QTY))
            symbol = _clean_symbol(_cell(row, COL_TICKER)) or _normalize_isin(_cell(row, COL_ISIN))
            isin = _normalize_isin(_cell(row, COL_ISIN))
            if not symbol and not isin:
                continue
            if abs(quantity) <= Decimal("0.0001") and not include_zero:
                continue
            records.append(
                {
                    "year": year,
                    "symbol": symbol,
                    "isin": isin,
                    "quantity": quantity,
                    "currency": _normalize_currency(_cell(row, COL_CURRENCY)) or FREEDOM_BASE_CURRENCY,
                    "asset_type": _asset_type(_cell(row, COL_ASSET_TYPE), symbol),
                    "source_report": str(report.path),
                }
            )
    return records


def _first_transfer_year(reports: Sequence[ParsedFreedomReport], symbol: str | None, isin: str | None) -> int | None:
    candidates: list[int] = []
    for report in reports:
        for row in report.rows.get(SECTION_SEC_IN_OUT, []):
            row_symbol = _clean_symbol(_cell(row, COL_TICKER))
            row_isin = _normalize_isin(_cell(row, COL_ISIN))
            if row_symbol != symbol and row_isin != isin:
                continue
            event_dt = _parse_datetime(_cell(row, COL_DATE))
            if event_dt:
                candidates.append(event_dt.year)
    return min(candidates) if candidates else None


def _fifo_source_trade_ids(fifo_rows: Sequence[Mapping[str, Any]]) -> set[str]:
    return {
        trade_id
        for row in fifo_rows
        for trade_id in [_none_text(row.get("source_trade_id"))]
        if trade_id and not trade_id.startswith("CA:")
    }


def _populate_raw_totals(
    totals: RawReportTotals,
    reports: Sequence[ParsedFreedomReport],
    trades: Sequence[Mapping[str, Any]],
    dividends: Sequence[Mapping[str, Any]],
    interest: Sequence[Mapping[str, Any]],
    coupons: Sequence[Mapping[str, Any]],
    transfer_totals_by_currency: Mapping[str, Decimal],
    fifo_source_trade_ids: set[str],
    fifo_rows: Sequence[Mapping[str, Any]],
    positions: Sequence[Mapping[str, Any]],
) -> None:
    gross_trades = Decimal("0")
    commissions = Decimal("0")
    realized_pl = Decimal("0")
    trades_by_id = {_none_text(trade.get("trade_id")): trade for trade in trades if _none_text(trade.get("trade_id"))}
    for trade in trades:
        trade_dt = _parse_datetime(trade.get("date_time"))
        year = trade_dt.year if trade_dt else None
        currency = _normalize_currency(trade.get("currency"))
        instrument_key = _none_text(trade.get("isin") or trade.get("symbol"))
        amount = _decimal(trade.get("amount"))
        commission = _decimal(trade.get("commission"))
        gross_trades += amount
        commissions += commission
        key = _dimension_key(metric=ReconciliationMetric.TRADE_GROSS_AMOUNT_BY_INSTRUMENT.value, year=year, currency=currency, instrument_key=instrument_key)
        totals.totals_by_metric_currency[key] = totals.totals_by_metric_currency.get(key, Decimal("0")) + amount
        trade_id = _none_text(trade.get("trade_id"))
        if _none_text(trade.get("_synthetic_source")) == "corporate_action" or trade_id not in fifo_source_trade_ids:
            continue
        broker_pl = trade.get("_broker_realized_pl")
        if broker_pl not in (None, ""):
            pl = _decimal(broker_pl)
            realized_pl += pl
    _populate_fifo_raw_pnl_totals(totals, trades_by_id, fifo_rows)

    dividends_gross = sum((_decimal(row.get("gross_amount")) for row in dividends), Decimal("0"))
    dividends_tax = sum((_decimal(row.get("withholding_tax")) for row in dividends), Decimal("0"))
    interest_gross = sum((_decimal(row.get("gross_amount")) for row in interest), Decimal("0"))
    coupons_gross = sum((_decimal(row.get("gross_amount")) for row in coupons), Decimal("0"))
    cash_transfer_total = sum(transfer_totals_by_currency.values(), Decimal("0"))
    totals.scalar_totals.update(
        {
            ReconciliationMetric.TOTAL_TRADES_GROSS_AMOUNT.value: gross_trades,
            ReconciliationMetric.TOTAL_COMMISSIONS.value: commissions,
            ReconciliationMetric.TOTAL_DIVIDENDS_GROSS.value: dividends_gross,
            ReconciliationMetric.TOTAL_DIVIDENDS_TAX.value: dividends_tax,
            ReconciliationMetric.TOTAL_DIVIDENDS_NET.value: dividends_gross + dividends_tax,
            ReconciliationMetric.TOTAL_INTEREST.value: interest_gross,
            ReconciliationMetric.TOTAL_COUPONS.value: coupons_gross,
            ReconciliationMetric.TOTAL_DEPOSITS_WITHDRAWALS_TRANSFERS.value: cash_transfer_total,
            ReconciliationMetric.REALIZED_PL.value: realized_pl,
        }
    )
    for currency, amount in transfer_totals_by_currency.items():
        totals.totals_by_metric_currency[_dimension_key(metric=ReconciliationMetric.TOTAL_DEPOSITS_WITHDRAWALS_TRANSFERS.value, currency=currency)] = amount
    _populate_raw_positions(totals, reports, positions)
    _populate_raw_cash(totals, reports)


def _populate_fifo_raw_pnl_totals(
    totals: RawReportTotals,
    trades_by_id: Mapping[str | None, Mapping[str, Any]],
    fifo_rows: Sequence[Mapping[str, Any]],
) -> None:
    for row in fifo_rows:
        source_trade_id = _none_text(row.get("source_trade_id"))
        if not source_trade_id or source_trade_id.startswith("CA:"):
            continue
        trade = trades_by_id.get(source_trade_id)
        if not trade:
            continue
        broker_pl_value = trade.get("_broker_realized_pl")
        if broker_pl_value in (None, ""):
            continue
        broker_pl = _decimal(broker_pl_value)
        trade_quantity = abs(_decimal(trade.get("quantity")))
        exit_quantity = abs(_decimal(row.get("exit_quantity")))
        allocated_pl = broker_pl if trade_quantity == 0 or exit_quantity == trade_quantity else broker_pl * exit_quantity / trade_quantity
        if trade.get("_broker_realized_pl_includes_commissions") is False:
            allocated_pl -= abs(_decimal(row.get("enter_commission"))) + abs(_decimal(row.get("exit_commission")))
        exit_dt = _parse_datetime(row.get("exit_date"))
        key = _dimension_key(
            metric=ReconciliationMetric.PNL_AFTER_ALL_COMMISSIONS_BY_INSTRUMENT.value,
            year=exit_dt.year if exit_dt else None,
            currency=_normalize_currency(row.get("currency")),
            instrument_key=_none_text(row.get("isin") or row.get("symbol")),
        )
        totals.totals_by_metric_currency[key] = totals.totals_by_metric_currency.get(key, Decimal("0")) + allocated_pl


def _populate_raw_positions(
    totals: RawReportTotals,
    reports: Sequence[ParsedFreedomReport],
    positions: Sequence[Mapping[str, Any]],
) -> None:
    raw_records = _raw_position_records(reports, include_zero=True)
    known_instrument_keys = {_position_identity_key(raw) for raw in raw_records}
    known_instrument_keys |= {_position_identity_key(row) for row in positions}
    known_instrument_keys.discard(None)
    for raw in raw_records:
        key = _dimension_key(year=raw["year"], instrument_key=_position_identity_key(raw))
        totals.positions_by_key[key] = totals.positions_by_key.get(key, Decimal("0")) + raw["quantity"]
    snapshot_years = _raw_position_snapshot_years(reports)
    for year in snapshot_years:
        for instrument_key in known_instrument_keys:
            totals.positions_by_key.setdefault(_dimension_key(year=year, instrument_key=instrument_key), Decimal("0"))
    for row in positions:
        year = _int_or_none(row.get("year"))
        if year not in snapshot_years:
            continue
        key = _dimension_key(year=year, instrument_key=_position_identity_key(row))
        totals.positions_by_key.setdefault(key, Decimal("0"))


def _raw_position_snapshot_years(reports: Sequence[ParsedFreedomReport]) -> set[int]:
    years: set[int] = set()
    for report in reports:
        if SECTION_SECURITIES not in report.rows:
            continue
        year = _year_for_report(report)
        if year is not None:
            years.add(year)
    return years


def _populate_raw_cash(totals: RawReportTotals, reports: Sequence[ParsedFreedomReport]) -> None:
    for report in _latest_reports_by_year(reports).values():
        year = _year_for_report(report)
        if year is None:
            continue
        for row in report.rows.get(SECTION_CASH_FLOWS, []):
            currency = _normalize_currency(_cell(row, COL_CURRENCY))
            if not currency:
                continue
            key = _dimension_key(year=year, currency=currency)
            totals.cash_by_currency[key] = totals.cash_by_currency.get(key, Decimal("0")) + _decimal(_cell(row, COL_END_CASH))


def _latest_reports_by_year(reports: Sequence[ParsedFreedomReport]) -> dict[int, ParsedFreedomReport]:
    latest: dict[int, ParsedFreedomReport] = {}
    for report in reports:
        if report.period_end is None:
            continue
        year = report.period_end.year
        current = latest.get(year)
        if current is None or (current.period_end is not None and report.period_end > current.period_end):
            latest[year] = report
    return latest


def _earliest_freedom_reports(reports: Sequence[ParsedFreedomReport]) -> list[ParsedFreedomReport]:
    dated = [
        (report.period_start or report.period_end, report)
        for report in reports
        if report.period_start is not None or report.period_end is not None
    ]
    if not dated:
        return list(reports[:1])
    earliest = min(report_date for report_date, _ in dated)
    return [report for report_date, report in dated if report_date == earliest]


def _report_start_datetime(report: ParsedFreedomReport) -> datetime | None:
    report_date = report.period_start or report.period_end
    if report_date is None:
        return None
    return datetime(report_date.year, report_date.month, report_date.day)


def _trade_id(report: ParsedFreedomReport, idx: int, row: Mapping[str, Any]) -> str:
    raw = _none_text(_cell(row, COL_ORDER_ID))
    return f"{report.path.name}:{raw}:{idx}" if raw else f"{report.path.name}:trade:{idx}"


def _is_buy_operation(operation: str) -> bool:
    text = operation.lower()
    return "покуп" in text or "купля" in text or "buy" in text


def _is_swap_operation(operation: str) -> bool:
    text = operation.lower()
    return "\u0441\u0432\u043e\u043f" in text or "swap" in text


def _is_repo_operation(operation: str) -> bool:
    text = operation.lower()
    return "\u0440\u0435\u043f\u043e" in text or "repo" in text


def _is_financing_operation(operation: str) -> bool:
    return _is_swap_operation(operation) or _is_repo_operation(operation)


def _is_financing_opening(operation: str) -> bool:
    text = operation.lower()
    return "\u043e\u0442\u043a\u0440" in text or "open" in text


def _is_financing_closing(operation: str) -> bool:
    text = operation.lower()
    return "\u0437\u0430\u043a\u0440" in text or "close" in text


def _financing_kind(operation: str) -> str:
    return "repo" if _is_repo_operation(operation) else "swap"


def _financing_group_id(report: ParsedFreedomReport, idx: int, row: Mapping[str, Any]) -> str:
    raw = _none_text(_cell(row, COL_ORDER_ID))
    if raw and "/" in raw:
        return raw.rsplit("/", 1)[-1]
    return raw or f"{report.path.name}:financing:{idx}"


def _financing_interest_row(
    group_rows: Sequence[tuple[ParsedFreedomReport, int, Mapping[str, Any]]],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> dict[str, Any] | None:
    if not group_rows:
        return None
    opening = next((item for item in group_rows if _is_financing_opening(str(_cell(item[2], COL_OPERATION) or ""))), None)
    closing = next((item for item in group_rows if _is_financing_closing(str(_cell(item[2], COL_OPERATION) or ""))), None)
    if opening is None or closing is None:
        closing = closing or max(group_rows, key=lambda item: abs(_decimal(_cell(item[2], COL_REALIZED_PL))))
        opening = opening or next((item for item in group_rows if item != closing), None)
    if opening is None or closing is None:
        return None

    report, idx, closing_row = closing
    _, _, opening_row = opening
    close_dt = _parse_datetime(_cell(closing_row, COL_TRADE_DATE))
    year = close_dt.year if close_dt else _year_for_report(report)
    currency = _normalize_currency(_cell(closing_row, COL_CURRENCY) or _cell(opening_row, COL_CURRENCY)) or FREEDOM_BASE_CURRENCY
    open_amount = abs(_decimal(_cell(opening_row, COL_AMOUNT)))
    close_amount = abs(_decimal(_cell(closing_row, COL_AMOUNT)))
    opening_commission = abs(_decimal(_cell(opening_row, COL_COMMISSION)))
    gross_amount = close_amount - open_amount - opening_commission
    rate = _annual_rate(fx_provider, year, currency, warnings)
    symbol = _clean_symbol(_cell(closing_row, COL_TICKER) or _cell(opening_row, COL_TICKER))
    kind = _financing_kind(str(_cell(closing_row, COL_OPERATION) or _cell(opening_row, COL_OPERATION) or ""))
    group_id = _financing_group_id(report, idx, closing_row)
    description = (
        f"{kind.upper()} reward {group_id} {symbol or ''} "
        f"qty={_decimal_text(abs(_decimal(_cell(closing_row, COL_QTY))))} "
        f"open_price={_decimal_text(_decimal(_cell(opening_row, COL_PRICE)))} "
        f"close_price={_decimal_text(_decimal(_cell(closing_row, COL_PRICE)))} "
        f"open_amount={_money_text(open_amount)} close_amount={_money_text(close_amount)}"
    ).strip()
    return {
        "date": close_dt.date().isoformat() if close_dt else None,
        "description": description,
        "financing_kind": kind,
        "currency": currency,
        "gross_amount": _money_text(gross_amount),
        "withholding_tax": "0.00",
        "net_amount": _money_text(gross_amount),
        "kzt_rate": str(rate) if rate is not None else None,
        "gross_amount_kzt": _amount_kzt(gross_amount, rate),
        "withholding_tax_kzt": "0.00",
        "net_amount_kzt": _amount_kzt(gross_amount, rate),
        "commission": _money_text(opening_commission),
        "source_report": "; ".join(sorted({str(item[0].path) for item in group_rows})),
        "_financing_trade_id": group_id,
    }


def _conversion_identities(description: str) -> tuple[str | None, str | None, str | None, str | None]:
    match = re.search(r"securities\s+([A-Z0-9_.]+)\s+\(([A-Z0-9]{12})\)\s+->\s+([A-Z0-9_.]+)\s+\(([A-Z0-9]{12})\)", description, re.IGNORECASE)
    if not match:
        return None, None, None, None
    return _clean_symbol(match.group(1)), match.group(2).upper(), _clean_symbol(match.group(3)), match.group(4).upper()


def _conversion_ratio(description: str) -> Decimal:
    match = re.search(r"ratio:\s*(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)", description, re.IGNORECASE)
    if not match:
        return Decimal("1")
    denominator = Decimal(match.group(2))
    return Decimal(match.group(1)) / denominator if denominator else Decimal("1")


def _split_ratio(description: str) -> Decimal:
    factor_match = re.search(r"Factor\s+(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)", description, re.IGNORECASE)
    if factor_match:
        denominator = Decimal(factor_match.group(2))
        return Decimal(factor_match.group(1)) / denominator if denominator else Decimal("1")
    return _conversion_ratio(description)


def _compensation_symbol(description: str) -> str | None:
    match = re.search(r"\b([A-Z0-9_]+\.US|[A-Z0-9_]+\.SPB)\s+к получению", description)
    if match:
        return _clean_symbol(match.group(1))
    match = re.search(r"\b([A-Z0-9_]+\.US|[A-Z0-9_]+\.SPB)\b", description)
    return _clean_symbol(match.group(1)) if match else None


def _compensation_quantity(action: Mapping[str, Any]) -> Decimal:
    description = str(action.get("description") or "")
    to_receive = _first_decimal_match(description, r"получению\s+(\d+(?:\.\d+)?)")
    received = _first_decimal_match(description, r"получено\s+(\d+(?:\.\d+)?)")
    if to_receive is not None and received is not None:
        return received - to_receive
    proceeds = _decimal(action.get("proceeds"))
    price = _decimal(action.get("_amount_per_one"))
    if proceeds and price:
        return -(proceeds / price)
    return Decimal("0")


def _compensation_expected_quantity(action: Mapping[str, Any]) -> Decimal:
    description = str(action.get("description") or "")
    to_receive = _first_decimal_match(description, r"получению\s+(\d+(?:\.\d+)?)")
    return to_receive or Decimal("0")


def _first_decimal_match(text: str, pattern: str) -> Decimal | None:
    match = re.search(pattern, text)
    return Decimal(match.group(1)) if match else None


def _parse_tax_amount(value: Any) -> Decimal:
    if value in (None, "", "-") or _is_nan(value):
        return Decimal("0")
    text = str(value).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return _decimal(match.group(0)) if match else Decimal("0")


def _text_key(value: Any) -> str:
    return str(value or "").strip().casefold()


def _text_in(value: Any, options: set[str]) -> bool:
    return _text_key(value) in {option.casefold() for option in options}


def _is_security_asset(value: Any) -> bool:
    return _text_in(value, {"бумаги", "security", "securities"})


def _is_money_asset(value: Any) -> bool:
    return _text_in(value, {"деньги", "money", "cash"})


def _is_dividend_type(value: Any) -> bool:
    return _text_in(value, {"дивиденды", "dividend", "dividends"})


def _is_tax_type(value: Any) -> bool:
    return _text_in(value, {"налоги", "tax", "taxes", "withholding tax"})


def _is_coupon_type(value: Any) -> bool:
    return _text_in(value, {"купон", "coupon", "coupons"})


def _is_redemption_type(value: Any) -> bool:
    return _text_in(value, {"погашение", "redemption", "maturity"})


def _is_conversion_type(value: Any) -> bool:
    return _text_in(value, {"конвертация", "conversion"})


def _is_non_transfer_cash_type(value: Any) -> bool:
    return _text_in(
        value,
        {
            "дивиденды",
            "dividend",
            "dividends",
            "налоги",
            "tax",
            "taxes",
            "погашение",
            "redemption",
            "maturity",
            "купон",
            "coupon",
            "coupons",
            "конвертация",
            "conversion",
            "компенсация по корпоративному действию",
            "corporate action compensation",
            "блокировка",
            "block",
            "blocking",
            "разблокировка",
            "unblock",
            "unblocking",
            "сборы агента при выплате дивидендов",
            "agent fee on dividend payment",
        },
    )


def _is_audit_only_security_transfer_type(value: Any) -> bool:
    return _text_in(
        value,
        {
            "зачисление прав",
            "rights",
            "rights issue",
            "конвертация",
            "conversion",
            "спин-офф",
            "спинофф",
            "spin-off",
            "spinoff",
            "погашение",
            "redemption",
            "maturity",
        },
    )


def _cell(row: Mapping[str, Any], key: str) -> Any:
    if key in row:
        return row.get(key)
    candidates = (key, *COLUMN_ALIASES.get(key, ()))
    stripped_candidates = {str(candidate).strip().casefold() for candidate in candidates}
    for candidate, value in row.items():
        if str(candidate).strip().casefold() in stripped_candidates:
            return value
    return None


def _clean_record(row: dict[str, Any]) -> None:
    for key, value in list(row.items()):
        if _is_nan(value):
            row[key] = None


def _is_nan(value: Any) -> bool:
    try:
        return bool(value != value)
    except Exception:
        return False


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, "") or _is_nan(value):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip().replace("T", " ")
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
        "%d %B %Y",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal:
    if value in (None, "", "-") or _is_nan(value):
        return Decimal("0")
    text = str(value).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not text or text.lower() in {"none", "nan", "nat", "null"}:
        return Decimal("0")
    return _ib_decimal(text)


def _none_text(value: Any) -> str | None:
    text = _string_or_none(value)
    if text is None or text.lower() in {"none", "nan", "nat", "null"}:
        return None
    return text


def _normalize_isin(value: Any) -> str | None:
    text = _none_text(value)
    if text is None:
        return None
    normalized = text.strip().upper()
    if normalized in {"NONE", "NAN", "NULL", "000000000000"}:
        return None
    return normalized if ISIN_RE.fullmatch(normalized) else None


def _clean_symbol(value: Any) -> str | None:
    text = _none_text(value)
    if text is None:
        return None
    return text.strip().upper()


def _symbol_base(symbol: str | None) -> str | None:
    text = _clean_symbol(symbol)
    if text is None:
        return None
    for suffix in (".US", ".SPB", ".NYSE", ".NASDAQ"):
        text = text.removesuffix(suffix)
    return text.replace("_RUR", "")


def _normalize_currency(value: Any) -> str | None:
    text = _none_text(value)
    if text is None:
        return None
    normalized = text.strip().upper()
    if normalized in {"RUR", "RUB"}:
        return "RUB"
    return normalized


def _normalize_exchange(value: Any) -> str | None:
    text = _none_text(value)
    if text is None:
        return None
    text = text.strip()
    if text == "NYSE/NASDAQ":
        return "US"
    return text


def _asset_type(value: Any, symbol: str | None = None) -> str:
    text = str(value or "").strip().lower()
    if _is_currency_pair_symbol(symbol):
        return "Forex"
    if "bond" in text or "облиг" in text:
        return "Bonds"
    if "currency" in text or "валют" in text:
        return "Forex"
    if symbol and ".SWAP" in symbol:
        return "Stocks"
    return "Stocks"


def _is_currency_pair_symbol(symbol: str | None) -> bool:
    if not symbol or "/" not in symbol:
        return False
    parts = [part.strip().upper() for part in symbol.split("/")]
    return len(parts) == 2 and all(re.fullmatch(r"[A-Z]{3}", part) for part in parts)


def _country_from_isin(isin: str | None) -> str | None:
    return isin[:2] if isin and ISIN_RE.fullmatch(isin) else None


def _country_from_symbol(symbol: str | None) -> str | None:
    symbol = _clean_symbol(symbol)
    if not symbol:
        return None
    if symbol.endswith(".US"):
        return "US"
    if symbol.endswith(".SPB") or symbol.endswith("_RUR.SPB"):
        return "RU"
    return None


def _date_key(value: datetime | None) -> str | None:
    return value.date().isoformat() if value else None


def _year_for_report(report: ParsedFreedomReport) -> int | None:
    return report.period_end.year if report.period_end else None


def _max_report_year(reports: Sequence[ParsedFreedomReport], trades: Sequence[Mapping[str, Any]], transfers: Sequence[Mapping[str, Any]]) -> int | None:
    years: list[int] = []
    for report in reports:
        if report.period_end:
            years.append(report.period_end.year)
    for row in (*trades, *transfers):
        parsed = _parse_datetime(row.get("date_time") or row.get("date"))
        if parsed:
            years.append(parsed.year)
    return max(years) if years else None


def _int_or_none(value: Any) -> int | None:
    if value in (None, "") or _is_nan(value):
        return None
    return int(value)


def _dimension_key(*, metric: str | None = None, year: int | None = None, currency: str | None = None, instrument_key: str | None = None) -> str:
    return "|".join("" if value is None else str(value) for value in (metric, year, currency, instrument_key))
