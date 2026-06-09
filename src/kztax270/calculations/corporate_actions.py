"""Corporate action service contracts."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


class CorporateActionApplier:
    """Apply normalized corporate actions before FIFO.

    Broker-specific action parsing belongs in broker adapters. Shared mechanics
    such as splits, redemptions and spin-offs should move here over time.
    """

    def apply(
        self,
        trades: Iterable[Mapping[str, Any]],
        corporate_actions: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        return [dict(row) for row in trades]
