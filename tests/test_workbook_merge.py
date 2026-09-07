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
                        "tax_exchange": "outofKZ",
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
                        "tax_exchange": "outofKZ",
                        "currency": "EUR",
                        "tax_kzt": "50",
                        "withhold_kzt": "-200",
                        "tax_kzt_withhold": "0",
                    },
                ]
            )
        for row in rows:
            base = "1000" if row["currency"] == "USD" else "500"
            if row["table"] == "Yearly Trades":
                row["pnl_kzt"] = base
            elif row["table"] == "Yearly Dividends":
                row["amount_kzt"] = base
            else:
                row["only_profit_kzt"] = base

        merged = aggregate_years_results(rows)

        for table in ("Yearly Trades", "Yearly Dividends", "Yearly Coupons"):
            table_rows = [row for row in merged if row["table"] == table]
            self.assertEqual(sum(Decimal(row["tax_kzt_withhold"]) for row in table_rows), Decimal("0"))

    def test_preferential_dividend_withholding_does_not_cover_taxable_dividends_after_merge(self) -> None:
        rows = [
            {
                "table": "Yearly Dividends",
                "year": 2024,
                "flag": "non-preferential",
                "country": "US",
                "currency": "USD",
                "amount_kzt": "1000",
                "tax_kzt": "100",
                "withhold_kzt": "-10",
            },
            {
                "table": "Yearly Dividends",
                "year": 2024,
                "flag": "preferential_kase",
                "country": "US",
                "currency": "USD",
                "amount_kzt": "10000",
                "tax_kzt": "0",
                "withhold_kzt": "-1000",
            },
        ]

        merged = aggregate_years_results(rows)

        taxable = next(row for row in merged if row["flag"] == "non-preferential")
        preferential = next(row for row in merged if row["flag"] == "preferential_kase")
        self.assertEqual(Decimal(taxable["tax_kzt_withhold"]), Decimal("90"))
        self.assertEqual(Decimal(preferential["tax_kzt_withhold"]), Decimal("0"))

    def test_coupons_remain_exempt_after_merge(self) -> None:
        rows = [
            {
                "table": "Yearly Coupons",
                "year": 2024,
                "flag": "non-preferential",
                "country": "US",
                "currency": "USD",
                "only_profit_kzt": "1000",
                "tax_kzt": "100",
                "withhold_kzt": "-10",
            },
            {
                "table": "Yearly Coupons",
                "year": 2024,
                "flag": "preferential",
                "country": "US",
                "currency": "EUR",
                "only_profit_kzt": "10000",
                "tax_kzt": "0",
                "withhold_kzt": "-1000",
            },
        ]

        merged = aggregate_years_results(rows)

        taxable = next(row for row in merged if row["flag"] == "non-preferential")
        preferential = next(row for row in merged if row["flag"] == "preferential")
        self.assertEqual(Decimal(taxable["tax_kzt"]), Decimal("0"))
        self.assertEqual(Decimal(taxable["tax_kzt_withhold"]), Decimal("0"))
        self.assertEqual(Decimal(preferential["tax_kzt_withhold"]), Decimal("0"))

    def test_merge_recalculates_trade_tax_after_account_losses_offset_profits(self) -> None:
        dimensions = {
            "table": "Yearly Trades",
            "year": 2025,
            "flag": "non-preferential",
            "country": "US",
            "tax_exchange": "outofKZ",
            "currency": "USD",
        }

        merged = aggregate_years_results(
            [
                {**dimensions, "pnl": "-8797.44", "pnl_kzt": "-4588657.77", "tax_kzt": "0"},
                {**dimensions, "pnl": "3537.23", "pnl_kzt": "1844984.84", "tax_kzt": "184498.48"},
            ]
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(Decimal(merged[0]["pnl_kzt"]), Decimal("-2743672.93"))
        self.assertEqual(Decimal(merged[0]["tax_kzt"]), Decimal("0"))
        self.assertEqual(Decimal(merged[0]["tax_kzt_withhold"]), Decimal("0"))

    def test_trade_losses_reduce_profitable_rows_in_the_same_form270_bucket(self) -> None:
        common = {
            "table": "Yearly Trades",
            "year": 2025,
            "flag": "non-preferential",
            "tax_exchange": "outofKZ",
            "currency": "USD",
            "withhold_kzt": "0",
        }

        merged = aggregate_years_results(
            [
                {**common, "country": "US", "pnl_kzt": "-1000", "tax_kzt": "0"},
                {**common, "country": "CA", "pnl_kzt": "1600", "tax_kzt": "160"},
            ]
        )
        by_country = {row["country"]: row for row in merged}

        self.assertEqual(Decimal(by_country["US"]["tax_kzt"]), Decimal("0"))
        self.assertEqual(Decimal(by_country["CA"]["tax_kzt"]), Decimal("60"))
        self.assertEqual(sum(Decimal(row["tax_kzt"]) for row in merged), Decimal("60"))

    def test_dividend_withholding_is_recalculated_after_pooling_accounts_by_country(self) -> None:
        common = {
            "table": "Yearly Dividends",
            "year": 2025,
            "flag": "non-preferential",
            "currency": "USD",
        }

        merged = aggregate_years_results(
            [
                {**common, "country": "US", "amount_kzt": "1000", "withhold_kzt": "-150"},
                {**common, "country": "US", "amount_kzt": "500", "withhold_kzt": "-75"},
                {**common, "country": "CA", "amount_kzt": "1000", "withhold_kzt": "-50"},
            ]
        )
        by_country = {row["country"]: row for row in merged}

        self.assertEqual(Decimal(by_country["US"]["tax_kzt"]), Decimal("150"))
        self.assertEqual(Decimal(by_country["US"]["tax_kzt_withhold"]), Decimal("0"))
        self.assertEqual(Decimal(by_country["CA"]["tax_kzt"]), Decimal("100"))
        self.assertEqual(Decimal(by_country["CA"]["tax_kzt_withhold"]), Decimal("50"))

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
                "tax_exchange": "KASE",
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
                "tax_exchange": "KASE",
                "currency": "USD",
                "pnl": "20",
                "pnl_kzt": "9400",
                "tax_kzt": "0",
            },
            {
                "table": "Yearly Trades",
                "year": 2024,
                "flag": "preferential",
                "tax_exchange": "AIX",
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
        kase = next(row for row in yearly if row["table"] == "Yearly Trades" and row["tax_exchange"] == "KASE")
        aix = next(row for row in yearly if row["table"] == "Yearly Trades" and row["tax_exchange"] == "AIX")
        dividends = next(row for row in yearly if row["table"] == "Yearly Dividends")
        self.assertEqual(Decimal(str(kase["pnl_kzt"])), Decimal("14100"))
        self.assertEqual(Decimal(str(aix["pnl_kzt"])), Decimal("14100"))
        self.assertEqual(Decimal(str(dividends["amount"])), Decimal("12"))
        self.assertEqual(Decimal(str(dividends["amount_kzt"])), Decimal("5640"))
        self.assertEqual(Decimal(str(dividends["withhold_kzt"])), Decimal("-846"))


if __name__ == "__main__":
    unittest.main()
