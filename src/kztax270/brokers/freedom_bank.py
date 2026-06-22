"""Native Freedom Bank PDF parser."""

from __future__ import annotations

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
from kztax270.transfers import TransferInFifoLot, TransferInRequest

from .base import BrokerReport, ParseResult
from .discovery import DiscoveryRule, discover_raw_reports
from .ib import (
    _build_broker_trade_realized_pl,
    _build_fifo_and_positions,
    _build_unprocessed_rows,
    _build_years_results,
    _canonical_trade_rows,
    _canonical_transfer_rows,
    _sort_trades_by_datetime,
)

BROKER_CODE = "freedom_bank"
RAW_FOLDER = "freedom bank"

FREEDOM_BANK_SYMBOL = "FRHCSPC.ETN"
FREEDOM_BANK_DESCRIPTION = "FRHC Fractional SPC Ltd."
FREEDOM_BANK_EXCHANGE = "Freedom"


@dataclass(slots=True)
class ParsedFreedomBankReport:
    path: Path
    account_id: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    account_currency: str | None = None
    brokerage_account: str | None = None
    security_type: str | None = None
    issuer: str | None = None
    isin: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    summary_rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class FreedomBankRawRow:
    source_report: str
    source_page: int
    source_row: int
    broker_trade_id: str | None
    date_time: datetime
    operation: str
    price_usd: Decimal
    price_kzt: Decimal
    quantity: Decimal
    amount_usd: Decimal
    amount_kzt: Decimal
    realized_pl_kzt: Decimal
    details: str | None


class FreedomBankParser:
    broker_code = BROKER_CODE

    def __init__(
        self,
        fx_provider: AnnualFxRateProvider | None = None,
    ) -> None:
        self.fx_provider = fx_provider or AnnualFxRateProvider({})

    def discover_reports(self, raw_root: Path, account_id: str) -> list[BrokerReport]:
        return discover_raw_reports(
            raw_root,
            DiscoveryRule(
                broker=RAW_FOLDER,
                account_id=account_id,
                extensions=frozenset({".pdf"}),
            ),
        )

    def parse_reports(self, reports: Sequence[BrokerReport], account_id: str) -> ParseResult:
        parsed_reports = [parse_freedom_bank_pdf(report.path, account_id=account_id) for report in reports]
        dataset = build_canonical_dataset(parsed_reports, account_id, self.fx_provider)
        dataset.raw_totals.source_reports = [str(report.path) for report in reports]
        return ParseResult(
            broker=self.broker_code,
            account_id=account_id,
            reports=reports,
            dataset=dataset,
            raw_totals=dataset.raw_totals,
        )


def parse_freedom_bank_pdf(path: Path, *, account_id: str | None = None) -> ParsedFreedomBankReport:
    pdfplumber = _pdfplumber()
    parsed = ParsedFreedomBankReport(path=path, account_id=account_id)
    pending: dict[str, str | None] = {}

    with pdfplumber.open(path) as pdf:
        for page_idx, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            _populate_report_metadata(parsed, text)
            for table in page.extract_tables() or []:
                _append_summary_rows(parsed, table)
                header_idx = _trade_header_index(table)
                if header_idx is None:
                    continue
                for row_idx, raw_row in enumerate(table[header_idx + 1 :], start=header_idx + 1):
                    parsed_row = _parse_trade_table_row(
                        raw_row,
                        source_report=str(path),
                        source_page=page_idx,
                        source_row=row_idx,
                        pending=pending,
                    )
                    if parsed_row is None:
                        continue
                    parsed.rows.append(_raw_row_as_record(parsed_row))
    return parsed


