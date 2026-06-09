from __future__ import annotations

from decimal import Decimal
import unittest

from conftest_imports import SRC  # noqa: F401
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


if __name__ == "__main__":
    unittest.main()
