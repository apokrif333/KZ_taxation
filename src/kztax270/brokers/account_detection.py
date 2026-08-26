"""Structured brokerage-account detection shared by local and web workflows."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BrokerReportSpec:
    code: str
    display_name: str
    extensions: frozenset[str]
    account_detector: Callable[[Path], str | None] | None

    @property
    def account_id_optional(self) -> bool:
        return self.account_detector is not None


class AccountDetectionError(ValueError):
    """A report lacks a reliable account ID or reports disagree."""


def _ib_account(path: Path) -> str | None:
    from kztax270.brokers.ib import parse_ib_csv_report

    return parse_ib_csv_report(path).account_id


def _exante_account(path: Path) -> str | None:
    from kztax270.brokers.exante import parse_exante_csv_report

    return parse_exante_csv_report(path).account_id


def _tsifra_account(path: Path) -> str | None:
    from kztax270.brokers.tsifra import parse_tsifra_xml_report

    return parse_tsifra_xml_report(path).account_id


def _tabys_account(path: Path) -> str | None:
    from kztax270.brokers.tabys import parse_tabys_pdf

    return parse_tabys_pdf(path).account_id


def _freedom_bank_account(path: Path) -> str | None:
    from kztax270.brokers.freedom_bank import parse_freedom_bank_pdf

    parsed = parse_freedom_bank_pdf(path)
    return parsed.brokerage_account or parsed.iin


def _alatay_account(path: Path) -> str | None:
    from kztax270.brokers.alatay import parse_alatay_report

    return parse_alatay_report(path).account_id


BROKER_REPORT_SPECS: dict[str, BrokerReportSpec] = {
    "alatay": BrokerReportSpec("alatay", "Alatau City Invest", frozenset({".csv", ".xlsx"}), _alatay_account),
    "exante": BrokerReportSpec("exante", "Exante", frozenset({".csv"}), _exante_account),
    "freedom": BrokerReportSpec("freedom", "Freedom Broker", frozenset({".xlsx"}), None),
    "freedom_bank": BrokerReportSpec(
        "freedom_bank", "Freedom Bank", frozenset({".pdf"}), _freedom_bank_account
    ),
    "ib": BrokerReportSpec("ib", "Interactive Brokers", frozenset({".csv"}), _ib_account),
    "tabys": BrokerReportSpec("tabys", "Tabys", frozenset({".pdf"}), _tabys_account),
    "tsifra": BrokerReportSpec("tsifra", "Цифра Брокер", frozenset({".xml"}), _tsifra_account),
}


def detect_report_account_id(broker: str, report_path: Path) -> str | None:
    """Return one structured account ID, or ``None`` when the broker cannot expose it."""

    spec = BROKER_REPORT_SPECS[broker]
    if spec.account_detector is None:
        return None
    value = spec.account_detector(Path(report_path))
    return value.strip() if value and value.strip() else None


def detect_account_id(broker: str, report_paths: Sequence[Path]) -> str | None:
    """Return one account ID when every report identifies the same account."""

    spec = BROKER_REPORT_SPECS[broker]
    if spec.account_detector is None:
        return None
    detected = [detect_report_account_id(broker, path) for path in report_paths]
    if not detected or any(value is None for value in detected):
        return None
    unique = set(detected)
    if len(unique) != 1:
        raise AccountDetectionError("Reports contain different account IDs")
    return detected[0]