def build_canonical_dataset(
    reports: Sequence[ParsedFreedomBankReport],
    account_id: str,
    fx_provider: AnnualFxRateProvider,
) -> CanonicalDataset:
    base_currency = next((report.account_currency for report in reports if report.account_currency), "USD") or "USD"
    dataset = CanonicalDataset(metadata=AccountMetadata(broker=BROKER_CODE, account_id=account_id, base_currency=base_currency))

    instrument = _instrument_record(reports, account_id)
    dataset.tables["Instruments"] = [instrument] if instrument else []

    internal_trades = _sort_trades_by_datetime(_build_trades(reports))
    transfers = _build_transfers(reports)
    transfers.extend(_build_summary_position_adjustments(reports))
    dataset.tables["Trades"] = _canonical_trade_rows(internal_trades)
    dataset.tables["_BrokerTradeRealizedPL"] = _build_broker_trade_realized_pl(internal_trades)

    max_year = max((year for year in (_year_for_report(report) for report in reports) if year), default=None)
    fifo_rows, positions, transfer_rows = _build_fifo_and_positions(
        internal_trades,
        transfers=transfers,
        initial_lots=[],
        max_year=max_year,
        fx_provider=fx_provider,
        warnings=dataset.warnings,
        symbol_history={},
        transfer_in_resolver=_freedom_bank_transfer_resolver(transfers),
    )
    dataset.tables["Fifo"] = fifo_rows
    dataset.tables["Positions"] = positions
    dataset.tables["Transfers"] = _canonical_transfer_rows(transfer_rows)
    dataset.tables["Unprocessed"] = _build_unprocessed_rows(dataset.tables["Trades"], fifo_rows)
    dataset.tables["CorporateActions"] = []
    dataset.tables["Dividends"] = []
    dataset.tables["Interest"] = []
    dataset.tables["Coupons"] = []
    dataset.tables["CashBalances"] = []
    dataset.tables["Years_Results"] = _build_years_results(dataset)

    _populate_raw_totals(dataset.raw_totals, reports, internal_trades)
    return dataset


def _populate_report_metadata(report: ParsedFreedomBankReport, text: str) -> None:
    if report.period_start is None or report.period_end is None:
        period = re.search(r"за период с\s+(\d{2}\.\d{2}\.\d{4})\s+по\s+(\d{2}\.\d{2}\.\d{4})", text, re.IGNORECASE)
        if period:
            report.period_start = _parse_date(period.group(1))
            report.period_end = _parse_date(period.group(2))
    if report.account_currency is None:
        currency = re.search(r"Валюта счета:\s*([A-Z]{3})", text)
        if currency:
            report.account_currency = currency.group(1)
    if report.brokerage_account is None:
        account = re.search(r"Номер брокерского счета Валюта\s+(\d+)\s+([A-Z]{3})", text)
        if account:
            report.brokerage_account = account.group(1)
            report.account_currency = report.account_currency or account.group(2)
    if report.security_type is None:
        security_type = re.search(r"Вид ценной бумаги:\s*(.+)", text)
        if security_type:
            report.security_type = _clean_text(security_type.group(1))
    if report.issuer is None:
        issuer = re.search(r"Наименования эмитента:\s*(.+)", text)
        if issuer:
            report.issuer = _clean_text(issuer.group(1))
    if report.isin is None:
        isin = re.search(r"ISIN:\s*([A-Z0-9]{12})", text)
        if isin:
            report.isin = isin.group(1)


def _append_summary_rows(report: ParsedFreedomBankReport, table: Sequence[Sequence[Any]]) -> None:
    if not table:
        return
    for row in table:
        if len(row) < 4:
            continue
        label = _clean_text(row[0])
        if not label:
            continue
        if label.startswith("Доступно") or label in {"Покупка", "Продажа", "Прибыль"} or label.startswith("Дарение"):
            report.summary_rows.append(
                {
                    "label": label,
                    "quantity": _decimal(row[1]),
                    "amount_usd": _decimal(row[2]),
                    "amount_kzt": _decimal(row[3]),
                    "source_report": str(report.path),
                }
            )


def _trade_header_index(table: Sequence[Sequence[Any]]) -> int | None:
    for idx, row in enumerate(table):
        values = [_clean_text(value) for value in row]
        if len(values) >= 10 and values[0] == "Номер сделки" and values[2] == "Операция" and values[5] == "Количество":
            return idx
    return None


