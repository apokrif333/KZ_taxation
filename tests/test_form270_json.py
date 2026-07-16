from __future__ import annotations

from decimal import Decimal
import tempfile
import unittest
from pathlib import Path

from conftest_imports import ROOT, SRC  # noqa: F401
from kztax270.canonical.schema import CanonicalDataset
from kztax270.config import load_form270_run_config
from kztax270.form270.json_builder import (
    COUNTRY_CODES_FILE,
    CURRENCY_CODES_FILE,
    Form270JsonBuilder,
    Form270Owner,
    ASSET_TYPES_FILE,
    TRADES_TYPES_FILE,
    _country_code_for_form,
    _currency_code_for_form,
    _reference_codes,
    _trade_type_code_for_form,
)
from kztax270.form270.merge import merge_form270_jsons
from kztax270.form270.split import split_form270_json


class Form270JsonTests(unittest.TestCase):
    def test_builder_fills_application_01_from_years_results(self) -> None:
        dataset = CanonicalDataset.empty("ib", "UTEST")
        dataset.tables["Years_Results"] = [
            {
                "table": "Yearly Trades",
                "year": 2024,
                "flag": "non-preferential",
                "currency": "USD",
                "pnl_kzt": "1000",
                "tax_kzt": "100",
                "tax_kzt_withhold": "100",
            },
            {
                "table": "Yearly Trades",
                "year": 2024,
                "flag": "offshore",
                "currency": "USD",
                "pnl_kzt": "3000",
                "tax_kzt": "300",
                "tax_kzt_withhold": "300",
            },
            {
                "table": "Yearly Trades",
                "year": 2024,
                "flag": "preferential",
                "currency": "USD",
                "pnl_kzt": "2000",
                "tax_kzt": "0",
                "tax_kzt_withhold": "0",
            },
            {
                "table": "Yearly Dividends",
                "year": 2024,
                "flag": "non-preferential",
                "currency": "USD",
                "amount_kzt": "500",
                "tax_kzt": "50",
                "tax_kzt_withhold": "20",
            },
            {
                "table": "Yearly Interest",
                "year": 2024,
                "flag": "non-preferential",
                "currency": "USD",
                "only_profit_kzt": "300",
                "tax_kzt": "30",
            },
            {
                "table": "Yearly Coupons",
                "year": 2024,
                "flag": "non-preferential",
                "currency": "USD",
                "amount_kzt": "400",
                "tax_kzt": "0",
                "tax_kzt_withhold": "0",
            },
            {
                "table": "Yearly Bonds Redemption",
                "year": 2024,
                "flag": "non-preferential",
                "currency": "USD",
                "pnl_kzt": "800",
                "tax_kzt": "0",
            },
            {
                "table": "Yearly Derivatives",
                "year": 2024,
                "flag": "non-preferential",
                "exchange": "outofKZ",
                "currency": "USD",
                "pnl_kzt": "-100",
                "only_profit_kzt": "600",
                "tax_kzt": "60",
            },
        ]

        form = _builder().build_account_draft(
            dataset,
            tax_year=2024,
            taxpayer=Form270Owner("Test", "Owner", "", "000000000001"),
        )

        app = form["fnoContent"]["application_01"]
        self.assertEqual(app["A"]["_01"], 2000)
        self.assertEqual(app["A"]["_02"], 4000)
        self.assertEqual(app["A"]["_A"], 6000)
        self.assertEqual(app["B"]["_04"], 500)
        self.assertEqual(app["B"]["_05"], 1500)
        self.assertEqual(app["B"]["_09"], 600)
        self.assertEqual(app["_D"], 8600)
        self.assertEqual(app["E"]["_E"], 3200)
        self.assertEqual(app["_G"], 5400)
        self.assertEqual(app["_H"], 540)
        self.assertEqual(app["_I"], 30)
        self.assertEqual(app["_K"], 510)
        self.assertEqual(form["taxpayerCode"], "000000000001")
        self.assertEqual(form["taxpayerNameRu"], "TEST OWNER")
        self.assertIsNone(form["periodValue"])
        self.assertEqual(form["fnoYear"], 2024)
        self.assertEqual(form["fnoContent"]["commonInfo"]["selectedApplications"], ["application_01"])

    def test_builder_fills_application_04_from_trades_cash_and_positions(self) -> None:
        dataset = _dataset_with_application_04_rows()

        form = _builder().build_account_draft(
            dataset,
            tax_year=2024,
            taxpayer=Form270Owner("Test", "Owner", "", "000000000001"),
        )

        app = form["fnoContent"]["application_04"]
        self.assertEqual(form["fnoContent"]["commonInfo"]["selectedApplications"], ["application_04"])
        self.assertEqual(
            [row["F"] for row in app["B"]],
            ["02.01.2024", "05.01.2024", "06.01.2024", "07.01.2024", "08.01.2024", "09.01.2024", "10.01.2024", "11.01.2024"],
        )
        trades_by_identifier = {row["E"]: row for row in app["B"]}
        self.assertEqual(trades_by_identifier["US0000000001"]["B"], "1")
        self.assertEqual(trades_by_identifier["US0000000001"]["C"], "3")
        self.assertEqual(trades_by_identifier["US0000000001"]["D"], 12)
        self.assertEqual(trades_by_identifier["US0000000001"]["F"], "02.01.2024")
        self.assertEqual(trades_by_identifier["US0000000001"]["G"], "-")
        self.assertEqual(trades_by_identifier["US0000000001"]["H"], "USA")
        self.assertEqual(trades_by_identifier["US0000000001"]["I"], "USD")
        self.assertIn(trades_by_identifier["US0000000001"]["H"], _reference_codes(COUNTRY_CODES_FILE))
        self.assertIn(trades_by_identifier["US0000000001"]["I"], _reference_codes(CURRENCY_CODES_FILE))
        self.assertEqual(trades_by_identifier["US0000000001"]["val_J"], {"value": 120, "manual": True})
        self.assertNotIn("EUR.USD", trades_by_identifier)
        self.assertEqual(trades_by_identifier["SPY 19JAN24 100 C"]["B"], "4")
        self.assertEqual(trades_by_identifier["SPY 19JAN24 100 C"]["C"], "4")
        self.assertEqual(trades_by_identifier["EUR/AUD"]["B"], "4")
        self.assertEqual(trades_by_identifier["EUR/AUD"]["C"], "4")
        self.assertEqual(trades_by_identifier["EUR/AUD"]["H"], "CYP")
        self.assertEqual(trades_by_identifier["MES"]["B"], "1")
        self.assertEqual(trades_by_identifier["MES"]["C"], "4")
        self.assertIn(trades_by_identifier["MES"]["C"], _reference_codes(ASSET_TYPES_FILE))
        self.assertEqual(trades_by_identifier["US0000000002"]["B"], "6")
        self.assertEqual(trades_by_identifier["US0000000003"]["B"], "2")
        self.assertEqual(trades_by_identifier["US0000000004"]["B"], "13")
        self.assertEqual(trades_by_identifier["US0000000004"]["_01"], "Погашение")
        self.assertEqual(trades_by_identifier["US0000000004"]["val_J"], {"value": 1000, "manual": True})
        self.assertEqual(trades_by_identifier["US0000000005"]["B"], "4")
        self.assertIsNone(trades_by_identifier["US0000000005"]["_01"])
        self.assertEqual(len(app["C"]), 1)
        self.assertEqual(app["C"][0]["A"], "00000001")
        self.assertEqual(app["C"][0]["B"], "IBKRUS33XXX")
        self.assertEqual(app["C"][0]["E"], "USD")
        self.assertEqual(app["C"][0]["F"], 123)
        self.assertEqual(app["E"][0]["C"], "US0000000001")
        self.assertEqual(app["E"][0]["D"], "USA")
        self.assertEqual(app["E"][0]["E"], "США")

    def test_builder_places_exchange_preferential_dividends_in_e1_and_e4(self) -> None:
        dataset = CanonicalDataset.empty("ib", "UPREF")
        dataset.tables["Years_Results"] = [
            {
                "table": "Yearly Dividends",
                "year": 2024,
                "flag": "non-preferential",
                "amount_kzt": "1000",
                "tax_kzt": "100",
                "tax_kzt_withhold": "100",
            },
            {
                "table": "Yearly Dividends",
                "year": 2024,
                "flag": "preferential_kase",
                "amount_kzt": "2000",
                "tax_kzt": "0",
                "tax_kzt_withhold": "0",
            },
            {
                "table": "Yearly Dividends",
                "year": 2024,
                "flag": "preferential_aix",
                "amount_kzt": "3000",
                "tax_kzt": "0",
                "tax_kzt_withhold": "0",
            },
        ]

        form = _builder().build_account_draft(dataset, tax_year=2024)
        app = form["fnoContent"]["application_01"]

        self.assertEqual(app["B"]["_04"], 6000)
        self.assertEqual(app["E"]["_E1"], 2000)
        self.assertEqual(app["E"]["_E4"], 3000)
        self.assertEqual(app["E"]["_E"], 5000)
        self.assertEqual(app["_G"], 1000)
        self.assertEqual(app["_H"], 100)

    def test_builder_places_preferential_trades_by_exchange_in_e1_and_e4(self) -> None:
        dataset = CanonicalDataset.empty("freedom", "7A3453")
        dataset.tables["Years_Results"] = [
            {
                "table": "Yearly Trades",
                "year": 2023,
                "flag": "preferential",
                "exchange": "KASE",
                "pnl_kzt": "2000",
            },
            {
                "table": "Yearly Trades",
                "year": 2023,
                "flag": "preferential",
                "exchange": "AIX",
                "pnl_kzt": "3000",
            },
            {
                "table": "Yearly Trades",
                "year": 2023,
                "flag": "non-preferential",
                "exchange": "outofKZ",
                "pnl_kzt": "1000",
            },
        ]

        form = _builder().build_account_draft(dataset, tax_year=2023)
        app = form["fnoContent"]["application_01"]

        self.assertEqual(app["A"]["_A"], 6000)
        self.assertEqual(app["E"]["_E1"], 2000)
        self.assertEqual(app["E"]["_E4"], 3000)
        self.assertEqual(app["E"]["_E"], 5000)
        self.assertEqual(app["_G"], 1000)

    def test_foreign_tax_credit_groups_dividends_by_country_across_preferential_flags(self) -> None:
        dataset = CanonicalDataset.empty("exante", "HXR2208.001")
        dataset.tables["Years_Results"] = [
            {
                "table": "Yearly Dividends",
                "year": 2023,
                "flag": "non-preferential",
                "country": "US",
                "amount_kzt": "407644.54",
                "withhold_kzt": "-122332.15",
                "tax_kzt": "40764.45",
                "tax_kzt_withhold": "0",
            },
            {
                "table": "Yearly Dividends",
                "year": 2023,
                "flag": "preferential_kase",
                "country": "USA",
                "amount_kzt": "1373.49",
                "withhold_kzt": "-419.81",
                "tax_kzt": "0",
                "tax_kzt_withhold": "0",
            },
            {
                "table": "Yearly Dividends",
                "year": 2023,
                "flag": "non-preferential",
                "country": "TW",
                "amount_kzt": "19192.40",
                "withhold_kzt": "-4033.78",
                "tax_kzt": "1919.24",
                "tax_kzt_withhold": "0",
            },
        ]

        form = _builder().build_account_draft(dataset, tax_year=2023)
        app = form["fnoContent"]["application_01"]

        self.assertEqual(app["E"]["_E1"], 1373)
        self.assertEqual(app["_I"], 42821)

    def test_builder_split_halves_amounts_and_sets_second_owner_iin(self) -> None:
        dataset = _dataset_with_application_04_rows()
        dataset.tables["Years_Results"] = [
            {
                "table": "Yearly Dividends",
                "year": 2024,
                "flag": "non-preferential",
                "currency": "USD",
                "amount_kzt": "1000",
                "tax_kzt": "100",
                "tax_kzt_withhold": "0",
            }
        ]

        form = _builder().build_account_draft(
            dataset,
            tax_year=2024,
            taxpayer={
                "fio1": "Owner",
                "fio2": "One",
                "iin": "000000000011",
                "spouse_iin": "000000000012",
            },
            split=True,
        )

        self.assertEqual(form["taxpayerCode"], "000000000011")
        self.assertEqual(form["fnoContent"]["commonInfo"]["_6"], "000000000012")
        self.assertEqual(form["fnoContent"]["application_01"]["_D"], 500)
        self.assertEqual(form["fnoContent"]["application_01"]["_I"], 50)
        split_trades = {row["E"]: row for row in form["fnoContent"]["application_04"]["B"]}
        self.assertEqual(split_trades["US0000000001"]["D"], 6)
        self.assertEqual(split_trades["US0000000001"]["val_J"], {"value": 60, "manual": True})
        self.assertEqual(form["fnoContent"]["application_04"]["C"][0]["F"], 62)

    def test_form270_run_config_loads_forms_and_banks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "form270.toml"
            path.write_text(
                """
[paths]
processed_data = "data/processed"
output_data = "data/output"
form270_template = "data/templates/270 new template.json"

[form270]
tax_year = 2024
phone = "+7"

[form270.banks.ib]
code = "IBKRUS33XXX"
name = "Interactive Brokers LLC"
country = "USA"

[[form270.forms]]
broker = "ib"
account_id = "UTEST"
joint_account = true
fio1 = "Owner"
fio2 = "One"
iin = "000000000001"
second_fio1 = "Owner"
second_fio2 = "Two"
second_iin = "000000000002"
""",
                encoding="utf-8",
            )

            config = load_form270_run_config(path)

        self.assertEqual(config.defaults.tax_year, 2024)
        self.assertEqual(config.defaults.phone, "+7")
        self.assertEqual(config.banks["ib"].code, "IBKRUS33XXX")
        self.assertEqual(config.forms[0].account_id, "UTEST")
        self.assertTrue(config.forms[0].joint_account)
        self.assertEqual(config.forms[0].second_owner.iin, "000000000002")

    def test_reference_dictionaries_normalize_form_codes(self) -> None:
        self.assertEqual(_country_code_for_form("US"), "USA")
        self.assertEqual(_country_code_for_form("США"), "USA")
        self.assertEqual(_country_code_for_form("Cyprus"), "CYP")
        self.assertEqual(_country_code_for_form("Russia"), "RUS")
        self.assertEqual(_country_code_for_form("Kazakhstan"), "KAZ")
        self.assertEqual(_currency_code_for_form("Доллар США"), "USD")
        self.assertEqual(_trade_type_code_for_form("Покупка"), "1")
        self.assertEqual(_trade_type_code_for_form("Приобретено путем обмена"), "2")
        self.assertEqual(_trade_type_code_for_form("Продажа"), "4")
        self.assertIn(_trade_type_code_for_form("Продажа"), _reference_codes(TRADES_TYPES_FILE))

    def test_merge_concatenates_lists_and_keeps_existing_scalar(self) -> None:
        merged = merge_form270_jsons(
            [
                {"fnoYear": 2024, "rows": [{"amount": "10"}], "taxpayerCode": "111"},
                {"fnoYear": 2024, "rows": [{"amount": "20"}], "taxpayerCode": "222"},
            ]
        )
        self.assertEqual(merged["taxpayerCode"], "111")
        self.assertEqual(merged["rows"], [{"amount": "10"}, {"amount": "20"}])
        self.assertEqual(merged["_kztax270"]["status"], "merged_draft")

    def test_split_scales_amount_like_keys(self) -> None:
        split = split_form270_json(
            {"rows": [{"gross_amount": "100", "symbol": "AAPL"}]},
            {"a": Decimal("0.25"), "b": Decimal("0.75")},
        )
        self.assertEqual(split["a"]["rows"][0]["gross_amount"], "25.00")
        self.assertEqual(split["b"]["rows"][0]["symbol"], "AAPL")


