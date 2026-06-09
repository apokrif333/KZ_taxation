"""Raw-vs-canonical reconciliation engine."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from kztax270.canonical.schema import CanonicalDataset

from .models import ReconciliationItem, ReconciliationMetric, ReconciliationSeverity

ERROR_METRICS = {
    ReconciliationMetric.ENDING_CASH,
    ReconciliationMetric.ENDING_POSITION_QUANTITY,
    ReconciliationMetric.TOTAL_TRADES_GROSS_AMOUNT,
    ReconciliationMetric.TRADE_GROSS_AMOUNT_BY_INSTRUMENT,
    ReconciliationMetric.PNL_AFTER_ALL_COMMISSIONS_BY_INSTRUMENT,
    ReconciliationMetric.UNPROCESSED_ROWS,
}


@dataclass(frozen=True, slots=True)
class ReconciliationRule:
    metric: ReconciliationMetric
    tolerance: Decimal = Decimal("0.01")
    severity_on_mismatch: ReconciliationSeverity = ReconciliationSeverity.WARNING


DEFAULT_RULES: dict[ReconciliationMetric, ReconciliationRule] = {
    metric: ReconciliationRule(
        metric=metric,
        tolerance=Decimal("0.0001") if metric == ReconciliationMetric.ENDING_POSITION_QUANTITY else Decimal("0.01"),
        severity_on_mismatch=ReconciliationSeverity.ERROR if metric in ERROR_METRICS else ReconciliationSeverity.WARNING,
    )
    for metric in ReconciliationMetric
}


def _decimal(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or "0"))


class ReconciliationEngine:
    def __init__(self, rules: Mapping[ReconciliationMetric, ReconciliationRule] | None = None) -> None:
        self.rules = dict(rules or DEFAULT_RULES)

    def compare_scalar(
        self,
        metric: ReconciliationMetric,
        broker_value: object,
        canonical_value: object,
        *,
        currency: str | None = None,
        instrument_key: str | None = None,
        year: int | None = None,
        source: str | None = None,
        details: str | None = None,
    ) -> ReconciliationItem:
        rule = self.rules.get(metric, ReconciliationRule(metric=metric))
        broker_decimal = _decimal(broker_value)
        canonical_decimal = _decimal(canonical_value)
        difference = canonical_decimal - broker_decimal
        severity = (
            ReconciliationSeverity.INFO
            if abs(difference) <= rule.tolerance
            else rule.severity_on_mismatch
        )
        return ReconciliationItem(
            metric=metric,
            severity=severity,
            broker_value=broker_decimal,
            canonical_value=canonical_decimal,
            difference=difference,
            tolerance=rule.tolerance,
            currency=currency,
            instrument_key=instrument_key,
            year=year,
            source=source,
            details=details,
        )

    def reconcile_totals(
        self,
        broker_totals: Mapping[ReconciliationMetric | str, object],
        canonical_totals: Mapping[ReconciliationMetric | str, object],
    ) -> list[ReconciliationItem]:
        items: list[ReconciliationItem] = []
        keys = sorted(set(broker_totals) | set(canonical_totals), key=str)
        for key in keys:
            metric = key if isinstance(key, ReconciliationMetric) else ReconciliationMetric(str(key))
            items.append(self.compare_scalar(metric, broker_totals.get(key, 0), canonical_totals.get(key, 0)))
        return items

    def reconcile_positions(
        self,
        broker_positions: Mapping[str, object],
        canonical_positions: Mapping[str, object],
    ) -> list[ReconciliationItem]:
        items: list[ReconciliationItem] = []
        for instrument_key in sorted(set(broker_positions) | set(canonical_positions)):
            items.append(
                self.compare_scalar(
                    ReconciliationMetric.ENDING_POSITION_QUANTITY,
                    broker_positions.get(instrument_key, 0),
                    canonical_positions.get(instrument_key, 0),
                    instrument_key=instrument_key,
                )
            )
        return items

    def reconcile_dataset(self, dataset: CanonicalDataset) -> list[ReconciliationItem]:
        """Compare parser-extracted raw totals with canonical workbook tables."""

        items: list[ReconciliationItem] = []
        canonical_scalars = _canonical_scalar_totals(dataset)
        for metric in (
            ReconciliationMetric.TOTAL_TRADES_GROSS_AMOUNT,
            ReconciliationMetric.TOTAL_COMMISSIONS,
            ReconciliationMetric.TOTAL_DIVIDENDS_GROSS,
            ReconciliationMetric.TOTAL_DIVIDENDS_NET,
            ReconciliationMetric.TOTAL_DIVIDENDS_TAX,
            ReconciliationMetric.TOTAL_INTEREST,
            ReconciliationMetric.TOTAL_COUPONS,
            ReconciliationMetric.TOTAL_DEPOSITS_WITHDRAWALS_TRANSFERS,
            ReconciliationMetric.REALIZED_PL,
        ):
            raw_available = metric.value in dataset.raw_totals.scalar_totals
            if not raw_available:
                continue
            items.append(
                self.compare_scalar(
                    metric,
                    dataset.raw_totals.scalar_totals.get(metric.value, 0),
                    canonical_scalars.get(metric, 0),
                )
            )

        for metric, canonical_by_key, details in (
            (
                ReconciliationMetric.TRADE_GROSS_AMOUNT_BY_INSTRUMENT,
                _canonical_trade_amount_by_instrument(dataset),
                "Traded turnover by year/currency/instrument.",
            ),
            (
                ReconciliationMetric.PNL_AFTER_ALL_COMMISSIONS_BY_INSTRUMENT,
                _canonical_fifo_pnl_after_all_commissions_by_instrument(dataset),
                "Realized P/L after all commissions by year/currency/instrument.",
            ),
        ):
            raw_by_key = {
                key: value
                for key, value in dataset.raw_totals.totals_by_metric_currency.items()
                if key.startswith(metric.value)
            }
            for key in sorted(set(raw_by_key) | set(canonical_by_key)):
                year, currency, instrument_key = _parse_dimension_key(key)
                canonical_key = key if key in canonical_by_key else _dimension_key(
                    metric=metric.value,
                    year=year,
                    currency=currency,
                    instrument_key=instrument_key,
                )
                raw_key = key if key in raw_by_key else _dimension_key(
                    metric=metric.value,
                    year=year,
                    currency=currency,
                    instrument_key=instrument_key,
                )
                items.append(
                    self.compare_scalar(
                        metric,
                        raw_by_key.get(raw_key, 0),
                        canonical_by_key.get(canonical_key, 0),
                        year=year,
                        currency=currency,
                        instrument_key=instrument_key,
                        details=details,
                    )
                )

        canonical_cash = _canonical_cash_by_key(dataset)
        for key in sorted(set(dataset.raw_totals.cash_by_currency) | set(canonical_cash)):
            year, currency, _ = _parse_dimension_key(key)
            items.append(
                self.compare_scalar(
                    ReconciliationMetric.ENDING_CASH,
                    dataset.raw_totals.cash_by_currency.get(key, 0),
                    canonical_cash.get(key, 0),
                    year=year,
                    currency=currency,
                    details="Ending cash by year/currency.",
                )
            )

        canonical_positions = _canonical_positions_by_key(dataset)
        for key in sorted(set(dataset.raw_totals.positions_by_key) | set(canonical_positions)):
            year, currency, instrument_key = _parse_dimension_key(key)
            items.append(
                self.compare_scalar(
                    ReconciliationMetric.ENDING_POSITION_QUANTITY,
                    dataset.raw_totals.positions_by_key.get(key, 0),
                    canonical_positions.get(key, 0),
                    year=year,
                    currency=currency,
                    instrument_key=instrument_key,
                    details="Ending position quantity by year/currency/instrument.",
                )
            )

        canonical_transfer_by_currency = _canonical_transfers_by_currency(dataset)
        transfer_metric = ReconciliationMetric.TOTAL_DEPOSITS_WITHDRAWALS_TRANSFERS
        raw_transfer_by_currency = {
            key: value
            for key, value in dataset.raw_totals.totals_by_metric_currency.items()
            if key.startswith(transfer_metric.value)
        }
        for key in sorted(set(raw_transfer_by_currency) | set(canonical_transfer_by_currency)):
            _, currency, _ = _parse_dimension_key(key)
            canonical_key = key if key in canonical_transfer_by_currency else _dimension_key(metric=transfer_metric.value, currency=currency)
            raw_key = key if key in raw_transfer_by_currency else _dimension_key(metric=transfer_metric.value, currency=currency)
            items.append(
                self.compare_scalar(
                    transfer_metric,
                    raw_transfer_by_currency.get(raw_key, 0),
                    canonical_transfer_by_currency.get(canonical_key, 0),
                    currency=currency,
                    details="Deposits/withdrawals/transfers by native currency.",
                )
            )

        for row in dataset.tables.get("Unprocessed", []):
            items.append(
                self.compare_scalar(
                    ReconciliationMetric.UNPROCESSED_ROWS,
                    0,
                    1,
                    year=_year_from_record(row),
                    currency=_str_or_none(row.get("currency")),
                    instrument_key=_str_or_none(row.get("symbol") or row.get("isin")),
                    source=_str_or_none(row.get("trade_id") or row.get("source_report")),
                    details=f"{row.get('reason')}: {row.get('details')} See Unprocessed sheet.",
                )
            )
        return items


def _canonical_scalar_totals(dataset: CanonicalDataset) -> dict[ReconciliationMetric, Decimal]:
    dividends_gross = _sum_table(dataset, "Dividends", "gross_amount")
    dividends_tax = _sum_table(dataset, "Dividends", "withholding_tax")
    broker_realized_pl = (
        _sum_table(dataset, "_BrokerTradeRealizedPL", "realized_pl")
        + _sum_table(dataset, "CorporateActions", "realized_pl")
        + _sum_years_results(dataset, "Yearly FX Trades", "pnl")
    )
    return {
        ReconciliationMetric.TOTAL_TRADES_GROSS_AMOUNT: _sum_table(dataset, "Trades", "amount"),
        ReconciliationMetric.TOTAL_COMMISSIONS: _sum_table(dataset, "Trades", "commission"),
        ReconciliationMetric.TOTAL_DIVIDENDS_GROSS: dividends_gross,
        ReconciliationMetric.TOTAL_DIVIDENDS_TAX: dividends_tax,
        ReconciliationMetric.TOTAL_DIVIDENDS_NET: dividends_gross + dividends_tax,
        ReconciliationMetric.TOTAL_INTEREST: _sum_table(dataset, "Interest", "gross_amount"),
        ReconciliationMetric.TOTAL_COUPONS: _sum_table(dataset, "Coupons", "gross_amount"),
        ReconciliationMetric.TOTAL_DEPOSITS_WITHDRAWALS_TRANSFERS: _sum_table(dataset, "Transfers", "amount"),
        ReconciliationMetric.REALIZED_PL: broker_realized_pl,
    }


def _sum_table(dataset: CanonicalDataset, sheet_name: str, column: str) -> Decimal:
    total = Decimal("0")
    for record in dataset.tables.get(sheet_name, []):
        total += _decimal_or_zero(record.get(column))
    return total


def _sum_years_results(dataset: CanonicalDataset, table_name: str, column: str) -> Decimal:
    total = Decimal("0")
    for record in dataset.tables.get("Years_Results", []):
        if record.get("table") == table_name:
            total += _decimal_or_zero(record.get(column))
    return total


def _canonical_cash_by_key(dataset: CanonicalDataset) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for record in dataset.tables.get("CashBalances", []):
        key = _dimension_key(year=_int_or_none(record.get("year")), currency=_str_or_none(record.get("currency")))
        result[key] = result.get(key, Decimal("0")) + _decimal_or_zero(record.get("ending_cash"))
    return result


def _canonical_trade_amount_by_instrument(dataset: CanonicalDataset) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    metric = ReconciliationMetric.TRADE_GROSS_AMOUNT_BY_INSTRUMENT.value
    for record in dataset.tables.get("Trades", []):
        key = _dimension_key(
            metric=metric,
            year=_year_from_record(record),
            currency=_str_or_none(record.get("currency")),
            instrument_key=_instrument_key(record),
        )
        result[key] = result.get(key, Decimal("0")) + _decimal_or_zero(record.get("amount"))
    return result


def _canonical_fifo_pnl_after_all_commissions_by_instrument(dataset: CanonicalDataset) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    metric = ReconciliationMetric.PNL_AFTER_ALL_COMMISSIONS_BY_INSTRUMENT.value
    for record in dataset.tables.get("Fifo", []):
        if str(record.get("source_trade_id") or "").startswith("CA:"):
            continue
        pnl_after_all_commissions = record.get("pnl_after_all_commissions")
        if pnl_after_all_commissions in (None, ""):
            continue
        key = _dimension_key(
            metric=metric,
            year=_year_from_record(record),
            currency=_str_or_none(record.get("currency")),
            instrument_key=_instrument_key(record),
        )
        result[key] = result.get(key, Decimal("0")) + _decimal_or_zero(pnl_after_all_commissions)
    return result


def _canonical_positions_by_key(dataset: CanonicalDataset) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for record in dataset.tables.get("Positions", []):
        instrument_key = _str_or_none(record.get("symbol") or record.get("isin"))
        key = _dimension_key(
            year=_int_or_none(record.get("year")),
            currency=_str_or_none(record.get("currency")),
            instrument_key=instrument_key,
        )
        result[key] = result.get(key, Decimal("0")) + _decimal_or_zero(record.get("quantity"))
    return result


def _instrument_key(record: Mapping[str, Any]) -> str | None:
    return _str_or_none(record.get("isin") or record.get("symbol"))


def _canonical_transfers_by_currency(dataset: CanonicalDataset) -> dict[str, Decimal]:
    metric = ReconciliationMetric.TOTAL_DEPOSITS_WITHDRAWALS_TRANSFERS.value
    result: dict[str, Decimal] = {}
    for record in dataset.tables.get("Transfers", []):
        currency = _str_or_none(record.get("currency"))
        key = _dimension_key(metric=metric, currency=currency)
        result[key] = result.get(key, Decimal("0")) + _decimal_or_zero(record.get("amount"))
    return result


def _decimal_or_zero(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _str_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _year_from_record(record: Mapping[str, Any]) -> int | None:
    explicit_year = record.get("year")
    if explicit_year not in (None, ""):
        return int(explicit_year)
    for key in ("date_time", "date", "exit_date"):
        value = record.get(key)
        if value in (None, ""):
            continue
        text = str(value)
        if len(text) >= 4 and text[:4].isdigit():
            return int(text[:4])
    return None


def _dimension_key(
    *,
    metric: str | None = None,
    year: int | None = None,
    currency: str | None = None,
    instrument_key: str | None = None,
) -> str:
    return "|".join("" if value is None else str(value) for value in (metric, year, currency, instrument_key))


def _parse_dimension_key(key: str) -> tuple[int | None, str | None, str | None]:
    parts = key.split("|")
    if len(parts) == 4:
        _, year_text, currency, instrument_key = parts
    else:
        year_text = parts[1] if len(parts) > 1 else ""
        currency = parts[2] if len(parts) > 2 else ""
        instrument_key = parts[3] if len(parts) > 3 else ""
    year = int(year_text) if year_text else None
    return year, currency or None, instrument_key or None
