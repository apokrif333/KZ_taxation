"""Native Tsifra XML parser."""

from __future__ import annotations

import xml.etree.ElementTree as ET
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
    _build_unprocessed_rows,
    _build_years_results,
    _canonical_trade_rows,
    _canonical_transfer_rows,
    _decimal as _ib_decimal,
    _effective_transaction_multiplier,
    _instrument_identity_key_from_values,
    _instrument_symbol_history,
    _money_text,
    _multiplier_text,
    _sort_trades_by_datetime,
    _string_or_none,
)

TSIFRA_SECTION_POSITIONS = "positions"
TSIFRA_SECTION_TRADES = "trades"
TSIFRA_SECTION_REPOS = "repos"
TSIFRA_SECTION_MONEY_MOVES = "money_move"
TSIFRA_SECTION_ACTIVE_MOVES = "active_moves"
TSIFRA_SECTION_STOCK_INCOME = "stock_income"
MOEX_EXCHANGE = "MOEX"
TSIFRA_BASE_CURRENCY = "RUB"


@dataclass(slots=True)
class ParsedTsifraReport:
    path: Path
    account_id: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    report_date: date | None = None
    base_currency: str | None = TSIFRA_BASE_CURRENCY
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    fields: dict[str, str] = field(default_factory=dict)


class TsifraParser:
    broker_code = "tsifra"

    def __init__(self, fx_provider: AnnualFxRateProvider | None = None, transfer_in_resolver: TransferInFifoResolver | None = None) -> None:
        self.fx_provider = fx_provider or AnnualFxRateProvider({})
        self.transfer_in_resolver = transfer_in_resolver

    def discover_reports(self, raw_root: Path, account_id: str) -> list[BrokerReport]:
        return discover_raw_reports(raw_root, DiscoveryRule(broker="tsifra", account_id=account_id, extensions=frozenset({".xml"})))

    def parse_reports(self, reports: Sequence[BrokerReport], account_id: str) -> ParseResult:
        parsed_reports = [parse_tsifra_xml_report(report.path, account_id=account_id) for report in reports]
        dataset = build_canonical_dataset(parsed_reports, account_id, self.fx_provider, transfer_in_resolver=self.transfer_in_resolver)
        dataset.raw_totals.source_reports = [str(report.path) for report in reports]
        return ParseResult(broker=self.broker_code, account_id=account_id, reports=reports, dataset=dataset, raw_totals=dataset.raw_totals)


def parse_tsifra_xml_report(path: Path, *, account_id: str | None = None) -> ParsedTsifraReport:
    root = ET.parse(path).getroot()
    parsed = ParsedTsifraReport(path=path, account_id=account_id)
    parsed.report_date = _parse_date(root.attrib.get("date"))
    parsed.period_start, parsed.period_end = _parse_period(root.attrib.get("period"))

    account = root.find("account")
    if account is not None:
        parsed.fields.update(account.attrib)
        parsed.account_id = account.attrib.get("id") or account.attrib.get("account") or parsed.account_id

    money = root.find("money")
    if money is not None:
        row = dict(money.attrib)
        _add_report_metadata(row, parsed)
        parsed.rows["money"].append(row)

    for tag in (TSIFRA_SECTION_POSITIONS, TSIFRA_SECTION_ACTIVE_MOVES, "commiss_moves", "nalog_moves"):
        section = root.find(tag)
        if section is None:
            continue
        for child in section:
            row = dict(child.attrib)
            _add_report_metadata(row, parsed)
            parsed.rows[tag].append(row)

    for tag in (TSIFRA_SECTION_MONEY_MOVES, TSIFRA_SECTION_STOCK_INCOME):
        for row_elem in root.findall(tag):
            row = dict(row_elem.attrib)
            _add_report_metadata(row, parsed)
            parsed.rows[tag].append(row)

    orders = root.find("orders")
    if orders is not None:
        for order_idx, order in enumerate(orders, start=1):
            order_fields = {f"order_{key}": value for key, value in order.attrib.items()}
            for trade_idx, trade in enumerate(order.findall("trade"), start=1):
                row = {**order_fields, **trade.attrib, "_order_index": order_idx, "_trade_index": trade_idx}
                _add_report_metadata(row, parsed)
                parsed.rows[TSIFRA_SECTION_TRADES].append(row)
            for repo_idx, repo in enumerate(order.findall("repo"), start=1):
                row = {**order_fields, **repo.attrib, "_order_index": order_idx, "_repo_index": repo_idx}
                _add_report_metadata(row, parsed)
                parsed.rows[TSIFRA_SECTION_REPOS].append(row)
    return parsed