def _parse_trade_table_row(
    raw_row: Sequence[Any],
    *,
    source_report: str,
    source_page: int,
    source_row: int,
    pending: dict[str, str | None] | None = None,
) -> FreedomBankRawRow | None:
    values = [_clean_text(value) for value in raw_row[:10]]
    if len(values) < 10 or not any(values):
        return None
    if values[0] == "Номер сделки":
        return None
    operation = _operation(values[2])
    if operation is None:
        _remember_partial_row(values, pending)
        return None
    date_text = values[1]
    if not _has_full_date(date_text) and pending and pending.get("date"):
        date_text = f"{pending['date']} {date_text}"
    date_time = _parse_datetime(date_text)
    if date_time is None:
        _remember_partial_row(values, pending)
        return None
    price_usd = _decimal(values[3])
    if price_usd == 0 and pending and pending.get("price_usd"):
        price_usd = _decimal(pending.get("price_usd"))
    broker_trade_id = values[0] or None
    if broker_trade_id and pending and pending.get("trade_id") and broker_trade_id in {"1", "-1"}:
        broker_trade_id = f"{pending['trade_id']}{broker_trade_id}"
    if pending is not None:
        pending.clear()
    return FreedomBankRawRow(
        source_report=source_report,
        source_page=source_page,
        source_row=source_row,
        broker_trade_id=broker_trade_id,
        date_time=date_time,
        operation=operation,
        price_usd=price_usd,
        price_kzt=_decimal(values[4]),
        quantity=abs(_decimal(values[5])),
        amount_usd=abs(_decimal(values[6])),
        amount_kzt=abs(_decimal(values[7])),
        realized_pl_kzt=_decimal(values[8]),
        details=values[9] or None,
    )


def _remember_partial_row(values: Sequence[str], pending: dict[str, str | None] | None) -> None:
    if pending is None:
        return
    if values and values[0].startswith("B1"):
        pending["trade_id"] = values[0]
    if len(values) > 1:
        parsed_date = re.search(r"\d{2}\.\d{2}\.\d{4}", values[1])
        if parsed_date:
            pending["date"] = parsed_date.group(0)
    if len(values) > 3 and _decimal(values[3]) != 0:
        pending["price_usd"] = values[3]


def _raw_row_as_record(row: FreedomBankRawRow) -> dict[str, Any]:
    return {
        "source_report": row.source_report,
        "source_page": row.source_page,
        "source_row": row.source_row,
        "broker_trade_id": row.broker_trade_id,
        "date_time": row.date_time.isoformat(sep=" "),
        "operation": row.operation,
        "price_usd": str(row.price_usd),
        "price_kzt": str(row.price_kzt),
        "quantity": str(row.quantity),
        "amount_usd": str(row.amount_usd),
        "amount_kzt": str(row.amount_kzt),
        "realized_pl_kzt": str(row.realized_pl_kzt),
        "details": row.details,
    }


def _build_trades(reports: Sequence[ParsedFreedomBankReport]) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for report in reports:
        isin = report.isin or FREEDOM_BANK_SYMBOL
        for row in report.rows:
            operation = _clean_text(row.get("operation"))
            if operation not in {"buy", "sell"}:
                continue
            signed_quantity = _decimal(row.get("quantity")) if operation == "buy" else -_decimal(row.get("quantity"))
            price = _decimal(row.get("price_usd"))
            amount = _decimal(row.get("amount_usd")) or abs(signed_quantity * price)
            trade_id = _trade_id(report, row)
            trades.append(
                {
                    "date_time": row.get("date_time"),
                    "trade_id": trade_id,
                    "trade_type": "trade",
                    "symbol": FREEDOM_BANK_SYMBOL,
                    "isin": isin,
                    "asset_type": report.security_type or "ETN",
                    "quantity": str(signed_quantity),
                    "calculation_quantity": str(signed_quantity),
                    "price": str(price),
                    "calculation_price": str(price),
                    "multiplier": "1",
                    "_calculation_multiplier": "1",
                    "amount": str(amount),
                    "commission": "0",
                    "amount_with_commission": str(amount),
                    "currency": report.account_currency or "USD",
                    "exchange": FREEDOM_BANK_EXCHANGE,
                    "country": _country_from_isin(isin),
                    "source_report": row.get("source_report"),
                    "_instrument_identity_key": isin,
                    "_broker_realized_pl": str(_decimal(row.get("realized_pl_kzt"))),
                    "_broker_realized_pl_currency": "KZT",
                    "_freedom_bank_amount_kzt": str(_decimal(row.get("amount_kzt"))),
                    "_freedom_bank_price_kzt": str(_decimal(row.get("price_kzt"))),
                }
            )
    return trades


