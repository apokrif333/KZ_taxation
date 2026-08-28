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
        if rule.filename_must_contain_account and not _filename_contains_account_id(path.name, rule.account_id):
            continue
        reports.append(BrokerReport(broker=rule.broker, account_id=rule.account_id, path=path))
    return reports


def _filename_contains_account_id(filename: str, account_id: str) -> bool:
    """Match a complete account ID instead of a substring of another ID."""

    normalized_filename = filename.casefold()
    normalized_account_id = account_id.casefold()
    start = 0
    while (index := normalized_filename.find(normalized_account_id, start)) >= 0:
        end = index + len(normalized_account_id)
        left_is_boundary = index == 0 or not normalized_filename[index - 1].isalnum()
        right_is_boundary = end == len(normalized_filename) or not normalized_filename[end].isalnum()
        if left_is_boundary and right_is_boundary:
            return True
        start = index + 1
    return False


def is_transfer_out_source_file(path: Path) -> bool:
    normalized = path.stem.lower().replace("_", " ").replace("-", " ")
    parts = normalized.split()
    return "transfer" in parts and "out" in parts


def discover_many(raw_root: Path, rules: Iterable[DiscoveryRule]) -> list[BrokerReport]:
    reports: list[BrokerReport] = []
    for rule in rules:
        reports.extend(discover_raw_reports(raw_root, rule))
    return sorted(reports, key=lambda report: str(report.path).lower())
