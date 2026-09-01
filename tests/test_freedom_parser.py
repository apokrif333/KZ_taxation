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
from kztax270.reconciliation.engine import ReconciliationEngine
from kztax270.reconciliation.models import ReconciliationMetric
from kztax270.transfers import TransferInFifoLot, TransferInRequest


class FreedomParserTests(unittest.TestCase):
    def test_russian_conversion_description_parses_identities_and_ratio(self) -> None:
        description = (
            "Конвертация бумаг PARA.US (US92556H2067) -> PSKY.US (US69932A2042). "
            "Дата среза 2025-08-07, коэффициент: 7/2."
        )

        self.assertEqual(
            fe._conversion_identities(description),
            ("PARA.US", "US92556H2067", "PSKY.US", "US69932A2042"),
        )
        self.assertEqual(fe._conversion_ratio(description), Decimal("3.5"))

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
                            "Номер сделки/Номер приказа": "open/swap-1",
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
                            "Номер сделки/Номер приказа": "close/swap-1",
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
                            "Номер сделки/Номер приказа": "open/repo-1",
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
                            "Номер сделки/Номер приказа": "close/repo-1",
                        },
                    ]
                ).to_excel(writer, sheet_name="Trades 20240101 - 20241231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "1467068"), "1467068")

        self.assertEqual(result.dataset.tables["Trades"], [])
        self.assertEqual(result.dataset.tables["Fifo"], [])
        self.assertEqual(len(result.dataset.tables["Interest"]), 2)
        by_id = {row["_financing_trade_id"]: row for row in result.dataset.tables["Interest"]}
        self.assertEqual(by_id["swap-1"]["financing_kind"], "swap")
        self.assertEqual(by_id["swap-1"]["gross_amount"], "11.84")
        self.assertEqual(by_id["swap-1"]["commission"], "0.50")
        self.assertIn("SWAP reward swap-1 SWAP.US", by_id["swap-1"]["description"])
        self.assertIn("open_price=100", by_id["swap-1"]["description"])
        self.assertIn("close_price=101.234", by_id["swap-1"]["description"])
        self.assertEqual(by_id["repo-1"]["financing_kind"], "repo")
        self.assertEqual(by_id["repo-1"]["gross_amount"], "5.00")
        self.assertEqual(by_id["repo-1"]["commission"], "0.00")
        self.assertIn("REPO reward repo-1 REPO.US", by_id["repo-1"]["description"])
        yearly_derivatives = [row for row in result.dataset.tables["Years_Results"] if row["table"] == "Yearly Derivatives"]
        self.assertEqual(len(yearly_derivatives), 1)
        self.assertEqual(yearly_derivatives[0]["flag"], "non-preferential")
        self.assertEqual(yearly_derivatives[0]["tax_exchange"], "outofKZ")
        self.assertEqual(yearly_derivatives[0]["pnl"], "11.84")
        self.assertEqual(yearly_derivatives[0]["pnl_kzt"], "5552.96")
        self.assertEqual(yearly_derivatives[0]["only_profit"], "11.84")
        self.assertEqual(yearly_derivatives[0]["only_profit_kzt"], "5552.96")
        self.assertEqual(yearly_derivatives[0]["tax_kzt"], "555.30")
        yearly_interest = [row for row in result.dataset.tables["Years_Results"] if row["table"] == "Yearly Interest"]
        self.assertEqual(len(yearly_interest), 1)
        self.assertEqual(yearly_interest[0]["amount"], "5.00")
        self.assertEqual(yearly_interest[0]["only_profit"], "5.00")
        self.assertEqual(yearly_interest[0]["tax_kzt"], "234.50")
        self.assertEqual(
            result.dataset.raw_totals.scalar_totals[ReconciliationMetric.TOTAL_INTEREST.value],
            Decimal("16.84"),
        )

        # Reconciliation must use raw financing P/L, rather than merely
        # comparing the Interest table with itself.
        result.dataset.tables["Interest"] = []
        interest_item = next(
            item
            for item in ReconciliationEngine().reconcile_dataset(result.dataset)
            if item.metric == ReconciliationMetric.TOTAL_INTEREST
        )
        self.assertEqual(interest_item.broker_value, Decimal("16.84"))
        self.assertEqual(interest_item.canonical_value, Decimal("0"))
        self.assertEqual(interest_item.difference, Decimal("-16.84"))

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

    def test_split_security_legs_do_not_become_transfers_or_request_fifo_source(self) -> None:
        import pandas as pd  # type: ignore

        seen_requests: list[TransferInRequest] = []

        def resolver(request: TransferInRequest) -> None:
            seen_requests.append(request)
            return None

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "703847_2024-01-01 00_00_00_2024-12-31 23_59_59_all.xlsx"
            split_comment = "Stock split FORD.US (US3498624093). Factor 10/1."

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "FORD.US",
                            fe.COL_ISIN: "US3498624093",
                            fe.COL_MARKET: "NYSE/NASDAQ",
                            fe.COL_OPERATION: "Buy",
                            fe.COL_QTY: 16,
                            fe.COL_PRICE: 4,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 64,
                            fe.COL_REALIZED_PL: 0,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2024-01-02 10:00:00",
                            fe.COL_ORDER_ID: "ford-buy",
                        }
                    ]
                ).to_excel(writer, sheet_name="Trades 20240101 - 20241231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TYPE: "Split",
                            fe.COL_DATE: "2024-06-18",
                            fe.COL_ASSET: "Securities",
                            fe.COL_AMOUNT: -16,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_TICKER: "FORD.US",
                            fe.COL_ISIN: "US3498624093",
                            fe.COL_COMMENT: split_comment,
                        },
                        {
                            fe.COL_TYPE: "Split",
                            fe.COL_DATE: "2024-06-18",
                            fe.COL_ASSET: "Securities",
                            fe.COL_AMOUNT: 2,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_TICKER: "FORD.US",
                            fe.COL_ISIN: "US3498624093",
                            fe.COL_COMMENT: split_comment,
                        },
                    ]
                ).to_excel(writer, sheet_name="Corpactions 20240101 - 20241231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TYPE: "Split",
                            fe.COL_DATE: "2024-06-18 15:00:00",
                            fe.COL_QTY: -16,
                            fe.COL_TICKER: "FORD.US",
                            fe.COL_ISIN: "US3498624093",
                            fe.COL_COMMENT: split_comment,
                        },
                        {
                            fe.COL_TYPE: "Split",
                            fe.COL_DATE: "2024-06-18 15:00:01",
                            fe.COL_QTY: 2,
                            fe.COL_TICKER: "FORD.US",
                            fe.COL_ISIN: "US3498624093",
                            fe.COL_COMMENT: split_comment,
                        },
                    ]
                ).to_excel(writer, sheet_name="Sec In Out 20240101 - 20241231", index=False)

            rates = AnnualFxRateProvider({(2024, "USD"): Decimal("469")})
            parser = FreedomParser(
                fx_provider=rates,
                transfer_in_resolver=resolver,
            )
            result = parser.parse_reports(parser.discover_reports(raw_root, "703847"), "703847")

        self.assertEqual(seen_requests, [])
        self.assertFalse(any(row["symbol"] == "FORD.US" for row in result.dataset.tables["Transfers"]))
        ford_positions = [
            row
            for row in result.dataset.tables["Positions"]
            if row["symbol"] == "FORD.US" and row["year"] == 2024
        ]
        self.assertEqual(sum(Decimal(row["quantity"]) for row in ford_positions), Decimal("1.6"))

    def test_spinoff_security_dividend_does_not_become_transfer_in(self) -> None:
        import pandas as pd  # type: ignore

        seen_requests: list[TransferInRequest] = []

        def resolver(request: TransferInRequest) -> None:
            seen_requests.append(request)
            return None

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "703847_2022-01-01 00_00_00_2022-12-31 23_59_59_all.xlsx"
            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_TYPE: "Stock dividend",
                            fe.COL_DATE: "2022-04-13 15:00:00",
                            fe.COL_QTY: 1,
                            fe.COL_TICKER: "WBD.US",
                            fe.COL_ISIN: "US9344231041",
                            fe.COL_COMMENT: "Spin-off: T.US (US00206R1023) -> WBD.US (US9344231041). Factor: 1/0.241917.",
                        }
                    ]
                ).to_excel(writer, sheet_name="Sec In Out 20220101 - 20221231", index=False)

            rates = AnnualFxRateProvider({(2022, "USD"): Decimal("460")})
            parser = FreedomParser(fx_provider=rates, transfer_in_resolver=resolver)
            result = parser.parse_reports(parser.discover_reports(raw_root, "703847"), "703847")

        self.assertEqual(seen_requests, [])
        self.assertEqual(result.dataset.tables["Transfers"], [])
        spinoff = result.dataset.tables["CorporateActions"][0]
        self.assertEqual((spinoff["symbol"], spinoff["action_type"], spinoff["quantity"]), ("WBD.US", "spinoff", "1"))
        trade = result.dataset.tables["Trades"][0]
        self.assertEqual((trade["symbol"], trade["trade_type"], trade["amount"]), ("WBD.US", "corporate_action:spinoff", "0"))

    def test_cash_only_reorg_and_split_liquidations_create_fifo_exits(self) -> None:
        import pandas as pd  # type: ignore

        seen_requests: list[TransferInRequest] = []

        def resolver(request: TransferInRequest) -> None:
            seen_requests.append(request)
            return None

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "703847_2021-01-01 00_00_00_2022-12-31 23_59_59_all.xlsx"
            wifi_comment = "Reorg New Symbol: 1 WIFI (US09739C1027) -> 0 WIFI (US09739C1027); Reorg Cash: 1 WIFI -> $ 14"
            potx_comment = "Stock split POTX.US (US37954Y4263). Record date 2022-06-10, factor: 6/1."

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "WIFI.US",
                            fe.COL_ISIN: "US09739C1027",
                            fe.COL_OPERATION: "Buy",
                            fe.COL_QTY: 3,
                            fe.COL_PRICE: 14.04,
                            fe.COL_AMOUNT: 42.12,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2021-03-05 10:00:00",
                            fe.COL_ORDER_ID: "wifi-buy",
                        },
                        {
                            fe.COL_TICKER: "POTX.US",
                            fe.COL_ISIN: "US37954Y1459",
                            fe.COL_OPERATION: "Buy",
                            fe.COL_QTY: 2,
                            fe.COL_PRICE: 16,
                            fe.COL_AMOUNT: 32,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2021-04-06 10:00:00",
                            fe.COL_ORDER_ID: "potx-buy",
                        },
                    ]
                ).to_excel(writer, sheet_name="Trades 20210101 - 20221231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TYPE: "Split",
                            fe.COL_DATE: "2021-06-03",
                            fe.COL_ASSET: "Securities",
                            fe.COL_AMOUNT: -3,
                            fe.COL_TICKER: "WIFI.US",
                            fe.COL_ISIN: "US09739C1027",
                            fe.COL_CURRENCY: "USD",
                            fe.COL_COMMENT: wifi_comment,
                        },
                        {
                            fe.COL_TYPE: "Split",
                            fe.COL_DATE: "2022-06-13",
                            fe.COL_ASSET: "Securities",
                            fe.COL_AMOUNT: -2,
                            fe.COL_TICKER: "POTX.US",
                            fe.COL_ISIN: "US37954Y1459",
                            fe.COL_CURRENCY: "USD",
                            fe.COL_COMMENT: potx_comment,
                        },
                    ]
                ).to_excel(writer, sheet_name="Corpactions 20210101 - 20221231", index=False)
                pd.DataFrame(
                    [
                        {fe.COL_TYPE: "Corporate actions", fe.COL_DATE: "2021-06-04", fe.COL_AMOUNT: 42, fe.COL_CURRENCY: "USD", fe.COL_COMMENT: wifi_comment},
                        {
                            fe.COL_TYPE: "Split",
                            fe.COL_DATE: "2022-07-11",
                            fe.COL_AMOUNT: 6.06,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_COMMENT: "Компенсация при проведении корпоративного действия с бумагами (POTX.US), цена для оценки выбывающих бумаг 3.03 USD",
                        },
                    ]
                ).to_excel(writer, sheet_name="Cash In Out 20210101 - 20221231", index=False)

            parser = FreedomParser(
                fx_provider=AnnualFxRateProvider({(2021, "USD"): Decimal("426"), (2022, "USD"): Decimal("460")}),
                transfer_in_resolver=resolver,
            )
            result = parser.parse_reports(parser.discover_reports(raw_root, "703847"), "703847")

        self.assertEqual(seen_requests, [])
        exits = {
            row["symbol"]: (row["trade_type"], Decimal(row["quantity"]), Decimal(row["price"]), Decimal(row["amount"]))
            for row in result.dataset.tables["Trades"]
            if str(row["trade_type"]).startswith("corporate_action:")
        }
        self.assertEqual(exits["WIFI.US"], ("corporate_action:reorg_cash", Decimal("-3"), Decimal("14"), Decimal("42")))
        self.assertEqual(exits["POTX.US"], ("corporate_action:split_compensation", Decimal("-2"), Decimal("3.03"), Decimal("6.06")))
        self.assertTrue(all(row["_opening_lot_status"] == "matched" for row in result.dataset.tables["Fifo"]))
        self.assertEqual(result.dataset.tables["Transfers"], [])
        self.assertEqual(result.dataset.tables["Unprocessed"], [])

    def test_spinoff_and_split_rounding_compensations_use_cash_direction(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "703847_2023-01-01 00_00_00_2025-12-31 23_59_59_all.xlsx"
            ford_split = "Сплит по FORD.US (US3498624093). Дата среза 2024-06-17, коэффициент: 10/1."
            fgen_split = "Сплит по FGEN.US (US31572Q8814). Дата среза 2025-06-16, коэффициент: 25/1."
            solv_compensation = "Компенсация при проведении корпоративного действия с бумагами (MMM.US), расчетное количество бумаг SOLV.US к получению 0.5, получено 1, цена для оценки выбывающих бумаг 67.76 USD"
            ford_compensation = "Компенсация при проведении корпоративного действия с бумагами (FORD.US), расчетное количество бумаг к получению 1.6, получено 2, цена для оценки выбывающих бумаг 0.487 USD"
            fgen_compensation = "Компенсация при проведении корпоративного действия с бумагами (FGEN.US), расчетное количество бумаг к получению 7.52, получено 7, цена для оценки выбывающих бумаг 0.2661 USD"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {fe.COL_TICKER: "MMM.US", fe.COL_ISIN: "US88579Y1010", fe.COL_OPERATION: "Buy", fe.COL_QTY: 2, fe.COL_PRICE: 100, fe.COL_AMOUNT: 200, fe.COL_CURRENCY: "USD", fe.COL_COMMISSION: 0, fe.COL_TRADE_DATE: "2023-08-01", fe.COL_ORDER_ID: "mmm-buy"},
                        {fe.COL_TICKER: "FORD.US", fe.COL_ISIN: "US3498624093", fe.COL_OPERATION: "Buy", fe.COL_QTY: 16, fe.COL_PRICE: 3, fe.COL_AMOUNT: 48, fe.COL_CURRENCY: "USD", fe.COL_COMMISSION: 0, fe.COL_TRADE_DATE: "2024-01-01", fe.COL_ORDER_ID: "ford-buy-16"},
                        {fe.COL_TICKER: "FGEN.US", fe.COL_ISIN: "US31572Q8814", fe.COL_OPERATION: "Buy", fe.COL_QTY: 188, fe.COL_PRICE: 2, fe.COL_AMOUNT: 376, fe.COL_CURRENCY: "USD", fe.COL_COMMISSION: 0, fe.COL_TRADE_DATE: "2024-02-01", fe.COL_ORDER_ID: "fgen-buy"},
                        {fe.COL_TICKER: "SOLV.US", fe.COL_ISIN: "US83444M1018", fe.COL_OPERATION: "Sell", fe.COL_QTY: 1, fe.COL_PRICE: 75, fe.COL_AMOUNT: 75, fe.COL_REALIZED_PL: 20, fe.COL_CURRENCY: "USD", fe.COL_COMMISSION: 0, fe.COL_TRADE_DATE: "2025-01-31", fe.COL_ORDER_ID: "solv-sell"},
                        {fe.COL_TICKER: "FORD.US", fe.COL_ISIN: "US3498624093", fe.COL_OPERATION: "Buy", fe.COL_QTY: 7, fe.COL_PRICE: 6, fe.COL_AMOUNT: 42, fe.COL_CURRENCY: "USD", fe.COL_COMMISSION: 0, fe.COL_TRADE_DATE: "2025-02-03", fe.COL_ORDER_ID: "ford-buy-7"},
                        {fe.COL_TICKER: "FORD.US", fe.COL_ISIN: "US3498624093", fe.COL_OPERATION: "Sell", fe.COL_QTY: 9, fe.COL_PRICE: 7, fe.COL_AMOUNT: 63, fe.COL_REALIZED_PL: 10, fe.COL_CURRENCY: "USD", fe.COL_COMMISSION: 0, fe.COL_TRADE_DATE: "2025-06-13", fe.COL_ORDER_ID: "ford-sell-9"},
                    ]
                ).to_excel(writer, sheet_name="Trades 20230101 - 20251231", index=False)
                pd.DataFrame(
                    [
                        {fe.COL_TYPE: "Spin-off", fe.COL_DATE: "2024-04-01", fe.COL_ASSET: "Securities", fe.COL_AMOUNT: 1, fe.COL_PER_ONE: 67.76, fe.COL_TICKER: "SOLV.US", fe.COL_ISIN: "US83444M1018", fe.COL_CURRENCY: "USD", fe.COL_COMMENT: "Corporate action MMM.US (US88579Y1010) -> SOLV.US (US83444M1018). Factor 4/1."},
                        {fe.COL_TYPE: "Corporate action compensation", fe.COL_DATE: "2024-04-01", fe.COL_ASSET: "Money", fe.COL_AMOUNT: -33.88, fe.COL_PER_ONE: 67.76, fe.COL_TICKER: "MMM.US", fe.COL_ISIN: "US88579Y1010", fe.COL_CURRENCY: "USD", fe.COL_COMMENT: solv_compensation},
                        {fe.COL_TYPE: "Split", fe.COL_DATE: "2024-06-18", fe.COL_ASSET: "Securities", fe.COL_AMOUNT: -16, fe.COL_TICKER: "FORD.US", fe.COL_ISIN: "US3498624093", fe.COL_CURRENCY: "USD", fe.COL_COMMENT: ford_split},
                        {fe.COL_TYPE: "Split", fe.COL_DATE: "2024-06-18", fe.COL_ASSET: "Securities", fe.COL_AMOUNT: 2, fe.COL_TICKER: "FORD.US", fe.COL_ISIN: "US3498624093", fe.COL_CURRENCY: "USD", fe.COL_COMMENT: ford_split},
                        {fe.COL_TYPE: "Corporate action compensation", fe.COL_DATE: "2024-06-18", fe.COL_ASSET: "Money", fe.COL_AMOUNT: -1.95, fe.COL_PER_ONE: 0.487, fe.COL_TICKER: "FORD.US", fe.COL_ISIN: "US3498624093", fe.COL_CURRENCY: "USD", fe.COL_COMMENT: ford_compensation},
                        {fe.COL_TYPE: "Split", fe.COL_DATE: "2025-06-18", fe.COL_ASSET: "Securities", fe.COL_AMOUNT: -188, fe.COL_TICKER: "FGEN.US", fe.COL_ISIN: "US31572Q8814", fe.COL_CURRENCY: "USD", fe.COL_COMMENT: fgen_split},
                        {fe.COL_TYPE: "Split", fe.COL_DATE: "2025-06-18", fe.COL_ASSET: "Securities", fe.COL_AMOUNT: 7, fe.COL_TICKER: "FGEN.US", fe.COL_ISIN: "US31572Q8814", fe.COL_CURRENCY: "USD", fe.COL_COMMENT: fgen_split},
                        {fe.COL_TYPE: "Split", fe.COL_DATE: "2025-06-20", fe.COL_ASSET: "Money", fe.COL_AMOUNT: 3.46, fe.COL_PER_ONE: 0.2661, fe.COL_TICKER: "FGEN.US", fe.COL_ISIN: "US31572Q8814", fe.COL_CURRENCY: "USD", fe.COL_COMMENT: fgen_compensation},
                    ]
                ).to_excel(writer, sheet_name="Corpactions 20230101 - 20251231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2023, "USD"): Decimal("456"), (2024, "USD"): Decimal("469"), (2025, "USD"): Decimal("521")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "703847"), "703847")

        corporate_trades = [row for row in result.dataset.tables["Trades"] if str(row["trade_type"]).startswith("corporate_action:")]
        solv_entries = [(Decimal(row["quantity"]), Decimal(row["price"])) for row in corporate_trades if row["symbol"] == "SOLV.US"]
        self.assertEqual(solv_entries, [(Decimal("0.5"), Decimal("0")), (Decimal("0.5"), Decimal("67.76"))])
        ford_compensation_trade = next(row for row in corporate_trades if row["trade_type"] == "corporate_action:split_compensation" and row["symbol"] == "FORD.US")
        self.assertEqual((Decimal(ford_compensation_trade["quantity"]), Decimal(ford_compensation_trade["price"])), (Decimal("0.4"), Decimal("4.875")))
        fgen_compensation_trade = next(row for row in corporate_trades if row["trade_type"] == "corporate_action:split_compensation" and row["symbol"] == "FGEN.US")
        self.assertEqual(Decimal(fgen_compensation_trade["quantity"]), Decimal("-0.52"))
        self.assertEqual(Decimal(fgen_compensation_trade["amount"]), Decimal("3.46"))
        self.assertEqual(sum(Decimal(row["exit_quantity"]) for row in result.dataset.tables["Fifo"] if row["symbol"] == "FORD.US"), Decimal("9"))
        self.assertTrue(all(row["_opening_lot_status"] == "matched" for row in result.dataset.tables["Fifo"] if row["symbol"] in {"SOLV.US", "FORD.US", "FGEN.US"}))
        self.assertEqual(result.dataset.tables["Unprocessed"], [])

    def test_conversion_and_delayed_redemption_cash_are_linked_to_fifo(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "703847_2021-01-01 00_00_00_2026-12-31 23_59_59_all.xlsx"
            conversion = "Conversion of securities SHI.US (US82935M1099) -> SHIIY.US (US82935M1099). Cut date 2022-11-08, ratio: 1/1."

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {fe.COL_TICKER: "SHI.US", fe.COL_ISIN: "US82935M1099", fe.COL_OPERATION: "Buy", fe.COL_QTY: 10, fe.COL_PRICE: 23.71, fe.COL_AMOUNT: 237.1, fe.COL_CURRENCY: "USD", fe.COL_COMMISSION: 0, fe.COL_TRADE_DATE: "2021-06-25", fe.COL_ORDER_ID: "shi-buy"},
                        {fe.COL_TICKER: "MFRFB11.KZ", fe.COL_ISIN: "KZ2P00010937", fe.COL_OPERATION: "Buy", fe.COL_QTY: 10, fe.COL_PRICE: 100.6, fe.COL_AMOUNT: 10100, fe.COL_CURRENCY: "KZT", fe.COL_COMMISSION: 0, fe.COL_TRADE_DATE: "2025-04-22", fe.COL_ORDER_ID: "bond-buy"},
                    ]
                ).to_excel(writer, sheet_name="Trades 20210101 - 20261231", index=False)
                pd.DataFrame(
                    [
                        {fe.COL_TYPE: "Split", fe.COL_DATE: "2022-11-09", fe.COL_ASSET: "Securities", fe.COL_AMOUNT: -10, fe.COL_TICKER: "SHI.US", fe.COL_ISIN: "US82935M1099", fe.COL_CURRENCY: "USD", fe.COL_COMMENT: conversion},
                        {fe.COL_TYPE: "Split", fe.COL_DATE: "2022-11-09", fe.COL_ASSET: "Securities", fe.COL_AMOUNT: 10, fe.COL_TICKER: "SHIIY.US", fe.COL_ISIN: "US82935M1099", fe.COL_CURRENCY: "USD", fe.COL_COMMENT: conversion},
                        {fe.COL_TYPE: "Redemption", fe.COL_DATE: "2024-06-11", fe.COL_ASSET: "Securities", fe.COL_AMOUNT: -10, fe.COL_PER_ONE: 13.602935, fe.COL_TICKER: "SHIIY.US", fe.COL_ISIN: "US82935M1099", fe.COL_CURRENCY: "USD", fe.COL_COMMENT: "Redemption of SHIIY.US"},
                        {fe.COL_TYPE: "Redemption", fe.COL_DATE: "2024-06-09", fe.COL_ASSET: "Money", fe.COL_AMOUNT: 136.03, fe.COL_PER_ONE: 13.602935, fe.COL_TICKER: "SHIIY.US", fe.COL_ISIN: "US82935M1099", fe.COL_CURRENCY: "USD", fe.COL_COMMENT: "Redemption payment SHIIY.US"},
                        {fe.COL_TYPE: "Redemption", fe.COL_DATE: "2026-02-06", fe.COL_ASSET: "Money", fe.COL_AMOUNT: 10000, fe.COL_PER_ONE: 1000, fe.COL_TICKER: "MFRFB11.KZ", fe.COL_ISIN: "KZ2P00010937", fe.COL_CURRENCY: "KZT", fe.COL_COMMENT: "Redemption payment MFRFB11.KZ"},
                        {fe.COL_TYPE: "Redemption", fe.COL_DATE: "2026-02-10", fe.COL_ASSET: "Securities", fe.COL_AMOUNT: -10, fe.COL_PER_ONE: 1000, fe.COL_TICKER: "MFRFB11.KZ", fe.COL_ISIN: "KZ2P00010937", fe.COL_CURRENCY: "KZT", fe.COL_COMMENT: "Redemption of MFRFB11.KZ"},
                    ]
                ).to_excel(writer, sheet_name="Corpactions 20210101 - 20261231", index=False)
                pd.DataFrame(
                    [
                        {fe.COL_TICKER: "SHIIY.US", fe.COL_ISIN: "US82935M1099", fe.COL_ASSET_TYPE: "Stocks", fe.COL_END_QTY: 0},
                        {fe.COL_TICKER: "MFRFB11.KZ", fe.COL_ISIN: "KZ2P00010937", fe.COL_ASSET_TYPE: "Bonds", fe.COL_END_QTY: 0},
                    ]
                ).to_excel(writer, sheet_name="Securities 20210101 - 20261231", index=False)

            rates = AnnualFxRateProvider({(2021, "USD"): Decimal("426"), (2024, "USD"): Decimal("469"), (2025, "KZT"): Decimal("1"), (2026, "KZT"): Decimal("1")})
            result = FreedomParser(fx_provider=rates).parse_reports(FreedomParser().discover_reports(raw_root, "703847"), "703847")

        trades_by_symbol = {row["symbol"]: row for row in result.dataset.tables["Trades"] if row["trade_type"] == "corporate_action:redemption"}
        self.assertEqual((Decimal(trades_by_symbol["SHIIY.US"]["quantity"]), Decimal(trades_by_symbol["SHIIY.US"]["amount"])), (Decimal("-10"), Decimal("136.03")))
        self.assertEqual((trades_by_symbol["MFRFB11.KZ"]["asset_type"], Decimal(trades_by_symbol["MFRFB11.KZ"]["amount"])), ("Bonds", Decimal("10000")))
        self.assertNotIn("SHI.US", {row["symbol"] for row in result.dataset.tables["Trades"]})
        redemption_fifo = [row for row in result.dataset.tables["Fifo"] if row["corporate_action_type"] == "redemption"]
        self.assertEqual({row["symbol"] for row in redemption_fifo}, {"SHIIY.US", "MFRFB11.KZ"})
        self.assertTrue(all(row["_opening_lot_status"] == "matched" for row in redemption_fifo))
        bond_fifo = next(row for row in redemption_fifo if row["symbol"] == "MFRFB11.KZ")
        self.assertEqual((bond_fifo["enter_multiplier"], bond_fifo["exit_multiplier"]), ("10", "1"))
        self.assertEqual(Decimal(bond_fifo["pnl"]), Decimal("-60"))

    def test_ticker_change_round_trip_normalizes_to_latest_symbol(self) -> None:
        actions = [
            {"date_time": "2023-10-06 17:00:00", "action_type": "ticker_change", "isin": "US31572Q8814", "description": "Ticker change FGEN.US -> FGEN.ITS"},
            {"date_time": "2023-11-03 10:00:00", "action_type": "ticker_change", "isin": "US31572Q8814", "description": "Ticker change FGEN.ITS -> FGEN.US"},
        ]

        aliases = fe._ticker_change_aliases(actions)

        self.assertEqual(aliases[("US31572Q8814", "FGEN.US")], "FGEN.US")
        self.assertEqual(aliases[("US31572Q8814", "FGEN.ITS")], "FGEN.US")

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

    def test_conversion_alias_preserves_dividend_isin_and_country(self) -> None:
        import pandas as pd  # type: ignore

        conversion = "Conversion of securities GOLD.US (CA0679011084) -> B.US (CA06849F1080). Cut date 2025-05-08, ratio: 1/1."
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "GOLD_2023-01-01 00_00_00_2025-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {fe.COL_TICKER: "B.US", fe.COL_ISIN: "CA06849F1080", fe.COL_ASSET_TYPE: "Stocks", fe.COL_END_QTY: 155, fe.COL_CURRENCY: "USD"},
                    ]
                ).to_excel(writer, sheet_name="Securities 20230101 - 20251231", index=False)
                pd.DataFrame(
                    [
                        {fe.COL_TYPE: "Conversion", fe.COL_DATE: "2025-05-09", fe.COL_ASSET: "Securities", fe.COL_AMOUNT: -155, fe.COL_TICKER: "GOLD_OLD.US", fe.COL_ISIN: "CA0679011084", fe.COL_CURRENCY: "USD", fe.COL_COMMENT: conversion},
                        {fe.COL_TYPE: "Conversion", fe.COL_DATE: "2025-05-09", fe.COL_ASSET: "Securities", fe.COL_AMOUNT: 155, fe.COL_TICKER: "B.US", fe.COL_ISIN: "CA06849F1080", fe.COL_CURRENCY: "USD", fe.COL_COMMENT: conversion},
                    ]
                ).to_excel(writer, sheet_name="Corpactions 20230101 - 20251231", index=False)
                pd.DataFrame(
                    [
                        {fe.COL_TYPE: "Dividends", fe.COL_DATE: "2024-12-18", fe.COL_AMOUNT: 14.10, fe.COL_CURRENCY: "USD", fe.COL_COMMENT: "Dividends on security (Barrick Gold Corporation (GOLD.US)), record date 2024-11-29"},
                    ]
                ).to_excel(writer, sheet_name="Cash In Out 20230101 - 20251231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469"), (2025, "USD"): Decimal("522")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "GOLD"), "GOLD")

        dividend = result.dataset.tables["Dividends"][0]
        self.assertEqual(dividend["symbol"], "GOLD.US")
        self.assertEqual(dividend["isin"], "CA0679011084")
        self.assertEqual(dividend["country"], "CA")

    def test_split_isin_change_preserves_fifo_entry_date(self) -> None:
        import pandas as pd  # type: ignore

        split = "Stock split SQQQ.US (US74347G4322). Record date 2024-11-06, factor: 5/1."
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "SQQQ_2023-01-01 00_00_00_2025-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {fe.COL_TICKER: "SQQQ.US", fe.COL_ISIN: "US74347G1922", fe.COL_OPERATION: "Buy", fe.COL_QTY: 300, fe.COL_PRICE: 38.93, fe.COL_AMOUNT: 11679, fe.COL_CURRENCY: "USD", fe.COL_COMMISSION: 0, fe.COL_TRADE_DATE: "2023-03-15 17:11:59", fe.COL_ORDER_ID: "sqqq-buy"},
                        {fe.COL_TICKER: "SQQQ.US", fe.COL_ISIN: "US74350P6759", fe.COL_OPERATION: "Sell", fe.COL_QTY: 60, fe.COL_PRICE: 46.28, fe.COL_AMOUNT: 2776.8, fe.COL_REALIZED_PL: -8902.2, fe.COL_CURRENCY: "USD", fe.COL_COMMISSION: 14.6, fe.COL_TRADE_DATE: "2025-04-07 19:19:00", fe.COL_ORDER_ID: "sqqq-sell"},
                    ]
                ).to_excel(writer, sheet_name="Trades 20230101 - 20251231", index=False)
                pd.DataFrame(
                    [
                        {fe.COL_TYPE: "Split", fe.COL_DATE: "2024-11-07", fe.COL_ASSET: "Securities", fe.COL_AMOUNT: -300, fe.COL_TICKER: "SQQQ.US", fe.COL_ISIN: "US74347G1922", fe.COL_CURRENCY: "USD", fe.COL_COMMENT: split},
                        {fe.COL_TYPE: "Split", fe.COL_DATE: "2024-11-07", fe.COL_ASSET: "Securities", fe.COL_AMOUNT: 60, fe.COL_TICKER: "SQQQ.US", fe.COL_ISIN: "US74347G1922", fe.COL_CURRENCY: "USD", fe.COL_COMMENT: split},
                    ]
                ).to_excel(writer, sheet_name="Corpactions 20230101 - 20251231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2023, "USD"): Decimal("450"), (2025, "USD"): Decimal("522")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "SQQQ"), "SQQQ")

        fifo = result.dataset.tables["Fifo"]
        self.assertEqual(len(fifo), 1)
        self.assertEqual(fifo[0]["isin"], "US74350P6759")
        self.assertEqual(fifo[0]["enter_date"], "2023-03-15 17:11:59")
        self.assertEqual(fifo[0]["enter_quantity"], "60")
        self.assertEqual(fifo[0]["enter_price"], "194.65")

    def test_ticker_change_positions_match_raw_by_isin_without_duplicates(self) -> None:
        import pandas as pd  # type: ignore

        internal_transfer = "\u041f\u0435\u0440\u0435\u0432\u043e\u0434 \u0432\u043d\u0443\u0442\u0440\u0438 \u043a\u043e\u043c\u043f\u0430\u043d\u0438\u0438"
        ticker_change = "\u0421\u043c\u0435\u043d\u0430 \u0442\u0438\u043a\u0435\u0440\u0430"

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "759023_2022-05-12 23_59_59_2025-08-12 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "KEGC3.KZ",
                            fe.COL_ISIN: "KZ1C00000959",
                            fe.COL_MARKET: "KASE",
                            fe.COL_OPERATION: "Buy",
                            fe.COL_QTY: 887,
                            fe.COL_PRICE: 1482,
                            fe.COL_CURRENCY: "KZT",
                            fe.COL_AMOUNT: 1314534,
                            fe.COL_REALIZED_PL: 0,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2023-11-09 17:13:00",
                            fe.COL_ORDER_ID: "buy-kegc",
                        }
                    ]
                ).to_excel(writer, sheet_name="Trades 20220512 - 20250812", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_DATE: "2023-11-09 15:00:00",
                            fe.COL_TYPE: internal_transfer,
                            fe.COL_TICKER: "KEGC3.KZ",
                            fe.COL_ISIN: "KZ1C00000959",
                            fe.COL_QTY: -887,
                            fe.COL_COMMENT: ticker_change,
                        },
                        {
                            fe.COL_DATE: "2023-11-09 15:00:00",
                            fe.COL_TYPE: internal_transfer,
                            fe.COL_TICKER: "KEGC.KZ",
                            fe.COL_ISIN: "KZ1C00000959",
                            fe.COL_QTY: 887,
                            fe.COL_COMMENT: ticker_change,
                        },
                    ]
                ).to_excel(writer, sheet_name="Sec In Out 20220512 - 20250812", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "KEGC.KZ",
                            fe.COL_ISIN: "KZ1C00000959",
                            fe.COL_ASSET_TYPE: "Stocks",
                            fe.COL_START_QTY: 0,
                            fe.COL_END_QTY: 887,
                            fe.COL_PRICE: 1445.8,
                            fe.COL_CURRENCY: "KZT",
                        },
                        {
                            fe.COL_TICKER: "KEGC3.KZ",
                            fe.COL_ISIN: "KZ1C00000959",
                            fe.COL_ASSET_TYPE: "Stocks",
                            fe.COL_START_QTY: 0,
                            fe.COL_END_QTY: 0,
                            fe.COL_PRICE: 0,
                            fe.COL_CURRENCY: "KZT",
                        },
                    ]
                ).to_excel(writer, sheet_name="Securities 20220512 - 20250812", index=False)

            parser = FreedomParser(
                fx_provider=AnnualFxRateProvider({(2023, "KZT"): Decimal("1"), (2024, "KZT"): Decimal("1"), (2025, "KZT"): Decimal("1")})
            )
            result = parser.parse_reports(parser.discover_reports(raw_root, "759023"), "759023")

        kegc_positions = [row for row in result.dataset.tables["Positions"] if row["isin"] == "KZ1C00000959"]
        self.assertEqual([row["year"] for row in kegc_positions], [2023, 2024, 2025])
        self.assertTrue(all(row["symbol"] == "KEGC.KZ" for row in kegc_positions))
        self.assertEqual([Decimal(row["quantity"]) for row in kegc_positions], [Decimal("887"), Decimal("887"), Decimal("887")])
        self.assertFalse(any(row.get("_position_cost_basis_status") == "missing_transfer_in_fifo_source" for row in kegc_positions))

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
        self.assertIsNone(fifo_kzto["enter_date"])
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
        unprocessed_by_symbol = {
            row["symbol"]: row
            for row in result.dataset.tables["Unprocessed"]
            if row["symbol"] in {"KZTO.KZ", "KCEL.KZ", "HSBK.KZ"}
        }
        # Starting-balance lots that were sold surface as inferred transfer-in rows; HSBK has no sells.
        self.assertEqual(set(unprocessed_by_symbol), {"KZTO.KZ", "KCEL.KZ"})
        self.assertTrue(
            all(row["reason"] == "broker_pl_inferred_transfer_in" for row in unprocessed_by_symbol.values())
        )

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
        conversion_legs = [
            row
            for row in result.dataset.tables["Transfers"]
            if row["date"] == "2024-03-25" and row["symbol"] in {"GRPH.US", "LENZ.US"}
        ]
        self.assertEqual(len(conversion_legs), 2)
        self.assertTrue(all(row["_internal_corporate_action_transfer"] for row in conversion_legs))
        self.assertEqual(len(lenz_fifo), 2)
        self.assertTrue(all(row["_opening_lot_status"] == "matched" for row in lenz_fifo))
        self.assertEqual([row["enter_price"] for row in lenz_fifo], ["119", "119"])
        self.assertEqual([row["exit_quantity"] for row in lenz_fifo], ["0.7142857142857", "1"])
        self.assertFalse(any(row["symbol"] in {"GRPH.US", "LENZ.US"} for row in result.dataset.tables["Positions"]))
        self.assertEqual(result.dataset.tables["Unprocessed"], [])

    def test_repeated_transfer_out_attempts_net_to_single_outgoing_transfer(self) -> None:
        import pandas as pd  # type: ignore

        seen_requests: list[TransferInRequest] = []

        def resolver(request: TransferInRequest) -> list[TransferInFifoLot]:
            seen_requests.append(request)
            return []

        block = "Блокировка"
        withdraw = "Вывод в другой депозитарий"
        reclaim = "Перевод из другого депозитария"

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "744347_2023-01-01 00_00_00_2023-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "B.0.061324.BND",
                            fe.COL_ISIN: "US912797FS14",
                            fe.COL_ASSET_TYPE: "Bonds",
                            fe.COL_OPERATION: "Buy",
                            fe.COL_QTY: 2000,
                            fe.COL_PRICE: 1,
                            fe.COL_CURRENCY: "USD",
                            fe.COL_AMOUNT: 2000,
                            fe.COL_REALIZED_PL: 0,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2023-09-01 10:00:00",
                            fe.COL_ORDER_ID: "buy-bond",
                        }
                    ]
                ).to_excel(writer, sheet_name="Trades 20230101 - 20231231", index=False)
                # Two failed withdrawal attempts (blocked, withdrawn, then cancelled/reclaimed)
                # followed by a third attempt that actually leaves the account.
                pd.DataFrame(
                    [
                        {fe.COL_TYPE: block, fe.COL_DATE: "2023-09-21 11:32:49", fe.COL_ACCOUNT: "торговый", fe.COL_QTY: -2000, fe.COL_TICKER: "B.0.061324.BND", fe.COL_ISIN: "US912797FS14", fe.COL_COMMENT: "Блокировка по поручению 23112126"},
                        {fe.COL_TYPE: block, fe.COL_DATE: "2023-09-21 11:32:49", fe.COL_ACCOUNT: "Заблокировано под вывод", fe.COL_QTY: 2000, fe.COL_TICKER: "B.0.061324.BND", fe.COL_ISIN: "US912797FS14", fe.COL_COMMENT: "Блокировка по поручению 23112126"},
                        {fe.COL_TYPE: withdraw, fe.COL_DATE: "2023-09-22 15:00:00", fe.COL_ACCOUNT: "Заблокировано под вывод", fe.COL_QTY: -2000, fe.COL_TICKER: "B.0.061324.BND", fe.COL_ISIN: "US912797FS14", fe.COL_COMMENT: "Перевод по поручению 23112126"},
                        {fe.COL_TYPE: withdraw, fe.COL_DATE: "2023-09-25 09:09:48", fe.COL_ACCOUNT: "Заблокировано под вывод", fe.COL_QTY: 2000, fe.COL_TICKER: "B.0.061324.BND", fe.COL_ISIN: "US912797FS14", fe.COL_COMMENT: "Обратные проводки по отмене поручения 23112126"},
                        {fe.COL_TYPE: block, fe.COL_DATE: "2023-09-25 09:09:49", fe.COL_ACCOUNT: "Заблокировано под вывод", fe.COL_QTY: -2000, fe.COL_TICKER: "B.0.061324.BND", fe.COL_ISIN: "US912797FS14", fe.COL_COMMENT: "Обратные проводки по отмене поручения 23112126"},
                        {fe.COL_TYPE: block, fe.COL_DATE: "2023-09-25 09:09:49", fe.COL_ACCOUNT: "торговый", fe.COL_QTY: 2000, fe.COL_TICKER: "B.0.061324.BND", fe.COL_ISIN: "US912797FS14", fe.COL_COMMENT: "Обратные проводки по отмене поручения 23112126"},
                        {fe.COL_TYPE: block, fe.COL_DATE: "2023-09-28 16:46:31", fe.COL_ACCOUNT: "торговый", fe.COL_QTY: -2000, fe.COL_TICKER: "B.0.061324.BND", fe.COL_ISIN: "US912797FS14", fe.COL_COMMENT: "Блокировка по поручению 23194099"},
                        {fe.COL_TYPE: block, fe.COL_DATE: "2023-09-28 16:46:31", fe.COL_ACCOUNT: "Заблокировано под вывод", fe.COL_QTY: 2000, fe.COL_TICKER: "B.0.061324.BND", fe.COL_ISIN: "US912797FS14", fe.COL_COMMENT: "Блокировка по поручению 23194099"},
                        {fe.COL_TYPE: withdraw, fe.COL_DATE: "2023-10-03 15:00:00", fe.COL_ACCOUNT: "Заблокировано под вывод", fe.COL_QTY: -2000, fe.COL_TICKER: "B.0.061324.BND", fe.COL_ISIN: "US912797FS14", fe.COL_COMMENT: "Перевод по поручению 23194099"},
                        {fe.COL_TYPE: reclaim, fe.COL_DATE: "2023-10-03 15:00:01", fe.COL_ACCOUNT: "торговый", fe.COL_QTY: 2000, fe.COL_TICKER: "B.0.061324.BND", fe.COL_ISIN: "US912797FS14", fe.COL_COMMENT: "reclaim of 23194099"},
                        {fe.COL_TYPE: block, fe.COL_DATE: "2023-10-23 08:31:23", fe.COL_ACCOUNT: "торговый", fe.COL_QTY: -2000, fe.COL_TICKER: "B.0.061324.BND", fe.COL_ISIN: "US912797FS14", fe.COL_COMMENT: "Блокировка по поручению 23444243"},
                        {fe.COL_TYPE: block, fe.COL_DATE: "2023-10-23 08:31:23", fe.COL_ACCOUNT: "Заблокировано под вывод", fe.COL_QTY: 2000, fe.COL_TICKER: "B.0.061324.BND", fe.COL_ISIN: "US912797FS14", fe.COL_COMMENT: "Блокировка по поручению 23444243"},
                        {fe.COL_TYPE: withdraw, fe.COL_DATE: "2023-10-23 15:00:00", fe.COL_ACCOUNT: "Заблокировано под вывод", fe.COL_QTY: -2000, fe.COL_TICKER: "B.0.061324.BND", fe.COL_ISIN: "US912797FS14", fe.COL_COMMENT: "Перевод по поручению 23444243"},
                    ]
                ).to_excel(writer, sheet_name="Sec In Out 20230101 - 20231231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2023, "USD"): Decimal("460")}), transfer_in_resolver=resolver)
            result = parser.parse_reports(parser.discover_reports(raw_root, "744347"), "744347")

        # The retry saga must not look like a transfer in: no transfer-out file is requested.
        self.assertEqual(seen_requests, [])

        # The cancelling in/out duplicates are gone from the audit sheet: only the net row remains.
        bond_transfers = [row for row in result.dataset.tables["Transfers"] if row.get("isin") == "US912797FS14"]
        self.assertEqual(len(bond_transfers), 1)
        self.assertIn("Net of repeated transfer attempts", bond_transfers[0].get("broker_comment") or "")
        self.assertEqual(bond_transfers[0]["direction"], "out")
        self.assertEqual(Decimal(bond_transfers[0]["quantity"]), Decimal("2000"))

        # The 2000 bonds leave the account, leaving no residual position and nothing unprocessed.
        self.assertFalse(any(row.get("isin") == "US912797FS14" for row in result.dataset.tables["Positions"]))
        self.assertEqual([row for row in result.dataset.tables["Unprocessed"] if row.get("isin") == "US912797FS14"], [])

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
        self.assertEqual(yearly["flag"], "preferential")
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
        self.assertEqual(fx_trade["country"], "Kazakhstan")
        self.assertFalse(any("/" in str(row.get("symbol") or "") for row in result.dataset.tables["Positions"]))
        pnl_metric_prefix = ReconciliationMetric.PNL_AFTER_ALL_COMMISSIONS_BY_INSTRUMENT.value
        self.assertFalse(any(key.startswith(pnl_metric_prefix) and "US02376R1023" in key for key in result.raw_totals.totals_by_metric_currency))
        fx_fifo = next(row for row in result.dataset.tables["Fifo"] if row["asset_type"] == "Forex" and row["symbol"] == "KZT/USD")
        self.assertEqual(fx_fifo["country"], "Kazakhstan")

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
        self.assertEqual(yearly_trades[0]["flag"], "preferential")
        self.assertEqual(yearly_trades[0]["tax_exchange"], "KASE")
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
        self.assertIsNone(fifo["enter_date"])
        self.assertEqual(fifo["enter_price"], "16.9995")
        self.assertEqual(fifo["acquisition_cost_with_commission"], "67.998")
        self.assertEqual(fifo["pnl"], "-44.95")
        self.assertEqual(fifo["pnl_after_all_commissions"], "-46.15")
        self.assertNotEqual(fifo["enter_price"], "0")
        unprocessed = result.dataset.tables["Unprocessed"]
        self.assertEqual(len(unprocessed), 1)
        self.assertEqual(unprocessed[0]["reason"], "broker_pl_inferred_transfer_in")
        self.assertEqual(unprocessed[0]["symbol"], "CTKB.US")

    def test_freedom_average_cost_pnl_infers_transfer_in_price_before_fifo(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "861022_2018-06-24 23_59_59_2024-07-18 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_DATE: "2018-08-08 06:25:30",
                            fe.COL_TYPE: "Transfer",
                            fe.COL_TICKER: "BAST.KZ",
                            fe.COL_ISIN: "KZ1C00001015",
                            fe.COL_QTY: 369,
                            fe.COL_COMMENT: "Transfer in",
                        }
                    ]
                ).to_excel(writer, sheet_name="Sec In Out 20180624 - 20240718", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "BAST.KZ",
                            fe.COL_ISIN: "KZ1C00001015",
                            fe.COL_MARKET: "KASE",
                            fe.COL_OPERATION: "Buy",
                            fe.COL_QTY: 200,
                            fe.COL_PRICE: 29730.72,
                            fe.COL_CURRENCY: "KZT",
                            fe.COL_AMOUNT: 5946144,
                            fe.COL_REALIZED_PL: 0,
                            fe.COL_COMMISSION: 11893,
                            fe.COL_TRADE_DATE: "2019-02-07 13:34:39",
                            fe.COL_ORDER_ID: "buy-bast",
                        },
                        {
                            fe.COL_TICKER: "BAST.KZ",
                            fe.COL_ISIN: "KZ1C00001015",
                            fe.COL_MARKET: "KASE",
                            fe.COL_OPERATION: "Sell",
                            fe.COL_QTY: 200,
                            fe.COL_PRICE: 29584.82,
                            fe.COL_CURRENCY: "KZT",
                            fe.COL_AMOUNT: 5916964,
                            fe.COL_REALIZED_PL: -3198076.77,
                            fe.COL_COMMISSION: 11834,
                            fe.COL_TRADE_DATE: "2019-02-07 14:31:35",
                            fe.COL_ORDER_ID: "sell-bast-1",
                        },
                        {
                            fe.COL_TICKER: "BAST.KZ",
                            fe.COL_ISIN: "KZ1C00001015",
                            fe.COL_MARKET: "KASE",
                            fe.COL_OPERATION: "Sell",
                            fe.COL_QTY: 69,
                            fe.COL_PRICE: 29050.23,
                            fe.COL_CURRENCY: "KZT",
                            fe.COL_AMOUNT: 2004465.87,
                            fe.COL_REALIZED_PL: -1140223.20,
                            fe.COL_COMMISSION: 903,
                            fe.COL_TRADE_DATE: "2019-05-02 15:19:00",
                            fe.COL_ORDER_ID: "sell-bast-2",
                        },
                        {
                            fe.COL_TICKER: "BAST.KZ",
                            fe.COL_ISIN: "KZ1C00001015",
                            fe.COL_MARKET: "KASE",
                            fe.COL_OPERATION: "Sell",
                            fe.COL_QTY: 300,
                            fe.COL_PRICE: 29000.45,
                            fe.COL_CURRENCY: "KZT",
                            fe.COL_AMOUNT: 8700135,
                            fe.COL_REALIZED_PL: -4972426.16,
                            fe.COL_COMMISSION: 3916,
                            fe.COL_TRADE_DATE: "2019-05-02 15:26:48",
                            fe.COL_ORDER_ID: "sell-bast-3",
                        },
                    ]
                ).to_excel(writer, sheet_name="Trades 20180624 - 20240718", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2018, "KZT"): Decimal("1"), (2019, "KZT"): Decimal("1")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "861022"), "861022")

        bast_fifo = [row for row in result.dataset.tables["Fifo"] if row["symbol"] == "BAST.KZ"]
        inferred_rows = [row for row in bast_fifo if row["_opening_lot_status"] == "broker_average_inferred_transfer_in"]
        matched_rows = [row for row in bast_fifo if row["_opening_lot_status"] == "matched"]

        self.assertEqual([Decimal(row["enter_quantity"]) for row in inferred_rows], [Decimal("200"), Decimal("69"), Decimal("100")])
        self.assertTrue(all(Decimal(row["enter_price"]).quantize(Decimal("0.01")) == Decimal("54163.00") for row in inferred_rows))
        self.assertTrue(all(Decimal(row["enter_price"]).quantize(Decimal("0.01")) != Decimal("45575.20") for row in inferred_rows))
        self.assertEqual([Decimal(row["enter_quantity"]) for row in matched_rows], [Decimal("200")])
        self.assertEqual(Decimal(matched_rows[0]["enter_price"]), Decimal("29730.72"))
        self.assertEqual(
            {row["reason"] for row in result.dataset.tables["Unprocessed"] if row["symbol"] == "BAST.KZ"},
            {"broker_average_inferred_transfer_in"},
        )

    def test_security_transfer_uses_trade_currency_and_raw_zero_position_is_reconciled(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "861022_2018-06-24 23_59_59_2024-07-18 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_DATE: "2018-08-02 06:25:30",
                            fe.COL_TYPE: "Transfer",
                            fe.COL_TICKER: "ARWAB1.KZ",
                            fe.COL_ISIN: "KZ2P00003635",
                            fe.COL_QTY: 74950,
                            fe.COL_COMMENT: "Transfer in",
                        }
                    ]
                ).to_excel(writer, sheet_name="Sec In Out 20180624 - 20240718", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "ARWAB1.KZ",
                            fe.COL_ISIN: "KZ2P00003635",
                            fe.COL_MARKET: "KASE",
                            fe.COL_OPERATION: "Sell",
                            fe.COL_QTY: 74950,
                            fe.COL_PRICE: 728.75,
                            fe.COL_CURRENCY: "KZT",
                            fe.COL_AMOUNT: 54604812.5,
                            fe.COL_REALIZED_PL: -1250000,
                            fe.COL_COMMISSION: 5000,
                            fe.COL_TRADE_DATE: "2019-09-24 12:00:00",
                            fe.COL_ORDER_ID: "sell-arwab",
                        }
                    ]
                ).to_excel(writer, sheet_name="Trades 20180624 - 20240718", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "ARWAB1.KZ",
                            fe.COL_ISIN: "KZ2P00003635",
                            fe.COL_ASSET_TYPE: "Bonds",
                            fe.COL_END_QTY: 0,
                        }
                    ]
                ).to_excel(writer, sheet_name="Securities 20180624 - 20240718", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2018, "KZT"): Decimal("1"), (2019, "KZT"): Decimal("1")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "861022"), "861022")

        transfer = next(row for row in result.dataset.tables["Transfers"] if row["symbol"] == "ARWAB1.KZ")
        self.assertEqual(transfer["currency"], "KZT")

        positions = [row for row in result.dataset.tables["Positions"] if row["symbol"] == "ARWAB1.KZ"]
        self.assertEqual([int(row["year"]) for row in positions], [2018])
        self.assertEqual(Decimal(positions[0]["quantity"]), Decimal("74950"))

        raw_position_keys = result.dataset.raw_totals.positions_by_key
        self.assertEqual(raw_position_keys.get("|2024||KZ2P00003635"), Decimal("0"))
        self.assertNotIn(2024, {int(row["year"]) for row in positions})

    def test_old_freedom_trade_headers_and_empty_yearly_securities_snapshots(self) -> None:
        import pandas as pd  # type: ignore

        account = "broker_urbissinov83@gmail_com"
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_2018 = broker_root / f"{account}_2018_06_24_23_59_59_2018_12_31_23_59_59.xlsx"
            report_2019 = broker_root / f"{account}_2018_12_30_23_59_59_2019_12_31_23_59_59.xlsx"
            report_2020 = broker_root / f"{account}_2019_12_30_23_59_59_2020_12_31_23_59_59.xlsx"

            with pd.ExcelWriter(report_2018) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_DATE: "2018-08-08 06:25:30",
                            fe.COL_TYPE: "Transfer",
                            fe.COL_TICKER: "BAST.KZ",
                            fe.COL_ISIN: "KZ1C00001015",
                            fe.COL_QTY: 369,
                            fe.COL_COMMENT: "Transfer in",
                        }
                    ]
                ).to_excel(writer, sheet_name="Sec In Out 20180624 - 20181231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "BAST.KZ",
                            fe.COL_ISIN: "KZ1C00001015",
                            fe.COL_ASSET_TYPE: "Stocks",
                            fe.COL_START_QTY: 0,
                            fe.COL_END_QTY: 369,
                            fe.COL_CURRENCY: "KZT",
                        }
                    ]
                ).to_excel(writer, sheet_name="Securities 20180624 - 20181231", index=False)

            with pd.ExcelWriter(report_2019) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "BAST.KZ",
                            fe.COL_ISIN: "KZ1C00001015",
                            fe.COL_MARKET: "KASE",
                            fe.COL_OPERATION: "Покупка",
                            fe.COL_QTY: 200,
                            fe.COL_PRICE: 29730.72,
                            fe.COL_CURRENCY: "KZT",
                            fe.COL_AMOUNT: 5946144,
                            "Прибыль": 0,
                            fe.COL_COMMISSION: 11893,
                            "Дата": "2019-02-07 13:34:39",
                            fe.COL_ORDER_ID: "buy-bast",
                        },
                        {
                            fe.COL_TICKER: "BAST.KZ",
                            fe.COL_ISIN: "KZ1C00001015",
                            fe.COL_MARKET: "KASE",
                            fe.COL_OPERATION: "Продажа",
                            fe.COL_QTY: 200,
                            fe.COL_PRICE: 29584.82,
                            fe.COL_CURRENCY: "KZT",
                            fe.COL_AMOUNT: 5916964,
                            "Прибыль": -3198076.77,
                            fe.COL_COMMISSION: 11834,
                            "Дата": "2019-02-07 14:31:35",
                            fe.COL_ORDER_ID: "sell-bast-1",
                        },
                        {
                            fe.COL_TICKER: "BAST.KZ",
                            fe.COL_ISIN: "KZ1C00001015",
                            fe.COL_MARKET: "KASE",
                            fe.COL_OPERATION: "Продажа",
                            fe.COL_QTY: 369,
                            fe.COL_PRICE: 29000.45,
                            fe.COL_CURRENCY: "KZT",
                            fe.COL_AMOUNT: 10701166.05,
                            "Прибыль": -6112649.36,
                            fe.COL_COMMISSION: 4819,
                            "Дата": "2019-05-02 15:26:48",
                            fe.COL_ORDER_ID: "sell-bast-2",
                        },
                    ]
                ).to_excel(writer, sheet_name="Trades 20181230 - 20191231", index=False)
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "BAST.KZ",
                            fe.COL_ISIN: "KZ1C00001015",
                            fe.COL_ASSET_TYPE: "Stocks",
                            fe.COL_START_QTY: 369,
                            fe.COL_END_QTY: 0,
                        }
                    ]
                ).to_excel(writer, sheet_name="Securities 20181230 - 20191231", index=False)

            with pd.ExcelWriter(report_2020) as writer:
                pd.DataFrame(columns=[fe.COL_TICKER, fe.COL_ISIN, fe.COL_ASSET_TYPE, fe.COL_START_QTY, fe.COL_END_QTY]).to_excel(
                    writer,
                    sheet_name="Securities 20191230 - 20201231",
                    index=False,
                )

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2018, "KZT"): Decimal("1"), (2019, "KZT"): Decimal("1")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, account), account)

        self.assertTrue(all(row["date_time"] for row in result.dataset.tables["Trades"]))
        self.assertEqual([int(row["year"]) for row in result.dataset.tables["Positions"] if row["symbol"] == "BAST.KZ"], [2018])
        self.assertEqual(result.dataset.raw_totals.positions_by_key.get("|2020||KZ1C00001015"), Decimal("0"))
        inferred_rows = [
            row
            for row in result.dataset.tables["Fifo"]
            if row["symbol"] == "BAST.KZ" and row["_opening_lot_status"] == "broker_average_inferred_transfer_in"
        ]
        self.assertTrue(inferred_rows)
        self.assertTrue(all(Decimal(row["enter_price"]) > 0 for row in inferred_rows))

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

    def test_coupon_identifies_fully_sold_bond_and_excludes_accrued_coupon_from_multiplier(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "BOND10_2025-01-01 00_00_00_2025-12-31 23_59_59_all.xlsx"

            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {
                            fe.COL_TICKER: "BOND10.KZ",
                            fe.COL_ISIN: "KZ2C00013258",
                            fe.COL_MARKET: "KASE",
                            fe.COL_OPERATION: "Buy",
                            fe.COL_QTY: 19,
                            fe.COL_PRICE: 98.95,
                            fe.COL_AMOUNT: 19110.83,
                            fe.COL_CURRENCY: "KZT",
                            fe.COL_REALIZED_PL: 0,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2025-06-10 11:29:55",
                            fe.COL_ORDER_ID: "bond-buy",
                        },
                        {
                            fe.COL_TICKER: "BOND10.KZ",
                            fe.COL_ISIN: "KZ2C00013258",
                            fe.COL_MARKET: "KASE",
                            fe.COL_OPERATION: "Sell",
                            fe.COL_QTY: 19,
                            fe.COL_PRICE: 100,
                            fe.COL_AMOUNT: 19099.75,
                            fe.COL_CURRENCY: "KZT",
                            fe.COL_REALIZED_PL: 199.5,
                            fe.COL_COMMISSION: 0,
                            fe.COL_TRADE_DATE: "2025-10-21 14:34:15",
                            fe.COL_ORDER_ID: "bond-sell",
                        },
                    ]
                ).to_excel(writer, sheet_name="Trades 20250101 - 20251231", index=False)
                # The position is closed, therefore Securities does not
                # contain the instrument and cannot supply its asset type.
                pd.DataFrame(columns=[fe.COL_TICKER, fe.COL_ISIN, fe.COL_ASSET_TYPE]).to_excel(
                    writer,
                    sheet_name="Securities 20250101 - 20251231",
                    index=False,
                )
                pd.DataFrame(
                    [
                        {
                            fe.COL_TYPE: "Coupon",
                            fe.COL_DATE: "2025-07-18",
                            fe.COL_ASSET: "Money",
                            fe.COL_AMOUNT: 332.5,
                            fe.COL_TICKER: "BOND10.KZ",
                            fe.COL_ISIN: "KZ2C00013258",
                            fe.COL_CURRENCY: "KZT",
                        }
                    ]
                ).to_excel(writer, sheet_name="Corpactions 20250101 - 20251231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2025, "KZT"): Decimal("1")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "BOND10"), "BOND10")

        trades = result.dataset.tables["Trades"]
        self.assertEqual([row["asset_type"] for row in trades], ["Bonds", "Bonds"])
        self.assertEqual([row["multiplier"] for row in trades], ["10", "10"])
        self.assertEqual(Decimal(result.dataset.tables["Fifo"][0]["pnl"]), Decimal("199.5"))


    def test_coupon_revert_reduces_only_profit_but_negative_nkd_does_not(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / fe.BROKER_CODE
            broker_root.mkdir(parents=True)
            report_path = broker_root / "7A3453_2024-01-01 00_00_00_2024-12-31 23_59_59_all.xlsx"
            description = "Coupon on security (Bond issuer (BOND.AIX.KZ)), record date 2024-05-18."
            with pd.ExcelWriter(report_path) as writer:
                pd.DataFrame(
                    [
                        {fe.COL_TYPE: "Coupon", fe.COL_DATE: "2024-05-01", fe.COL_AMOUNT: 20, fe.COL_CURRENCY: "USD", fe.COL_COMMENT: description},
                        {fe.COL_TYPE: "Coupon", fe.COL_DATE: "2024-05-02", fe.COL_AMOUNT: -5, fe.COL_CURRENCY: "USD", fe.COL_COMMENT: description},
                        {fe.COL_TYPE: "Coupon", fe.COL_DATE: "2024-05-03", fe.COL_AMOUNT: -20, fe.COL_CURRENCY: "USD", fe.COL_COMMENT: f"Reverted: {description}"},
                        {fe.COL_TYPE: "Coupon", fe.COL_DATE: "2024-05-03", fe.COL_AMOUNT: 20, fe.COL_CURRENCY: "USD", fe.COL_COMMENT: description},
                    ]
                ).to_excel(writer, sheet_name="Cash In Out 20240101 - 20241231", index=False)

            parser = FreedomParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "7A3453"), "7A3453")

        coupons = result.dataset.tables["Coupons"]
        self.assertEqual([row["is_revert"] for row in coupons], [False, False, True, False])
        yearly_coupon = next(row for row in result.dataset.tables["Years_Results"] if row["table"] == "Yearly Coupons")
        self.assertEqual(yearly_coupon["amount"], "15.00")
        self.assertEqual(yearly_coupon["only_profit"], "20.00")
        self.assertEqual(yearly_coupon["only_profit_kzt"], "9380.00")


if __name__ == "__main__":
    unittest.main()
