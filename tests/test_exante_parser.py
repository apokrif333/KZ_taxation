from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from conftest_imports import SRC  # noqa: F401
from kztax270.brokers.exante import ExanteParser
from kztax270.reconciliation.engine import ReconciliationEngine
from kztax270.reconciliation.models import ReconciliationSeverity
from kztax270.reference.fx import AnnualFxRateProvider

MINIMAL_EXANTE_CSV = '''"Costs and Charges Report: 2023-01-01 - 2023-12-31"
"Account"\t"EX1"
""
"Time"\t"Account ID"\t"Side"\t"Symbol ID"\t"ISIN"\t"Type"\t"Price"\t"Currency"\t"Quantity"\t"Commission"\t"Commission Currency"\t"P&L"\t"Traded Volume"\t"Order Id"\t"Order pos"\t"Value Date"\t"Unique Transaction Identifier (UTI)"\t"Trade type"\t"Exchange Order ID"
"2023-01-10 10:00:00"\t"EX1"\t"buy"\t"AAPL.NASDAQ"\t"US0378331005"\t"STOCK"\t"100"\t"USD"\t"10"\t"1"\t"USD"\t"0"\t"1000"\t"1"\t"1"\t""\t""\t""\t""
"2023-02-10 10:00:00"\t"EX1"\t"sell"\t"AAPL.NASDAQ"\t"US0378331005"\t"STOCK"\t"110"\t"USD"\t"5"\t"1"\t"USD"\t"50"\t"550"\t"2"\t"1"\t""\t""\t""\t""
"Transaction ID"\t"Account ID"\t"Symbol ID"\t"ISIN"\t"Operation type"\t"When"\t"Sum"\t"Asset"\t"EUR equivalent"\t"Comment"\t"UUID"\t"Parent UUID"
"10"\t"EX1"\t"AAPL.NASDAQ"\t"None"\t"DIVIDEND"\t"2023-03-01 06:00:00"\t"10"\t"USD"\t""\t"10 shares ExD 2023-02-25 PD 2023-03-01 dividend AAPL.NASDAQ 10 USD (1 per share) tax -1.50 USD (-15.000%) DivCntry US USIncmCode 52"\t""\t""
"11"\t"EX1"\t"AAPL.NASDAQ"\t"None"\t"US TAX"\t"2023-03-01 06:00:01"\t"-1.5"\t"USD"\t""\t"10 shares ExD 2023-02-25 PD 2023-03-01 dividend AAPL.NASDAQ 10 USD (1 per share) tax -1.50 USD (-15.000%) DivCntry US USIncmCode 52"\t""\t""
"12"\t"EX1"\t"AAPL.NASDAQ"\t"US0378331005"\t"SECURITY TRANSFER"\t"2023-12-31 12:00:00"\t"-5"\t"AAPL.NASDAQ"\t""\t"None"\t""\t""
'''