def build_canonical_dataset(
    reports: Sequence[ParsedTsifraReport],
    account_id: str,
    fx_provider: AnnualFxRateProvider,
    *,
    transfer_in_resolver: TransferInFifoResolver | None = None,
) -> CanonicalDataset:
    dataset = CanonicalDataset(metadata=AccountMetadata(broker="tsifra", account_id=account_id, base_currency=TSIFRA_BASE_CURRENCY))
    instruments = _build_instruments(reports, account_id)
    instrument_lookup = _instrument_lookup(instruments)
    symbol_history = _instrument_symbol_history(instruments)
    dataset.tables["Instruments"] = instruments
    dataset.tables["CorporateActions"] = []
    dataset.tables["Dividends"] = _build_dividends(reports, instrument_lookup, fx_provider, dataset.warnings)
    transfers, transfer_totals_by_currency = _build_transfers(reports, instrument_lookup)
    internal_trades = _sort_trades_by_datetime(_build_trades(reports, instrument_lookup))
    dataset.tables["Trades"] = _canonical_trade_rows(internal_trades)
    dataset.tables["_BrokerTradeRealizedPL"] = _build_broker_trade_realized_pl(internal_trades)
    fifo_rows, fifo_positions, transfer_rows = _build_fifo_and_positions(
        internal_trades,
        transfers=transfers,
        initial_lots=[],
        max_year=_max_report_year(reports, internal_trades, transfers),
        fx_provider=fx_provider,
        warnings=dataset.warnings,
        symbol_history=symbol_history,
        transfer_in_resolver=transfer_in_resolver,
    )
    dataset.tables["Fifo"] = fifo_rows
    dataset.tables["Positions"] = fifo_positions
    dataset.tables["Transfers"] = _canonical_transfer_rows(transfer_rows)
    dataset.tables["Interest"] = _build_interest(reports, fx_provider, dataset.warnings)
    dataset.tables["Coupons"] = _build_coupons(reports, instrument_lookup, fx_provider, dataset.warnings)
    dataset.tables["_TradeWithholdingTax"] = _build_trade_withholding_taxes(
        reports,
        dataset.tables["Coupons"],
        fx_provider,
        dataset.warnings,
    )
    dataset.tables["CashBalances"] = _build_cash_balances(reports, fx_provider, dataset.warnings)
    dataset.tables["Unprocessed"] = [
        *_build_unprocessed_rows(dataset.tables["Trades"], fifo_rows),
        *_build_unprocessed_repo_rows(reports, dataset.tables["Interest"]),
    ]
    dataset.tables["Years_Results"] = _build_years_results(dataset)
    _populate_raw_totals(
        dataset.raw_totals,
        reports,
        internal_trades,
        dataset.tables["Dividends"],
        dataset.tables["Interest"],
        dataset.tables["Coupons"],
        transfer_totals_by_currency,
    )
    return dataset


def _add_report_metadata(row: dict[str, Any], report: ParsedTsifraReport) -> None:
    row["source_report"] = str(report.path)
    row["report_period_start"] = report.period_start.isoformat() if report.period_start else None
    row["report_period_end"] = report.period_end.isoformat() if report.period_end else None


def _parse_period(value: str | None) -> tuple[date | None, date | None]:
    if not value or "-" not in value:
        return None, None
    start_text, end_text = value.split("-", 1)
    return _parse_date(start_text), _parse_date(end_text)


def _parse_date(value: Any) -> date | None:
    dt = _parse_datetime(value)
    return dt.date() if dt is not None else None


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip().replace("Z", "").replace("T", " ")
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
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
    text = text.upper()
    return text if ISIN_RE.fullmatch(text) else None


def _country_from_isin(isin: str | None) -> str | None:
    if not isin:
        return None
    return "BE" if isin[:2] == "XS" else isin[:2]


def _asset_type_from_stock_type(value: Any) -> str | None:
    text = _none_text(value)
    if text is None:
        return None
    normalized = text.lower()
    if normalized == "об":
        return "Bonds"
    if normalized in {"ао", "ап", "п"}:
        return "Stocks"
    return "Stocks"


def _micex_code(row: Mapping[str, Any]) -> str | None:
    for key, value in row.items():
        if str(key).startswith("Micex"):
            return _none_text(value)
    return None


def _symbol_from_position(row: Mapping[str, Any]) -> str | None:
    return _none_text(row.get("code") or _micex_code(row))


