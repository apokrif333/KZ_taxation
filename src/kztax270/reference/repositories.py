"""CSV-backed reference data repositories."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .schemas import FX_RATE_COLUMNS, INSTRUMENT_COLUMNS, JURISDICTION_COLUMNS, KASE_AIX_COLUMNS


@dataclass(slots=True)
class CsvReferenceTable:
    path: Path
    columns: Sequence[str]
    key_columns: Sequence[str]

    def ensure_exists(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(self.columns))
                writer.writeheader()

    def read_all(self) -> list[dict[str, str]]:
        self.ensure_exists()
        with self.path.open("r", newline="", encoding="utf-8-sig") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    def upsert_many(self, rows: Iterable[Mapping[str, object]]) -> int:
        self.ensure_exists()
        current = self.read_all()
        indexed = {self._key(row): row for row in current}
        changed = 0
        for row in rows:
            normalized = {column: str(row.get(column, "") or "") for column in self.columns}
            key = self._key(normalized)
            if indexed.get(key) != normalized:
                indexed[key] = normalized
                changed += 1
        ordered_rows = sorted(indexed.values(), key=lambda item: self._key(item))
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.columns))
            writer.writeheader()
            writer.writerows(ordered_rows)
        return changed

    def _key(self, row: Mapping[str, object]) -> tuple[str, ...]:
        return tuple(str(row.get(column, "") or "") for column in self.key_columns)


@dataclass(slots=True)
class ReferenceDataStore:
    root: Path

    @property
    def fx_rates(self) -> CsvReferenceTable:
        return CsvReferenceTable(
            self.root / "fx_rates" / "nbk_average_annual_rates.csv",
            FX_RATE_COLUMNS,
            ("year", "currency"),
        )

    @property
    def instruments(self) -> CsvReferenceTable:
        return CsvReferenceTable(
            self.root / "instruments" / "instrument_master.csv",
            INSTRUMENT_COLUMNS,
            ("isin", "figi", "conid", "symbol"),
        )

    @property
    def jurisdictions(self) -> CsvReferenceTable:
        return CsvReferenceTable(
            self.root / "jurisdictions" / "jurisdictions.csv",
            JURISDICTION_COLUMNS,
            ("country_code", "valid_from"),
        )

    @property
    def kase_aix(self) -> CsvReferenceTable:
        return CsvReferenceTable(
            self.root / "kase_aix" / "official_lists.csv",
            KASE_AIX_COLUMNS,
            ("isin", "exchange", "source_date"),
        )

    def ensure_all(self) -> None:
        self.fx_rates.ensure_exists()
        self.instruments.ensure_exists()
        self.jurisdictions.ensure_exists()
        self.kase_aix.ensure_exists()