def _build_transfers(reports: Sequence[ParsedFreedomBankReport]) -> list[dict[str, Any]]:
    transfers: list[dict[str, Any]] = []
    for report in reports:
        isin = report.isin or FREEDOM_BANK_SYMBOL
        for row in report.rows:
            operation = _clean_text(row.get("operation"))
            if operation not in {"gift_in", "gift_out"}:
                continue
            raw_quantity = _decimal(row.get("quantity")) if operation == "gift_in" else -_decimal(row.get("quantity"))
            transfers.append(
                {
                    "date": _date_part(row.get("date_time")),
                    "transfer_type": "security",
                    "direction": "in" if raw_quantity > 0 else "out",
                    "asset_type": report.security_type or "ETN",
                    "symbol": FREEDOM_BANK_SYMBOL,
                    "isin": isin,
                    "currency": report.account_currency or "USD",
                    "quantity": str(abs(raw_quantity)),
                    "price": str(_decimal(row.get("price_usd"))),
                    "enter_date": None,
                    "amount": None,
                    "broker_comment": row.get("details") or operation,
                    "counterparty": None,
                    "source_report": row.get("source_report"),
                    "country": _country_from_isin(isin),
                    "_raw_quantity": str(raw_quantity),
                    "_transfer_id": _trade_id(report, row),
                    "_instrument_identity_key": isin,
                    "_multiplier": "1",
                    "_transfer_cost_basis_status": "matched" if raw_quantity > 0 and _decimal(row.get("price_usd")) else "pending_transfer_in_fifo_source",
                }
            )
    return transfers


def _build_summary_position_adjustments(reports: Sequence[ParsedFreedomBankReport]) -> list[dict[str, Any]]:
    adjustments: list[dict[str, Any]] = []
    previous_raw_quantity = Decimal("0")
    for report in sorted(reports, key=lambda item: item.period_end or date.max):
        raw_quantity = _summary_quantity(report, "Доступно")
        if raw_quantity is None:
            continue
        movement_quantity = _report_security_movement(report)
        expected_quantity = previous_raw_quantity + movement_quantity
        residual_quantity = raw_quantity - expected_quantity
        if residual_quantity:
            adjustments.append(_summary_position_adjustment(report, residual_quantity))
        previous_raw_quantity = raw_quantity
    return adjustments


def _freedom_bank_transfer_resolver(transfers: Sequence[Mapping[str, Any]]):
    lots_by_key: dict[tuple[str | None, str | None, str | None, str | None, Decimal], list[TransferInFifoLot]] = defaultdict(list)
    for transfer in transfers:
        if transfer.get("transfer_type") != "security" or _clean_text(transfer.get("direction")).lower() != "in":
            continue
        quantity = abs(_decimal(transfer.get("_raw_quantity") or transfer.get("quantity")))
        if not quantity:
            continue
        transfer_dt = _parse_datetime(transfer.get("date"))
        lots_by_key[
            (
                transfer_dt.date().isoformat() if transfer_dt else None,
                _clean_text(transfer.get("symbol")) or None,
                _clean_text(transfer.get("isin")) or None,
                _clean_text(transfer.get("source_report")) or None,
                quantity,
            )
        ].append(
            TransferInFifoLot(
                quantity=quantity,
                price=_decimal(transfer.get("price")),
                enter_date=_parse_datetime(transfer.get("enter_date")) or transfer_dt,
                source_broker=BROKER_CODE,
                source_file=_clean_text(transfer.get("source_report")) or None,
            )
        )

    def resolve(request: TransferInRequest) -> Sequence[TransferInFifoLot] | None:
        key = (
            request.transfer_date.isoformat() if request.transfer_date else None,
            request.symbol,
            request.isin,
            request.source_report,
            request.quantity,
        )
        lots = lots_by_key.get(key)
        if not lots:
            return None
        return (lots.pop(0),)

    return resolve