def _build_instruments(reports: Sequence[ParsedTsifraReport], account_id: str) -> list[dict[str, Any]]:
    instruments: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None, int | None]] = set()

    def add_instrument(
        *,
        report: ParsedTsifraReport,
        symbol: str | None,
        isin: str | None,
        description: str | None = None,
        issuer: str | None = None,
        asset_type: str | None = None,
        currency: str | None = None,
        security_id: str | None = None,
        year: int | None = None,
    ) -> None:
        isin_norm = _normalize_isin(isin)
        symbol_norm = _none_text(symbol) or isin_norm
        if not symbol_norm and not isin_norm:
            return
        instrument_year = year if year is not None else _year_for_report(report)
        key = (symbol_norm, isin_norm, instrument_year)
        if key in seen:
            return
        seen.add(key)
        country = _country_from_isin(isin_norm)
        asset_type = asset_type or "Stocks"
        instruments.append(
            {
                "symbol": symbol_norm,
                "description": _none_text(description) or _none_text(issuer) or symbol_norm,
                "conid": None,
                "security_id": security_id or isin_norm,
                "underlying": None,
                "listing_exchange": MOEX_EXCHANGE,
                "multiplier": "1",
                "type": asset_type,
                "asset_type": asset_type,
                "code": symbol_norm,
                "year": instrument_year,
                "expiry": None,
                "delivery_month": None,
                "strike": None,
                "issuer": _none_text(issuer),
                "maturity": None,
                "cusip": None,
                "country": country,
                "isin": isin_norm,
                "figi": None,
                "issuer_country": country,
                "offshore_flag": None,
                "issuer_outside_kz_flag": True if country and country != "KZ" else None,
                "preferential_tax_flag": None,
                "source_broker": "tsifra",
                "source_account": account_id,
                "source_report": str(report.path),
                "as_of_date": report.period_end.isoformat() if report.period_end else None,
                "_currency": currency or TSIFRA_BASE_CURRENCY,
            }
        )

    for report in reports:
        for row in report.rows.get(TSIFRA_SECTION_POSITIONS, []):
            add_instrument(
                report=report,
                symbol=_symbol_from_position(row),
                isin=row.get("isin"),
                description=_none_text(row.get("issuer")),
                issuer=_none_text(row.get("issuer")),
                asset_type=_asset_type_from_stock_type(row.get("StockType")),
                currency=_none_text(row.get("price_curr")) or TSIFRA_BASE_CURRENCY,
                security_id=_none_text(row.get("numGosReg")) or _normalize_isin(row.get("isin")),
            )
        for row in report.rows.get(TSIFRA_SECTION_TRADES, []):
            isin = _normalize_isin(row.get("isin_code"))
            add_instrument(
                report=report,
                symbol=_none_text(row.get("security_name")) or isin,
                isin=isin,
                description=_none_text(row.get("security_name")),
                currency=_none_text(row.get("currency")) or TSIFRA_BASE_CURRENCY,
                security_id=isin,
            )
        for row in report.rows.get(TSIFRA_SECTION_ACTIVE_MOVES, []):
            isin = _normalize_isin(row.get("ISIN") or row.get("isin"))
            add_instrument(
                report=report,
                symbol=_symbol_for_isin(instruments, isin) or isin,
                isin=isin,
                description=_none_text(row.get("active_name")),
                currency=TSIFRA_BASE_CURRENCY,
                security_id=isin,
            )
        for row in report.rows.get(TSIFRA_SECTION_MONEY_MOVES, []):
            isin = _normalize_isin(row.get("isin"))
            add_instrument(
                report=report,
                symbol=_none_text(row.get("ticker")) or _symbol_for_isin(instruments, isin) or isin,
                isin=isin,
                description=_none_text(row.get("description")),
                asset_type="Bonds" if _is_coupon_money_row(row) else None,
                currency=_none_text(row.get("currency_code")) or TSIFRA_BASE_CURRENCY,
                security_id=isin,
            )
        for row in report.rows.get(TSIFRA_SECTION_STOCK_INCOME, []):
            isin = _normalize_isin(row.get("isin"))
            add_instrument(
                report=report,
                symbol=_none_text(row.get("MicexСode")) or _none_text(row.get("stock_code")) or isin,
                isin=isin,
                description=_none_text(row.get("stock_shortname")),
                issuer=_none_text(row.get("issuer")),
                asset_type="Bonds" if _none_text(row.get("operation_type")) == "coupon" else None,
                currency=TSIFRA_BASE_CURRENCY,
                security_id=_none_text(row.get("numGosReg")) or isin,
            )
    return _dedupe_instruments(
        sorted(instruments, key=lambda row: (row.get("year") or 0, str(row.get("symbol") or ""), str(row.get("isin") or "")))
    )


def _dedupe_instruments(instruments: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    preferred_keys = {
        (_none_text(row.get("isin")), _int_or_none(row.get("year")))
        for row in instruments
        if _none_text(row.get("isin")) and _none_text(row.get("symbol")) and row.get("symbol") != row.get("isin")
    }
    result: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None, int | None]] = set()
    for row in instruments:
        isin = _none_text(row.get("isin"))
        symbol = _none_text(row.get("symbol"))
        year = _int_or_none(row.get("year"))
        if (isin, year) in preferred_keys and symbol == isin:
            continue
        key = (symbol, isin, year)
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(row))
    return result


def _instrument_lookup(instruments: Sequence[Mapping[str, Any]]) -> dict[tuple[str, int | None], dict[str, Any]]:
    lookup: dict[tuple[str, int | None], dict[str, Any]] = {}
    for instrument in instruments:
        year = _int_or_none(instrument.get("year"))
        isin = _none_text(instrument.get("isin"))
        symbol = _none_text(instrument.get("symbol"))
        for key_value in (isin, symbol, _none_text(instrument.get("security_id"))):
            if not key_value:
                continue
            _set_preferred_instrument(lookup, (key_value, year), instrument, isin, symbol)
            _set_preferred_instrument(lookup, (key_value, None), instrument, isin, symbol)
    return lookup