def _builder() -> Form270JsonBuilder:
    return Form270JsonBuilder(ROOT / "data" / "templates" / "270 new template.json")


def _dataset_with_application_04_rows() -> CanonicalDataset:
    dataset = CanonicalDataset.empty("ib", "UTEST")
    dataset.tables["Trades"] = [
        {
            "date_time": "2024-01-02 10:00:00",
            "symbol": "AAA",
            "isin": "US0000000001",
            "asset_type": "Stocks",
            "quantity": "10",
            "amount": "100",
            "amount_with_commission": "101",
            "currency": "USD",
            "country": "US",
        },
        {
            "date_time": "2024-01-02 15:00:00",
            "symbol": "AAA",
            "isin": "US0000000001",
            "asset_type": "Stocks",
            "quantity": "2",
            "amount": "20",
            "amount_with_commission": "20",
            "currency": "USD",
            "country": "US",
        },
        {
            "date_time": "2024-01-05 10:00:00",
            "symbol": "SPY 19JAN24 100 C",
            "asset_type": "Equity and Index Options",
            "quantity": "-3",
            "amount": "30",
            "amount_with_commission": "30",
            "currency": "USD",
            "country": "US",
        },
        {
            "date_time": "2024-01-06 10:00:00",
            "symbol": "SPIN",
            "isin": "US0000000002",
            "asset_type": "Stocks",
            "quantity": "5",
            "amount": "0",
            "amount_with_commission": "0",
            "currency": "USD",
            "country": "US",
            "trade_type": "corporate_action:spinoff",
        },
        {
            "date_time": "2024-01-07 10:00:00",
            "symbol": "MERGER",
            "isin": "US0000000003",
            "asset_type": "Stocks",
            "quantity": "7",
            "amount": "0",
            "amount_with_commission": "0",
            "currency": "USD",
            "country": "US",
            "trade_type": "corporate_action:merger",
        },
        {
            "date_time": "2024-01-08 10:00:00",
            "symbol": "BOND",
            "isin": "US0000000004",
            "asset_type": "Bonds",
            "quantity": "-1",
            "amount": "1000",
            "amount_with_commission": "1000",
            "currency": "USD",
            "country": "US",
            "trade_type": "corporate_action:full_call",
        },
        {
            "date_time": "2024-01-03 10:00:00",
            "symbol": "KZ",
            "isin": "KZ0000000001",
            "asset_type": "Stocks",
            "quantity": "10",
            "amount": "100",
            "amount_with_commission": "100",
            "currency": "KZT",
            "country": "KZ",
        },
        {
            "date_time": "2024-01-04 10:00:00",
            "symbol": "EUR.USD",
            "asset_type": "Forex",
            "quantity": "100",
            "amount": "100",
            "amount_with_commission": "100",
            "currency": "USD",
            "country": "USA",
        },
        {
            "date_time": "2024-01-09 10:00:00",
            "symbol": "EUR/AUD",
            "asset_type": "FX Spot",
            "quantity": "-1000",
            "amount": "1500",
            "amount_with_commission": "1500",
            "currency": "AUD",
            "country": "Cyprus",
        },
        {
            "date_time": "2024-01-10 10:00:00",
            "symbol": "MES",
            "asset_type": "Futures",
            "quantity": "1",
            "amount": "100",
            "amount_with_commission": "100",
            "currency": "USD",
            "country": "US",
        },
        {
            "date_time": "2024-01-11 10:00:00",
            "symbol": "STOCKRED",
            "isin": "US0000000005",
            "asset_type": "Stocks",
            "quantity": "-4",
            "amount": "40",
            "amount_with_commission": "40",
            "currency": "USD",
            "country": "US",
            "trade_type": "corporate_action:redemption",
        },
    ]
    dataset.tables["CashBalances"] = [
        {"year": 2024, "currency": "USD", "ending_cash": "123.45", "ending_cash_kzt": "57960.17"},
        {"year": 2024, "currency": "EUR", "ending_cash": "0", "ending_cash_kzt": "0"},
        {"year": 2024, "currency": "RUB", "ending_cash": "0.49", "ending_cash_kzt": "2.51"},
        {"year": 2024, "currency": "CHF", "ending_cash": "1.49", "ending_cash_kzt": "800.00"},
    ]
    dataset.tables["Positions"] = [
        {
            "year": 2024,
            "asset_type": "Stocks",
            "symbol": "AAA",
            "isin": "US0000000001",
            "country": "US",
            "quantity": "10",
        },
        {
            "year": 2024,
            "asset_type": "Stocks",
            "symbol": "KZ",
            "isin": "KZ0000000001",
            "country": "KZ",
            "quantity": "10",
        },
    ]
    return dataset


if __name__ == "__main__":
    unittest.main()
