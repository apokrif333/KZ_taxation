from __future__ import annotations

from decimal import Decimal
import tempfile
import unittest
from pathlib import Path

from conftest_imports import SRC  # noqa: F401
from kztax270.canonical.schema import CanonicalDataset
from kztax270.excel.audit_workbook import ExcelAuditWorkbookWriter
from kztax270.excel.merge_workbooks import aggregate_years_results, merge_audit_workbooks
from kztax270.form270.json_builder import load_processed_workbook_tables


class WorkbookMergeTests(unittest.TestCase):
    def test_withholding_is_pooled_by_table_year_and_country_after_merge(self) -> None:
        rows = []
        for table in ("Yearly Trades", "Yearly Dividends", "Yearly Coupons"):
            rows.extend(
                [
                    {
                        "table": table,
                        "year": 2022,
                        "flag": "non-preferential",
                        "country": "US",
                        "exchange": "outofKZ",
                        "currency": "USD",
                        "tax_kzt": "100",
                        "withhold_kzt": "-10",
                        "tax_kzt_withhold": "90",
                    },
                    {
                        "table": table,
                        "year": 2022,
                        "flag": "non-preferential",
                        "country": "US",
                        "exchange": "outofKZ",
                        "currency": "EUR",
                        "tax_kzt": "50",
                        "withhold_kzt": "-200",
                        "tax_kzt_withhold": "0",
                    },
                ]
            )

        merged = aggregate_years_results(rows)

        for table in ("Yearly Trades", "Yearly Dividends", "Yearly Coupons"):
            table_rows = [row for row in merged if row["table"] == table]
            self.assertEqual(sum(Decimal(row["tax_kzt_withhold"]) for row in table_rows), Decimal("0"))

    def test_merge_concatenates_detail_sheets_and_aggregates_years_results(self) -> None:
        first = CanonicalDataset.empty("freedom", "A1")
        first.tables["Instruments"] = [{"symbol": "AAA", "isin": "US0000000001"}]
        first.tables["Trades"] = [
            {"date_time": "2024-01-01", "trade_id": "freedom-1", "symbol": "AAA", "quantity": "1"}
        ]
        first.tables["CashBalances"] = [
            {"year": 2024, "currency": "USD", "ending_cash": "100", "ending_cash_kzt": "47000"}
        ]
        first.tables["Years_Results"] = [
            {
                "table": "Yearly Trades",
                "year": 2024,
                "flag": "preferential",
                "exchange": "KASE",
                "currency": "USD",
                "pnl": "10",
                "pnl_kzt": "4700",
                "tax_kzt": "0",
            },
            {
                "table": "Yearly Dividends",
                "year": 2024,
                "flag": "non-preferential",
                "country": "US",
                "currency": "USD",
                "amount": "5",
                "amount_kzt": "2350",
                "withhold_kzt": "-352.5",
            },
        ]

        second = CanonicalDataset.empty("exante", "B2")
        second.tables["Instruments"] = [{"symbol": "BBB", "isin": "US0000000002"}]
        second.tables["Trades"] = [
            {"date_time": "2024-02-01", "trade_id": "exante-1", "symbol": "BBB", "quantity": "2"}
        ]
        second.tables["CashBalances"] = [
            {"year": 2024, "currency": "USD", "ending_cash": "200", "ending_cash_kzt": "94000"}
        ]
        second.tables["Years_Results"] = [
            {
                "table": "Yearly Trades",
                "year": 2024,
                "flag": "preferential",
                "exchange": "KASE",
                "currency": "USD",
                "pnl": "20",
                "pnl_kzt": "9400",
                "tax_kzt": "0",
            },
            {
                "table": "Yearly Trades",
                "year": 2024,
                "flag": "preferential",
                "exchange": "AIX",
                "currency": "USD",
                "pnl": "30",
                "pnl_kzt": "14100",
                "tax_kzt": "0",
            },
            {
                "table": "Yearly Dividends",
                "year": 2024,
                "flag": "non-preferential",
                "country": "US",
                "currency": "USD",
                "amount": "7",
                "amount_kzt": "3290",
                "withhold_kzt": "-493.5",
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_path = root / "freedom_A1_audit.xlsx"
            second_path = root / "exante_B2_audit.xlsx"
            output_path = root / "merged_Test_User.xlsx"
            ExcelAuditWorkbookWriter().write(first, first_path)
            ExcelAuditWorkbookWriter().write(second, second_path)

            merge_audit_workbooks((first_path, second_path), output_path)
            tables = load_processed_workbook_tables(output_path)

        self.assertEqual(len(tables["Instruments"]), 2)
        self.assertEqual(len(tables["Trades"]), 2)
        cash = {(row["broker"], row["account_id"]): row for row in tables["CashBalances"]}
        self.assertEqual(set(cash), {("freedom", "A1"), ("exante", "B2")})

        yearly = tables["Years_Results"]
        kase = next(row for row in yearly if row["table"] == "Yearly Trades" and row["exchange"] == "KASE")
        aix = next(row for row in yearly if row["table"] == "Yearly Trades" and row["exchange"] == "AIX")
        dividends = next(row for row in yearly if row["table"] == "Yearly Dividends")
        self.assertEqual(Decimal(str(kase["pnl_kzt"])), Decimal("14100"))
        self.assertEqual(Decimal(str(aix["pnl_kzt"])), Decimal("14100"))
        self.assertEqual(Decimal(str(dividends["amount"])), Decimal("12"))
        self.assertEqual(Decimal(str(dividends["amount_kzt"])), Decimal("5640"))
        self.assertEqual(Decimal(str(dividends["withhold_kzt"])), Decimal("-846"))


if __name__ == "__main__":
    unittest.main()
