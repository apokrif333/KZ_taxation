"""Money helpers shared by parsers and calculations."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any


def to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value in (None, ""):
        return default
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value).replace(",", ""))


def round_money(value: Decimal, places: str = "0.01") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)