TRANSACTIONS_FIRST_WITH_MARGIN_TAIL_EXANTE_CSV = '''"Metrics per symbols"
"Symbol"\t"Turnover"\t"Realised P&L"\t"Unrealised P&L"\t"P&L"
"AAPL.NASDAQ"\t"1"\t"0"\t"0"\t"0"
""
"Transaction ID"\t"Account ID"\t"Symbol ID"\t"ISIN"\t"Operation type"\t"When"\t"Sum"\t"Asset"\t"EUR equivalent"\t"Comment"\t"UUID"\t"Parent UUID"\t""\t""\t""\t""\t""\t""\t""\t""
"1"\t"EX2"\t"None"\t"None"\t"FUNDING/WITHDRAWAL"\t"4/19/2023 11:08"\t"1000"\t"USD"\t"900"\t"Cash in"\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""
"2"\t"EX2"\t"AAPL.NASDAQ"\t"US0378331005"\t"EXERCISE"\t"4/21/2023 11:08"\t"1"\t"AAPL.NASDAQ"\t"100"\t"Option exercise placeholder"\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""
"3"\t"EX2"\t"EUR/USD.E.FX"\t"None"\t"AUTOCONVERSION"\t"4/22/2023 11:08"\t"-100"\t"USD"\t"90"\t"crossrate=1.1 commission=0.002 conversion rate=1.1 is commission applied=true"\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""
"4"\t"EX2"\t"AAPL.NASDAQ"\t"US0378331005"\t"ROLLOVER"\t"4/23/2023 11:08"\t"-1"\t"USD"\t"1"\t"short 1.00 AAPL.NASDAQ from 2023-04-24 till 2023-04-25"\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""
"5"\t"EX2"\t"None"\t"None"\t"ELECTRONIC TRANSFER"\t"4/24/2023 11:08"\t"-200"\t"USD"\t"180"\t"from EX2 to EX3"\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""
"6"\t"EX2"\t"None"\t"None"\t"BANK CHARGE"\t"4/25/2023 11:08"\t"-15"\t"USD"\t"13.5"\t"Bank withdrawal fee"\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""
""
"Time"\t"Account ID"\t"Side"\t"Symbol ID"\t"ISIN"\t"Type"\t"Price"\t"Currency"\t"Quantity"\t"Commission"\t"Commission Currency"\t"P&L"\t"Traded Volume"\t"Order Id"\t"Order pos"\t"Value Date"\t"Unique Transaction Identifier (UTI)"\t"Trade type"\t"Exchange Order ID"\t""
"4/20/2023 10:55"\t"EX2"\t"buy"\t"AAPL.NASDAQ"\t"US0378331005"\t"STOCK"\t"100"\t"USD"\t"1"\t"1"\t"USD"\t"0"\t"100"\t""\t""\t"4/24/2023"\t""\t"TRADE"\t""\t""
"4/20/2023 10:56"\t"EX2"\t"buy"\t"AAPL.NASDAQ"\t"US0378331005"\t"STOCK"\t"0"\t"USD"\t"0"\t"None"\t"USD"\t"0"\t"0"\t""\t""\t"4/24/2023"\t""\t"TRADE"\t""\t""
"4/20/2023 10:57"\t"EX2"\t"exercise"\t"AAPL.NASDAQ"\t"US0378331005"\t"STOCK"\t"0"\t"USD"\t"0"\t"None"\t"USD"\t"0"\t"0"\t""\t""\t"4/24/2023"\t""\t"TRADE"\t""\t""
""
"General EX2, 2023-12-31"\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""
"Account"\t"Date"\t"Account Value"\t"Available"\t"Used Margin"\t"Margin Utilization"\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""
"EX2"\t"2/27/2024"\t"0"\t"0"\t"0"\t"0.00%"\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""\t""
'''


SNAPSHOT_REVERT_FX_EXANTE_CSV = '''"Costs and Charges Report: 2024-01-01 - 2024-12-31"
"Account"\t"EX3"
""
"Time"\t"Account ID"\t"Side"\t"Symbol ID"\t"ISIN"\t"Type"\t"Price"\t"Currency"\t"Quantity"\t"Commission"\t"Commission Currency"\t"P&L"\t"Traded Volume"\t"Order Id"\t"Order pos"\t"Value Date"\t"Unique Transaction Identifier (UTI)"\t"Trade type"\t"Exchange Order ID"
"2024-02-19 08:25:14"\t"EX3"\t"buy"\t"EUR/USD.E.FX"\t"None"\t"FOREX"\t"1.07855"\t"USD"\t"1000"\t"0.06"\t"USD"\t"-1.67"\t"1078.55"\t"fx1"\t"1"\t""\t""\t""\t""
"Transaction ID"\t"Account ID"\t"Symbol ID"\t"ISIN"\t"Operation type"\t"When"\t"Sum"\t"Asset"\t"EUR equivalent"\t"Comment"\t"UUID"\t"Parent UUID"
"1"\t"EX3"\t"TLT.NASDAQ"\t"US4642874329"\t"SECURITY TRANSFER"\t"2024-01-02 10:00:00"\t"285"\t"TLT.NASDAQ"\t""\t"Transfer in"\t""\t""
"2"\t"EX3"\t"TLT.NASDAQ"\t"None"\t"DIVIDEND"\t"2024-09-06 06:00:00"\t"89.15"\t"USD"\t""\t"285 shares ExD 2024-09-03 PD 2024-09-06 dividend TLT.NASDAQ 89.15 USD (0.312782 per share) tax -26.75 USD (-30.00%) DivCntry US"\t""\t""
"3"\t"EX3"\t"TLT.NASDAQ"\t"None"\t"US TAX"\t"2024-10-02 13:33:55"\t"13.38"\t"USD"\t""\t"Tax recalculation for 285.0 shares ExD 2024-09-03 PD 2024-09-06 dividend TLT.NASDAQ 89.15 USD (0.312782 per share) tax -26.75 USD (-30.00%) DivCntry US"\t""\t""
"4"\t"EX3"\t"None"\t"None"\t"INTEREST"\t"2024-02-20 03:00:00"\t"-0.15"\t"USD"\t""\t"margin interest"\t""\t""
"Cash Balance EX3, 2024-07-30"
"Instrument"\t"ISO"\t"Value"\t"Locked value"\t"Value in USD"
"US Dollar"\t"USD"\t"42.42"\t"0"\t"42.42"
""
"Stocks & ETFs (EX3, 2024-07-30)"
"Instrument"\t"Financial Instrument Global Identifier (FIGI)"\t"Description"\t"Name"\t"QTY"\t"Locked quantity"\t"Avg Price"\t"Price"\t"Currency"\t"P&L"\t"P&L in USD"\t"P&L, %"\t"Value"\t"Value in USD"\t"Daily P&L"\t"Daily P&L in USD"\t"Daily P&L, %"\t"ISIN"
"TLT.NASDAQ"\t"BBG000BJL314"\t"Ishares Trust Barclays 20+ Year Treasury Bond ETF"\t"TLT"\t"285"\t"0"\t"90"\t"95"\t"USD"\t"0"\t"0"\t"0"\t"27075"\t"27075"\t"0"\t"0"\t"0"\t"US4642874329"
'''


