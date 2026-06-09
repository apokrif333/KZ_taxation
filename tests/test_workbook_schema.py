from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from conftest_imports import SRC  # noqa: F401
from kztax270.canonical.schema import CanonicalDataset
from kztax270.canonical.workbook_schema import CANONICAL_SHEET_NAMES, required_columns
from kztax270.excel.audit_workbook import ExcelAuditWorkbookWriter, display_column_name


class WorkbookSchemaTests(unittest.TestCase):
    def test_canonical_sheet_order_is_stable(self) -> None:
        self.assertEqual(
            CANONICAL_SHEET_NAMES,
            (
                "Instruments",
                "CorporateActions",
                "Dividends",
                "Transfers",
                "Trades",
                "Fifo",
                "Positions",
                "Interest",
                "Coupons",
                "CashBalances",
                "Years_Results",
                "Unprocessed",
                "Reconciliation",
            ),
        )

    def test_reconciliation_columns_include_severity(self) -> None:
        self.assertIn("severity", required_columns("Reconciliation"))

    def test_positions_columns_do_not_include_raw_audit_noise(self) -> None:
        columns = required_columns("Positions")
        self.assertNotIn("exchange", columns)
        self.assertNotIn("calculation_price", columns)
        self.assertNotIn("source_report", columns)

    def test_dividends_columns_include_row_tax_and_exclude_old_kzt_net_tax(self) -> None:
        columns = required_columns("Dividends")
        self.assertIn("tax", columns)
        self.assertIn("tax_kzt", columns)
        self.assertNotIn("tax_kzt_usd", columns)
        self.assertNotIn("withholding_tax_kzt", columns)
        self.assertNotIn("net_amount_kzt", columns)

    def test_transfers_columns_include_fifo_price(self) -> None:
        columns = required_columns("Transfers")
        self.assertIn("quantity", columns)
        self.assertIn("price", columns)
        self.assertIn("enter_date", columns)

    def test_trades_columns_include_trade_type_marker(self) -> None:
        columns = required_columns("Trades")
        self.assertIn("trade_type", columns)

    def test_yearly_interest_columns_use_only_profit_tax_base(self) -> None:
        from kztax270.canonical.workbook_schema import YEARS_RESULTS_TABLE_COLUMNS

        self.assertEqual(
            YEARS_RESULTS_TABLE_COLUMNS["Yearly Interest"],
            ("year", "flag", "currency", "amount", "amount_kzt", "only_profit", "only_profit_kzt", "tax_kzt"),
        )

    def test_excel_headers_use_legacy_style_title_case(self) -> None:
        self.assertEqual(display_column_name("date_time"), "Date_Time")
        self.assertEqual(display_column_name("gross_amount_kzt"), "Gross_Amount_KZT")
        self.assertEqual(display_column_name("tax"), "Tax")
        self.assertEqual(display_column_name("only_profit_kzt"), "OnlyProfit_KZT")
        self.assertEqual(display_column_name("pnl_kzt"), "PnL_KZT")
        self.assertEqual(display_column_name("source_trade_id"), "Source_Trade_ID")

    def test_excel_writer_writes_numeric_columns_as_numbers(self) -> None:
        from openpyxl import load_workbook

        dataset = CanonicalDataset.empty("ib", "UTEST")
        dataset.tables["Trades"] = [
            {
                "date_time": "2024-01-01 10:00:00",
                "trade_id": "trade-1",
                "trade_type": "trade",
                "symbol": "AAPL",
                "isin": "US0378331005",
                "asset_type": "Stocks",
                "quantity": "10",
                "price": "100.25",
                "multiplier": "1",
                "amount": "1002.50",
                "commission": "1.25",
                "amount_with_commission": "1003.75",
                "currency": "USD",
                "country": "US",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.xlsx"
            ExcelAuditWorkbookWriter().write(dataset, path)
            workbook = load_workbook(path, data_only=True)

        ws = workbook["Trades"]
        headers = [cell.value for cell in ws[1]]
        quantity_cell = ws.cell(row=2, column=headers.index("Quantity") + 1)
        amount_cell = ws.cell(row=2, column=headers.index("Amount") + 1)
        isin_cell = ws.cell(row=2, column=headers.index("ISIN") + 1)

        self.assertEqual(quantity_cell.data_type, "n")
        self.assertEqual(amount_cell.data_type, "n")
        self.assertEqual(isin_cell.data_type, "s")


if __name__ == "__main__":
    unittest.main()