def _set_preferred_instrument(
    lookup: dict[tuple[str, int | None], dict[str, Any]],
    key: tuple[str, int | None],
    instrument: Mapping[str, Any],
    isin: str | None,
    symbol: str | None,
) -> None:
    existing = lookup.get(key)
    if existing is None:
        lookup[key] = dict(instrument)
        return
    existing_symbol = _none_text(existing.get("symbol"))
    if key[0] == isin and existing_symbol == isin and symbol and symbol != isin:
        lookup[key] = dict(instrument)


def _lookup_instrument(lookup: Mapping[tuple[str, int | None], dict[str, Any]], *, symbol: str | None = None, isin: str | None = None, year: int | None = None) -> dict[str, Any] | None:
    for key_value in (isin, symbol):
        key_value = _none_text(key_value)
        if not key_value:
            continue
        for key in ((key_value, year), (key_value, None)):
            if key in lookup:
                return lookup[key]
    return None


def _symbol_for_isin(instruments: Sequence[Mapping[str, Any]], isin: str | None) -> str | None:
    if not isin:
        return None
    for instrument in reversed(instruments):
        if _none_text(instrument.get("isin")) == isin:
            return _none_text(instrument.get("symbol"))
    return None


def _build_trades(reports: Sequence[ParsedTsifraReport], instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]]) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for report in reports:
        for idx, row in enumerate(report.rows.get(TSIFRA_SECTION_TRADES, []), start=1):
            trade_dt = _parse_datetime(row.get("t_date"))
            year = trade_dt.year if trade_dt else _year_for_report(report)
            isin = _normalize_isin(row.get("isin_code"))
            raw_symbol = _none_text(row.get("security_name")) or isin
            instrument = _lookup_instrument(instrument_lookup, symbol=raw_symbol, isin=isin, year=year) or {}
            symbol = _none_text(instrument.get("symbol")) or raw_symbol
            quantity = _decimal(row.get("t_q"))
            price = _decimal(row.get("t_price"))
            broker_amount = abs(_decimal(row.get("t_sum")))
            instrument_multiplier = _decimal(instrument.get("multiplier") or "1") or Decimal("1")
            multiplier = _effective_transaction_multiplier(instrument_multiplier, quantity, price, broker_amount)
            amount = broker_amount if broker_amount else abs(quantity * price * multiplier)
            commission = abs(_decimal(row.get("comis"))) + abs(_decimal(row.get("comis_nds")))
            currency = _none_text(row.get("currency")) or _none_text(instrument.get("_currency")) or TSIFRA_BASE_CURRENCY
            asset_type = _none_text(instrument.get("asset_type") or instrument.get("type")) or "Stocks"
            country = _none_text(instrument.get("country")) or _country_from_isin(isin)
            trades.append(
                {
                    "date_time": trade_dt.isoformat(sep=" ") if trade_dt else None,
                    "trade_id": _tsifra_trade_id(report, idx, row),
                    "trade_type": "trade",
                    "symbol": symbol,
                    "isin": isin,
                    "asset_type": asset_type,
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
                    "exchange": MOEX_EXCHANGE if "Московская" in str(row.get("t_place") or "") else None,
                    "country": country,
                    "source_report": str(report.path),
                    "_instrument_identity_key": _instrument_identity_key_from_values(isin=isin, symbol=symbol),
                    "_broker_realized_pl": None,
                    "_broker_code": None,
                }
            )
    return trades


def _tsifra_trade_id(report: ParsedTsifraReport, idx: int, row: Mapping[str, Any]) -> str:
    trade_id = _none_text(row.get("t_id")) or _none_text(row.get("deal_number"))
    return f"{report.path.name}:{trade_id}:{idx}" if trade_id else f"{report.path.name}:trade:{idx}"