SELL_ONLY_EXANTE_CSV = '''"Costs and Charges Report: 2024-01-01 - 2024-12-31"
"Account"\t"EX4"
""
"Time"\t"Account ID"\t"Side"\t"Symbol ID"\t"ISIN"\t"Type"\t"Price"\t"Currency"\t"Quantity"\t"Commission"\t"Commission Currency"\t"P&L"\t"Traded Volume"\t"Order Id"\t"Order pos"\t"Value Date"\t"Unique Transaction Identifier (UTI)"\t"Trade type"\t"Exchange Order ID"
"2024-05-29 19:49:07"\t"EX4"\t"sell"\t"CTKB.NASDAQ"\t"US19200A1051"\t"STOCK"\t"50"\t"USD"\t"2"\t"1"\t"USD"\t"-20"\t"100"\t"sell-ctkb"\t"1"\t""\t""\t""\t""
'''


DERIVATIVES_EXANTE_CSV = '''"Costs and Charges Report: 2024-01-01 - 2024-12-31"
"Account"\t"EXD"
""
"Time"\t"Account ID"\t"Side"\t"Symbol ID"\t"ISIN"\t"Type"\t"Price"\t"Currency"\t"Quantity"\t"Commission"\t"Commission Currency"\t"P&L"\t"Traded Volume"\t"Order Id"\t"Order pos"\t"Value Date"\t"Unique Transaction Identifier (UTI)"\t"Trade type"\t"Exchange Order ID"
"2024-01-10 10:00:00"\t"EXD"\t"buy"\t"MES.CME.Z2024"\t"None"\t"FUTURE"\t"100"\t"USD"\t"1"\t"2"\t"USD"\t"0"\t"100"\t"fut-open"\t"1"\t""\t""\t""\t""
"2024-01-11 10:00:00"\t"EXD"\t"sell"\t"MES.CME.Z2024"\t"None"\t"FUTURE"\t"110"\t"USD"\t"1"\t"3"\t"USD"\t"10"\t"110"\t"fut-close"\t"1"\t""\t""\t""\t""
"2024-02-19 08:25:14"\t"EXD"\t"sell"\t"EUR/USD.E.FX"\t"None"\t"FX_SPOT"\t"1.53"\t"USD"\t"1000"\t"0.2"\t"USD"\t"0"\t"1530"\t"fx-open"\t"1"\t""\t""\t""\t""
"2024-02-20 08:25:14"\t"EXD"\t"buy"\t"EUR/USD.E.FX"\t"None"\t"FX_SPOT"\t"1.52"\t"USD"\t"1000"\t"0.3"\t"USD"\t"10"\t"1520"\t"fx-close"\t"1"\t""\t""\t""\t""
'''


