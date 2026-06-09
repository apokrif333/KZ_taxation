"""Annual official FX rates used by post-2024 tax rules."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from .nbk import read_nbk_average_annual_rates_xlsx
from .repositories import ReferenceDataStore


@dataclass(frozen=True, slots=True)
class AnnualFxRateProvider:
    """Look up average annual NBK official rates by tax year and currency."""

    rates: Mapping[tuple[int, str], Decimal]

    @classmethod
    def from_reference_store(cls, store: ReferenceDataStore) -> "AnnualFxRateProvider":
        rows = store.fx_rates.read_all()
        return cls.from_rows(rows)

    @classmethod
    def from_nbk_rates_xlsx(cls, path: Path) -> "AnnualFxRateProvider":
        return cls.from_rows(read_nbk_average_annual_rates_xlsx(path))

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
        return self.rates.get((year, currency))
