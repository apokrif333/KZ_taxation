from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from conftest_imports import SRC  # noqa: F401
from kztax270.brokers import freedom as fe
from kztax270.brokers.freedom import FreedomParser
from kztax270.reference.fx import AnnualFxRateProvider
from kztax270.reconciliation.models import ReconciliationMetric
from kztax270.transfers import TransferInFifoLot, TransferInRequest


class FreedomParserTests(unittest.TestCase):
    def test_financing_operations_are_interest_not_trades_or_fifo(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "1467068_2024-01-01 00_00_00_2024-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "LENZ.US",
                            fe.COL_ISIN: "US52635N1037",
                            fe.COL_MARKET: "US",
                            fe.COL_OPERATION: "Sell",
                            fe.COL_QTY: 1,
                            fe.COL_PRICE: 15.4971,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 15.4971,
                            fe.COL_REALIZED_PL: -103.5029,
                            fe.COL_COMMISSION: 1.2,
                            fe.COL_TRADE_DATE: "2024-05-29 19:56:46",
                            fe.COL_ORDER_ID: "sell-lenz",
                        }
                    ]
                ).to_excel(writer, sheet_name="Trades 20240101 - 20241231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "SWAP.US",
                            fe.COL_ISIN: "US0000000002",
                            fe.COL_MARKET: "US",
                            fe.COL_OPERATION: "Открытие свопа акциями. Покупка.",
                            fe.COL_QTY: 10,
                            fe.COL_PRICE: 100,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 1000,
                            fe.COL_REALIZED_PL: 0,
                            fe.COL_COMMISSION: 0.5,
                            fe.COL_TRADE_DATE: "2024-03-01 10:00:00",
                            fe.COL_ORDER_ID: "open/swap-1",
                        },
                        {
                            fe.COL_TICKER: "SWAP.US",
                            fe.COL_ISIN: "US0000000002",
                            fe.COL_MARKET: "US",
                            fe.COL_OPERATION: "Закрытие свопа акциями. Продажа.",
                            fe.COL_QTY: 10,
                            fe.COL_PRICE: 101.234,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 1012.34,
                            fe.COL_REALIZED_PL: 12.34,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2024-03-02 10:00:00",
                            fe.COL_ORDER_ID: "close/swap-1",
                        },
                        {
                            fe.COL_TICKER: "REPO.US",
                            fe.COL_ISIN: "US0000000003",
                            fe.COL_MARKET: "US",
                            fe.COL_OPERATION: "Открытие репо с неттингом. Покупка.",
                            fe.COL_QTY: 20,
                            fe.COL_PRICE: 100,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 2000,
                            fe.COL_REALIZED_PL: 0,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2024-04-01 10:00:00",
                            fe.COL_ORDER_ID: "open/repo-1",
                        },
                        {
                            fe.COL_TICKER: "REPO.US",
                            fe.COL_ISIN: "US0000000003",
                            fe.COL_MARKET: "US",
                            fe.COL_OPERATION: "Закрытие репо с неттингом. Продажа.",
                            fe.COL_QTY: 20,
                            fe.COL_PRICE: 100.25,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 2005,
                            fe.COL_REALIZED_PL: 5,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2024-04-02 10:00:00",
                            fe.COL_ORDER_ID: "close/repo-1",
                        },
                    ]
                ).to_excel(writer, sheet_name="Trades 20240101 - 20241231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "1467068"), "1467068")

        self.assertEqual(result.dataset.tables["Trades"], [])
        self.assertEqual(result.dataset.tables["Fifo"], [])
        self.assertEqual(len(result.dataset.tables["Interest"]), 2)
        by_id = {row["_financing_trade_id"]: row for row in result.dataset.tables["Interest"]}
        self.assertEqual(by_id["swap-1"]["gross_amount"], "11.84")
        self.assertEqual(by_id["swap-1"]["commission"], "0.50")
        self.assertIn("SWAP reward swap-1 SWAP.US", by_id["swap-1"]["description"])
        self.assertIn("open_price=100", by_id["swap-1"]["description"])
        self.assertIn("close_price=101.234", by_id["swap-1"]["description"])
        self.assertEqual(by_id["repo-1"]["gross_amount"], "5.00")
        self.assertEqual(by_id["repo-1"]["commission"], "0.00")
        self.assertIn("REPO reward repo-1 REPO.US", by_id["repo-1"]["description"])

    def test_ignores_legacy_trading_report_and_resolves_transfer_in_lots(self) -> None:
        import pandas as pd  # type: ignore

        seen_requests: list[TransferInRequest] = []

        def resolver(request: TransferInRequest) -> list[TransferInFifoLot]:
            seen_requests.append(request)
            return [
                TransferInFifoLot(
                    quantity=Decimal("5"),
                    price=Decimal("100"),
                    enter_date=datetime(2023, 12, 1, 10, 0, 0),
                    source_broker="manual",
                    source_file="transfer_out_template.xlsx",
                    source_row=2,
                )
            ]

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            new_report = broker_root / "1467068_2024-01-01 00_00_00_2024-12-31 23_59_59_all.xlsx"
            legacy_report = broker_root / "Trading report 1467068_2020-08-11_2024-03-28.xlsx"
            office_temp_report = broker_root / "~$1467068_2024-01-01 00_00_00_2024-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(new_report) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "TEST.US",
                            fe.COL_ISIN: "US0000000001",
                            fe.COL_MARKET: "US",
                            fe.COL_OPERATION: "Sell",
                            fe.COL_QTY: 5,
                            fe.COL_PRICE: 110,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 550,
                            fe.COL_REALIZED_PL: 49,
                            fe.COL_COMMISSION: 1,
                            fe.COL_TRADE_DATE: "2024-02-01 10:00:00",
                            fe.COL_ORDER_ID: "sell-1",
                        }
                    ]
                ).to_excel(writer, sheet_name="Trades 20240101 - 20241231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_DATE: "2024-01-15",
                            fe.COL_TYPE: "Transfer",
                            fe.COL_TICKER: "TEST.US",
                            fe.COL_ISIN: "US0000000001",
                            fe.COL_QTY: 5,
                            fe.COL_COMMENT: "Transfer in",
                        }
                    ]
                ).to_excel(writer, sheet_name="Sec In Out 20240101 - 20241231", index=False)
                pd.DataFrame([["5. Trades"], ["old row that must be ignored"]]).to_excel(writer, sheet_name="Worksheet", index=False, header=False)

            with pd.ExcelWriter(legacy_report) as writer:
                pd.DataFrame([["5. Trades"], ["legacy trade that must be ignored"]]).to_excel(writer, sheet_name="Worksheet", index=False, header=False)
            office_temp_report.write_text("", encoding="utf-8")

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469")}), transfer_in_resolver=resolver)
            reports = parser.discover_reports(raw_root, "1467068")
            result = parser.parse_reports(reports, "1467068")

        self.assertEqual([report.path.name for report in reports], [new_report.name])
        self.assertEqual(len(seen_requests), 1)
        self.assertEqual(seen_requests[0].symbol, "TEST.US")
        self.assertEqual(seen_requests[0].quantity, Decimal("5"))

        security_transfers = [row for row in result.dataset.tables["Transfers"] if row["transfer_type"] == "security"]
        self.assertEqual(len(security_transfers), 1)
        self.assertEqual(security_transfers[0]["price"], "100")
        self.assertEqual(security_transfers[0]["enter_date"], "2023-12-01 10:00:00")
        self.assertIn("fifo_source:transfer_out_template.xlsx", security_transfers[0]["source_report"])

        self.assertEqual(result.dataset.tables["Unprocessed"], [])
        fifo = result.dataset.tables["Fifo"][0]
        self.assertEqual(fifo["symbol"], "TEST.US")
        self.assertEqual(fifo["_opening_lot_status"], "matched")

    def test_transfer_in_before_conversion_still_resolves_fifo_source(self) -> None:
        import pandas as pd  # type: ignore

        seen_requests: list[TransferInRequest] = []

        def resolver(request: TransferInRequest) -> list[TransferInFifoLot]:
            seen_requests.append(request)
            return [
                TransferInFifoLot(
                    quantity=Decimal("12"),
                    price=Decimal("17"),
                    enter_date=datetime(2024, 1, 1),
                    source_broker="freedom",
                    source_file="transfer_out_freedom Sholpan.xlsx",
                    source_row=66,
                )
            ]

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "1467068_2024-01-01 00_00_00_2024-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_DATE: "2024-02-14",
                            fe.COL_TYPE: "Transfer",
                            fe.COL_TICKER: "GRPH.US",
                            fe.COL_ISIN: "US38870X1046",
                            fe.COL_QTY: 12,
                            fe.COL_COMMENT: "Перевод бумаг по поручению 25522629",
                        }
                    ]
                ).to_excel(writer, sheet_name="Sec In Out 20240101 - 20241231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_DATE: "2024-03-25",
                            fe.COL_TYPE: "Конвертация",
                            fe.COL_ASSET: "Деньги",
                            fe.COL_TICKER: "GRPH.US",
                            fe.COL_ISIN: "US38870X1046",
                            fe.COL_AMOUNT: 1,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_COMMENT: "Conversion of securities GRPH.US (US38870X1046) -> LENZ.US (US52635N1037).",
                        }
                    ]
                ).to_excel(writer, sheet_name="Corpactions 20240101 - 20241231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469")}), transfer_in_resolver=resolver)
            result = parser.parse_reports(parser.discover_reports(raw_root, "1467068"), "1467068")

        self.assertEqual(len(seen_requests), 1)
        self.assertEqual(seen_requests[0].symbol, "GRPH.US")
        transfer = next(row for row in result.dataset.tables["Transfers"] if row["symbol"] == "GRPH.US")
        self.assertEqual(transfer["price"], "17")
        self.assertEqual(transfer["enter_date"], "2024-01-01 00:00:00")
        self.assertIn("fifo_source:transfer_out_freedom Sholpan.xlsx", transfer["source_report"])

    def test_internal_ticker_change_does_not_request_transfer_in_price(self) -> None:
        import pandas as pd  # type: ignore

        seen_requests: list[TransferInRequest] = []

        def resolver(request: TransferInRequest) -> list[TransferInFifoLot]:
            seen_requests.append(request)
            return []

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "7F8339_2023-01-01 00_00_00_2023-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_DATE: "2023-10-06 17:00:00",
                            fe.COL_TYPE: "Перевод внутри компании",
                            fe.COL_TICKER: "BMRN.US",
                            fe.COL_ISIN: "US09061G1013",
                            fe.COL_QTY: -1,
                            fe.COL_COMMENT: "Смена тикера",
                        },
                        {
                            fe.COL_DATE: "2023-10-06 17:00:00",
                            fe.COL_TYPE: "Перевод внутри компании",
                            fe.COL_TICKER: "BMRN.ITS",
                            fe.COL_ISIN: "US09061G1013",
                            fe.COL_QTY: 1,
                            fe.COL_COMMENT: "Смена тикера",
                        },
                    ]
                ).to_excel(writer, sheet_name="Sec In Out 20230101 - 20231231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2023, "USD"): Decimal("460")}), transfer_in_resolver=resolver)
            result = parser.parse_reports(parser.discover_reports(raw_root, "7F8339"), "7F8339")

        self.assertEqual(seen_requests, [])
        self.assertEqual(result.dataset.tables["Fifo"], [])
        self.assertEqual(result.dataset.tables["Transfers"], [])
        actions = result.dataset.tables["CorporateActions"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action_type"], "ticker_change")
        self.assertIn("BMRN.US", actions[0]["description"])
        self.assertIn("BMRN.ITS", actions[0]["description"])

    def test_ticker_change_normalizes_trades_dividends_and_positions(self) -> None:
        import pandas as pd  # type: ignore

        internal_transfer = "\u041f\u0435\u0440\u0435\u0432\u043e\u0434 \u0432\u043d\u0443\u0442\u0440\u0438 \u043a\u043e\u043c\u043f\u0430\u043d\u0438\u0438"
        ticker_change = "C\u043c\u0435\u043d\u0430 \u0442\u0438\u043a\u0435\u0440\u0430"

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "8A0627_2023-12-31 23_59_59_2024-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {fe.COL_TICKER: "AIRA.U.AIX.KZ", fe.COL_ISIN: "KZ1C00004050", fe.COL_ASSET_TYPE: "Stocks", fe.COL_START_QTY: 0, fe.COL_END_QTY: 131, fe.COL_CURRENCY: "USD"},
                    ]
                ).to_excel(writer, sheet_name="Securities 20231231 - 20241231", index=False)
                pd.DataFrame(
                    [
                        {fe.COL_DATE: "2024-02-12 15:00:00", fe.COL_TYPE: internal_transfer, fe.COL_TICKER: "AIRA.AIX.KZ", fe.COL_ISIN: "KZ1C00004050", fe.COL_QTY: -93, fe.COL_COMMENT: ticker_change},
                        {fe.COL_DATE: "2024-02-12 15:00:00", fe.COL_TYPE: internal_transfer, fe.COL_TICKER: "AIRA.U.AIX.KZ", fe.COL_ISIN: "KZ1C00004050", fe.COL_QTY: 93, fe.COL_COMMENT: ticker_change},
                    ]
                ).to_excel(writer, sheet_name="Sec In Out 20231231 - 20241231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "AIRA.AIX.KZ",
                            fe.COL_ISIN: "KZ1C00004050",
                            fe.COL_OPERATION: "Buy",
                            fe.COL_QTY: 93,
                            fe.COL_PRICE: 1073.83,
                            fe.COL_CURRENCY: "KZT",
                            fe.COL_AMOUNT: 99866.19,
                            fe.COL_REALIZED_PL: 0,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2024-02-09 21:10:00",
                            fe.COL_ORDER_ID: "buy-aira-before",
                        },
                        {
                            fe.COL_TICKER: "AIRA.AIX.KZ",
                            fe.COL_ISIN: "KZ1C00004050",
                            fe.COL_OPERATION: "Buy",
                            fe.COL_QTY: 38,
                            fe.COL_PRICE: 2,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 76,
                            fe.COL_REALIZED_PL: 0,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2024-04-09 16:27:51",
                            fe.COL_ORDER_ID: "buy-aira-after",
                        },
                    ]
                ).to_excel(writer, sheet_name="Trades 20231231 - 20241231", index=False)
                pd.DataFrame(
                    [
                        {fe.COL_TYPE: "Dividends", fe.COL_DATE: "2024-05-15", fe.COL_AMOUNT: 10, fe.COL_CURRENCY: "USD", fe.COL_COMMENT: "Dividends on security (AIRA.AIX.KZ), ISIN KZ1C00004050"},
                    ]
                ).to_excel(writer, sheet_name="Cash In Out 20231231 - 20241231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469"), (2024, "KZT"): Decimal("1")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "8A0627"), "8A0627")

        self.assertEqual({row["symbol"] for row in result.dataset.tables["Trades"]}, {"AIRA.U.AIX.KZ"})
        self.assertEqual({row["symbol"] for row in result.dataset.tables["Dividends"]}, {"AIRA.U.AIX.KZ"})
        self.assertEqual(
            sum(Decimal(row["quantity"]) for row in result.dataset.tables["Positions"] if row["year"] == 2024 and row["symbol"] == "AIRA.U.AIX.KZ"),
            Decimal("131"),
        )
        self.assertFalse(any(row["symbol"] == "AIRA.AIX.KZ" for row in result.dataset.tables["Positions"]))

    def test_internal_depository_change_does_not_request_transfer_in_price(self) -> None:
        import pandas as pd  # type: ignore

        seen_requests: list[TransferInRequest] = []
        internal_transfer = "\u041f\u0435\u0440\u0435\u0432\u043e\u0434 \u0432\u043d\u0443\u0442\u0440\u0438 \u043a\u043e\u043c\u043f\u0430\u043d\u0438\u0438"
        depository_change = "\u0421\u043c\u0435\u043d\u0430 \u043c\u0435\u0441\u0442\u0430 \u0445\u0440\u0430\u043d\u0435\u043d\u0438\u044f \u0426\u0411"

        def resolver(request: TransferInRequest) -> list[TransferInFifoLot]:
            seen_requests.append(request)
            return []

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "8A0627_2021-01-01 00_00_00_2023-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_DATE: "2022-04-25 15:00:00",
                            fe.COL_TYPE: internal_transfer,
                            fe.COL_TICKER: "RU_SBER.KZ",
                            fe.COL_ISIN: "RU0009029540",
                            fe.COL_QTY: -2,
                            fe.COL_COMMENT: f"{depository_change}\n",
                        },
                        {
                            fe.COL_DATE: "2022-04-25 15:00:00",
                            fe.COL_TYPE: internal_transfer,
                            fe.COL_TICKER: "RU_SBER.KZ",
                            fe.COL_ISIN: "RU0009029540",
                            fe.COL_QTY: 2,
                            fe.COL_COMMENT: depository_change,
                        },
                    ]
                ).to_excel(writer, sheet_name="Sec In Out 20210101 - 20231231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2022, "USD"): Decimal("460")}), transfer_in_resolver=resolver)
            result = parser.parse_reports(parser.discover_reports(raw_root, "8A0627"), "8A0627")

        self.assertEqual(seen_requests, [])
        self.assertEqual(result.dataset.tables["Transfers"], [])
        self.assertEqual(result.dataset.tables["Fifo"], [])
        actions = result.dataset.tables["CorporateActions"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action_type"], "depository_change")
        self.assertIn("RU_SBER.KZ", actions[0]["description"])

    def test_starting_securities_seed_fifo_and_request_transfer_out(self) -> None:
        import pandas as pd  # type: ignore

        seen_requests: list[TransferInRequest] = []

        def resolver(request: TransferInRequest) -> list[TransferInFifoLot]:
            seen_requests.append(request)
            return []

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "8A0627_2021-12-31 23_59_59_2022-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {fe.COL_TICKER: "KZTO.KZ", fe.COL_ISIN: "KZ1C00000744", fe.COL_ASSET_TYPE: "Stocks", fe.COL_START_QTY: 17, fe.COL_END_QTY: 47, fe.COL_CURRENCY: "KZT"},
                        {fe.COL_TICKER: "KCEL.KZ", fe.COL_ISIN: "KZ1C00000876", fe.COL_ASSET_TYPE: "Stocks", fe.COL_START_QTY: 24, fe.COL_END_QTY: 0, fe.COL_CURRENCY: "KZT"},
                        {fe.COL_TICKER: "HSBK.KZ", fe.COL_ISIN: "KZ000A0LE0S4", fe.COL_ASSET_TYPE: "Stocks", fe.COL_START_QTY: 17, fe.COL_END_QTY: 194, fe.COL_CURRENCY: "KZT"},
                        {fe.COL_TICKER: "RU_UKFFIPO.KZ", fe.COL_ISIN: "RU000A101NK4", fe.COL_ASSET_TYPE: "Stocks", fe.COL_START_QTY: 106, fe.COL_END_QTY: 106, fe.COL_CURRENCY: "USD"},
                    ]
                ).to_excel(writer, sheet_name="Securities 20211231 - 20221231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "KZTO.KZ",
                            fe.COL_ISIN: "KZ1C00000744",
                            fe.COL_OPERATION: "Sell",
                            fe.COL_QTY: 15,
                            fe.COL_PRICE: 1069.5,
                            fe.COL_CURRENCY: "KZT",
                            fe.COL_AMOUNT: 16042.5,
                            fe.COL_REALIZED_PL: -555.44,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2022-01-11 14:52:50",
                            fe.COL_ORDER_ID: "sell-kzto",
                        },
                        {
                            fe.COL_TICKER: "KZTO.KZ",
                            fe.COL_ISIN: "KZ1C00000744",
                            fe.COL_OPERATION: "Buy",
                            fe.COL_QTY: 45,
                            fe.COL_PRICE: 900,
                            fe.COL_CURRENCY: "KZT",
                            fe.COL_AMOUNT: 40500,
                            fe.COL_REALIZED_PL: 0,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2022-03-28 12:24:05",
                            fe.COL_ORDER_ID: "buy-kzto",
                        },
                        {
                            fe.COL_TICKER: "KCEL.KZ",
                            fe.COL_ISIN: "KZ1C00000876",
                            fe.COL_OPERATION: "Sell",
                            fe.COL_QTY: 24,
                            fe.COL_PRICE: 1602,
                            fe.COL_CURRENCY: "KZT",
                            fe.COL_AMOUNT: 38448,
                            fe.COL_REALIZED_PL: 1200,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2022-01-17 11:39:45",
                            fe.COL_ORDER_ID: "sell-kcel",
                        },
                        {
                            fe.COL_TICKER: "HSBK.KZ",
                            fe.COL_ISIN: "KZ000A0LE0S4",
                            fe.COL_OPERATION: "Buy",
                            fe.COL_QTY: 177,
                            fe.COL_PRICE: 120,
                            fe.COL_CURRENCY: "KZT",
                            fe.COL_AMOUNT: 21240,
                            fe.COL_REALIZED_PL: 0,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2022-02-11 14:57:08",
                            fe.COL_ORDER_ID: "buy-hsbk",
                        },
                    ]
                ).to_excel(writer, sheet_name="Trades 20211231 - 20221231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2022, "KZT"): Decimal("1"), (2022, "USD"): Decimal("460")}), transfer_in_resolver=resolver)
            result = parser.parse_reports(parser.discover_reports(raw_root, "8A0627"), "8A0627")

        requested_symbols = {request.symbol for request in seen_requests}
        self.assertTrue({"KZTO.KZ", "KCEL.KZ", "HSBK.KZ", "RU_UKFFIPO.KZ"}.issubset(requested_symbols))

        fifo_kzto = next(row for row in result.dataset.tables["Fifo"] if row["symbol"] == "KZTO.KZ")
        self.assertEqual(fifo_kzto["_opening_lot_status"], "broker_pl_inferred_transfer_in")
        self.assertEqual(fifo_kzto["enter_date"], "2021-12-31 00:00:00")
        self.assertNotEqual(fifo_kzto["enter_price"], "0")

        positions_2022 = result.dataset.tables["Positions"]
        quantities = {
            symbol: sum(Decimal(row["quantity"]) for row in positions_2022 if row["year"] == 2022 and row["symbol"] == symbol)
            for symbol in ("KZTO.KZ", "KCEL.KZ", "HSBK.KZ", "RU_UKFFIPO.KZ")
        }
        self.assertEqual(quantities["KZTO.KZ"], Decimal("47"))
        self.assertEqual(quantities["KCEL.KZ"], Decimal("0"))
        self.assertEqual(quantities["HSBK.KZ"], Decimal("194"))
        self.assertEqual(quantities["RU_UKFFIPO.KZ"], Decimal("106"))
        self.assertEqual([row for row in result.dataset.tables["Unprocessed"] if row["symbol"] in {"KZTO.KZ", "KCEL.KZ", "HSBK.KZ"}], [])

    def test_grph_to_lenz_conversion_carries_transfer_in_cost_basis(self) -> None:
        import pandas as pd  # type: ignore

        def resolver(request: TransferInRequest) -> list[TransferInFifoLot]:
            return [
                TransferInFifoLot(
                    quantity=Decimal("12"),
                    price=Decimal("17"),
                    enter_date=datetime(2024, 1, 1),
                    source_file="transfer_out_freedom Sholpan.xlsx",
                    source_row=66,
                )
            ]

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "1467068_2024-01-01 00_00_00_2024-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "LENZ.US",
                            fe.COL_ISIN: "US52635N1037",
                            fe.COL_MARKET: "US",
                            fe.COL_OPERATION: "Sell",
                            fe.COL_QTY: 1,
                            fe.COL_PRICE: 15.4971,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 15.4971,
                            fe.COL_REALIZED_PL: -103.5029,
                            fe.COL_COMMISSION: 1.2,
                            fe.COL_TRADE_DATE: "2024-05-29 19:56:46",
                            fe.COL_ORDER_ID: "sell-lenz",
                        }
                    ]
                ).to_excel(writer, sheet_name="Trades 20240101 - 20241231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_DATE: "2024-02-14",
                            fe.COL_TYPE: "Transfer",
                            fe.COL_TICKER: "GRPH.US",
                            fe.COL_ISIN: "US38870X1046",
                            fe.COL_QTY: 12,
                            fe.COL_COMMENT: "Перевод бумаг по поручению 25522629",
                        },
                        {
                            fe.COL_DATE: "2024-03-25",
                            fe.COL_TYPE: "Конвертация",
                            fe.COL_TICKER: "GRPH.US",
                            fe.COL_ISIN: "US38870X1046",
                            fe.COL_QTY: -12,
                            fe.COL_COMMENT: "Conversion of securities GRPH.US (US38870X1046) -> LENZ.US (US52635N1037). Cut date 2024-03-21, ratio: 7/1.",
                        },
                        {
                            fe.COL_DATE: "2024-03-25",
                            fe.COL_TYPE: "Конвертация",
                            fe.COL_TICKER: "LENZ.US",
                            fe.COL_ISIN: "US52635N1037",
                            fe.COL_QTY: 1,
                            fe.COL_COMMENT: "Conversion of securities GRPH.US (US38870X1046) -> LENZ.US (US52635N1037). Cut date 2024-03-21, ratio: 7/1.",
                        },
                    ]
                ).to_excel(writer, sheet_name="Sec In Out 20240101 - 20241231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_DATE: "2024-03-26",
                            fe.COL_TYPE: "Конвертация",
                            fe.COL_ASSET: "Деньги",
                            fe.COL_TICKER: "GRPH.US",
                            fe.COL_ISIN: "US38870X1046",
                            fe.COL_AMOUNT: 15.9,
                            fe.COL_PER_ONE: 3.18,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_COMMENT: "Компенсация при проведении корпоративного действия с бумагами (GRPH.US), расчетное количество бумаг LENZ.US к получению 1.7142857142857, получено 1, цена для оценки выбывающих бумаг 3.18 USD",
                        }
                    ]
                ).to_excel(writer, sheet_name="Corpactions 20240101 - 20241231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469")}), transfer_in_resolver=resolver)
            result = parser.parse_reports(parser.discover_reports(raw_root, "1467068"), "1467068")

        lenz_fifo = [row for row in result.dataset.tables["Fifo"] if row["symbol"] == "LENZ.US"]
        self.assertEqual(len(lenz_fifo), 2)
        self.assertTrue(all(row["_opening_lot_status"] == "matched" for row in lenz_fifo))
        self.assertEqual([row["enter_price"] for row in lenz_fifo], ["119", "119"])
        self.assertEqual([row["exit_quantity"] for row in lenz_fifo], ["0.7142857142857", "1"])
        self.assertFalse(any(row["symbol"] in {"GRPH.US", "LENZ.US"} for row in result.dataset.tables["Positions"]))
        self.assertEqual(result.dataset.tables["Unprocessed"], [])

    def test_cash_dividend_rows_are_not_collapsed_by_record_date(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "1467068_2024-01-01 00_00_00_2025-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {fe.COL_TYPE: "Дивиденды", fe.COL_DATE: "2024-05-15", fe.COL_AMOUNT: 5.55, fe.COL_CURRENCY: "USD", fe.COL_COMMENT: "Dividends on security (T.US), record date 2024-04-10 23:59:59. Per security USD 0.2775. Balance on the record date is 20"},
                        {fe.COL_TYPE: "Дивиденды", fe.COL_DATE: "2024-07-09", fe.COL_AMOUNT: 5.55, fe.COL_CURRENCY: "USD", fe.COL_COMMENT: "Dividends on security (AT&T Inc (T.US)), record date 2024-04-10 23:59:59. Per security USD 0.2775. Balance on the record date is 20"},
                        {fe.COL_TYPE: "Налоги", fe.COL_DATE: "2024-07-09", fe.COL_AMOUNT: -1.67, fe.COL_CURRENCY: "USD", fe.COL_COMMENT: "Tax for a corporate action on security (T.US), record date 2024-04-10 23:59:59. Tax rate 30 Balance on the record date is 20"},
                        {fe.COL_TYPE: "Дивиденды", fe.COL_DATE: "2024-10-03", fe.COL_AMOUNT: -5.55, fe.COL_CURRENCY: "USD", fe.COL_COMMENT: "Reverted Dividends on security (T.US), record date 2024-04-10"},
                        {fe.COL_TYPE: "Дивиденды", fe.COL_DATE: "2025-05-01", fe.COL_AMOUNT: 3.88, fe.COL_CURRENCY: "USD", fe.COL_COMMENT: "Dividends on security (AT&T Inc (T.US)), record date 2024-04-10. Per security 0.2775 USD. Balance on the record date is 20"},
                        {fe.COL_TYPE: "Дивиденды", fe.COL_DATE: "2025-05-01", fe.COL_AMOUNT: -5.55, fe.COL_CURRENCY: "USD", fe.COL_COMMENT: "Reverted: Dividends on security (AT&T Inc (T.US)), record date 2024-04-10 23:59:59. Per security USD 0.2775. Balance on the record date is 20"},
                        {fe.COL_TYPE: "Налоги", fe.COL_DATE: "2025-05-01", fe.COL_AMOUNT: 1.67, fe.COL_CURRENCY: "USD", fe.COL_COMMENT: "Reverted: Tax for a corporate action on security (T.US), record date 2024-04-10 23:59:59. Tax rate 30 Balance on the record date is 20"},
                    ]
                ).to_excel(writer, sheet_name="Cash In Out 20240101 - 20251231", index=False)

            parser = FreedomParser(
                fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469"), (2025, "USD"): Decimal("500")})
            )
            result = parser.parse_reports(parser.discover_reports(raw_root, "1467068"), "1467068")

        dividends = result.dataset.tables["Dividends"]
        self.assertEqual([(row["date"], row["gross_amount"], row["withholding_tax"]) for row in dividends], [
            ("2024-05-15", "5.55", "0.00"),
            ("2024-07-09", "5.55", "-1.67"),
            ("2024-10-03", "-5.55", "0.00"),
            ("2025-05-01", "3.88", "0.00"),
            ("2025-05-01", "-5.55", "1.67"),
        ])
        yearly_2025 = next(row for row in result.dataset.tables["Years_Results"] if row["table"] == "Yearly Dividends" and row["year"] == 2025)
        self.assertEqual(yearly_2025["amount"], "-1.67")
        self.assertEqual(yearly_2025["tax_kzt_withhold"], "0.00")
        self.assertEqual(result.dataset.tables["Transfers"], [])

    def test_kz_dividends_keep_amount_only_and_zero_reporting_fields(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "7A3453_2024-01-01 00_00_00_2024-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "KZDIV.KZ",
                            fe.COL_ISIN: "KZ0000000001",
                            fe.COL_ACCOUNT: "trading",
                            fe.COL_ASSET_TYPE: "Stocks",
                            fe.COL_END_QTY: 10,
                            fe.COL_CURRENCY: "USD",
                        }
                    ]
                ).to_excel(writer, sheet_name="Securities 20240101 - 20241231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TYPE: "Dividends",
                            fe.COL_DATE: "2024-05-15",
                            fe.COL_AMOUNT: 5.55,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_COMMENT: "Dividends on security (KZDIV.KZ), record date 2024-04-10 23:59:59. ISIN KZ0000000001",
                        }
                    ]
                ).to_excel(writer, sheet_name="Cash In Out 20240101 - 20241231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "7A3453"), "7A3453")

        dividend = result.dataset.tables["Dividends"][0]
        self.assertEqual(dividend["gross_amount"], "5.55")
        self.assertEqual(dividend["gross_amount_kzt"], "0.00")
        self.assertEqual(dividend["withholding_tax_kzt"], "0.00")
        self.assertEqual(dividend["net_amount_kzt"], "0.00")
        self.assertEqual(dividend["tax"], "0.00")
        self.assertEqual(dividend["tax_kzt"], "0.00")
        yearly = next(row for row in result.dataset.tables["Years_Results"] if row["table"] == "Yearly Dividends")
        self.assertEqual(yearly["flag"], "Issuer_KZ")
        self.assertEqual(yearly["amount"], "5.55")
        self.assertEqual(yearly["amount_kzt"], "0.00")
        self.assertEqual(yearly["withhold_kzt"], "0.00")
        self.assertEqual(yearly["tax_kzt"], "0.00")
        self.assertEqual(yearly["tax_kzt_withhold"], "0.00")

    def test_russian_report_column_aliases_and_types_are_supported(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "7F8339_2024-01-01 00_00_00_2024-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "AAL.US",
                            fe.COL_ISIN: "US02376R1023",
                            fe.COL_MARKET: "NYSE/NASDAQ",
                            fe.COL_OPERATION: "Покупка",
                            fe.COL_QTY: 4,
                            fe.COL_PRICE: 13.118,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 52.47,
                            fe.COL_REALIZED_PL: 0,
                            fe.COL_COMMISSION: 1.51,
                            fe.COL_TRADE_DATE: "2024-01-04 01:30:05",
                            "Order ID": "362673767/332551377",
                        }
                    ]
                ).to_excel(writer, sheet_name="Trades 20240101 - 20241231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TYPE: "Дивиденды",
                            fe.COL_DATE: "2024-01-03",
                            fe.COL_AMOUNT: 0.35,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_COMMENT: "Дивиденды по бумаге (Paramount Global (PARA.US)), дата среза 2023-12-15",
                        },
                        {
                            fe.COL_TYPE: "Налоги",
                            fe.COL_DATE: "2024-01-03",
                            fe.COL_AMOUNT: -0.05,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_COMMENT: "Налог по бумаге (PARA.US), дата среза 2023-12-15",
                        },
                    ]
                ).to_excel(writer, sheet_name="Cash In Out 20240101 - 20241231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "7F8339"), "7F8339")

        trades = result.dataset.tables["Trades"]
        self.assertEqual(trades[0]["trade_id"], f"{report_path.name}:362673767/332551377:1")
        self.assertEqual(trades[0]["quantity"], "4")
        self.assertEqual(result.dataset.tables["Transfers"], [])
        self.assertEqual(
            [(row["symbol"], row["gross_amount"], row["withholding_tax"]) for row in result.dataset.tables["Dividends"]],
            [("PARA.US", "0.35", "-0.05")],
        )

    def test_currency_pairs_are_forex_and_open_trades_do_not_create_pnl_reconciliation_bucket(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "7F8339_2024-01-01 00_00_00_2024-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "AAL.US",
                            fe.COL_ISIN: "US02376R1023",
                            fe.COL_MARKET: "NYSE/NASDAQ",
                            fe.COL_OPERATION: "Покупка",
                            fe.COL_QTY: 4,
                            fe.COL_PRICE: 13.118,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 52.47,
                            fe.COL_REALIZED_PL: 0,
                            fe.COL_COMMISSION: 1.51,
                            fe.COL_TRADE_DATE: "2024-01-04 01:30:05",
                            "Order ID": "stock-open",
                        },
                        {
                            fe.COL_TICKER: "KZT/USD",
                            fe.COL_ISIN: "-",
                            fe.COL_MARKET: "OTC",
                            fe.COL_OPERATION: "Продажа",
                            fe.COL_QTY: 41146.91,
                            fe.COL_PRICE: 0.002208,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 90.85,
                            fe.COL_REALIZED_PL: 0,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2024-02-22 10:03:59",
                            "Order ID": "fx-1",
                        },
                    ]
                ).to_excel(writer, sheet_name="Trades 20240101 - 20241231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "7F8339"), "7F8339")

        fx_trade = next(row for row in result.dataset.tables["Trades"] if row["symbol"] == "KZT/USD")
        self.assertEqual(fx_trade["asset_type"], "Forex")
        self.assertFalse(any("/" in str(row.get("symbol") or "") for row in result.dataset.tables["Positions"]))
        pnl_metric_prefix = ReconciliationMetric.PNL_AFTER_ALL_COMMISSIONS_BY_INSTRUMENT.value
        self.assertFalse(any(key.startswith(pnl_metric_prefix) and "US02376R1023" in key for key in result.raw_totals.totals_by_metric_currency))
        self.assertTrue(any(row["asset_type"] == "Forex" and row["symbol"] == "KZT/USD" for row in result.dataset.tables["Fifo"]))

    def test_kz_issuer_trades_do_not_accrue_capital_gain_tax(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "7A7579_2024-01-01 00_00_00_2024-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "KZTEST.KZ",
                            fe.COL_ISIN: "KZ0000000001",
                            fe.COL_MARKET: "KASE",
                            fe.COL_OPERATION: "Buy",
                            fe.COL_QTY: 1,
                            fe.COL_PRICE: 100,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 100,
                            fe.COL_REALIZED_PL: 0,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2024-01-10 10:00:00",
                            "Order ID": "kz-buy",
                        },
                        {
                            fe.COL_TICKER: "KZTEST.KZ",
                            fe.COL_ISIN: "KZ0000000001",
                            fe.COL_MARKET: "KASE",
                            fe.COL_OPERATION: "Sell",
                            fe.COL_QTY: 1,
                            fe.COL_PRICE: 150,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 150,
                            fe.COL_REALIZED_PL: 50,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2024-02-10 10:00:00",
                            "Order ID": "kz-sell",
                        },
                    ]
                ).to_excel(writer, sheet_name="Trades 20240101 - 20241231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "7A7579"), "7A7579")

        yearly_trades = [row for row in result.dataset.tables["Years_Results"] if row["table"] == "Yearly Trades"]
        self.assertEqual(len(yearly_trades), 1)
        self.assertEqual(yearly_trades[0]["pnl"], "50.00")
        self.assertEqual(yearly_trades[0]["tax_kzt"], "0.00")
        self.assertEqual(yearly_trades[0]["tax_kzt_withhold"], "0.00")

    def test_unresolved_transfer_in_sale_uses_broker_pl_excluding_commission_to_infer_enter_price(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "1467068_2024-01-01 00_00_00_2024-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "CTKB.US",
                            fe.COL_ISIN: "US19200A1051",
                            fe.COL_MARKET: "US",
                            fe.COL_OPERATION: "Sell",
                            fe.COL_QTY: 4,
                            fe.COL_PRICE: 5.762,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 23.048,
                            fe.COL_REALIZED_PL: -44.95,
                            fe.COL_COMMISSION: 1.2,
                            fe.COL_TRADE_DATE: "2024-05-29 19:49:07",
                            fe.COL_ORDER_ID: "sell-ctkb",
                        }
                    ]
                ).to_excel(writer, sheet_name="Trades 20240101 - 20241231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_DATE: "2024-02-14 20:57:50",
                            fe.COL_TYPE: "Transfer",
                            fe.COL_TICKER: "CTKB.US",
                            fe.COL_ISIN: "US19200A1051",
                            fe.COL_QTY: 4,
                            fe.COL_COMMENT: "Transfer in",
                        }
                    ]
                ).to_excel(writer, sheet_name="Sec In Out 20240101 - 20241231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "1467068"), "1467068")

        fifo = result.dataset.tables["Fifo"][0]
        self.assertEqual(fifo["symbol"], "CTKB.US")
        self.assertEqual(fifo["_opening_lot_status"], "broker_pl_inferred_transfer_in")
        self.assertEqual(fifo["enter_date"], "2024-02-14 00:00:00")
        self.assertEqual(fifo["enter_price"], "16.9995")
        self.assertEqual(fifo["acquisition_cost_with_commission"], "67.998")
        self.assertEqual(fifo["pnl"], "-44.95")
        self.assertEqual(fifo["pnl_after_all_commissions"], "-46.15")
        self.assertNotEqual(fifo["enter_price"], "0")
        self.assertEqual(result.dataset.tables["Unprocessed"], [])

    def test_bond_trades_keep_multiplier_one_and_coupons_follow_cash_in_out(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "7A3453_2023-01-01 00_00_00_2025-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "FFSPC1.1228.AIX.KZ",
                            fe.COL_ISIN: "KZX000001862",
                            fe.COL_MARKET: "AIX",
                            fe.COL_OPERATION: "Buy",
                            fe.COL_QTY: 20,
                            fe.COL_PRICE: 99.9,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 2000,
                            fe.COL_REALIZED_PL: 0,
                            fe.COL_COMMISSION: 1.7,
                            fe.COL_TRADE_DATE: "2023-12-20 14:54:41",
                            fe.COL_ORDER_ID: "bond-buy",
                        },
                        {
                            fe.COL_TICKER: "FFSPC1.1228.AIX.KZ",
                            fe.COL_ISIN: "KZX000001862",
                            fe.COL_MARKET: "AIX",
                            fe.COL_OPERATION: "Sell",
                            fe.COL_QTY: 20,
                            fe.COL_PRICE: 106.1,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 2129.33,
                            fe.COL_REALIZED_PL: 124,
                            fe.COL_COMMISSION: 1.81,
                            fe.COL_TRADE_DATE: "2025-01-29 12:45:24",
                            fe.COL_ORDER_ID: "bond-sell",
                        },
                    ]
                ).to_excel(writer, sheet_name="Trades 20230101 - 20251231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "FFSPC1.1228.AIX.KZ",
                            fe.COL_ISIN: "KZX000001862",
                            fe.COL_ACCOUNT: "trading",
                            fe.COL_ASSET_TYPE: "Bond",
                            fe.COL_END_QTY: 0,
                            fe.COL_CURRENCY: "USD",
                        }
                    ]
                ).to_excel(writer, sheet_name="Securities 20230101 - 20251231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TYPE: "Coupon",
                            fe.COL_DATE: "2024-05-02",
                            fe.COL_AMOUNT: 20,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_COMMENT: "Coupon on security (Freedom Finance SPC Ltd (FFSPC1.1228.AIX.KZ)), record date 2024-04-18 23:59:59.",
                        }
                    ]
                ).to_excel(writer, sheet_name="Corpactions 20230101 - 20251231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TYPE: "Coupon",
                            fe.COL_DATE: "2024-05-02",
                            fe.COL_ACCOUNT: "trading",
                            fe.COL_AMOUNT: 20,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_COMMENT: "Coupon on security (Freedom Finance SPC Ltd (FFSPC1.1228.AIX.KZ)), record date 2024-04-18 23:59:59.",
                        },
                        {
                            fe.COL_TYPE: "Coupon",
                            fe.COL_DATE: "2024-05-31",
                            fe.COL_ACCOUNT: "trading",
                            fe.COL_AMOUNT: 20,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_COMMENT: "Выплата купона по ЦБ KZX000001862 , тикер FFSPC1.1228, дата фиксации 18.05.2024 23:59:59, цена 1 , по месту хранения AIX.KZ",
                        },
                    ]
                ).to_excel(writer, sheet_name="Cash In Out 20230101 - 20251231", index=False)

            parser = FreedomParser(
                fx_provider=AnnualFxRateProvider({(2023, "USD"): Decimal("456"), (2024, "USD"): Decimal("469"), (2025, "USD"): Decimal("521.59")})
            )
            result = parser.parse_reports(parser.discover_reports(raw_root, "7A3453"), "7A3453")

        trades = [row for row in result.dataset.tables["Trades"] if row["symbol"] == "FFSPC1.1228.AIX.KZ"]
        self.assertEqual([row["multiplier"] for row in trades], ["1", "1"])
        fifo = result.dataset.tables["Fifo"][0]
        self.assertEqual(fifo["enter_multiplier"], "1")
        self.assertEqual(fifo["exit_multiplier"], "1")
        self.assertEqual(Decimal(fifo["pnl_after_all_commissions"]), Decimal("120.49"))
        coupons = result.dataset.tables["Coupons"]
        self.assertEqual([(row["date"], Decimal(row["gross_amount"])) for row in coupons], [("2024-05-02", Decimal("20")), ("2024-05-31", Decimal("20"))])
        self.assertTrue(all(row["symbol"] == "FFSPC1.1228.AIX.KZ" for row in coupons))
        self.assertEqual(coupons[1]["symbol"], "FFSPC1.1228.AIX.KZ")
        self.assertEqual(coupons[1]["isin"], "KZX000001862")


if __name__ == "__main__":
    unittest.main()
