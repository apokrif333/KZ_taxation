"""Income categorization contracts."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


class IncomeCategorizer:
    def categorize(self, rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        categorized: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.setdefault("income_category", "unclassified")
            categorized.append(item)
        return categorized