HXR_TRANSFER_KSPI_OPTION_EXANTE_CSV = '''"Costs and Charges Report: 2022-01-01 - 2023-12-31"
"Account"\t"HXR2208.001"
""
"Time"\t"Account ID"\t"Side"\t"Symbol ID"\t"ISIN"\t"Type"\t"Price"\t"Currency"\t"Quantity"\t"Commission"\t"Commission Currency"\t"P&L"\t"Traded Volume"\t"Order Id"\t"Order pos"\t"Value Date"\t"Unique Transaction Identifier (UTI)"\t"Trade type"\t"Exchange Order ID"
"2023-02-15 10:00:00"\t"HXR2208.001"\t"buy"\t"SPY.CBOE.30M2023.P350"\t"None"\t"STOCK"\t"1.5"\t"USD"\t"2"\t"1"\t"USD"\t"0"\t"300"\t"opt1"\t"1"\t""\t""\t""\t""
"2023-07-02 21:02:08"\t"HXR2208.001"\t"sell"\t"SPY.CBOE.30M2023.P350"\t"None"\t"OPTION"\t"0"\t"USD"\t"2"\t"None"\t"None"\t"-300"\t"0"\t"opt-exp"\t"1"\t""\t""\t"EXERCISE"\t""
"Transaction ID"\t"Account ID"\t"Symbol ID"\t"ISIN"\t"Operation type"\t"When"\t"Sum"\t"Asset"\t"EUR equivalent"\t"Comment"\t"UUID"\t"Parent UUID"
"100"\t"HXR2208.001"\t"KSPI.LSEIOB"\t"US48581R2058"\t"FUNDING/WITHDRAWAL"\t"2022-09-02 07:06:03"\t"86"\t"KSPI.LSEIOB"\t""\t"Securities transfer"\t""\t""
"101"\t"HXR2208.001"\t"KSPI.LSEIOB"\t"None"\t"DIVIDEND"\t"2023-01-02 06:00:00"\t"100"\t"USD"\t""\t"86 shares ExD 2022-12-29 PD 2023-01-02 dividend KSPI.LSEIOB 100 USD (1.16279 per share) tax 0 USD"\t""\t""
"102"\t"HXR2208.001"\t"SPY.CBOE.30M2023.P350"\t"None"\t"EXERCISE"\t"2023-07-02 21:02:08"\t"0"\t"USD"\t""\t"Option expiration cash leg"\t""\t""
"103"\t"HXR2208.001"\t"SPY.CBOE.30M2023.P350"\t"None"\t"EXERCISE"\t"2023-07-02 21:02:08"\t"-2"\t"SPY.CBOE.30M2023.P350"\t""\t"Option expiration"\t""\t""
'''


