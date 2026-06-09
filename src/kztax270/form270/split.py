"""Split joint-account Form 270 drafts by ownership ratio."""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any, Mapping

SCALABLE_KEY_TOKENS = (
    "amount",
    "tax",
    "profit",
    "pnl",
    "value",
    "income",
    "deduction",
)


def split_form270_json(form: Mapping[str, Any], ownership_ratios: Mapping[str, Decimal | str | float]) -> dict[str, dict[str, Any]]:
    ratios = {owner: Decimal(str(ratio)) for owner, ratio in ownership_ratios.items()}
    total = sum(ratios.values(), Decimal("0"))
    if total != Decimal("1"):
        raise ValueError(f"Ownership ratios must sum to 1, got {total}")

    result: dict[str, dict[str, Any]] = {}
    for owner, ratio in ratios.items():
        split = _scale(copy.deepcopy(dict(form)), ratio)
        split.setdefault("_kztax270", {})
        split["_kztax270"].update({"status": "split_draft", "owner": owner, "ownership_ratio": str(ratio)})
        result[owner] = split
    return result


def _scale(value: Any, ratio: Decimal, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {child_key: _scale(child_value, ratio, child_key) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_scale(item, ratio, key) for item in value]
    if key and _is_scalable_key(key) and isinstance(value, (int, float, Decimal, str)):
        try:
            return str(Decimal(str(value)) * ratio)
        except Exception:
            return value
    return value


def _is_scalable_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in SCALABLE_KEY_TOKENS)
