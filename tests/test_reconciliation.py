from __future__ import annotations

import unittest
from decimal import Decimal

from conftest_imports import SRC  # noqa: F401
from kztax270.canonical.schema import CanonicalDataset
from kztax270.canonical.validation import validate_dataset_for_tax_forms
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

    def test_missing_country_and_fx_rate_are_unprocessed_reconciliation_errors(self) -> None:
        dataset = CanonicalDataset.empty("test", "account")
        dataset.tables["Trades"] = [
            {
                "date_time": "2024-01-10 10:00:00",
                "trade_id": "trade-1",
                "symbol": "UNKNOWN",
                "asset_type": "Stocks",
                "currency": "ZZZ",
                "exchange": "UNKNOWN",
                "country": None,
            }
        ]
        dataset.tables["Fifo"] = [
            {
                "symbol": "UNKNOWN",
                "asset_type": "Stocks",
                "currency": "ZZZ",
                "exchange": "UNKNOWN",
                "country": None,
            }
        ]
        dataset.warnings.append("Missing annual NBK FX rate for ZZZ/2024; KZT fields left empty.")

        validate_dataset_for_tax_forms(dataset)

        self.assertEqual(
            {row["reason"] for row in dataset.tables["Unprocessed"]},
            {"missing_instrument_country", "missing_kzt_fx_rate"},
        )
        country_row = next(row for row in dataset.tables["Unprocessed"] if row["reason"] == "missing_instrument_country")
        self.assertEqual(country_row["source_sheet"], "Fifo, Trades")

        items = ReconciliationEngine().reconcile_dataset(dataset)
        unprocessed = [item for item in items if item.metric == ReconciliationMetric.UNPROCESSED_ROWS]
        self.assertEqual(len(unprocessed), 2)
        self.assertTrue(all(item.severity == ReconciliationSeverity.ERROR for item in unprocessed))

    def test_cme_country_is_filled_before_country_validation(self) -> None:
        dataset = CanonicalDataset.empty("test", "account")
        dataset.tables["Trades"] = [
            {
                "symbol": "MES",
                "asset_type": "Futures",
                "currency": "USD",
                "exchange": "CME.Z2022",
                "country": None,
            }
        ]
        dataset.tables["Fifo"] = [
            {
                "symbol": "MES",
                "asset_type": "Futures",
                "currency": "USD",
                "exchange": "CME.Z2022",
                "country": None,
            }
        ]

        validate_dataset_for_tax_forms(dataset)

        self.assertEqual({row["country"] for row in dataset.tables["Trades"]}, {"US"})
        self.assertEqual({row["country"] for row in dataset.tables["Fifo"]}, {"US"})
        self.assertEqual(dataset.tables["Unprocessed"], [])


if __name__ == "__main__":
    unittest.main()
