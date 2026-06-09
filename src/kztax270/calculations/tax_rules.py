"""Tax rule interfaces.

The production tax engine is intentionally not implemented in this iteration.
Rules consume canonical tables and reference data and will eventually produce
Form270 field mappings.

Current rule contract for the new Form 270 pipeline:

1. FIFO taxable result uses acquisition cost including the opening/initiating
   trade commission. Liquidation commission is not deducted from tax
   `Fifo.pnl`; it remains available as `Fifo.exit_commission` and
   `Fifo.pnl_after_all_commissions` for audit.
2. Foreign-currency income is converted to KZT using the average annual official
   NBK rate for the year of income recognition. Daily rates from legacy
   `Currency.xlsx` must not be used in the new engine.
3. Instrument tax treatment depends on explicit reference-data flags:
   `offshore_flag`, `issuer_outside_kz_flag`, and `preferential_tax_flag`.
   These flags are stubs until KASE/AIX and jurisdiction enrichment is added.
"""

from __future__ import annotations

from typing import Any, Mapping

from kztax270.canonical.schema import CanonicalDataset


class TaxRuleEngine:
    def apply(self, dataset: CanonicalDataset) -> list[dict[str, Any]]:
        return list(dataset.tables.get("Years_Results", []))

    def explain(self) -> Mapping[str, str]:
        return {
            "status": "stub",
            "fx_rule": "Use annual average official NBK rate by income year.",
            "fifo_rule": "Use acquisition cost including opening trade commission.",
            "instrument_flags": "offshore_flag, issuer_outside_kz_flag, preferential_tax_flag",
        }
