"""Reference data update service stubs."""

from __future__ import annotations

from dataclasses import dataclass

from .repositories import ReferenceDataStore


@dataclass(slots=True)
class FxRatesUpdater:
    store: ReferenceDataStore

    def update_nbk_average_annual_rates(self, year: int) -> int:
        raise NotImplementedError(
            f"NBK FX updater for {year} is not implemented yet. Store rows in reference/fx_rates manually for now."
        )


@dataclass(slots=True)
class KaseAixUpdater:
    store: ReferenceDataStore

    def update_official_lists(self) -> int:
        raise NotImplementedError(
            "KASE/AIX official-list downloader is not implemented yet. Store snapshots in reference/kase_aix manually."
        )
