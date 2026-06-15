"""Raw report discovery helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .base import BrokerReport

DEFAULT_REPORT_EXTENSIONS = {".csv", ".xlsx", ".xls", ".xml", ".pdf", ".json"}


@dataclass(frozen=True, slots=True)
class DiscoveryRule:
    broker: str
    account_id: str
    extensions: frozenset[str] = frozenset(DEFAULT_REPORT_EXTENSIONS)
    filename_must_contain_account: bool = True


def discover_raw_reports(raw_root: Path, rule: DiscoveryRule) -> list[BrokerReport]:
    broker_root = raw_root / rule.broker
    if not broker_root.exists():
        return []

    reports: list[BrokerReport] = []
    for path in sorted(p for p in broker_root.rglob("*") if p.is_file()):
        if path.suffix.lower() not in rule.extensions:
            continue
        if is_transfer_out_source_file(path):
            continue
        if rule.filename_must_contain_account and rule.account_id.lower() not in path.name.lower():
            continue
        reports.append(BrokerReport(broker=rule.broker, account_id=rule.account_id, path=path))
    return reports


def is_transfer_out_source_file(path: Path) -> bool:
    normalized = path.stem.lower().replace("_", " ").replace("-", " ")
    parts = normalized.split()
    return "transfer" in parts and "out" in parts


def discover_many(raw_root: Path, rules: Iterable[DiscoveryRule]) -> list[BrokerReport]:
    reports: list[BrokerReport] = []
    for rule in rules:
        reports.extend(discover_raw_reports(raw_root, rule))
    return sorted(reports, key=lambda report: str(report.path).lower())