def _build_transfers(reports: Sequence[ParsedTsifraReport], instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Decimal]]:
    transfers: list[dict[str, Any]] = []
    cash_totals_by_currency: dict[str, Decimal] = defaultdict(Decimal)
    active_groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for report in reports:
        for row in report.rows.get(TSIFRA_SECTION_ACTIVE_MOVES, []):
            event_dt = _parse_datetime(row.get("date"))
            isin = _normalize_isin(row.get("ISIN") or row.get("isin"))
            if event_dt is None or not isin:
                continue
            active_groups[(event_dt.date().isoformat(), isin)].append(row)

    for (event_date, isin), group_rows in sorted(active_groups.items()):
        raw_quantity = sum((_decimal(row.get("in_qty")) - _decimal(row.get("out_qty")) for row in group_rows), Decimal("0"))
        if abs(raw_quantity) <= Decimal("0.0001"):
            continue
        event_year = int(event_date[:4]) if event_date[:4].isdigit() else None
        instrument = _lookup_instrument(instrument_lookup, isin=isin, year=event_year) or {}
        symbol = _none_text(instrument.get("symbol")) or isin
        comments = "; ".join(dict.fromkeys(_none_text(row.get("description")) or "" for row in group_rows if _none_text(row.get("description"))))
        transfers.append(
            {
                "date": event_date,
                "transfer_type": "security",
                "direction": "in" if raw_quantity > 0 else "out",
                "asset_type": _none_text(instrument.get("asset_type") or instrument.get("type")) or "Stocks",
                "symbol": symbol,
                "isin": isin,
                "currency": _none_text(instrument.get("_currency")) or TSIFRA_BASE_CURRENCY,
                "quantity": str(abs(raw_quantity)),
                "price": None,
                "enter_date": None,
                "amount": None,
                "broker_comment": comments or _none_text(group_rows[0].get("active_name")),
                "counterparty": None,
                "source_report": _combine_source_reports(group_rows),
                "country": _none_text(instrument.get("country")) or _country_from_isin(isin),
                "_raw_quantity": str(raw_quantity),
                "_transfer_id": f"{event_date}:{isin}:security_transfer",
                "_instrument_identity_key": _instrument_identity_key_from_values(isin=isin, symbol=symbol),
                "_multiplier": "1",
            }
        )

    for report in reports:
        for idx, row in enumerate(report.rows.get(TSIFRA_SECTION_MONEY_MOVES, []), start=1):
            is_client_cash_transfer = _is_client_cash_transfer(row)
            is_tax_withholding = _is_other_tax_money_row(row)
            if not (is_client_cash_transfer or is_tax_withholding):
                continue
            event_dt = _parse_datetime(row.get("date"))
            amount = _decimal(row.get("in_qty")) - _decimal(row.get("out_qty"))
            if amount == 0:
                continue
            currency = _none_text(row.get("currency_code")) or TSIFRA_BASE_CURRENCY
            transfers.append(
                {
                    "date": event_dt.date().isoformat() if event_dt else None,
                    "transfer_type": "tax" if is_tax_withholding else "cash",
                    "direction": "in" if amount > 0 else "out",
                    "asset_type": "Cash",
                    "symbol": None,
                    "isin": None,
                    "currency": currency,
                    "quantity": None,
                    "price": None,
                    "enter_date": None,
                    "amount": str(amount),
                    "broker_comment": _none_text(row.get("description") or row.get("oper_name")),
                    "counterparty": None,
                    "source_report": str(report.path),
                    "_transfer_id": f"{report.path.name}:{'tax' if is_tax_withholding else 'cash'}:{idx}",
                }
            )
            cash_totals_by_currency[currency] += amount
    return transfers, dict(cash_totals_by_currency)


def _is_client_cash_transfer(row: Mapping[str, Any]) -> bool:
    oper_name = str(row.get("oper_name") or "").lower()
    return "клиента" in oper_name or "клиент" in oper_name


