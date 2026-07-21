"""Native parser for Tabys / AIX CSD operation-report PDFs."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from kztax270.canonical.schema import AccountMetadata, CanonicalDataset
from kztax270.reconciliation.models import ReconciliationMetric
from kztax270.reference.fx import AnnualFxRateProvider
from kztax270.reference.securities import AixInstrumentResolver
from kztax270.transfers import TransferInFifoResolver

from .base import BrokerReport, ParseResult
from .discovery import DiscoveryRule, discover_raw_reports
from .ib import (
    _amount_kzt,
    _annual_rate,
    _build_broker_trade_realized_pl,
    _build_fifo_and_positions,
    _build_unprocessed_rows,
    _build_years_results,
    _canonical_trade_rows,
    _canonical_transfer_rows,
    _instrument_identity_key_from_values,
    _instrument_symbol_history,
    _money_text,
    _sort_trades_by_datetime,
)

BROKER_CODE = "tabys"
RAW_FOLDER = "tabys"
TABYS_BASE_CURRENCY = "KZT"
TABYS_EXCHANGE = "AIX"

@dataclass(slots=True)
class ParsedTabysReport:
    path: Path
    account_id: str | None = None
    holder_name: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)


class TabysParser:
    broker_code = BROKER_CODE

    def __init__(
        self,
        fx_provider: AnnualFxRateProvider | None = None,
        transfer_in_resolver: TransferInFifoResolver | None = None,
        instrument_resolver: AixInstrumentResolver | None = None,
    ) -> None:
        self.fx_provider = fx_provider or AnnualFxRateProvider({})
        self.transfer_in_resolver = transfer_in_resolver
        self.instrument_resolver = instrument_resolver or AixInstrumentResolver()

    def discover_reports(self, raw_root: Path, account_id: str) -> list[BrokerReport]:
        return discover_raw_reports(
            raw_root,
            DiscoveryRule(broker=RAW_FOLDER, account_id=account_id, extensions=frozenset({".pdf"})),
        )

    def parse_reports(self, reports: Sequence[BrokerReport], account_id: str) -> ParseResult:
        parsed_reports = [parse_tabys_pdf(report.path, account_id=account_id) for report in reports]
        dataset = build_canonical_dataset(
            parsed_reports,
            account_id,
            self.fx_provider,
            transfer_in_resolver=self.transfer_in_resolver,
            instrument_resolver=self.instrument_resolver,
        )
        dataset.raw_totals.source_reports = [str(report.path) for report in reports]
        return ParseResult(
            broker=self.broker_code,
            account_id=account_id,
            reports=reports,
            dataset=dataset,
            raw_totals=dataset.raw_totals,
        )


def parse_tabys_pdf(path: Path, *, account_id: str | None = None) -> ParsedTabysReport:
    parsed = ParsedTabysReport(path=path, account_id=account_id)
    pdfplumber = _pdfplumber()
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            _populate_metadata(parsed, text)
            for table in page.extract_tables() or []:
                header_index = _operation_header_index(table)
                if header_index is None:
                    continue
                for source_row, raw_row in enumerate(table[header_index + 1 :], start=header_index + 2):
                    row = _parse_operation_row(
                        raw_row,
                        source_report=str(path),
                        source_page=page_number,
                        source_row=source_row,
                    )
                    if row is not None:
                        parsed.rows.append(row)
    return parsed


def build_canonical_dataset(
    reports: Sequence[ParsedTabysReport],
    account_id: str,
    fx_provider: AnnualFxRateProvider,
    *,
    transfer_in_resolver: TransferInFifoResolver | None = None,
    instrument_resolver: AixInstrumentResolver | None = None,
) -> CanonicalDataset:
    dataset = CanonicalDataset(
        metadata=AccountMetadata(broker=BROKER_CODE, account_id=account_id, base_currency=TABYS_BASE_CURRENCY)
    )
    instruments = _build_instruments(
        reports,
        account_id,
        instrument_resolver or AixInstrumentResolver(),
    )
    instrument_lookup = {str(row["symbol"]): row for row in instruments}
    dataset.tables["Instruments"] = instruments
    dataset.tables["CorporateActions"] = []
    dataset.tables["Dividends"] = []

    internal_trades = _sort_trades_by_datetime(_build_trades(reports, instrument_lookup, dataset.warnings))
    transfers, transfer_totals = _build_transfers(reports, instrument_lookup, internal_trades)
    dataset.tables["Trades"] = _canonical_trade_rows(internal_trades)
    dataset.tables["_BrokerTradeRealizedPL"] = _build_broker_trade_realized_pl(internal_trades)

    fifo_rows, positions, transfer_rows = _build_fifo_and_positions(
        internal_trades,
        transfers=transfers,
        initial_lots=[],
        max_year=_max_report_year(reports),
        fx_provider=fx_provider,
        warnings=dataset.warnings,
        symbol_history=_instrument_symbol_history(instruments),
        transfer_in_resolver=transfer_in_resolver,
    )
    dataset.tables["Fifo"] = fifo_rows
    dataset.tables["Positions"] = positions
    dataset.tables["Transfers"] = _canonical_transfer_rows(transfer_rows)
    dataset.tables["Interest"] = []
    dataset.tables["Coupons"] = _build_coupons(reports, instrument_lookup, fx_provider, dataset.warnings)
    dataset.tables["CashBalances"] = []
    dataset.tables["Unprocessed"] = [
        *_build_unprocessed_rows(dataset.tables["Trades"], fifo_rows),
        *_build_tabys_unprocessed_rows(reports, instrument_lookup),
    ]
    dataset.tables["Years_Results"] = _build_years_results(dataset)
    _populate_raw_totals(dataset, internal_trades, transfer_totals)
    return dataset


def _populate_metadata(report: ParsedTabysReport, text: str) -> None:
    if report.period_start is None or report.period_end is None:
        match = re.search(r"[cс]\s+(\d{4}-\d{2}-\d{2})\s+по\s+(\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
        if match:
            report.period_start = date.fromisoformat(match.group(1))
            report.period_end = date.fromisoformat(match.group(2))
    if report.account_id is None:
        match = re.search(r"Номер\s+счета:\s*(\S+)", text, re.IGNORECASE)
        if match:
            report.account_id = match.group(1)
    if report.holder_name is None:
        match = re.search(r"ФИО:\s*(.+)", text)
        if match:
            report.holder_name = _clean_text(match.group(1))


def _operation_header_index(table: Sequence[Sequence[Any]]) -> int | None:
    for index, row in enumerate(table):
        values = [_clean_text(value) for value in row]
        if len(values) >= 15 and values[0] == "№" and values[5] == "Тип сделки":
            return index
    return None


def _parse_operation_row(
    raw_row: Sequence[Any],
    *,
    source_report: str,
    source_page: int,
    source_row: int,
) -> dict[str, Any] | None:
    values = [_clean_text(value) for value in raw_row[:15]]
    if len(values) < 15 or not values[0].isdigit():
        return None
    transaction_dt = _parse_datetime(values[1])
    settlement_dt = _parse_datetime(values[2])
    operation = values[5]
    if transaction_dt is None or not operation:
        return None
    return {
        "sequence": int(values[0]),
        "transaction_datetime": transaction_dt.isoformat(sep=" "),
        "settlement_datetime": settlement_dt.isoformat(sep=" ") if settlement_dt else None,
        "transaction_id": re.sub(r"\s+", "", values[3]) or None,
        "account_type": values[4],
        "operation": operation,
        "security": values[6] or None,
        "quantity": str(_decimal(values[7])),
        "price": str(_decimal(values[8])),
        "amount": str(_decimal(values[9])),
        "currency": values[10] or None,
        "exchange_rate": str(_decimal(values[11])),
        "amount_kzt": str(_decimal(values[12])),
        "status": values[13] or None,
        "commission_kzt": str(_decimal(values[14])),
        "source_report": source_report,
        "source_page": source_page,
        "source_row": source_row,
    }


def _build_instruments(
    reports: Sequence[ParsedTabysReport],
    account_id: str,
    resolver: AixInstrumentResolver,
) -> list[dict[str, Any]]:
    first_source_by_symbol: dict[str, str] = {}
    latest_date_by_symbol: dict[str, date | None] = {}
    for report in reports:
        for row in report.rows:
            symbol = _security_symbol(row)
            if symbol is None:
                continue
            if _is_security_account(row) or _is_income_operation(row):
                first_source_by_symbol.setdefault(symbol, str(report.path))
                latest_date_by_symbol[symbol] = max(
                    (value for value in (latest_date_by_symbol.get(symbol), report.period_end) if value is not None),
                    default=None,
                )

    instruments: list[dict[str, Any]] = []
    for symbol in sorted(first_source_by_symbol):
        latest_date = latest_date_by_symbol[symbol]
        reference = resolver.resolve(symbol, snapshot_year=latest_date.year if latest_date else None)
        isin = _text(reference.get("isin"))
        country = _text(reference.get("country")) or _country_from_isin(isin)
        asset_type = _text(reference.get("type"))
        instruments.append(
            {
                "symbol": symbol,
                "description": _text(reference.get("description")) or symbol,
                "conid": None,
                "security_id": isin,
                "underlying": None,
                "listing_exchange": TABYS_EXCHANGE,
                "multiplier": "1",
                "type": asset_type,
                "code": None,
                "year": None,
                "expiry": None,
                "delivery_month": None,
                "strike": None,
                "issuer": _text(reference.get("issuer")),
                "maturity": _text(reference.get("maturity")),
                "cusip": None,
                "country": country,
                "isin": isin,
                "figi": None,
                "issuer_country": country,
                "offshore_flag": False if country == "KZ" else None,
                "issuer_outside_kz_flag": False if country == "KZ" else (True if country else None),
                "preferential_tax_flag": None,
                "source_broker": BROKER_CODE,
                "source_account": account_id,
                "source_report": first_source_by_symbol[symbol],
                "as_of_date": latest_date.isoformat() if latest_date else None,
                "_currency": _text(reference.get("currency")),
                "_face_value": _text(reference.get("face_value")),
                "_coupon_rate": _text(reference.get("coupon_rate")),
                "_coupon_frequency": _text(reference.get("coupon_frequency")),
                "_reference_source": _text(reference.get("source")),
            }
        )
    return instruments


def _build_trades(
    reports: Sequence[ParsedTabysReport],
    instruments: Mapping[str, Mapping[str, Any]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for report in reports:
        for row in report.rows:
            if not _is_executed(row) or not _is_trade_operation(row):
                continue
            symbol = _security_symbol(row)
            instrument = instruments.get(symbol or "", {})
            quantity = _decimal(row.get("quantity"))
            if _operation_key(row) == "продажа":
                quantity = -quantity
            price = _decimal(row.get("price"))
            amount = abs(_decimal(row.get("amount"))) or abs(quantity * price)
            currency = _text(row.get("currency")) or _text(instrument.get("_currency")) or TABYS_BASE_CURRENCY
            commission = _commission_in_trade_currency(row, currency, warnings)
            isin = _text(instrument.get("isin"))
            country = _text(instrument.get("country")) or _country_from_isin(isin)
            trade_id = _event_id(report, row, "trade")
            trades.append(
                {
                    "date_time": row.get("transaction_datetime"),
                    "trade_id": trade_id,
                    "trade_type": "trade",
                    "symbol": symbol,
                    "isin": isin,
                    "asset_type": _text(instrument.get("type")) or "Stocks",
                    "quantity": str(quantity),
                    "calculation_quantity": str(quantity),
                    "price": str(price),
                    "calculation_price": str(price),
                    "multiplier": "1",
                    "_calculation_multiplier": "1",
                    "amount": str(amount),
                    "commission": str(commission),
                    "amount_with_commission": str(amount + commission),
                    "currency": currency,
                    "exchange": TABYS_EXCHANGE,
                    "country": country,
                    "source_report": row.get("source_report"),
                    "_instrument_identity_key": _instrument_identity_key_from_values(isin=isin, symbol=symbol),
                    "_broker_realized_pl": None,
                    "_settlement_datetime": row.get("settlement_datetime"),
                    "_commission_kzt": row.get("commission_kzt"),
                    "_exchange_rate": row.get("exchange_rate"),
                }
            )
    return trades


def _commission_in_trade_currency(row: Mapping[str, Any], currency: str, warnings: list[str]) -> Decimal:
    commission_kzt = abs(_decimal(row.get("commission_kzt")))
    if commission_kzt == 0:
        return Decimal("0")
    if currency == "KZT":
        return commission_kzt
    exchange_rate = _decimal(row.get("exchange_rate"))
    if exchange_rate:
        return commission_kzt / exchange_rate
    warning = (
        f"Tabys trade {row.get('transaction_id')} has {commission_kzt} KZT commission "
        f"but no KZT/{currency} transaction exchange rate; commission omitted from FIFO."
    )
    if warning not in warnings:
        warnings.append(warning)
    return Decimal("0")


def _build_transfers(
    reports: Sequence[ParsedTabysReport],
    instruments: Mapping[str, Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Decimal]]:
    transfers: list[dict[str, Any]] = []
    totals_by_currency: dict[str, Decimal] = defaultdict(Decimal)
    security_rows = sorted(
        (
            (report, row)
            for report in reports
            for row in report.rows
            if _is_executed(row) and _is_security_transfer_operation(row)
        ),
        key=lambda item: _parse_datetime(item[1].get("transaction_datetime")) or datetime.max,
    )
    holdings: dict[str, Decimal] = defaultdict(Decimal)
    trade_events = sorted(
        trades,
        key=lambda row: _parse_datetime(row.get("date_time")) or datetime.max,
    )
    trade_index = 0
    for report, row in security_rows:
        event_dt = _parse_datetime(row.get("transaction_datetime"))
        while trade_index < len(trade_events):
            trade = trade_events[trade_index]
            trade_dt = _parse_datetime(trade.get("date_time"))
            if event_dt is not None and trade_dt is not None and trade_dt > event_dt:
                break
            identity = _text(trade.get("isin") or trade.get("symbol"))
            if identity:
                holdings[identity] += _decimal(trade.get("quantity"))
            trade_index += 1

        symbol = _security_symbol(row)
        instrument = instruments.get(symbol or "", {})
        isin = _text(instrument.get("isin"))
        identity = isin or symbol or ""
        quantity = abs(_decimal(row.get("quantity")))
        raw_quantity = -quantity if holdings[identity] >= quantity else quantity
        holdings[identity] += raw_quantity
        raw_price = _decimal(row.get("price"))
        raw_amount = _decimal(row.get("amount"))
        direction_note = "направление определено по остатку бумаг до операции"
        valuation_note = f"оценка брокера: {raw_amount} {row.get('currency')} по цене {raw_price}"
        transfers.append(
            {
                "date": event_dt.date().isoformat() if event_dt else None,
                "transfer_type": "security",
                "direction": "in" if raw_quantity > 0 else "out",
                "asset_type": _text(instrument.get("type")) or "Stocks",
                "symbol": symbol,
                "isin": isin,
                "currency": _text(row.get("currency")) or _text(instrument.get("_currency")) or TABYS_BASE_CURRENCY,
                "quantity": str(quantity),
                "price": None,
                "enter_date": None,
                "amount": None,
                "broker_comment": f"{row.get('operation')}; {direction_note}; {valuation_note}",
                "counterparty": None,
                "source_report": row.get("source_report"),
                "country": _text(instrument.get("country")) or _country_from_isin(isin),
                "_raw_quantity": str(raw_quantity),
                "_transfer_id": _event_id(report, row, "security-transfer"),
                "_instrument_identity_key": _instrument_identity_key_from_values(isin=isin, symbol=symbol),
                "_multiplier": "1",
            }
        )

    for report in reports:
        for row in report.rows:
            if not _is_executed(row) or not _is_cash_transfer_operation(row):
                continue
            operation = _operation_key(row)
            amount = abs(_decimal(row.get("amount")))
            if operation == "вывод средств":
                amount = -amount
            currency = _text(row.get("currency")) or _security_symbol(row) or TABYS_BASE_CURRENCY
            totals_by_currency[currency] += amount
            commission_kzt = abs(_decimal(row.get("commission_kzt")))
            exchange_rate = _decimal(row.get("exchange_rate"))
            details = [str(row.get("operation"))]
            if commission_kzt:
                details.append(f"комиссия {commission_kzt} KZT")
            if exchange_rate:
                details.append(f"курс операции {exchange_rate} KZT/{currency}")
            event_dt = _parse_datetime(row.get("settlement_datetime") or row.get("transaction_datetime"))
            transfers.append(
                {
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
                    "broker_comment": "; ".join(details),
                    "counterparty": None,
                    "source_report": row.get("source_report"),
                    "_transfer_id": _event_id(report, row, "cash-transfer"),
                }
            )
    return transfers, dict(totals_by_currency)


def _build_coupons(
    reports: Sequence[ParsedTabysReport],
    instruments: Mapping[str, Mapping[str, Any]],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> list[dict[str, Any]]:
    coupons: list[dict[str, Any]] = []
    for report in reports:
        for row in report.rows:
            if not _is_executed(row) or not _is_income_operation(row):
                continue
            symbol = _security_symbol(row)
            instrument = instruments.get(symbol or "", {})
            if _text(instrument.get("type")) != "Bonds":
                continue
            event_dt = _parse_datetime(row.get("settlement_datetime") or row.get("transaction_datetime"))
            year = event_dt.year if event_dt else _year_for_report(report)
            currency = _text(row.get("currency")) or _text(instrument.get("_currency")) or TABYS_BASE_CURRENCY
            gross = _decimal(row.get("amount"))
            withholding = Decimal("0")
            rate = _annual_rate(fx_provider, year, currency, warnings)
            coupons.append(
                {
                    "date": event_dt.date().isoformat() if event_dt else None,
                    "symbol": symbol,
                    "isin": _text(instrument.get("isin")),
                    "country": _text(instrument.get("country")) or _country_from_isin(_text(instrument.get("isin"))),
                    "currency": currency,
                    "gross_amount": _money_text(gross),
                    "withholding_tax": _money_text(withholding),
                    "net_amount": _money_text(gross + withholding),
                    "kzt_rate": str(rate) if rate is not None else None,
                    "gross_amount_kzt": _amount_kzt(gross, rate),
                    "withholding_tax_kzt": _amount_kzt(withholding, rate),
                    "net_amount_kzt": _amount_kzt(gross + withholding, rate),
                    "is_revert": False,
                    "offshore_flag": False,
                    "source_report": row.get("source_report"),
                }
            )
    return coupons


def _build_tabys_unprocessed_rows(
    reports: Sequence[ParsedTabysReport],
    instruments: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        for row in report.rows:
            if not _is_executed(row):
                rows.append(_unprocessed_row(row, "tabys_operation_not_executed", "Tabys operation status is not Executed."))
                continue
            if _is_income_operation(row):
                instrument = instruments.get(_security_symbol(row) or "", {})
                if _text(instrument.get("type")) not in {"Bonds", "Stocks"}:
                    rows.append(
                        _unprocessed_row(
                            row,
                            "ambiguous_tabys_income_type",
                            "The PDF labels this as dividend or coupon, but the instrument type is unknown.",
                        )
                    )
                continue
            if not (_is_trade_operation(row) or _is_security_transfer_operation(row) or _is_cash_transfer_operation(row)):
                rows.append(_unprocessed_row(row, "unsupported_tabys_operation", "Tabys operation type is not supported."))
    return rows


def _unprocessed_row(row: Mapping[str, Any], reason: str, details: str) -> dict[str, Any]:
    return {
        "severity": "warning",
        "reason": reason,
        "details": details,
        "source_sheet": "Tabys operations",
        "source_report": row.get("source_report"),
        "trade_id": row.get("transaction_id"),
        "date_time": row.get("transaction_datetime"),
        "symbol": _security_symbol(row),
        "isin": None,
        "asset_type": None,
        "currency": row.get("currency"),
        "quantity": row.get("quantity"),
        "price": row.get("price"),
        "amount": row.get("amount"),
        "commission": row.get("commission_kzt"),
    }


def _populate_raw_totals(
    dataset: CanonicalDataset,
    trades: Sequence[Mapping[str, Any]],
    transfer_totals: Mapping[str, Decimal],
) -> None:
    trade_amount = sum((_decimal(row.get("amount")) for row in trades), Decimal("0"))
    trade_commission = sum((_decimal(row.get("commission")) for row in trades), Decimal("0"))
    coupon_amount = sum((_decimal(row.get("gross_amount")) for row in dataset.tables["Coupons"]), Decimal("0"))
    transfer_amount = sum(transfer_totals.values(), Decimal("0"))
    dataset.raw_totals.scalar_totals.update(
        {
            ReconciliationMetric.TOTAL_TRADES_GROSS_AMOUNT.value: trade_amount,
            ReconciliationMetric.TOTAL_COMMISSIONS.value: trade_commission,
            ReconciliationMetric.TOTAL_COUPONS.value: coupon_amount,
            ReconciliationMetric.TOTAL_DEPOSITS_WITHDRAWALS_TRANSFERS.value: transfer_amount,
        }
    )
    for trade in trades:
        trade_dt = _parse_datetime(trade.get("date_time"))
        key = _dimension_key(
            metric=ReconciliationMetric.TRADE_GROSS_AMOUNT_BY_INSTRUMENT.value,
            year=trade_dt.year if trade_dt else None,
            currency=_text(trade.get("currency")),
            instrument_key=_text(trade.get("isin") or trade.get("symbol")),
        )
        dataset.raw_totals.totals_by_metric_currency[key] = (
            dataset.raw_totals.totals_by_metric_currency.get(key, Decimal("0")) + _decimal(trade.get("amount"))
        )
    for currency, amount in transfer_totals.items():
        key = _dimension_key(
            metric=ReconciliationMetric.TOTAL_DEPOSITS_WITHDRAWALS_TRANSFERS.value,
            currency=currency,
        )
        dataset.raw_totals.totals_by_metric_currency[key] = amount


def _event_id(report: ParsedTabysReport, row: Mapping[str, Any], kind: str) -> str:
    transaction_id = _text(row.get("transaction_id")) or str(row.get("sequence") or row.get("source_row") or "unknown")
    return f"{report.path.name}:{kind}:{transaction_id}"


def _security_symbol(row: Mapping[str, Any]) -> str | None:
    symbol = _text(row.get("security"))
    if symbol in {"KZT", "USD", "EUR", "RUB", "GBP", "CHF"} and not _is_security_account(row):
        return None
    return symbol


def _is_security_account(row: Mapping[str, Any]) -> bool:
    return "ценные бумаги" in (_text(row.get("account_type")) or "").casefold()


def _operation_key(row: Mapping[str, Any]) -> str:
    return " ".join((_text(row.get("operation")) or "").casefold().split())


def _is_trade_operation(row: Mapping[str, Any]) -> bool:
    return _is_security_account(row) and _operation_key(row) in {"покупка", "продажа"}


def _is_security_transfer_operation(row: Mapping[str, Any]) -> bool:
    return _is_security_account(row) and "перевод ценных бумаг" in _operation_key(row)


def _is_cash_transfer_operation(row: Mapping[str, Any]) -> bool:
    return _operation_key(row) in {"вывод средств", "пополнение средств", "ввод средств"}


def _is_income_operation(row: Mapping[str, Any]) -> bool:
    return "получение дивидендов или купонов" in _operation_key(row)


def _is_executed(row: Mapping[str, Any]) -> bool:
    return (_text(row.get("status")) or "").casefold() == "исполнен"


def _max_report_year(reports: Sequence[ParsedTabysReport]) -> int | None:
    return max((year for year in (_year_for_report(report) for report in reports) if year is not None), default=None)


def _year_for_report(report: ParsedTabysReport) -> int | None:
    return report.period_end.year if report.period_end else (report.period_start.year if report.period_start else None)


def _country_from_isin(isin: str | None) -> str | None:
    if not isin:
        return None
    return "BE" if isin.startswith("XS") else isin[:2]


def _dimension_key(
    *,
    metric: str | None = None,
    year: int | None = None,
    currency: str | None = None,
    instrument_key: str | None = None,
) -> str:
    return "|".join("" if value is None else str(value) for value in (metric, year, currency, instrument_key))


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
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
    if text.casefold() in {"", "none", "nan", "nat", "null", "-"}:
        return Decimal("0")
    return Decimal(text)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\xa0", " ").split())


def _text(value: Any) -> str | None:
    text = _clean_text(value)
    return text or None


def _pdfplumber() -> Any:
    try:
        import pdfplumber  # type: ignore
    except Exception as exc:  # pragma: no cover - dependency error path
        raise RuntimeError("Parsing Tabys PDF reports requires pdfplumber.") from exc
    return pdfplumber
