"""Dataset-level validations required before an audit workbook is written."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from .schema import CanonicalDataset, Record

_MISSING_FX_RATE_RE = re.compile(
    r"^Missing annual NBK FX rate for (?P<currency>[A-Z]{3})/(?P<year>\d{4}); KZT fields left empty\.$"
)

# The country in a canonical transaction is the market country used by the tax
# forms.  Keep this small and explicit; broker adapters remain responsible for
# their own fuller exchange mappings.
_EXCHANGE_COUNTRIES = {
    "CME": "US",
}
_COUNTRY_SHEETS = ("Instruments", "Trades", "Fifo", "Positions", "Dividends", "Coupons")
_NON_SECURITY_ASSET_TYPES = {"cash", "forex", "fx spot"}


def validate_dataset_for_tax_forms(dataset: CanonicalDataset) -> None:
    """Enrich known market countries and surface blocking data gaps.

    ``Unprocessed`` is the single diagnostic source for the audit workbook.
    The reconciliation engine mirrors every row from it into ``Reconciliation``.
    """

    _fill_known_countries(dataset)
    unprocessed = dataset.table("Unprocessed")
    existing = {_unprocessed_key(row) for row in unprocessed}

    for diagnostic in _missing_country_diagnostics(dataset):
        _append_unique(unprocessed, existing, diagnostic)
    for diagnostic in _missing_fx_rate_diagnostics(dataset):
        _append_unique(unprocessed, existing, diagnostic)


def _fill_known_countries(dataset: CanonicalDataset) -> None:
    countries_by_isin: dict[str, str] = {}
    countries_by_symbol: dict[str, set[str]] = defaultdict(set)

    for row in _records(dataset, _COUNTRY_SHEETS):
        country = _text(row.get("country"))
        if not country:
            country = _country_from_exchange(row.get("exchange") or row.get("listing_exchange"))
        if not country:
            continue
        isin = _text(row.get("isin"))
        symbol = _text(row.get("symbol"))
        if isin:
            countries_by_isin.setdefault(isin, country)
        if symbol:
            countries_by_symbol[symbol].add(country)

    for row in _records(dataset, _COUNTRY_SHEETS):
        if _text(row.get("country")):
            continue
        country = _country_from_exchange(row.get("exchange") or row.get("listing_exchange"))
        if not country:
            country = countries_by_isin.get(_text(row.get("isin")) or "")
        if not country:
            symbol_countries = countries_by_symbol.get(_text(row.get("symbol")) or "", set())
            if len(symbol_countries) == 1:
                country = next(iter(symbol_countries))
        if country:
            row["country"] = country


def _missing_country_diagnostics(dataset: CanonicalDataset) -> Iterable[Record]:
    affected_sheets: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    samples: dict[tuple[str, str, str], Record] = {}

    for sheet_name in _COUNTRY_SHEETS:
        for row in dataset.tables.get(sheet_name, []):
            if _text(row.get("country")) or not _is_security_row(row):
                continue
            identity = _instrument_identity(row)
            if identity is None:
                continue
            affected_sheets[identity].add(sheet_name)
            samples.setdefault(identity, row)

    for identity, sheet_names in affected_sheets.items():
        sample = samples[identity]
        yield {
            "severity": "error",
            "reason": "missing_instrument_country",
            "details": (
                f"Country is missing for instrument {identity[2]}; affected sheets: "
                f"{', '.join(sorted(sheet_names))}."
            ),
            "source_sheet": ", ".join(sorted(sheet_names)),
            "source_report": sample.get("source_report"),
            "trade_id": sample.get("trade_id") or sample.get("source_trade_id"),
            "date_time": sample.get("date_time") or sample.get("date") or sample.get("exit_date"),
            "symbol": sample.get("symbol"),
            "isin": sample.get("isin"),
            "asset_type": sample.get("asset_type") or sample.get("type"),
            "currency": sample.get("currency"),
            "quantity": sample.get("quantity") or sample.get("exit_quantity"),
            "price": sample.get("price") or sample.get("exit_price"),
            "amount": sample.get("amount") or sample.get("exit_amount"),
            "commission": sample.get("commission") or sample.get("exit_commission"),
        }


def _missing_fx_rate_diagnostics(dataset: CanonicalDataset) -> Iterable[Record]:
    for warning in dataset.warnings:
        match = _MISSING_FX_RATE_RE.fullmatch(warning)
        if not match:
            continue
        currency = match.group("currency")
        year = match.group("year")
        yield {
            "severity": "error",
            "reason": "missing_kzt_fx_rate",
            "details": (
                f"No annual KZT rate for {currency}/{year}: the NBK reference has no rate "
                "and the Yahoo cross-rate fallback was unavailable."
            ),
            "source_sheet": "FX rates",
            "source_report": None,
            "trade_id": None,
            "date_time": f"{year}-01-01",
            "symbol": None,
            "isin": None,
            "asset_type": None,
            "currency": currency,
            "quantity": None,
            "price": None,
            "amount": None,
            "commission": None,
        }


def _records(dataset: CanonicalDataset, sheet_names: Iterable[str]) -> Iterable[Record]:
    for sheet_name in sheet_names:
        yield from dataset.tables.get(sheet_name, [])


def _country_from_exchange(value: Any) -> str | None:
    exchange = _text(value)
    if not exchange:
        return None
    return _EXCHANGE_COUNTRIES.get(exchange.upper().split(".", 1)[0])


def _is_security_row(row: Record) -> bool:
    asset_type = _text(row.get("asset_type") or row.get("type"))
    if asset_type and asset_type.casefold() in _NON_SECURITY_ASSET_TYPES:
        return False
    return bool(_text(row.get("isin")) or _text(row.get("symbol")))


def _instrument_identity(row: Record) -> tuple[str, str, str] | None:
    isin = _text(row.get("isin"))
    if isin:
        return "isin", isin, isin
    symbol = _text(row.get("symbol"))
    if not symbol:
        return None
    exchange = _text(row.get("exchange") or row.get("listing_exchange")) or ""
    return "symbol", exchange, symbol


def _append_unique(rows: list[Record], existing: set[tuple[str, str, str, str]], diagnostic: Record) -> None:
    key = _unprocessed_key(diagnostic)
    if key not in existing:
        rows.append(diagnostic)
        existing.add(key)


def _unprocessed_key(row: Record) -> tuple[str, str, str, str]:
    return (
        str(row.get("reason") or ""),
        str(row.get("source_sheet") or ""),
        str(row.get("isin") or row.get("symbol") or ""),
        str(row.get("currency") or ""),
    )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