def _build_dividends(
    reports: Sequence[ParsedTsifraReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    tax_by_key: dict[tuple[str | None, str | None, str | None], Decimal] = defaultdict(Decimal)
    for report in reports:
        for row in report.rows.get(TSIFRA_SECTION_MONEY_MOVES, []):
            if not _is_dividend_tax_money_row(row):
                continue
            key = (_event_date_key(row.get("date")), _normalize_isin(row.get("isin")), _none_text(row.get("currency_code")))
            tax_by_key[key] += abs(_decimal(row.get("out_qty")))

    for report in reports:
        for row in report.rows.get(TSIFRA_SECTION_MONEY_MOVES, []):
            if not _is_dividend_gross_money_row(row):
                continue
            event_dt = _parse_datetime(row.get("date"))
            year = event_dt.year if event_dt else _year_for_report(report)
            isin = _normalize_isin(row.get("isin"))
            currency = _none_text(row.get("currency_code")) or TSIFRA_BASE_CURRENCY
            key = (_event_date_key(row.get("date")), isin, currency)
            withholding_tax = -tax_by_key.get(key, Decimal("0"))
            instrument = _lookup_instrument(instrument_lookup, symbol=_none_text(row.get("ticker")), isin=isin, year=year) or {}
            symbol = _none_text(row.get("ticker")) or _none_text(instrument.get("symbol")) or isin
            gross_amount = _decimal(row.get("in_qty"))
            rate = _annual_rate(fx_provider, year, currency, warnings)
            tax = gross_amount * Decimal("0.10")
            rows.append(
                {
                    "date": event_dt.date().isoformat() if event_dt else None,
                    "pay_date": event_dt.date().isoformat() if event_dt else None,
                    "symbol": symbol,
                    "isin": isin,
                    "country": _none_text(instrument.get("country")) or _country_from_isin(isin),
                    "currency": currency,
                    "gross_amount": _money_text(gross_amount),
                    "withholding_tax": _money_text(withholding_tax),
                    "net_amount": _money_text(gross_amount + withholding_tax),
                    "kzt_rate": str(rate) if rate is not None else None,
                    "gross_amount_kzt": _amount_kzt(gross_amount, rate),
                    "tax": str(tax),
                    "tax_kzt": _amount_kzt(tax, rate),
                    "offshore_flag": None,
                    "kase_aix_preferential_flag": None,
                    "source_report": str(report.path),
                }
            )
    return rows


def _is_dividend_gross_money_row(row: Mapping[str, Any]) -> bool:
    if _none_text(row.get("type")) == "dividend":
        return _decimal(row.get("in_qty")) > 0
    oper_name = str(row.get("oper_name") or "").lower()
    return "дивиденд" in oper_name and _decimal(row.get("in_qty")) > 0


def _is_dividend_tax_money_row(row: Mapping[str, Any]) -> bool:
    if _none_text(row.get("type")) == "nalog_div":
        return _decimal(row.get("out_qty")) > 0
    oper_name = str(row.get("oper_name") or "").lower()
    return "дивиденд" in oper_name and "налог" in oper_name and _decimal(row.get("out_qty")) > 0


def _build_interest(reports: Sequence[ParsedTsifraReport], fx_provider: AnnualFxRateProvider, warnings: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    repo_groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for report in reports:
        for row in report.rows.get(TSIFRA_SECTION_REPOS, []):
            repo_id = _none_text(row.get("t_id"))
            if repo_id:
                repo_groups[repo_id].append(row)

    for repo_id, group_rows in sorted(repo_groups.items()):
        opening = _repo_part_row(group_rows, "1")
        closing = _repo_part_row(group_rows, "2")
        if opening is None or closing is None:
            continue
        close_dt = _parse_datetime(closing.get("exec_date") or closing.get("deal_date") or closing.get("deal_time"))
        open_amount = abs(_decimal(opening.get("deal_volume")))
        close_amount = abs(_decimal(closing.get("deal_volume")))
        opening_commission = abs(_decimal(opening.get("comis"))) + abs(_decimal(opening.get("comis_nds")))
        gross_amount = close_amount - open_amount - opening_commission
        currency = _none_text(closing.get("currency") or opening.get("currency")) or TSIFRA_BASE_CURRENCY
        year = close_dt.year if close_dt else _year_from_report_rows(group_rows)
        rate = _annual_rate(fx_provider, year, currency, warnings)
        rows.append(
            {
                "date": close_dt.date().isoformat() if close_dt else None,
                "description": f"Repo reward {repo_id} {_none_text(closing.get('security_name') or opening.get('security_name')) or ''}".strip(),
                "currency": currency,
                "gross_amount": _money_text(gross_amount),
                "withholding_tax": "0.00",
                "net_amount": _money_text(gross_amount),
                "kzt_rate": str(rate) if rate is not None else None,
                "gross_amount_kzt": _amount_kzt(gross_amount, rate),
                "withholding_tax_kzt": "0.00",
                "net_amount_kzt": _amount_kzt(gross_amount, rate),
                "commission": _money_text(opening_commission),
                "source_report": _combine_source_reports(group_rows),
                "_repo_id": repo_id,
            }
        )
    return rows


def _repo_part_row(rows: Sequence[Mapping[str, Any]], repo_part: str) -> Mapping[str, Any] | None:
    for row in rows:
        if _none_text(row.get("repo_part")) == repo_part:
            return row
    return None


def _year_from_report_rows(rows: Sequence[Mapping[str, Any]]) -> int | None:
    for row in rows:
        parsed = _parse_datetime(row.get("report_period_end") or row.get("exec_date") or row.get("deal_date"))
        if parsed is not None:
            return parsed.year
    return None


def _build_coupons(
    reports: Sequence[ParsedTsifraReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        for row in report.rows.get(TSIFRA_SECTION_MONEY_MOVES, []):
            if not _is_coupon_money_row(row):
                continue
            event_dt = _parse_datetime(row.get("date"))
            year = event_dt.year if event_dt else _year_for_report(report)
            isin = _normalize_isin(row.get("isin"))
            currency = _none_text(row.get("currency_code")) or TSIFRA_BASE_CURRENCY
            instrument = _lookup_instrument(instrument_lookup, isin=isin, year=year) or {}
            amount = _decimal(row.get("in_qty"))
            withholding_tax = -(amount * Decimal("0.30"))
            net_amount = amount + withholding_tax
            rate = _annual_rate(fx_provider, year, currency, warnings)
            rows.append(
                {
                    "date": event_dt.date().isoformat() if event_dt else None,
                    "symbol": _none_text(instrument.get("symbol")) or isin,
                    "isin": isin,
                    "country": _none_text(instrument.get("country")) or _country_from_isin(isin),
                    "currency": currency,
                    "gross_amount": _money_text(amount),
                    "withholding_tax": _money_text(withholding_tax),
                    "net_amount": _money_text(net_amount),
                    "kzt_rate": str(rate) if rate is not None else None,
                    "gross_amount_kzt": _amount_kzt(amount, rate),
                    "withholding_tax_kzt": _amount_kzt(withholding_tax, rate),
                    "net_amount_kzt": _amount_kzt(net_amount, rate),
                    "offshore_flag": None,
                    "source_report": str(report.path),
                }
            )
    return rows


def _is_coupon_money_row(row: Mapping[str, Any]) -> bool:
    if _none_text(row.get("type")) == "kupon":
        return _decimal(row.get("in_qty")) > 0
    oper_name = str(row.get("oper_name") or "").lower()
    return ("купон" in oper_name or "облигац" in oper_name) and _decimal(row.get("in_qty")) > 0


def _build_cash_balances(reports: Sequence[ParsedTsifraReport], fx_provider: AnnualFxRateProvider, warnings: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year, report in sorted(_latest_report_by_year(reports).items()):
        money_rows = report.rows.get("money", [])
        if not money_rows:
            continue
        ending_cash = _decimal(money_rows[0].get("end_m"))
        rate = _annual_rate(fx_provider, year, TSIFRA_BASE_CURRENCY, warnings)
        rows.append(
            {
                "year": year,
                "date": report.period_end.isoformat() if report.period_end else None,
                "currency": TSIFRA_BASE_CURRENCY,
                "ending_cash": str(ending_cash),
                "ending_cash_kzt": _amount_kzt(ending_cash, rate),
                "source_report": str(report.path),
            }
        )
    return rows


def _build_trade_withholding_taxes(
    reports: Sequence[ParsedTsifraReport],
    coupons: Sequence[Mapping[str, Any]],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> list[dict[str, Any]]:
    total_tax_paid: dict[tuple[int | None, str], Decimal] = defaultdict(Decimal)
    tax_rows_by_key: dict[tuple[int | None, str], list[Mapping[str, Any]]] = defaultdict(list)
    latest_date_by_key: dict[tuple[int | None, str], datetime] = {}
    for report in reports:
        for row in report.rows.get(TSIFRA_SECTION_MONEY_MOVES, []):
            if not _is_other_tax_money_row(row):
                continue
            event_dt = _parse_datetime(row.get("date"))
            year = event_dt.year if event_dt else _year_for_report(report)
            currency = _none_text(row.get("currency_code")) or TSIFRA_BASE_CURRENCY
            key = (year, currency)
            total_tax_paid[key] += _decimal(row.get("out_qty")) - _decimal(row.get("in_qty"))
            tax_rows_by_key[key].append(row)
            if event_dt is not None and (key not in latest_date_by_key or event_dt > latest_date_by_key[key]):
                latest_date_by_key[key] = event_dt

    coupon_tax_paid: dict[tuple[int | None, str], Decimal] = defaultdict(Decimal)
    for coupon in coupons:
        year = _record_year_from_local(coupon, "date")
        currency = _none_text(coupon.get("currency")) or TSIFRA_BASE_CURRENCY
        coupon_tax_paid[(year, currency)] += max(-_decimal(coupon.get("withholding_tax")), Decimal("0"))

    rows: list[dict[str, Any]] = []
    for key in sorted(total_tax_paid, key=lambda item: (-1 if item[0] is None else item[0], item[1])):
        year, currency = key
        trade_tax_paid = total_tax_paid[key] - coupon_tax_paid.get(key, Decimal("0"))
        if trade_tax_paid <= Decimal("0.0001"):
            if trade_tax_paid < Decimal("-0.0001"):
                warnings.append(
                    f"Tsifra {year} {currency}: inferred coupon withholding exceeds non-dividend tax rows by {_money_text(-trade_tax_paid)}."
                )
            continue
        rate = _annual_rate(fx_provider, year, currency, warnings)
        withholding_tax = -trade_tax_paid
        event_dt = latest_date_by_key.get(key)
        rows.append(
            {
                "year": year,
                "date": event_dt.date().isoformat() if event_dt else None,
                "currency": currency,
                "withholding_tax": _money_text(withholding_tax),
                "withholding_tax_kzt": _amount_kzt(withholding_tax, rate),
                "issuer_outside_kz_flag": True,
                "offshore_flag": False,
                "preferential_tax_flag": False,
                "source_report": _combine_source_reports(tax_rows_by_key[key]),
            }
        )
    return rows


def _record_year_from_local(record: Mapping[str, Any], *date_fields: str) -> int | None:
    for field_name in date_fields:
        parsed = _parse_datetime(record.get(field_name))
        if parsed is not None:
            return parsed.year
    year = record.get("year")
    return int(year) if year not in (None, "") else None


def _build_unprocessed_repo_rows(reports: Sequence[ParsedTsifraReport], interest_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    processed_repo_ids = {_none_text(row.get("_repo_id")) for row in interest_rows if _none_text(row.get("_repo_id"))}
    for report in reports:
        for idx, row in enumerate(report.rows.get(TSIFRA_SECTION_REPOS, []), start=1):
            repo_id = _none_text(row.get("t_id"))
            if repo_id in processed_repo_ids:
                continue
            event_dt = _parse_datetime(row.get("exec_date") or row.get("deal_date") or row.get("deal_time"))
            rows.append(
                {
                    "severity": "warning",
                    "reason": "tsifra_repo_unpaired",
                    "details": "Tsifra repo leg is preserved because matching opening/closing repo leg was not found.",
                    "source_sheet": "repos",
                    "source_report": str(report.path),
                    "trade_id": repo_id or f"{report.path.name}:repo:{idx}",
                    "date_time": event_dt.isoformat(sep=" ") if event_dt else None,
                    "symbol": _none_text(row.get("security_name")),
                    "isin": _normalize_isin(row.get("isin_code")),
                    "asset_type": _none_text(row.get("security_type")),
                    "currency": _none_text(row.get("currency")) or TSIFRA_BASE_CURRENCY,
                    "quantity": str(_decimal(row.get("quantity"))),
                    "price": str(_decimal(row.get("deal_price"))),
                    "amount": str(abs(_decimal(row.get("deal_volume")))),
                    "commission": str(abs(_decimal(row.get("comis"))) + abs(_decimal(row.get("comis_nds")))),
                }
            )
    return rows


def _is_other_tax_money_row(row: Mapping[str, Any]) -> bool:
    oper_name = str(row.get("oper_name") or "").lower()
    return "налог" in oper_name and "дивиденд" not in oper_name


def _populate_raw_totals(
    totals: RawReportTotals,
    reports: Sequence[ParsedTsifraReport],
    trades: Sequence[Mapping[str, Any]],
    dividends: Sequence[Mapping[str, Any]],
    interest: Sequence[Mapping[str, Any]],
    coupons: Sequence[Mapping[str, Any]],
    transfer_totals_by_currency: Mapping[str, Decimal],
) -> None:
    gross_trades = Decimal("0")
    commissions = Decimal("0")
    for trade in trades:
        trade_dt = _parse_datetime(trade.get("date_time"))
        year = trade_dt.year if trade_dt else None
        currency = _none_text(trade.get("currency"))
        instrument_key = _none_text(trade.get("isin") or trade.get("symbol"))
        amount = _decimal(trade.get("amount"))
        commission = _decimal(trade.get("commission"))
        gross_trades += amount
        commissions += commission
        key = _dimension_key(metric=ReconciliationMetric.TRADE_GROSS_AMOUNT_BY_INSTRUMENT.value, year=year, currency=currency, instrument_key=instrument_key)
        totals.totals_by_metric_currency[key] = totals.totals_by_metric_currency.get(key, Decimal("0")) + amount

    _populate_snapshot_positions(totals, reports)
    _populate_snapshot_cash_balances(totals, reports)
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
        }
    )
    for currency, amount in transfer_totals_by_currency.items():
        totals.totals_by_metric_currency[_dimension_key(metric=ReconciliationMetric.TOTAL_DEPOSITS_WITHDRAWALS_TRANSFERS.value, currency=currency)] = amount


def _populate_snapshot_positions(totals: RawReportTotals, reports: Sequence[ParsedTsifraReport]) -> None:
    latest_dates = {report.period_end for report in _latest_report_by_year(reports).values()}
    for report in reports:
        if report.period_end is None or report.period_end not in latest_dates:
            continue
        for row in report.rows.get(TSIFRA_SECTION_POSITIONS, []):
            quantity = _decimal(row.get("end_q"))
            if abs(quantity) <= Decimal("0.0001"):
                continue
            symbol = _symbol_from_position(row) or _normalize_isin(row.get("isin"))
            key = _dimension_key(year=report.period_end.year, currency=_none_text(row.get("price_curr")) or TSIFRA_BASE_CURRENCY, instrument_key=symbol)
            totals.positions_by_key[key] = totals.positions_by_key.get(key, Decimal("0")) + quantity


def _populate_snapshot_cash_balances(totals: RawReportTotals, reports: Sequence[ParsedTsifraReport]) -> None:
    for year, report in _latest_report_by_year(reports).items():
        money_rows = report.rows.get("money", [])
        if not money_rows:
            continue
        key = _dimension_key(year=year, currency=TSIFRA_BASE_CURRENCY)
        totals.cash_by_currency[key] = totals.cash_by_currency.get(key, Decimal("0")) + _decimal(money_rows[0].get("end_m"))


def _latest_report_by_year(reports: Sequence[ParsedTsifraReport]) -> dict[int, ParsedTsifraReport]:
    latest_by_year: dict[int, ParsedTsifraReport] = {}
    for report in reports:
        if report.period_end is None:
            continue
        current = latest_by_year.get(report.period_end.year)
        if current is None or (current.period_end and report.period_end > current.period_end):
            latest_by_year[report.period_end.year] = report
    return latest_by_year


def _combine_source_reports(rows: Sequence[Mapping[str, Any]]) -> str | None:
    values = [str(row.get("source_report")) for row in rows if row.get("source_report")]
    return "; ".join(dict.fromkeys(values)) if values else None


def _event_date_key(value: Any) -> str | None:
    event_dt = _parse_datetime(value)
    return event_dt.date().isoformat() if event_dt else None


def _max_report_year(reports: Sequence[ParsedTsifraReport], trades: Sequence[Mapping[str, Any]], transfers: Sequence[Mapping[str, Any]]) -> int | None:
    years: list[int] = []
    for report in reports:
        if report.period_end:
            years.append(report.period_end.year)
    for row in (*trades, *transfers):
        parsed = _parse_datetime(row.get("date_time") or row.get("date"))
        if parsed:
            years.append(parsed.year)
    return max(years) if years else None


def _year_for_report(report: ParsedTsifraReport) -> int | None:
    return report.period_end.year if report.period_end else None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _dimension_key(*, metric: str | None = None, year: int | None = None, currency: str | None = None, instrument_key: str | None = None) -> str:
    return "|".join("" if value is None else str(value) for value in (metric, year, currency, instrument_key))
