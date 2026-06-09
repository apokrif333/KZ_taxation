"""Reconciliation data model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any


class ReconciliationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ReconciliationMetric(StrEnum):
    TOTAL_TRADES_GROSS_AMOUNT = "total_trades_gross_amount"
    TOTAL_COMMISSIONS = "total_commissions"
    TOTAL_DIVIDENDS_GROSS = "total_dividends_gross"
    TOTAL_DIVIDENDS_NET = "total_dividends_net"
    TOTAL_DIVIDENDS_TAX = "total_dividends_tax"
    TOTAL_INTEREST = "total_interest"
    TOTAL_COUPONS = "total_coupons"
    TOTAL_DEPOSITS_WITHDRAWALS_TRANSFERS = "total_deposits_withdrawals_transfers"
    TRADE_GROSS_AMOUNT_BY_INSTRUMENT = "trade_gross_amount_by_instrument"
    PNL_AFTER_ALL_COMMISSIONS_BY_INSTRUMENT = "pnl_after_all_commissions_by_instrument"
    ENDING_CASH = "ending_cash"
    ENDING_POSITION_QUANTITY = "ending_position_quantity"
    REALIZED_PL = "realized_pl"
    UNPROCESSED_ROWS = "unprocessed_rows"


@dataclass(frozen=True, slots=True)
class ReconciliationItem:
    metric: ReconciliationMetric
    severity: ReconciliationSeverity
    broker_value: Decimal
    canonical_value: Decimal
    difference: Decimal
    tolerance: Decimal
    currency: str | None = None
    instrument_key: str | None = None
    year: int | None = None
    source: str | None = None
    details: str | None = None

    def as_record(self) -> dict[str, Any]:
        record = asdict(self)
        for key in ("metric", "severity"):
            record[key] = str(record[key])
        for key in ("broker_value", "canonical_value", "difference", "tolerance"):
            record[key] = str(record[key])
        return record
