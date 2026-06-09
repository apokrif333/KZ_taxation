"""Broker parser interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol, Sequence

from kztax270.canonical.schema import CanonicalDataset, RawReportTotals


@dataclass(frozen=True, slots=True)
class BrokerReport:
    broker: str
    account_id: str
    path: Path
    period_start: date | None = None
    period_end: date | None = None
    checksum: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParseResult:
    broker: str
    account_id: str
    reports: Sequence[BrokerReport]
    dataset: CanonicalDataset
    raw_totals: RawReportTotals = field(default_factory=RawReportTotals)


class BrokerAdapterError(RuntimeError):
    pass


class BrokerParser(Protocol):
    broker_code: str

    def discover_reports(self, raw_root: Path, account_id: str) -> list[BrokerReport]:
        """Find raw reports for one account."""

    def parse_reports(self, reports: Sequence[BrokerReport], account_id: str) -> ParseResult:
        """Parse broker reports and return canonical account data."""
