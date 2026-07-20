from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from conftest_imports import SRC  # noqa: F401
from kztax270.canonical.schema import CanonicalDataset
from kztax270.excel.audit_workbook import ExcelAuditWorkbookWriter
from kztax270.excel.joint_workbook import create_joint_audit_workbook, joint_workbook_path
from kztax270.form270.json_builder import load_processed_workbook_tables


class JointWorkbookTests(unittest.TestCase):
    def test_joint_workbook_scales_ownership_values_and_recalculates_yearly_results(self) -> None:
        dataset = CanonicalDataset.empty("ib", "U1")
        dataset.tables["Instruments"] = [
            {"symbol": "AAA", "strike": "120", "multiplier": "100", "isin": "US0000000001"}
        ]
        dataset.tables["Trades"] = [
            {
                "date_time": "2024-01-01",
                "trade_id": "T1",
                "symbol": "AAA",
                "quantity": "3",
                "price": "100",
                "multiplier": "1",
                "amount": "300",
                "commission": "-3",
                "amount_with_commission": "297",
                "currency": "USD",
            }
        ]
        dataset.tables["Fifo"] = [
            {
                "symbol": "AAA",
                "enter_quantity": "3",
                "enter_price": "100",
                "enter_amount": "300",
                "exit_quantity": "3",
                "exit_price": "120",
                "exit_amount": "360",
                "pnl": "60",
                "pnl_kzt": "27000",
            }
        ]
        dataset.tables["Dividends"] = [
            {
                "date": "2024-06-01",
                "symbol": "AAA",
                "currency": "USD",
                "gross_amount": "101",
                "withholding_tax": "-15.15",
                "net_amount": "85.85",
                "kzt_rate": "450",
                "gross_amount_kzt": "45450",
                "tax_kzt": "4545",
            }
        ]
        dataset.tables["CashBalances"] = [
            {"year": 2024, "currency": "USD", "ending_cash": "25", "ending_cash_kzt": "11250"}
        ]
        dataset.tables["Years_Results"] = [
            {
                "table": "Yearly Dividends",
                "year": 2024,
                "flag": "non-preferential",
                "country": "US",
                "currency": "USD",
                "amount": "100",
                "amount_kzt": "45000",
                "withhold_kzt": "-4500",
                "tax_kzt": "4500",
                "tax_kzt_withhold": "0",
            }
        ]
        dataset.tables["Reconciliation"] = [
            {
                "metric": "cash",
                "broker_value": "100",
                "canonical_value": "90",
                "difference": "10",
                "tolerance": "1",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "ib_U1_audit.xlsx"
            ExcelAuditWorkbookWriter().write(dataset, source)
            output = create_joint_audit_workbook(source)
            tables = load_processed_workbook_tables(output)

        self.assertEqual(output.name, "ib_U1_joint_audit.xlsx")
        self.assertEqual(Decimal(str(tables["Instruments"][0]["strike"])), Decimal("120"))
        self.assertEqual(Decimal(str(tables["Trades"][0]["quantity"])), Decimal("1.5"))
        self.assertEqual(Decimal(str(tables["Trades"][0]["price"])), Decimal("100"))
        self.assertEqual(Decimal(str(tables["Trades"][0]["amount"])), Decimal("150"))
        self.assertEqual(Decimal(str(tables["Fifo"][0]["pnl_kzt"])), Decimal("13500"))
        self.assertEqual(Decimal(str(tables["Dividends"][0]["gross_amount"])), Decimal("50.5"))
        self.assertEqual(
            Decimal(str(tables["Dividends"][0]["withholding_tax"])),
            Decimal("-7.575"),
        )
        self.assertEqual(Decimal(str(tables["CashBalances"][0]["ending_cash"])), Decimal("12.5"))

        yearly = tables["Years_Results"][0]
        self.assertEqual(Decimal(str(yearly["amount_kzt"])), Decimal("22500"))
        self.assertEqual(Decimal(str(yearly["withhold_kzt"])), Decimal("-2250"))
        self.assertEqual(Decimal(str(yearly["tax_kzt"])), Decimal("2250"))
        self.assertEqual(Decimal(str(yearly["tax_kzt_withhold"])), Decimal("0"))

        reconciliation = tables["Reconciliation"][0]
        self.assertEqual(Decimal(str(reconciliation["broker_value"])), Decimal("50"))
        self.assertEqual(Decimal(str(reconciliation["difference"])), Decimal("5"))
        self.assertEqual(Decimal(str(reconciliation["tolerance"])), Decimal("1"))

    def test_joint_workbook_name_helpers_and_double_split_guard(self) -> None:
        self.assertEqual(
            joint_workbook_path(Path("ib_U1_audit.xlsx")).name,
            "ib_U1_joint_audit.xlsx",
        )
        with tempfile.TemporaryDirectory() as tmp:
            already_joint = Path(tmp) / "ib_U1_joint_audit.xlsx"
            already_joint.touch()
            with self.assertRaisesRegex(ValueError, "already a joint workbook"):
                create_joint_audit_workbook(already_joint)


if __name__ == "__main__":
    unittest.main()