class ExanteParserTests(unittest.TestCase):
    def test_exante_trades_dividends_and_transfer_out_use_canonical_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / "exante"
            broker_root.mkdir(parents=True)
            path = broker_root / "Custom_EX1.csv"
            path.write_text(MINIMAL_EXANTE_CSV, encoding="utf-16")

            parser = ExanteParser(fx_provider=AnnualFxRateProvider({(2023, "USD"): Decimal("470")}))
            reports = parser.discover_reports(raw_root, "EX1")
            self.assertEqual(len(reports), 1)

            result = parser.parse_reports(reports, "EX1")
            dataset = result.dataset

        self.assertEqual(len(dataset.tables["Trades"]), 2)
        self.assertEqual(dataset.tables["Trades"][0]["quantity"], "10")
        self.assertEqual(dataset.tables["Trades"][1]["quantity"], "-5")

        fifo = dataset.tables["Fifo"][0]
        self.assertEqual(fifo["symbol"], "AAPL")
        self.assertEqual(fifo["pnl_before_commission"], "50")
        self.assertEqual(fifo["pnl"], "49.5")
        self.assertEqual(fifo["pnl_after_all_commissions"], "48.5")

        dividend = dataset.tables["Dividends"][0]
        self.assertEqual(dividend["isin"], "US0378331005")
        self.assertEqual(dividend["country"], "US")
        self.assertEqual(dividend["gross_amount"], "10.00")
        self.assertEqual(dividend["withholding_tax"], "-1.50")
        self.assertEqual(dividend["tax"], "1.00")
        self.assertEqual(dividend["tax_kzt"], "470.00")

        security_transfers = [row for row in dataset.tables["Transfers"] if row["transfer_type"] == "security"]
        self.assertEqual(len(security_transfers), 1)
        self.assertEqual(security_transfers[0]["symbol"], "AAPL")
        self.assertEqual(security_transfers[0]["quantity"], "5")
        self.assertEqual(security_transfers[0]["price"], "100.1")
        self.assertEqual(security_transfers[0]["enter_date"], "2023-01-10 10:00:00")

        reconciliation = ReconciliationEngine().reconcile_dataset(dataset)
        non_info = [item for item in reconciliation if item.severity != ReconciliationSeverity.INFO]
        self.assertEqual(non_info, [])

    def test_transactions_before_trades_does_not_pull_margin_tail_into_trades(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / "exante"
            broker_root.mkdir(parents=True)
            path = broker_root / "Custom_EX2.csv"
            path.write_text(TRANSACTIONS_FIRST_WITH_MARGIN_TAIL_EXANTE_CSV, encoding="utf-16")

            parser = ExanteParser(fx_provider=AnnualFxRateProvider({(2023, "USD"): Decimal("470")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "EX2"), "EX2")

        self.assertEqual(len(result.dataset.tables["Trades"]), 2)
        self.assertEqual(len(result.dataset.tables["Instruments"]), 1)
        self.assertEqual(result.dataset.tables["Trades"][0]["date_time"], "2023-04-20 10:55:00")
        self.assertEqual(result.dataset.tables["Trades"][0]["symbol"], "AAPL")
        self.assertEqual(result.dataset.tables["Trades"][1]["quantity"], "0")
        self.assertEqual([row["symbol"] for row in result.dataset.tables["Instruments"]], ["AAPL"])
        self.assertFalse(
            any("Margin Utilization" in str(value) for row in result.dataset.tables["Trades"] for value in row.values())
        )
        self.assertFalse(
            any("Margin Utilization" in str(value) for row in result.dataset.tables["Instruments"] for value in row.values())
        )
        self.assertFalse(
            any(str(value) in {"Side", "buy", "sell", "Account Value"} for row in result.dataset.tables["Instruments"] for value in row.values())
        )
        cash_transfers = [row for row in result.dataset.tables["Transfers"] if row["transfer_type"] == "cash"]
        self.assertEqual(len(cash_transfers), 3)
        self.assertEqual([row["amount"] for row in cash_transfers], ["1000", "-200", "-15"])
        self.assertEqual(cash_transfers[2]["broker_comment"], "BANK CHARGE: Bank withdrawal fee")
        self.assertFalse(any("crossrate=" in str(row.get("broker_comment")) for row in cash_transfers))
        self.assertFalse(any("from 2023-04-24 till" in str(row.get("broker_comment")) for row in cash_transfers))
        self.assertEqual(len(result.dataset.tables["Unprocessed"]), 2)
        reasons = {row["reason"] for row in result.dataset.tables["Unprocessed"]}
        self.assertEqual(reasons, {"unhandled_exante_transaction", "unsupported_exante_trade_side"})

    def test_exante_snapshots_reverts_and_fx_do_not_create_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / "exante"
            broker_root.mkdir(parents=True)
            path = broker_root / "Custom_EX3.csv"
            path.write_text(SNAPSHOT_REVERT_FX_EXANTE_CSV, encoding="utf-16")

            parser = ExanteParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469.44")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "EX3"), "EX3")
            dataset = result.dataset

        self.assertEqual(dataset.tables["CashBalances"], [
            {
                "year": 2024,
                "date": "2024-07-30",
                "currency": "USD",
                "ending_cash": "42.42",
                "ending_cash_kzt": "19913.6448",
                "source_report": str(path),
            }
        ])

        reverts = [
            row
            for row in dataset.tables["Dividends"]
            if Decimal(row["gross_amount"]) == 0 and Decimal(row["withholding_tax"]) > 0
        ]
        self.assertEqual(len(reverts), 1)
        self.assertEqual(reverts[0]["symbol"], "TLT")
        self.assertEqual(reverts[0]["isin"], "US4642874329")
        self.assertEqual(reverts[0]["pay_date"], "2024-09-06")
        self.assertEqual(reverts[0]["withholding_tax"], "13.38")

        self.assertFalse(any(row["asset_type"] == "Forex" for row in dataset.tables["Positions"]))
        self.assertTrue(any(row.get("position_type") == "fx" for row in dataset.tables["Fifo"]))
        fx_trades = [row for row in dataset.tables["Trades"] if row["asset_type"] == "Forex"]
        self.assertEqual(len(fx_trades), 1)
        self.assertEqual(fx_trades[0]["country"], "Cyprus")

        reconciliation = ReconciliationEngine().reconcile_dataset(dataset)
        non_info = [item for item in reconciliation if item.severity != ReconciliationSeverity.INFO]
        self.assertEqual(non_info, [])

    def test_excess_dividend_withholding_revert_is_clamped_to_zero_withhold(self) -> None:
        excessive_revert_csv = SNAPSHOT_REVERT_FX_EXANTE_CSV.replace(
            '"3"\t"EX3"\t"TLT.NASDAQ"\t"None"\t"US TAX"\t"2024-10-02 13:33:55"\t"13.38"',
            '"3"\t"EX3"\t"TLT.NASDAQ"\t"None"\t"US TAX"\t"2024-10-02 13:33:55"\t"100"',
        )
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / "exante"
            broker_root.mkdir(parents=True)
            path = broker_root / "Custom_EX3.csv"
            path.write_text(excessive_revert_csv, encoding="utf-16")

            parser = ExanteParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469.44")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "EX3"), "EX3")

        dividend_results = [row for row in result.dataset.tables["Years_Results"] if row["table"] == "Yearly Dividends"]
        self.assertEqual(len(dividend_results), 1)
        self.assertEqual(dividend_results[0]["withhold_kzt"], "0.00")
        self.assertEqual(dividend_results[0]["tax_kzt_withhold"], dividend_results[0]["tax_kzt"])

    def test_sell_only_trade_uses_broker_pl_excluding_commission_to_infer_enter_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / "exante"
            broker_root.mkdir(parents=True)
            path = broker_root / "Custom_EX4.csv"
            path.write_text(SELL_ONLY_EXANTE_CSV, encoding="utf-16")

            parser = ExanteParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("469")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "EX4"), "EX4")

        fifo = result.dataset.tables["Fifo"][0]
        self.assertEqual(fifo["symbol"], "CTKB")
        self.assertEqual(fifo["_opening_lot_status"], "missing_opening_lot")
        self.assertEqual(fifo["enter_price"], "60")
        self.assertEqual(fifo["acquisition_cost_with_commission"], "120")
        self.assertEqual(Decimal(fifo["pnl"]), Decimal("-20"))
        self.assertEqual(Decimal(fifo["pnl_after_all_commissions"]), Decimal("-21"))

    def test_exante_futures_and_fx_spot_yearly_derivatives_use_pnl_before_commission_tax_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / "exante"
            broker_root.mkdir(parents=True)
            path = broker_root / "Custom_EXD.csv"
            path.write_text(DERIVATIVES_EXANTE_CSV, encoding="utf-16")

            parser = ExanteParser(fx_provider=AnnualFxRateProvider({(2024, "USD"): Decimal("500")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "EXD"), "EXD")

        future_fifo = next(row for row in result.dataset.tables["Fifo"] if row["asset_type"] == "Futures")
        future_trades = [row for row in result.dataset.tables["Trades"] if row["asset_type"] == "Futures"]
        self.assertTrue(all(row["country"] == "US" for row in future_trades))
        self.assertEqual(future_fifo["country"], "US")
        self.assertEqual(future_fifo["pnl_before_commission"], "10")
        self.assertEqual(future_fifo["pnl"], "10")
        self.assertEqual(future_fifo["pnl_after_all_commissions"], "5")
        fx_fifo = next(row for row in result.dataset.tables["Fifo"] if row["asset_type"] == "FX Spot")
        fx_trade_rows = [row for row in result.dataset.tables["Trades"] if row["asset_type"] == "FX Spot"]
        self.assertEqual(len(fx_trade_rows), 2)
        self.assertTrue(all(row["country"] == "Cyprus" for row in fx_trade_rows))
        self.assertEqual(fx_fifo["country"], "Cyprus")
        self.assertEqual(fx_fifo["position_type"], "short")
        self.assertEqual(fx_fifo["enter_date"], "2024-02-19 08:25:14")
        self.assertEqual(fx_fifo["enter_quantity"], "1000")
        self.assertEqual(fx_fifo["enter_price"], "1.53")
        self.assertEqual(fx_fifo["enter_amount"], "1530")
        self.assertEqual(fx_fifo["exit_date"], "2024-02-20 08:25:14")
        self.assertEqual(fx_fifo["exit_quantity"], "1000")
        self.assertEqual(fx_fifo["exit_price"], "1.52")
        self.assertEqual(fx_fifo["exit_amount"], "1520.00")
        self.assertEqual(fx_fifo["pnl_before_commission"], "10.00")
        self.assertEqual(fx_fifo["pnl"], "9.8000")
        self.assertEqual(fx_fifo["pnl_after_all_commissions"], "9.5000")

        derivative_rows = [
            row for row in result.dataset.tables["Years_Results"] if row["table"] == "Yearly Derivatives"
        ]
        self.assertEqual(len(derivative_rows), 1)
        self.assertEqual(derivative_rows[0]["pnl"], "19.80")
        self.assertEqual(derivative_rows[0]["pnl_kzt"], "9900.00")
        self.assertEqual(derivative_rows[0]["only_profit"], "20.00")
        self.assertEqual(derivative_rows[0]["only_profit_kzt"], "10000.00")
        self.assertEqual(derivative_rows[0]["tax_kzt"], "1000.00")
        self.assertFalse(any(row["table"] == "Yearly FX Trades" for row in result.dataset.tables["Years_Results"]))

    def test_hxr_security_transfer_kspi_isin_and_option_expiration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / "exante"
            broker_root.mkdir(parents=True)
            path = broker_root / "Custom_HXR2208.001.csv"
            path.write_text(HXR_TRANSFER_KSPI_OPTION_EXANTE_CSV, encoding="utf-16")

            parser = ExanteParser(
                fx_provider=AnnualFxRateProvider({(2022, "USD"): Decimal("460"), (2023, "USD"): Decimal("456")})
            )
            result = parser.parse_reports(parser.discover_reports(raw_root, "HXR2208.001"), "HXR2208.001")
            dataset = result.dataset

        transfer = next(row for row in dataset.tables["Transfers"] if row["symbol"] == "KSPI")
        self.assertEqual(transfer["transfer_type"], "security")
        self.assertEqual(transfer["direction"], "in")
        self.assertEqual(transfer["isin"], "US48581R2058")
        self.assertEqual(transfer["quantity"], "86")

        dividend = next(row for row in dataset.tables["Dividends"] if row["symbol"] == "KSPI")
        self.assertEqual(dividend["isin"], "US48581R2058")
        self.assertEqual(dividend["country"], "US")

        option_trades = [row for row in dataset.tables["Trades"] if row["symbol"] == "SPY.CBOE.30M2023.P350"]
        self.assertEqual(len(option_trades), 2)
        self.assertTrue(all(row["asset_type"] == "Equity and Index Options" for row in option_trades))
        self.assertTrue(all(row["isin"] is None for row in option_trades))
        self.assertEqual(option_trades[0]["multiplier"], "100")
        self.assertEqual(option_trades[1]["trade_type"], "option_expiration")
        self.assertEqual(option_trades[1]["quantity"], "-2")
        self.assertEqual(option_trades[1]["price"], "0")

        option_fifo = [row for row in dataset.tables["Fifo"] if row["symbol"] == "SPY.CBOE.30M2023.P350"]
        self.assertEqual(len(option_fifo), 1)
        self.assertEqual(option_fifo[0]["exit_price"], "0")
        self.assertEqual(option_fifo[0]["pnl"], "-300.0")
        self.assertFalse(any(row["symbol"] == "SPY" for row in dataset.tables["Trades"]))
        self.assertFalse(any(row["reason"] == "unhandled_exante_transaction" for row in dataset.tables["Unprocessed"]))


if __name__ == "__main__":
    unittest.main()
