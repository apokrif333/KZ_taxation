from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from conftest_imports import SRC  # noqa: F401
from kztax270.brokers import tabys as tabys_module
from kztax270.brokers.registry import default_registry
from kztax270.brokers.tabys import ParsedTabysReport, TabysParser, build_canonical_dataset
from kztax270.canonical.validation import validate_dataset_for_tax_forms
from kztax270.reconciliation.engine import ReconciliationEngine
from kztax270.reconciliation.models import ReconciliationSeverity
from kztax270.reference.fx import AnnualFxRateProvider
from kztax270.reference.securities import AIX_COLUMNS, AIX_PROFILE_API_URL, AixInstrumentResolver


class TabysParserTests(unittest.TestCase):
    def test_operation_table_row_is_normalized(self) -> None:
        row = tabys_module._parse_operation_row(
            [
                "2",
                "19.02.2025 12:37",
                "19.02.2025 12:37",
                "INS000068\n254",
                "Ценные\nбумаги",
                "Перевод ценных\nбумаг",
                "SOLV3.0526",
                "100",
                "98.22",
                "9822",
                "USD",
                "",
                "",
                "Исполнен",
                "0",
            ],
            source_report="tabys.pdf",
            source_page=1,
            source_row=3,
        )

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["transaction_datetime"], "2025-02-19 12:37:00")
        self.assertEqual(row["transaction_id"], "INS000068254")
        self.assertEqual(row["account_type"], "Ценные бумаги")
        self.assertEqual(row["operation"], "Перевод ценных бумаг")
        self.assertEqual(row["quantity"], "100")

    def test_purchase_coupons_and_outgoing_transfer_build_canonical_history(self) -> None:
        reports = [
            ParsedTabysReport(
                path=Path("015727293 2024.pdf"),
                account_id="015727293",
                period_start=date(2024, 1, 1),
                period_end=date(2024, 12, 31),
                rows=[
                    _row(
                        sequence=1,
                        transaction_datetime="2024-05-28 23:19:00",
                        settlement_datetime="2024-05-29 16:10:00",
                        transaction_id="19612",
                        account_type="Ценные бумаги",
                        operation="Покупка",
                        security="SOLV3.0526",
                        quantity="100",
                        price="100.06",
                        amount="10006",
                        currency="USD",
                        exchange_rate="445",
                        amount_kzt="4452820",
                        commission_kzt="150",
                        source_report="015727293 2024.pdf",
                    ),
                    _row(
                        sequence=2,
                        transaction_datetime="2024-07-05 16:35:00",
                        transaction_id="coupon-2024",
                        account_type="Денежный счет",
                        operation="Получение дивидендов или купонов",
                        security="SOLV3.0526",
                        amount="87.5",
                        currency="USD",
                        source_report="015727293 2024.pdf",
                    ),
                ],
            ),
            ParsedTabysReport(
                path=Path("015727293 2025.pdf"),
                account_id="015727293",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                rows=[
                    _row(
                        sequence=1,
                        transaction_datetime="2025-01-05 17:46:00",
                        transaction_id="coupon-2025",
                        account_type="Денежный счет",
                        operation="Получение дивидендов или купонов",
                        security="SOLV3.0526",
                        amount="87.5",
                        currency="USD",
                        source_report="015727293 2025.pdf",
                    ),
                    _row(
                        sequence=2,
                        transaction_datetime="2025-02-19 12:37:00",
                        transaction_id="INS000068254",
                        account_type="Ценные бумаги",
                        operation="Перевод ценных бумаг",
                        security="SOLV3.0526",
                        quantity="100",
                        price="98.22",
                        amount="9822",
                        currency="USD",
                        source_report="015727293 2025.pdf",
                    ),
                    _row(
                        sequence=3,
                        transaction_datetime="2025-02-26 15:23:00",
                        settlement_datetime="2025-02-26 17:15:00",
                        transaction_id="cash-out",
                        account_type="Денежный счет",
                        operation="Вывод средств",
                        security="USD",
                        amount="87.5",
                        currency="USD",
                        exchange_rate="493",
                        amount_kzt="43137.5",
                        commission_kzt="0.44",
                        source_report="015727293 2025.pdf",
                    ),
                ],
            ),
        ]
        provider = AnnualFxRateProvider({(2024, "USD"): Decimal("469.44"), (2025, "USD"): Decimal("520")})

        with tempfile.TemporaryDirectory() as tmp:
            resolver = _local_solv_resolver(Path(tmp) / "aix_instruments.xlsx")
            dataset = build_canonical_dataset(
                reports,
                "015727293",
                provider,
                instrument_resolver=resolver,
            )
        validate_dataset_for_tax_forms(dataset)

        instrument = dataset.tables["Instruments"][0]
        self.assertEqual(instrument["isin"], "KZX000002241")
        self.assertEqual(instrument["type"], "Bonds")
        trade = dataset.tables["Trades"][0]
        self.assertEqual(trade["amount"], "10006")
        self.assertEqual(Decimal(trade["commission"]), Decimal("150") / Decimal("445"))
        position = dataset.tables["Positions"][0]
        self.assertEqual(position["year"], 2024)
        self.assertEqual(position["quantity"], "100")
        self.assertEqual(position["price"], "100.06")
        self.assertFalse(any(row["year"] == 2025 for row in dataset.tables["Positions"]))

        security_transfer = next(row for row in dataset.tables["Transfers"] if row["transfer_type"] == "security")
        self.assertEqual(security_transfer["direction"], "out")
        self.assertEqual(security_transfer["quantity"], "100")
        self.assertEqual(security_transfer["enter_date"], "2024-05-28 23:19:00")
        self.assertEqual(security_transfer["price"], "100.06337079")
        cash_transfer = next(row for row in dataset.tables["Transfers"] if row["transfer_type"] == "cash")
        self.assertEqual(cash_transfer["amount"], "-87.5")
        self.assertIn("комиссия 0.44 KZT", cash_transfer["broker_comment"])

        self.assertEqual([row["gross_amount"] for row in dataset.tables["Coupons"]], ["87.50", "87.50"])
        self.assertTrue(all(row["withholding_tax"] == "0.00" for row in dataset.tables["Coupons"]))
        yearly_coupons = [row for row in dataset.tables["Years_Results"] if row["table"] == "Yearly Coupons"]
        self.assertEqual([row["amount"] for row in yearly_coupons], ["87.50", "87.50"])
        self.assertTrue(all(row["only_profit"] == "0.00" for row in yearly_coupons))
        self.assertTrue(all(row["only_profit_kzt"] == "0.00" for row in yearly_coupons))
        self.assertEqual(dataset.tables["Unprocessed"], [])
        errors = [
            row
            for row in ReconciliationEngine().reconcile_dataset(dataset)
            if row.severity == ReconciliationSeverity.ERROR
        ]
        self.assertEqual(errors, [])

    def test_instrument_resolution_uses_local_aix_workbook_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resolver = _local_solv_resolver(Path(tmp) / "aix_instruments.xlsx")
            with patch("kztax270.reference.securities._requests") as requests_factory:
                instrument = resolver.resolve("SOLV3.0526")

        requests_factory.assert_not_called()
        self.assertEqual(instrument["isin"], "KZX000002241")
        self.assertEqual(instrument["type"], "Bonds")

    def test_missing_instrument_uses_aix_profile_and_is_cached_locally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            aix_path = Path(tmp) / "aix_instruments.xlsx"
            cache_path = Path(tmp) / "tabys_instruments.xlsx"
            resolver = AixInstrumentResolver(aix_path=aix_path, profile_cache_path=cache_path)
            profile = {
                "secCode": "SOLV3.0526",
                "shortName": "SOLV",
                "securityName": "Coupon bonds of Solva Group Ltd",
                "issuer": "Solva Group Limited",
                "instrument": "Debt",
                "securityGroup": "Debt",
                "currency": "USD",
                "country": "Kazakhstan",
                "isin": "KZX000002241",
                "maturityDate": "2026-05-27T00:00:00",
                "faceValue": 100,
                "couponRate": 10.5,
                "couponFreq": 12,
            }
            with patch("kztax270.reference.securities._requests") as requests_factory:
                response = requests_factory.return_value.get.return_value
                response.json.return_value = profile
                instrument = resolver.resolve("SOLV3.0526", snapshot_year=2025)

            requests_factory.return_value.get.assert_called_once_with(
                AIX_PROFILE_API_URL.format(ticker="SOLV3.0526"),
                timeout=30,
            )
            response.raise_for_status.assert_called_once_with()
            self.assertEqual(instrument["isin"], "KZX000002241")
            self.assertEqual(instrument["type"], "Bonds")
            self.assertEqual(instrument["country"], "KZ")

            self.assertFalse(aix_path.exists())
            cached = pd.read_excel(cache_path, engine="openpyxl")
            self.assertEqual(cached.loc[0, "secCode"], "SOLV3.0526")
            with patch("kztax270.reference.securities._requests") as second_requests_factory:
                cached_instrument = AixInstrumentResolver(
                    aix_path=aix_path,
                    profile_cache_path=cache_path,
                ).resolve("SOLV3.0526")
            second_requests_factory.assert_not_called()
            self.assertEqual(cached_instrument["isin"], "KZX000002241")

    def test_discovery_and_registry_expose_tabys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp)
            broker_root = raw_root / "tabys"
            broker_root.mkdir()
            matching = broker_root / "015727293 2024.pdf"
            matching.touch()
            (broker_root / "other 2024.pdf").touch()

            reports = TabysParser().discover_reports(raw_root, "015727293")

        self.assertEqual([report.path.name for report in reports], [matching.name])
        self.assertIn("tabys", default_registry().broker_codes())


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "sequence": 1,
        "transaction_datetime": None,
        "settlement_datetime": None,
        "transaction_id": None,
        "account_type": None,
        "operation": None,
        "security": None,
        "quantity": "0",
        "price": "0",
        "amount": "0",
        "currency": None,
        "exchange_rate": "0",
        "amount_kzt": "0",
        "status": "Исполнен",
        "commission_kzt": "0",
        "source_report": None,
        "source_page": 1,
        "source_row": 1,
    }
    row.update(overrides)
    return row


def _local_solv_resolver(path: Path) -> AixInstrumentResolver:
    record = {column: None for column in AIX_COLUMNS}
    record.update(
        {
            "year": 2025,
            "snapshot_type": "full",
            "isin": "KZX000002241",
            "secCode": "SOLV3.0526",
            "shortName": "Coupon bonds of Solva Group Ltd",
            "issuer": "Solva Group Limited",
            "instrument": "Debt",
            "assetClass": "Debt",
            "securityGroup": "Debt",
            "currency": "USD",
            "listingDate": "2024-05-27",
        }
    )
    pd.DataFrame([record], columns=AIX_COLUMNS).to_excel(path, index=False)
    return AixInstrumentResolver(aix_path=path, profile_cache_path=path.with_name("tabys_instruments.xlsx"))


if __name__ == "__main__":
    unittest.main()
