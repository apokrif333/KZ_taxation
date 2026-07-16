"""Annual official FX rates used by post-2024 tax rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from .nbk import ensure_yahoo_cross_rate, read_nbk_average_annual_rates_xlsx
from .repositories import ReferenceDataStore


@dataclass(frozen=True, slots=True)
class AnnualFxRateProvider:
    """Look up annual KZT rates, supplementing NBK gaps through Yahoo cross-rates."""

    rates: Mapping[tuple[int, str], Decimal]
    fallback_path: Path | None = None
    resolved_fallback_rates: dict[tuple[int, str], Decimal] = field(default_factory=dict, compare=False)

    @classmethod
    def from_reference_store(cls, store: ReferenceDataStore) -> "AnnualFxRateProvider":
        rows = store.fx_rates.read_all()
        return cls.from_rows(rows)

    @classmethod
    def from_nbk_rates_xlsx(cls, path: Path) -> "AnnualFxRateProvider":
        provider = cls.from_rows(read_nbk_average_annual_rates_xlsx(path))
        return cls(rates=provider.rates, fallback_path=path)

    @classmethod
    def from_rows(cls, rows: list[Mapping[str, object]]) -> "AnnualFxRateProvider":
        rates: dict[tuple[int, str], Decimal] = {}
        for row in rows:
            year = row.get("year")
            currency = (row.get("currency") or "").upper()
            rate = row.get("average_annual_rate")
            if not year or not currency or not rate:
                continue
            rates[(int(year), currency)] = Decimal(str(rate))
        return cls(rates=rates)

    @classmethod
    def from_reference_root(cls, root: Path) -> "AnnualFxRateProvider":
        return cls.from_reference_store(ReferenceDataStore(root))

    def rate(self, year: int, currency: str) -> Decimal | None:
        currency = currency.upper()
        if currency == "KZT":
            return Decimal("1")
        key = (year, currency)
        direct_rate = self.rates.get(key) or self.resolved_fallback_rates.get(key)
        if direct_rate is not None or self.fallback_path is None:
            return direct_rate

        fallback_rate = ensure_yahoo_cross_rate(self.fallback_path, year, currency)
        if fallback_rate is not None:
            self.resolved_fallback_rates[key] = fallback_rate
        return fallback_rate
