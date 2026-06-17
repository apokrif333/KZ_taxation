from __future__ import annotations

from decimal import Decimal
import unittest

from conftest_imports import SRC  # noqa: F401
from kztax270.canonical.schema import CanonicalDataset
from kztax270.reconciliation.engine import ReconciliationEngine
from kztax270.reconciliation.models import ReconciliationMetric, ReconciliationSeverity


class ReconciliationTests(unittest.TestCase):
    def test_scalar_within_tolerance_is_info(self) -> None:
        item = ReconciliationEngine().compare_scalar(
            ReconciliationMetric.TOTAL_COMMISSIONS,
            Decimal("10.00"),
            Decimal("10.004"),
        )
        self.assertEqual(item.severity, ReconciliationSeverity.INFO)

    def test_ending_position_mismatch_is_error(self) -> None:
        item = ReconciliationEngine().compare_scalar(
            ReconciliationMetric.ENDING_POSITION_QUANTITY,
            Decimal("10"),
            Decimal("11"),
            instrument_key="AAPL",
        )
        self.assertEqual(item.severity, ReconciliationSeverity.ERROR)
        self.assertEqual(item.instrument_key, "AAPL")

    def test_unprocessed_row_keeps_row_severity(self) -> None:
        dataset = CanonicalDataset.empty("test", "account")
        dataset.tables["Unprocessed"] = [
            {
                "severity": "warning",
                "reason": "known_ignored_row",
                "details": "Visible but not fatal.",
                "currency": "USD",
            }
        ]

        items = ReconciliationEngine().reconcile_dataset(dataset)
        unprocessed = [item for item in items if item.metric == ReconciliationMetric.UNPROCESSED_ROWS]

        self.assertEqual(len(unprocessed), 1)
        self.assertEqual(unprocessed[0].severity, ReconciliationSeverity.WARNING)

    def test_zero_raw_position_by_isin_flags_canonical_residual(self) -> None:
        dataset = CanonicalDataset.empty("test", "account")
        dataset.tables["Positions"] = [
            {
                "year": 2024,
                "symbol": "ARWAB1.KZ",
                "isin": "KZ2P00003635",
                "quantity": "74950",
            }
        ]
        dataset.raw_totals.positions_by_key["|2024||KZ2P00003635"] = Decimal("0")

        items = ReconciliationEngine().reconcile_dataset(dataset)
        position_errors = [
            item
            for item in items
            if item.metric == ReconciliationMetric.ENDING_POSITION_QUANTITY
            and item.instrument_key == "KZ2P00003635"
            and item.severity == ReconciliationSeverity.ERROR
        ]

        self.assertEqual(len(position_errors), 1)
        self.assertEqual(position_errors[0].difference, Decimal("74950"))


if __name__ == "__main__":
    unittest.main()
