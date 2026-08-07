"""Shared enrichment and Form 270.05 classification for canonical trades."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from kztax270.reference.fx import AnnualFxRateProvider

ZERO = Decimal("0")
SOURCE_OWN_FUNDS_CODE = "11"
SOURCE_ASSET_SALE_CODE = "12"


def enrich_trades_with_kzt(
    trades: Sequence[MutableMapping[str, Any]],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str] | None = None,
) -> None:
    """Add annual KZT rate and gross trade amount in KZT to every trade."""

    missing_rates: set[tuple[int, str]] = set()
    for row in trades:
        traded_at = parse_trade_datetime(row.get("date_time"))
        currency = str(row.get("currency") or "").strip().upper()
        amount = abs(decimal_value(row.get("amount")))
        if traded_at is None or not currency:
            row["kzt_rate"] = None
            row["amount_kzt"] = None
            continue

        rate = fx_provider.rate(traded_at.year, currency)
        if rate is None:
            row["kzt_rate"] = None
            row["amount_kzt"] = None
            missing_rates.add((traded_at.year, currency))
            continue
        row["kzt_rate"] = decimal_text(rate)
        row["amount_kzt"] = decimal_text(amount * rate)

    if warnings is not None:
        for year, currency in sorted(missing_rates):
            message = f"Missing annual KZT rate for Trades: year={year}, currency={currency}"
            if message not in warnings:
                warnings.append(message)


def classify_form270_05_sources(
    trades: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Sort all trades and assign the legacy all-or-nothing expense source.

    The pool is global across the complete workbook history.  A sale increases
    it.  A purchase uses sale proceeds only when the pool covers the purchase
    completely; an uncovered purchase leaves the existing pool untouched.
    """

    ordered = [dict(row) for row in trades]
    ordered = [
        row
        for _index, row in sorted(
            enumerate(ordered),
            key=lambda item: (parse_trade_datetime(item[1].get("date_time")) or datetime.max, item[0]),
        )
    ]

    sale_pool = ZERO
    for row in ordered:
        row["source_of_expense"] = None
        if not is_real_form270_05_trade(row):
            continue
        amount_kzt = abs(decimal_value(row.get("amount_kzt")))
        quantity = decimal_value(row.get("quantity"))
        if amount_kzt <= ZERO:
            continue
        if quantity < ZERO:
            sale_pool += amount_kzt
            continue
        if sale_pool >= amount_kzt:
            row["source_of_expense"] = SOURCE_ASSET_SALE_CODE
            sale_pool -= amount_kzt
        else:
            row["source_of_expense"] = SOURCE_OWN_FUNDS_CODE
    return ordered


def is_real_form270_05_trade(row: Mapping[str, Any]) -> bool:
    """Return whether a row is a non-zero purchase or disposal for 270.05.

    Paid corporate actions (cash mergers, redemptions, buybacks, and similar
    events) are purchases or disposals too. Eligibility therefore follows the
    economic values, not the broker-specific event name.
    """

    if is_swap_or_repo(row) or is_forex_trade(row):
        return False
    return decimal_value(row.get("quantity")) != ZERO and abs(decimal_value(row.get("amount"))) > ZERO


def is_swap_or_repo(row: Mapping[str, Any]) -> bool:
    identifier = " ".join(str(row.get(key) or "") for key in ("isin", "symbol", "security_id"))
    upper = identifier.upper()
    return ".SWAP" in upper or ".REPO" in upper


def is_forex_trade(row: Mapping[str, Any]) -> bool:
    """Exclude Forex/Currency trades while deliberately retaining FX Spot."""

    asset_type = str(row.get("asset_type") or row.get("Asset_Type") or "").strip().casefold()
    normalized = asset_type.replace("_", " ").replace("-", " ")
    if "fx spot" in normalized:
        return False
    return normalized in {"currency", "forex", "cash"} or "forex" in normalized


def parse_trade_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime()
        except Exception:
            return None
    text = str(value).strip()
    if not text or text.casefold() == "nan":
        return None
    for candidate in (text, text.replace("Z", ""), text.split(".")[0]):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def decimal_value(value: Any) -> Decimal:
    if value is None:
        return ZERO
    text = str(value).strip()
    if not text or text.casefold() == "nan":
        return ZERO
    try:
        return Decimal(text.replace(",", "."))
    except (InvalidOperation, ValueError):
        return ZERO


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
