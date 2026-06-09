"""Canonical in-memory data structures.

The project uses table-like records at package boundaries because broker parsers
currently produce pandas DataFrames while tests and reference data can use plain
Python dictionaries. Heavy DataFrame libraries are intentionally kept out of
module import paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, MutableMapping, Sequence

Record = dict[str, Any]
Table = list[Record]
Tables = dict[str, Table]


@dataclass(frozen=True, slots=True)
class MoneyAmount:
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str | None = None
    description: str | None = None
    conid: str | None = None
    security_id: str | None = None
    underlying: str | None = None
    listing_exchange: str | None = None
    multiplier: Decimal | None = None
    type: str | None = None
    code: str | None = None
    year: int | None = None
    expiry: date | None = None
    delivery_month: str | None = None
    strike: Decimal | None = None
    issuer: str | None = None
    maturity: date | None = None
    cusip: str | None = None
    country: str | None = None
    isin: str | None = None
    figi: str | None = None
    issuer_country: str | None = None
    offshore_flag: bool | None = None
    issuer_outside_kz_flag: bool | None = None
    preferential_tax_flag: bool | None = None
    source_broker: str | None = None
    source_account: str | None = None
    source_report: str | None = None
    as_of_date: date | None = None


@dataclass(frozen=True, slots=True)
class AccountMetadata:
    broker: str
    account_id: str
    client_id: str | None = None
    base_currency: str = "KZT"
    ownership_ratio: Decimal = Decimal("1")


@dataclass(slots=True)
class RawReportTotals:
    """Totals extracted directly from broker reports for reconciliation."""

    scalar_totals: MutableMapping[str, Decimal] = field(default_factory=dict)
    cash_by_currency: MutableMapping[str, Decimal] = field(default_factory=dict)
    positions_by_key: MutableMapping[str, Decimal] = field(default_factory=dict)
    realized_pl_by_currency: MutableMapping[str, Decimal] = field(default_factory=dict)
    totals_by_metric_currency: MutableMapping[str, Decimal] = field(default_factory=dict)
    source_reports: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CanonicalDataset:
    """Normalized broker account data in the canonical workbook shape."""

    metadata: AccountMetadata
    tables: Tables = field(default_factory=dict)
    raw_totals: RawReportTotals = field(default_factory=RawReportTotals)
    warnings: list[str] = field(default_factory=list)

    def table(self, sheet_name: str) -> Table:
        return self.tables.setdefault(sheet_name, [])

    def add_records(self, sheet_name: str, records: Sequence[Mapping[str, Any]]) -> None:
        table = self.table(sheet_name)
        for record in records:
            table.append(dict(record))

    def table_totals(self) -> dict[str, Decimal]:
        """Return simple numeric totals used by the first reconciliation layer."""

        totals: dict[str, Decimal] = {}
        for sheet_name, records in self.tables.items():
            for record in records:
                for key, value in record.items():
                    if isinstance(value, bool):
                        continue
                    try:
                        amount = Decimal(str(value))
                    except Exception:
                        continue
                    totals[f"{sheet_name}.{key}"] = totals.get(f"{sheet_name}.{key}", Decimal("0")) + amount
        return totals

    @classmethod
    def empty(cls, broker: str, account_id: str) -> "CanonicalDataset":
        return cls(metadata=AccountMetadata(broker=broker, account_id=account_id))


@dataclass(frozen=True, slots=True)
class ProcessingContext:
    account: AccountMetadata
    tax_year: int | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
