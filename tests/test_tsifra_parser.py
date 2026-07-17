from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from conftest_imports import SRC  # noqa: F401
from kztax270.brokers import tsifra as tsifra_module
from kztax270.brokers.tsifra import TsifraParser
from kztax270.reconciliation.engine import ReconciliationEngine
from kztax270.reconciliation.models import ReconciliationSeverity
from kztax270.reference.fx import AnnualFxRateProvider
from kztax270.transfers import TransferInFifoLot, TransferInRequest


MINIMAL_TSIFRA_XML = """<?xml version="1.0" encoding="utf-8"?>
<report date="05/01/2025" period="01/01/2024-31/12/2024">
  <account id="1432280" />
  <money end_m="100" />
  <positions>
    <position code="TEST" start_q="0" ch_q="5" end_q="5" plan_end_q="5" close_q="0" mkt_price="110" numGosReg="1" isin="RU0000000001" issuer="Issuer" MicexСode="TEST" StockType="ао" price_curr="RUB" />
  </positions>
  <active_moves>
    <active_move date="2024-01-01T10:00:00" active_name="Issuer - ао" ISIN="RU0000000001" in_qty="10" out_qty="0" description="Депозитарный договор" oper_name="Перевод ЦБ" />
  </active_moves>
  <orders>
    <order id="1">
      <trade t_id="T1" deal_number="1" kind="продажа" security_name="TEST" isin_code="RU0000000001" t_date="2024-02-01T10:00:00" t_place="ПАО Московская Биржа (Фондовый рынок)" t_q="-5" t_price="110" t_sum="550" comis="-1" comis_nds="0" currency="RUB" />
    </order>
    <order id="2">
      <repo t_id="R1" deal_time="2024-06-01T10:00:00" deal_date="2024-06-01T10:00:00" exec_date="2024-06-01T10:00:00" security_type="РђРєС†РёСЏ" security_name="REPO" isin_code="RU0000000003" deal_price="10" quantity="10" deal_volume="100" exec_symbol="S" repo_part="1" comis="-1" comis_nds="0" currency="RUB" />
      <repo t_id="R1" deal_time="2024-06-01T10:00:00" deal_date="2024-06-01T10:00:00" exec_date="2024-06-02T10:00:00" security_type="РђРєС†РёСЏ" security_name="REPO" isin_code="RU0000000003" deal_price="11.1" quantity="10" deal_volume="-111" exec_symbol="B" repo_part="2" comis="-1" comis_nds="0" currency="RUB" />
    </order>
  </orders>
  <money_move date="2024-03-01T10:00:00" in_qty="100" out_qty="0" description="Дивиденды" currency_code="RUB" oper_name="Начисление Дивидендов" isin="RU0000000001" ticker="TEST" type="dividend" quantity="5" />
  <money_move date="2024-03-01T10:00:00" in_qty="0" out_qty="15" description="Налог на дивиденды" currency_code="RUB" oper_name="Налоговое удержание по дивидендам" isin="RU0000000001" ticker="TEST" type="nalog_div" quantity="5" />
  <money_move date="2024-04-01T10:00:00" in_qty="20" out_qty="0" description="Купон" currency_code="RUB" oper_name="Купонные выплаты по облигациям (НКД)" isin="RU0000000002" type="kupon" quantity="1" />
  <money_move date="2024-05-01T10:00:00" in_qty="0" out_qty="50" currency_code="RUB" oper_name="Вывод ДС по поручению клиента" quantity="1" />
  <money_move date="2024-04-02T10:00:00" in_qty="0" out_qty="16" currency_code="RUB" oper_name="Налоговое удержание" quantity="1" />
</report>
"""


