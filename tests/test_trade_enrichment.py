from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from conftest_imports import SRC  # noqa: F401
from kztax270.canonical.schema import CanonicalDataset
from kztax270.canonical.trade_enrichment import (
    classify_form270_05_sources,
    enrich_trades_with_kzt,
)
from kztax270.excel.audit_workbook import ExcelAuditWorkbookWriter
from kztax270.excel.form270_05_trades import prepare_form270_05_trades_workbook
from kztax270.form270.json_builder import load_processed_workbook_tables
from kztax270.reference.fx import AnnualFxRateProvider


class TradeEnrichmentTests(unittest.TestCase):
    def test_kzt_values_use_gross_amount_without_commission(self) -> None:
        trades = [
            {
                "date_time": "2025-01-01 10:00:00",
                "quantity": "2",
                "amount": "100",
                "amount_with_commission": "103",
                "currency": "USD",
            }
        ]

        enrich_trades_with_kzt(trades, AnnualFxRateProvider({(2025, "USD"): Decimal("500")}))

        self.assertEqual(trades[0]["kzt_rate"], "500")
        self.assertEqual(trades[0]["amount_kzt"], "50000")

    def test_source_pool_is_cross_year_all_or_nothing_and_keeps_fx_spot(self) -> None:
        trades = [
            _trade("2024-01-01", "SALE", "-1", "100"),
            _trade("2025-01-01", "TOO-BIG", "1", "150"),
            _trade("2025-01-02", "EXACT", "1", "100"),
            _trade("2025-01-03", "FX-SPOT", "1", "10", asset_type="FX Spot"),
            _trade("2025-01-04", "FOREX", "-1", "999", asset_type="Forex"),
        ]

        classified = classify_form270_05_sources(list(reversed(trades)))
        by_symbol = {row["symbol"]: row for row in classified}

        self.assertEqual([row["symbol"] for row in classified], ["SALE", "TOO-BIG", "EXACT", "FX-SPOT", "FOREX"])
        self.assertIsNone(by_symbol["SALE"]["source_of_expense"])
        self.assertEqual(by_symbol["TOO-BIG"]["source_of_expense"], "11")
        self.assertEqual(by_symbol["EXACT"]["source_of_expense"], "12")
        self.assertEqual(by_symbol["FX-SPOT"]["source_of_expense"], "11")
        self.assertIsNone(by_symbol["FOREX"]["source_of_expense"])

    def test_paid_corporate_action_disposal_adds_to_sale_pool(self) -> None:
        trades = [
            _trade("2022-01-13", "PRIOR-BUY", "1", "100"),
            _trade(
                "2022-10-28",
                "TWTR",
                "-40",
                "2168",
                trade_type="corporate_action:merged",
            ),
            _trade("2022-11-01", "KSPI", "1", "650"),
            _trade("2022-11-01 09:35:49", "MSFT", "1", "703.765"),
            _trade(
                "2022-11-02",
                "ZERO-REDEMPTION",
                "-1",
                "0",
                trade_type="corporate_action:redemption",
            ),
        ]

        classified = classify_form270_05_sources(trades)
        by_symbol = {row["symbol"]: row for row in classified}

        self.assertEqual(by_symbol["KSPI"]["source_of_expense"], "12")
        self.assertEqual(by_symbol["MSFT"]["source_of_expense"], "12")
        self.assertIsNone(by_symbol["ZERO-REDEMPTION"]["source_of_expense"])

    def test_workbook_preparation_adds_columns_and_sorts_trades(self) -> None:
        dataset = CanonicalDataset.empty("ib", "U1")
        dataset.tables["Trades"] = [
            _trade("2025-01-02", "BUY", "1", "100", currency="USD"),
            _trade("2024-01-01", "SALE", "-1", "100", currency="USD"),
            _trade(
                "2024-06-01",
                "ZERO-SPINOFF",
                "1",
                "0",
                currency="USD",
                trade_type="corporate_action:spinoff",
            ),
        ]
        provider = AnnualFxRateProvider({(2024, "USD"): Decimal("400"), (2025, "USD"): Decimal("400")})

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ib_U1_audit.xlsx"
            ExcelAuditWorkbookWriter().write(dataset, path)
            prepare_form270_05_trades_workbook(path, provider)
            rows = load_processed_workbook_tables(path)["Trades"]

        self.assertEqual([row["symbol"] for row in rows], ["SALE", "ZERO-SPINOFF", "BUY"])
        self.assertEqual(Decimal(str(rows[0]["kzt_rate"])), Decimal("400"))
        self.assertEqual(Decimal(str(rows[0]["amount_kzt"])), Decimal("40000"))
        self.assertEqual(Decimal(str(rows[1]["amount"])), Decimal("0"))
        self.assertIsNone(rows[1]["source_of_expense"])
        self.assertEqual(str(rows[2]["source_of_expense"]), "12")


def _trade(
    date_time: str,
    symbol: str,
    quantity: str,
    amount: str,
    *,
    asset_type: str = "Stocks",
    currency: str = "KZT",
    trade_type: str = "trade",
) -> dict[str, str]:
    return {
        "date_time": date_time,
        "trade_id": symbol,
        "trade_type": trade_type,
        "symbol": symbol,
        "isin": "KZ0000000001",
        "asset_type": asset_type,
        "quantity": quantity,
        "price": amount,
        "multiplier": "1",
        "amount": amount,
        "amount_with_commission": amount,
        "amount_kzt": amount,
        "currency": currency,
        "country": "KZ",
    }


if __name__ == "__main__":
    unittest.main()
