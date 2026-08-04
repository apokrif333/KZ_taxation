from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from conftest_imports import SRC  # noqa: F401
from kztax270.brokers.alatay import AlatayParser, parse_alatay_csv
from kztax270.brokers.registry import default_registry
from kztax270.canonical.validation import validate_dataset_for_tax_forms
from kztax270.reconciliation.engine import ReconciliationEngine
from kztax270.reconciliation.models import ReconciliationMetric, ReconciliationSeverity

REPORT_2023 = """Отчет движения ценных бумаг
,
,ФИО/Наименование клиента:,ТЕСТОВЫЙ КЛИЕНТ
,№ лицевого счета:,ATEST
,Отчет составлен на:,01.01.2023-31.12.2023
,
Ценные бумаги в портфеле клиента на конец отчетного периода:
№,Эмитент,Вид ЦБ,ISIN,Номинал,Количество ЦБ,Код страны регистрации эмитента
1,ЭМИТЕНТ А,АКЦИИ,KZ0000000001,1,10,
,
Движение Ценных бумаг клиента за отчетный период:
Дата расчетов сделки/операции,Эмитент,Вид ЦБ,ISIN,Номинал,Вид сделки/тип операции,Количество ЦБ,Цена за 1 ЦБ (% от номинала облигации),Валюта сделки,Сумма сделки/операции,Рынок заключения сделки,Код страны регистрации эмитента,Сумма комиссии
01.02.2023,ЭМИТЕНТ А,АКЦИИ,KZ0000000001,1,Покупка,10,100,KZT,1 000,KASE_MOEX,,2
"""


REPORT_2024 = """Отчет движения ценных бумаг
,
,ФИО/Наименование клиента:,ТЕСТОВЫЙ КЛИЕНТ
,№ лицевого счета:,ATEST
,Отчет составлен на:,01.01.2024-31.12.2024
,
Ценные бумаги в портфеле клиента на конец отчетного периода:
№,Эмитент,Вид ЦБ,ISIN,Номинал,Количество ЦБ,Код страны регистрации эмитента
1,ЭМИТЕНТ А,АКЦИИ,KZ0000000001,1,7,
2,ЭМИТЕНТ Б,ОБЛИГАЦИИ,KZ2000000002,1 000,5,
,
Движение Ценных бумаг клиента за отчетный период:
Дата расчетов сделки/операции,Эмитент,Вид ЦБ,ISIN,Номинал,Вид сделки/тип операции,Количество ЦБ,Цена за 1 ЦБ (% от номинала облигации),Валюта сделки,Сумма сделки/операции,Рынок заключения сделки,Код страны регистрации эмитента,Сумма комиссии
01.03.2024,ЭМИТЕНТ А,АКЦИИ,KZ0000000001,1,Продажа,3,120,KZT,360,KASE_MOEX,,1
02.03.2024,ЭМИТЕНТ Б,ОБЛИГАЦИИ,KZ2000000002,1 000,Покупка,5,1 050,KZT,5 250,KASE_MOEX,,0
"""


class AlatayParserTests(unittest.TestCase):
    def test_csv_metadata_positions_and_trades_are_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ATEST alatay 2023.csv"
            path.write_text(REPORT_2023, encoding="utf-8")
            report = parse_alatay_csv(path)

        self.assertEqual(report.account_id, "ATEST")
        self.assertEqual(report.period_end.isoformat(), "2023-12-31")
        self.assertEqual(report.positions[0]["quantity"], "10")
        self.assertEqual(report.trades[0]["price"], "100")
        self.assertEqual(report.trades[0]["amount"], "1000")
        self.assertEqual(report.trades[0]["commission"], "2")

    def test_history_builds_fifo_positions_and_yearly_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp)
            broker_root = raw_root / "alatay"
            broker_root.mkdir()
            (broker_root / "ATEST alatay 2023.csv").write_text(REPORT_2023, encoding="utf-8")
            (broker_root / "ATEST alatay 2024.csv").write_text(REPORT_2024, encoding="utf-8")
            (broker_root / "OTHER alatay 2024.csv").write_text(REPORT_2024, encoding="utf-8")

            parser = AlatayParser()
            reports = parser.discover_reports(raw_root, "ATEST")
            result = parser.parse_reports(reports, "ATEST")

        self.assertEqual([report.path.name for report in reports], ["ATEST alatay 2023.csv", "ATEST alatay 2024.csv"])
        validate_dataset_for_tax_forms(result.dataset)
        self.assertEqual([row["quantity"] for row in result.dataset.tables["Trades"]], ["10", "-3", "5"])
        self.assertEqual(len(result.dataset.tables["Fifo"]), 1)
        self.assertEqual(result.dataset.tables["Fifo"][0]["enter_price"], "100")
        self.assertEqual(result.dataset.tables["Fifo"][0]["exit_price"], "120")
        self.assertEqual(result.dataset.tables["Unprocessed"], [])

        reconciliation = ReconciliationEngine().reconcile_dataset(result.dataset)
        position_rows = [
            row for row in reconciliation if row.metric == ReconciliationMetric.ENDING_POSITION_QUANTITY
        ]
        self.assertEqual(len(position_rows), 3)
        self.assertTrue(all(row.severity == ReconciliationSeverity.INFO for row in position_rows))
        by_key = {(row.year, row.instrument_key): row for row in position_rows}
        self.assertEqual(by_key[(2023, "KZ0000000001")].canonical_value, Decimal("10"))
        self.assertEqual(by_key[(2024, "KZ0000000001")].canonical_value, Decimal("7"))
        self.assertEqual(by_key[(2024, "KZ2000000002")].canonical_value, Decimal("5"))
        self.assertEqual(
            [row for row in reconciliation if row.severity == ReconciliationSeverity.ERROR],
            [],
        )

        instruments = {row["isin"]: row for row in result.dataset.tables["Instruments"]}
        self.assertEqual(instruments["KZ0000000001"]["listing_exchange"], "KASE")
        self.assertEqual(instruments["KZ2000000002"]["type"], "Bonds")

    def test_registry_exposes_alatay(self) -> None:
        self.assertIn("alatay", default_registry().broker_codes())


if __name__ == "__main__":
    unittest.main()