class TsifraParserTests(unittest.TestCase):
    def test_tsifra_transfer_in_trades_income_and_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / "tsifra"
            broker_root.mkdir(parents=True)
            path = broker_root / "Tsifra 1432280.xml"
            path.write_text(MINIMAL_TSIFRA_XML, encoding="utf-8")

            def resolver(request: TransferInRequest) -> list[TransferInFifoLot]:
                return [
                    TransferInFifoLot(
                        quantity=request.quantity,
                        price=Decimal("100"),
                        enter_date=datetime(2023, 12, 1, 10, 0, 0),
                        source_broker="ib",
                        source_file="source.xlsx",
                        source_row=2,
                    )
                ]

            parser = TsifraParser(fx_provider=AnnualFxRateProvider({(2024, "RUB"): Decimal("5")}), transfer_in_resolver=resolver)
            reports = parser.discover_reports(raw_root, "1432280")
            result = parser.parse_reports(reports, "1432280")
            dataset = result.dataset

        self.assertEqual(len(dataset.tables["Trades"]), 1)
        self.assertEqual(dataset.tables["Trades"][0]["symbol"], "TEST")
        self.assertEqual(dataset.tables["Trades"][0]["quantity"], "-5")

        security_transfers = [row for row in dataset.tables["Transfers"] if row["transfer_type"] == "security"]
        self.assertEqual(len(security_transfers), 1)
        self.assertEqual(security_transfers[0]["price"], "100")
        self.assertEqual(security_transfers[0]["enter_date"], "2023-12-01 10:00:00")
        tax_transfers = [row for row in dataset.tables["Transfers"] if row["transfer_type"] == "tax"]
        self.assertEqual(len(tax_transfers), 1)
        self.assertEqual(tax_transfers[0]["amount"], "-16")
        self.assertEqual(tax_transfers[0]["direction"], "out")

        fifo = dataset.tables["Fifo"][0]
        self.assertEqual(fifo["symbol"], "TEST")
        self.assertEqual(fifo["pnl"], "50")
        self.assertEqual(Decimal(fifo["pnl_after_all_commissions"]), Decimal("49"))

        dividend = dataset.tables["Dividends"][0]
        self.assertEqual(dividend["gross_amount"], "100.00")
        self.assertEqual(dividend["withholding_tax"], "-15.00")
        self.assertEqual(dividend["tax"], "10.00")

        coupon = dataset.tables["Coupons"][0]
        self.assertEqual(coupon["gross_amount"], "20.00")
        self.assertEqual(coupon["withholding_tax"], "-6.00")
        self.assertEqual(coupon["net_amount"], "14.00")

        interest = dataset.tables["Interest"][0]
        self.assertEqual(interest["gross_amount"], "10.00")
        self.assertEqual(interest["commission"], "1.00")
        self.assertEqual(dataset.tables["CashBalances"][0]["ending_cash"], "100")

        yearly_trades = [row for row in dataset.tables["Years_Results"] if row["table"] == "Yearly Trades"][0]
        self.assertEqual(yearly_trades["pnl_kzt"], "250.00")
        self.assertEqual(yearly_trades["withhold_kzt"], "-50.00")
        self.assertEqual(yearly_trades["tax_kzt"], "25.00")
        self.assertEqual(yearly_trades["tax_kzt_withhold"], "0.00")

        yearly_coupon = [row for row in dataset.tables["Years_Results"] if row["table"] == "Yearly Coupons"][0]
        self.assertEqual(yearly_coupon["amount"], "20.00")
        self.assertEqual(yearly_coupon["only_profit"], "20.00")
        self.assertEqual(yearly_coupon["only_profit_kzt"], "100.00")
        self.assertEqual(yearly_coupon["withhold_kzt"], "-30.00")
        self.assertEqual(yearly_coupon["tax_kzt"], "10.00")
        self.assertEqual(yearly_coupon["tax_kzt_withhold"], "0.00")

        reconciliation = ReconciliationEngine().reconcile_dataset(dataset)
        non_info = [item for item in reconciliation if item.severity != ReconciliationSeverity.INFO]
        self.assertEqual(non_info, [])

    def test_negative_coupon_nkd_and_explicit_revert_have_different_tax_effects(self) -> None:
        report = tsifra_module.ParsedTsifraReport(path=Path("tsifra.xml"), period_end=date(2024, 12, 31))
        report.rows[tsifra_module.TSIFRA_SECTION_MONEY_MOVES] = [
            {"date": "2024-04-01", "in_qty": "20", "out_qty": "0", "currency_code": "RUB", "type": "kupon"},
            {"date": "2024-04-02", "in_qty": "0", "out_qty": "5", "currency_code": "RUB", "type": "kupon", "description": "accrued coupon interest"},
            {"date": "2024-04-03", "in_qty": "0", "out_qty": "20", "currency_code": "RUB", "type": "kupon", "description": "Reverted: coupon"},
            {"date": "2024-04-03", "in_qty": "20", "out_qty": "0", "currency_code": "RUB", "type": "kupon", "description": "corrected coupon"},
        ]
        provider = AnnualFxRateProvider({(2024, "RUB"): Decimal("5")})

        coupons = tsifra_module._build_coupons([report], {}, provider, [])
        dataset = tsifra_module.CanonicalDataset.empty("tsifra", "1432280")
        dataset.tables["Coupons"] = coupons
        yearly_coupon = next(
            row for row in tsifra_module._build_years_results(dataset) if row["table"] == "Yearly Coupons"
        )

        self.assertEqual([row["is_revert"] for row in coupons], [False, False, True, False])
        self.assertEqual(yearly_coupon["amount"], "15.00")
        self.assertEqual(yearly_coupon["only_profit"], "20.00")
        self.assertEqual(yearly_coupon["only_profit_kzt"], "100.00")
        self.assertEqual(yearly_coupon["withhold_kzt"], "-30.00")
        self.assertEqual(yearly_coupon["tax_kzt"], "10.00")
        self.assertEqual(yearly_coupon["tax_kzt_withhold"], "0.00")


if __name__ == "__main__":
    unittest.main()
