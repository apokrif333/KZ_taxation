"""Broker upload metadata used by the API, including structured account detection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BrokerUploadSpec:
    code: str
    display_name: str
    extensions: frozenset[str]
    account_detector: Callable[[Path], str | None] | None

    @property
    def account_id_optional(self) -> bool:
        return self.account_detector is not None


class AccountDetectionError(ValueError):
    """The reports were parsed but do not identify one consistent account."""


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

    return parse_freedom_bank_pdf(path).brokerage_account


def _alatay_account(path: Path) -> str | None:
    from kztax270.brokers.alatay import parse_alatay_report

    return parse_alatay_report(path).account_id


# Only native parsers that consume explicitly supplied paths are exposed. The
# legacy adapters read repository data themselves and are not upload-safe.
BROKER_UPLOAD_SPECS: dict[str, BrokerUploadSpec] = {
    "alatay": BrokerUploadSpec(
        code="alatay",
        display_name="Alatau City Invest",
        extensions=frozenset({".csv", ".xlsx"}),
        account_detector=_alatay_account,
    ),
    "exante": BrokerUploadSpec(
        code="exante",
        display_name="Exante",
        extensions=frozenset({".csv"}),
        account_detector=_exante_account,
    ),
    "freedom": BrokerUploadSpec(
        code="freedom",
        display_name="Freedom Broker",
        extensions=frozenset({".xlsx"}),
        account_detector=None,
    ),
    "freedom_bank": BrokerUploadSpec(
        code="freedom_bank",
        display_name="Freedom Bank",
        extensions=frozenset({".pdf"}),
        account_detector=_freedom_bank_account,
    ),
    "ib": BrokerUploadSpec(
        code="ib",
        display_name="Interactive Brokers",
        extensions=frozenset({".csv"}),
        account_detector=_ib_account,
    ),
    "tabys": BrokerUploadSpec(
        code="tabys",
        display_name="Tabys",
        extensions=frozenset({".pdf"}),
        account_detector=_tabys_account,
    ),
    "tsifra": BrokerUploadSpec(
        code="tsifra",
        display_name="Цифра Брокер",
        extensions=frozenset({".xml"}),
        account_detector=_tsifra_account,
    ),
}


def detect_account_id(broker: str, report_paths: list[Path]) -> str | None:
    """Return one account ID when every report identifies the same account."""

    spec = BROKER_UPLOAD_SPECS[broker]
    if spec.account_detector is None:
        return None

    detected = [
        value.strip() if value and value.strip() else None
        for value in (spec.account_detector(path) for path in report_paths)
    ]
    if not detected or any(value is None for value in detected):
        return None
    unique = set(detected)
    if len(unique) != 1:
        raise AccountDetectionError("Uploaded reports contain different account IDs")
    return detected[0]