def _summary_position_adjustment(report: ParsedFreedomBankReport, quantity: Decimal) -> dict[str, Any]:
    isin = report.isin or FREEDOM_BANK_SYMBOL
    direction = "in" if quantity > 0 else "out"
    adjustment_date = report.period_start if direction == "in" else report.period_end
    price = _summary_adjustment_price(report) if direction == "in" else Decimal("0")
    absolute_quantity = abs(quantity)
    return {
        "date": adjustment_date.isoformat() if adjustment_date else None,
        "transfer_type": "security",
        "direction": direction,
        "asset_type": report.security_type or "ETN",
        "symbol": FREEDOM_BANK_SYMBOL,
        "isin": isin,
        "currency": report.account_currency or "USD",
        "quantity": str(absolute_quantity),
        "price": str(price),
        "enter_date": adjustment_date.isoformat() if adjustment_date and direction == "in" else None,
        "amount": None,
        "broker_comment": "Freedom Bank summary position reconciliation adjustment",
        "counterparty": None,
        "source_report": str(report.path),
        "country": _country_from_isin(isin),
        "_raw_quantity": str(quantity),
        "_transfer_id": f"{report.path.name}:summary-position-adjustment:{report.period_end or 'unknown'}:{quantity}",
        "_instrument_identity_key": isin,
        "_multiplier": "1",
        "_transfer_cost_basis_status": "matched" if direction == "in" and price else "summary_position_reconciliation",
    }


def _report_security_movement(report: ParsedFreedomBankReport) -> Decimal:
    movement = Decimal("0")
    for row in report.rows:
        operation = _clean_text(row.get("operation"))
        quantity = _decimal(row.get("quantity"))
        if operation in {"buy", "gift_in"}:
            movement += quantity
        elif operation in {"sell", "gift_out"}:
            movement -= quantity
    return movement


def _summary_adjustment_price(report: ParsedFreedomBankReport) -> Decimal:
    available_quantity = _summary_quantity(report, "Доступно") or Decimal("0")
    available_amount = _summary_amount_usd(report, "Доступно")
    if available_quantity and available_amount:
        return abs(available_amount / available_quantity)
    for row in sorted(report.rows, key=lambda item: _parse_datetime(item.get("date_time")) or datetime.min, reverse=True):
        price = _decimal(row.get("price_usd"))
        if price:
            return price
    return Decimal("0")


def _summary_quantity(report: ParsedFreedomBankReport, label_prefix: str) -> Decimal | None:
    row = _summary_row(report, label_prefix)
    return _decimal(row.get("quantity")) if row else None


def _summary_amount_usd(report: ParsedFreedomBankReport, label_prefix: str) -> Decimal | None:
    row = _summary_row(report, label_prefix)
    return _decimal(row.get("amount_usd")) if row else None


def _summary_row(report: ParsedFreedomBankReport, label_prefix: str) -> Mapping[str, Any] | None:
    for row in report.summary_rows:
        if _clean_text(row.get("label")).startswith(label_prefix):
            return row
    return None


def _instrument_record(reports: Sequence[ParsedFreedomBankReport], account_id: str) -> dict[str, Any] | None:
    report = next((item for item in reports if item.isin), None)
    if report is None:
        return None
    return {
        "symbol": FREEDOM_BANK_SYMBOL,
        "description": report.issuer or FREEDOM_BANK_DESCRIPTION,
        "conid": None,
        "security_id": report.isin,
        "underlying": None,
        "listing_exchange": FREEDOM_BANK_EXCHANGE,
        "multiplier": "1",
        "type": report.security_type or "ETN",
        "code": None,
        "year": _year_for_report(report),
        "expiry": None,
        "delivery_month": None,
        "strike": None,
        "issuer": report.issuer or FREEDOM_BANK_DESCRIPTION,
        "maturity": None,
        "cusip": None,
        "country": _country_from_isin(report.isin),
        "isin": report.isin,
        "figi": None,
        "issuer_country": _country_from_isin(report.isin),
        "offshore_flag": False,
        "issuer_outside_kz_flag": False,
        "preferential_tax_flag": False,
        "source_broker": BROKER_CODE,
        "source_account": account_id,
        "source_report": str(report.path),
        "as_of_date": report.period_end.isoformat() if report.period_end else None,
    }


