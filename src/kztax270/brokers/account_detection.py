"""Structured brokerage-account detection shared by local and web workflows."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DetectedReportMetadata:
    account_id: str | None
    period_end: date | None


@dataclass(frozen=True, slots=True)
class BrokerReportSpec:
    code: str
    display_name: str
    extensions: frozenset[str]
    report_detector: Callable[[Path], DetectedReportMetadata]
    detects_account_id: bool

    @property
    def account_id_optional(self) -> bool:
        return self.detects_account_id


class AccountDetectionError(ValueError):
    """A report lacks a reliable account ID or reports disagree."""


def _ib_report(path: Path) -> DetectedReportMetadata:
    from kztax270.brokers.ib import parse_ib_csv_report

    parsed = parse_ib_csv_report(path)
    return DetectedReportMetadata(parsed.account_id, parsed.period_end)


def _exante_report(path: Path) -> DetectedReportMetadata:
    from kztax270.brokers.exante import parse_exante_csv_report

    parsed = parse_exante_csv_report(path)
    return DetectedReportMetadata(parsed.account_id, parsed.period_end)


def _tsifra_report(path: Path) -> DetectedReportMetadata:
    from kztax270.brokers.tsifra import parse_tsifra_xml_report

    parsed = parse_tsifra_xml_report(path)
    return DetectedReportMetadata(parsed.account_id, parsed.period_end)


def _tabys_report(path: Path) -> DetectedReportMetadata:
    from kztax270.brokers.tabys import parse_tabys_pdf

    parsed = parse_tabys_pdf(path)
    return DetectedReportMetadata(parsed.account_id, parsed.period_end)


def _freedom_bank_report(path: Path) -> DetectedReportMetadata:
    from kztax270.brokers.freedom_bank import parse_freedom_bank_pdf

    parsed = parse_freedom_bank_pdf(path)
    return DetectedReportMetadata(parsed.brokerage_account or parsed.iin, parsed.period_end)


def _alatay_report(path: Path) -> DetectedReportMetadata:
    from kztax270.brokers.alatay import parse_alatay_report

    parsed = parse_alatay_report(path)
    return DetectedReportMetadata(parsed.account_id, parsed.period_end)


def _freedom_report(path: Path) -> DetectedReportMetadata:
    from kztax270.brokers.freedom import parse_freedom_report

    parsed = parse_freedom_report(path)
    return DetectedReportMetadata(None, parsed.period_end)


BROKER_REPORT_SPECS: dict[str, BrokerReportSpec] = {
    "alatay": BrokerReportSpec(
        "alatay", "Alatau City Invest", frozenset({".csv", ".xlsx"}), _alatay_report, True
    ),
    "exante": BrokerReportSpec(
        "exante", "Exante", frozenset({".csv"}), _exante_report, True
    ),
    "freedom": BrokerReportSpec(
        "freedom", "Freedom Broker", frozenset({".xlsx"}), _freedom_report, False
    ),
    "freedom_bank": BrokerReportSpec(
        "freedom_bank",
        "Freedom Bank",
        frozenset({".pdf"}),
        _freedom_bank_report,
        True,
    ),
    "ib": BrokerReportSpec(
        "ib", "Interactive Brokers", frozenset({".csv"}), _ib_report, True
    ),
    "tabys": BrokerReportSpec(
        "tabys", "Tabys", frozenset({".pdf"}), _tabys_report, True
    ),
    "tsifra": BrokerReportSpec(
        "tsifra", "Цифра Брокер", frozenset({".xml"}), _tsifra_report, True
    ),
}


def detect_report_account_id(broker: str, report_path: Path) -> str | None:
    """Return one structured account ID, or ``None`` when the broker cannot expose it."""

    spec = BROKER_REPORT_SPECS[broker]
    if not spec.detects_account_id:
        return None
    value = detect_report_metadata(broker, report_path).account_id
    return value.strip() if value and value.strip() else None


def detect_report_period_end(broker: str, report_path: Path) -> date | None:
    """Return the report period end parsed from structured broker metadata."""

    return detect_report_metadata(broker, report_path).period_end


def detect_report_metadata(broker: str, report_path: Path) -> DetectedReportMetadata:
    """Parse the account identity and report end date in one broker-specific pass."""

    return BROKER_REPORT_SPECS[broker].report_detector(Path(report_path))


def detect_account_id(broker: str, report_paths: Sequence[Path]) -> str | None:
    """Return one account ID when every report identifies the same account."""

    spec = BROKER_REPORT_SPECS[broker]
    if not spec.detects_account_id:
        return None
    detected = [detect_report_account_id(broker, path) for path in report_paths]
    if not detected or any(value is None for value in detected):
        return None
    unique = set(detected)
    if len(unique) != 1:
        raise AccountDetectionError("Reports contain different account IDs")
    return detected[0]
