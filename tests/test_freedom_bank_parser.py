from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from conftest_imports import SRC  # noqa: F401
from kztax270.brokers.freedom_bank import (
    FREEDOM_BANK_SYMBOL,
    ParsedFreedomBankReport,
    _parse_trade_table_row,
    build_canonical_dataset,
)
from kztax270.brokers.registry import default_registry
from kztax270.reconciliation.engine import ReconciliationEngine
from kztax270.reconciliation.models import ReconciliationSeverity
from kztax270.reference.fx import AnnualFxRateProvider


class FreedomBankParserTests(unittest.TestCase):
    def test_trade_row_parser_maps_freedom_bank_operations(self) -> None:
        row = _parse_trade_table_row(
            [
                "B123",
                "15.02.2025 10:11:12",
                "Покупка",
                "0.014",
                "7.21",
                "100",
                "1.40",
                "721",
                "0",
                "detail",
            ],
            source_report="report.pdf",
            source_page=1,
            source_row=2,
            pending={},
        )

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.operation, "buy")
        self.assertEqual(row.quantity, Decimal("100"))
        self.assertEqual(row.amount_usd, Decimal("1.40"))
        self.assertEqual(row.details, "detail")

    def test_summary_position_adjustments_make_positions_reconcile(self) -> None:
        reports = [
            ParsedFreedomBankReport(
                path=Path("freedom-bank-2024.pdf"),
                account_id="831117300478",
                period_start=date(2024, 1, 1),
                period_end=date(2024, 12, 31),
                account_currency="USD",
                security_type="ETN",
                issuer="FRHC Fractional SPC Ltd.",
                isin="KZX000002001",
                rows=[
                    {
                        "source_report": "freedom-bank-2024.pdf",
                        "source_page": 1,
                        "source_row": 1,
                        "broker_trade_id": "buy-1",
                        "date_time": "2024-02-01 10:00:00",
                        "operation": "buy",
                        "price_usd": "10",
                        "price_kzt": "4690",
                        "quantity": "10",
                        "amount_usd": "100",
                        "amount_kzt": "46900",
                        "realized_pl_kzt": "0",
                        "details": None,
                    }
                ],
                summary_rows=[
                    {
                        "label": "Доступно",
                        "quantity": Decimal("11"),
                        "amount_usd": Decimal("110"),
                        "amount_kzt": Decimal("51638.4"),
                    }
                ],
            ),
            ParsedFreedomBankReport(
                path=Path("freedom-bank-2025.pdf"),
                account_id="831117300478",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                account_currency="USD",
                security_type="ETN",
                issuer="FRHC Fractional SPC Ltd.",
                isin="KZX000002001",
                rows=[
                    {
                        "source_report": "freedom-bank-2025.pdf",
                        "source_page": 1,
                        "source_row": 1,
                        "broker_trade_id": "sell-1",
                        "date_time": "2025-03-01 10:00:00",
                        "operation": "sell",
                        "price_usd": "12",
                        "price_kzt": "6240",
                        "quantity": "2",
                        "amount_usd": "24",
                        "amount_kzt": "12480",
                        "realized_pl_kzt": "1000",
                        "details": None,
                    }
                ],
                summary_rows=[
                    {
                        "label": "Доступно",
                        "quantity": Decimal("0"),
                        "amount_usd": Decimal("0"),
                        "amount_kzt": Decimal("0"),
                    }
                ],
            ),
        ]

        dataset = build_canonical_dataset(
            reports,
            "831117300478",
            AnnualFxRateProvider({(2024, "USD"): Decimal("469.44"), (2025, "USD"): Decimal("520")}),
        )

        self.assertEqual(dataset.warnings, [])
        errors = [item for item in ReconciliationEngine().reconcile_dataset(dataset) if item.severity == ReconciliationSeverity.ERROR]
        self.assertEqual(errors, [])
        self.assertEqual(dataset.raw_totals.positions_by_key["|2024||KZX000002001"], Decimal("11"))
        self.assertEqual(dataset.raw_totals.positions_by_key["|2025||KZX000002001"], Decimal("0"))
        adjustment_rows = [
            row
            for row in dataset.tables["Transfers"]
            if row["broker_comment"] == "Freedom Bank summary position reconciliation adjustment"
        ]
        self.assertEqual({row["direction"] for row in adjustment_rows}, {"in", "out"})
        self.assertTrue(any(row["symbol"] == FREEDOM_BANK_SYMBOL and row["year"] == 2024 for row in dataset.tables["Positions"]))
        self.assertFalse(any(row["year"] == 2025 for row in dataset.tables["Positions"]))

    def test_registry_exposes_freedom_bank_parser(self) -> None:
        self.assertIn("freedom_bank", default_registry().broker_codes())


if __name__ == "__main__":
    unittest.main()