def _populate_raw_totals(
    totals: RawReportTotals,
    reports: Sequence[ParsedFreedomBankReport],
    trades: Sequence[Mapping[str, Any]],
) -> None:
    gross_trades = Decimal("0")
    for trade in trades:
        trade_dt = _parse_datetime(trade.get("date_time"))
        year = trade_dt.year if trade_dt else None
        currency = _clean_text(trade.get("currency")) or "USD"
        instrument_key = _clean_text(trade.get("isin") or trade.get("symbol"))
        amount = _decimal(trade.get("amount"))
        gross_trades += amount
        key = _dimension_key(
            metric=ReconciliationMetric.TRADE_GROSS_AMOUNT_BY_INSTRUMENT.value,
            year=year,
            currency=currency,
            instrument_key=instrument_key,
        )
        totals.totals_by_metric_currency[key] = totals.totals_by_metric_currency.get(key, Decimal("0")) + amount
    totals.scalar_totals.update(
        {
            ReconciliationMetric.TOTAL_TRADES_GROSS_AMOUNT.value: gross_trades,
            ReconciliationMetric.TOTAL_COMMISSIONS.value: Decimal("0"),
        }
    )

    for report in reports:
        year = _year_for_report(report)
        if year is None:
            continue
        isin = report.isin or FREEDOM_BANK_SYMBOL
        for row in report.summary_rows:
            label = _clean_text(row.get("label"))
            if label.startswith("Доступно"):
                key = _dimension_key(year=year, instrument_key=isin)
                totals.positions_by_key[key] = _decimal(row.get("quantity"))


def _operation(value: Any) -> str | None:
    text = _clean_text(value)
    mapping = {
        "Покупка": "buy",
        "Продажа": "sell",
        "Дарение передача": "gift_out",
        "Дарение получение": "gift_in",
    }
    return mapping.get(text)


def _trade_id(report: ParsedFreedomBankReport, row: Mapping[str, Any]) -> str:
    broker_trade_id = _clean_text(row.get("broker_trade_id"))
    page = row.get("source_page")
    source_row = row.get("source_row")
    if broker_trade_id and broker_trade_id not in {"1", "-1"}:
        return f"{report.path.name}:{broker_trade_id}"
    return f"{report.path.name}:page:{page}:row:{source_row}"


def _parse_datetime(value: Any) -> datetime | None:
    text = _clean_text(value).replace("\n", " ")
    if not text:
        return None
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    parsed = _parse_datetime(value)
    return parsed.date() if parsed else None


def _date_part(value: Any) -> str | None:
    parsed = _parse_datetime(value)
    return parsed.date().isoformat() if parsed else None


def _year_for_report(report: ParsedFreedomBankReport) -> int | None:
    return report.period_end.year if report.period_end else None


def _has_full_date(value: str) -> bool:
    return re.search(r"\d{2}\.\d{2}\.\d{4}", value) is not None


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    text = _clean_text(value)
    if text in {"", "-"}:
        return Decimal("0")
    text = text.replace("$", "").replace("₸", "").replace(",", "").replace(" ", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return Decimal(match.group(0)) if match else Decimal("0")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _country_from_isin(isin: str | None) -> str | None:
    return isin[:2] if isin and len(isin) >= 2 else None


def _dimension_key(
    *,
    metric: str | None = None,
    year: int | None = None,
    currency: str | None = None,
    instrument_key: str | None = None,
) -> str:
    return "|".join("" if value is None else str(value) for value in (metric, year, currency, instrument_key))


def _pdfplumber() -> Any:
    try:
        import pdfplumber  # type: ignore
    except Exception as exc:
        raise RuntimeError("Freedom Bank PDF parsing requires pdfplumber.") from exc
    return pdfplumber
