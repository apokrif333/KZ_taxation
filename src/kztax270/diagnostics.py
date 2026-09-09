"""Context attached to raw broker-report rows while a calculation is running."""

from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping, Sequence
from contextvars import ContextVar
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any


_CURRENT_RAW_ROW: ContextVar[dict[str, Any] | None] = ContextVar(
    "kztax270_current_raw_row",
    default=None,
)


class TrackedRawRow(dict[str, Any]):
    """A raw record that marks itself as the active diagnostic context on use."""

    def __init__(self, values: Mapping[str, Any], context: Mapping[str, Any]) -> None:
        super().__init__(values)
        self._context = dict(context)
        self._context["row"] = dict(values)

    def _activate(self) -> None:
        _CURRENT_RAW_ROW.set(self._context)

    def __getitem__(self, key: str) -> Any:
        self._activate()
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        self._activate()
        return super().get(key, default)

    def __contains__(self, key: object) -> bool:
        self._activate()
        return super().__contains__(key)

    def items(self):  # type: ignore[override]
        self._activate()
        return super().items()

    def keys(self):  # type: ignore[override]
        self._activate()
        return super().keys()

    def values(self):  # type: ignore[override]
        self._activate()
        return super().values()

    def copy(self) -> dict[str, Any]:
        self._activate()
        return dict(self)


def instrument_parsed_reports(broker: str, reports: Sequence[Any]) -> None:
    """Wrap raw records of native parsed reports with diagnostic context.

    The broker parsers use either a ``rows`` mapping or named row lists.  This
    helper handles both shapes without changing their public report classes.
    """

    for report in reports:
        report_path = str(getattr(report, "path", ""))
        if not is_dataclass(report):
            continue
        for item in fields(report):
            value = getattr(report, item.name)
            if isinstance(value, MutableMapping):
                for section, records in value.items():
                    if isinstance(records, list):
                        _instrument_record_list(broker, report_path, str(section), records)
            elif isinstance(value, list):
                _instrument_record_list(broker, report_path, item.name, value)


def clear_raw_row_context() -> None:
    _CURRENT_RAW_ROW.set(None)


def current_raw_row_log_context() -> str:
    """Return a log-safe, complete snapshot of the last accessed raw row."""

    context = _CURRENT_RAW_ROW.get()
    if context is None:
        return "raw_row=unavailable"
    return "raw_row=" + json.dumps(context, ensure_ascii=False, default=_json_default, sort_keys=True)


def _instrument_record_list(
    broker: str,
    report_path: str,
    section: str,
    records: list[Any],
) -> None:
    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping) or isinstance(record, TrackedRawRow):
            continue
        source_report = str(record.get("source_report") or report_path)
        source_sheet = str(record.get("source_sheet") or section)
        source_row = record.get("source_row") or index
        records[index - 1] = TrackedRawRow(
            record,
            {
                "broker": broker,
                "source_report": source_report,
                "source_sheet": source_sheet,
                "source_row": source_row,
            },
        )


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    return str(value)
