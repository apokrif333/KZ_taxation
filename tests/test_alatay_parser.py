from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

from conftest_imports import SRC  # noqa: F401
from kztax270.brokers.alatay import AlatayParser, parse_alatay_csv, parse_alatay_xlsx
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


HISTORICAL_SECURITIES_REPORT = """Ценные бумаги на начало отчетного периода и их движение до начала отчетного периода
,
,ФИО/Наименование клиента:,ТЕСТОВЫЙ КЛИЕНТ
,№ лицевого счета:,00123
,Отчет составлен на:,01.01.2025
,
Ценные бумаги в портфеле клиента на начало отчетного периода:
№,Эмитент,Вид ЦБ,ISIN,Номинал,Количество ЦБ
1,ЭМИТЕНТ А,АКЦИИ,KZ0000000001,1,10
,
Сделки по ценным бумагам, указанным на начало отчетного периода, за время до начала отчетного периода:
Дата расчетов сделки/операции,Эмитент,Вид ЦБ,ISIN,Номинал,Вид сделки/тип операции,Количество ЦБ,Цена за 1 ЦБ (% от номинала облигации),Валюта сделки,Сумма сделки/операции,Рынок заключения сделки,Сумма комиссии
01.02.2023,ЭМИТЕНТ А,АКЦИИ,KZ0000000001,1,Покупка /Народное IPO,10,100,KZT,1 000,KASE_MOEX,2
02.02.2023,ЭМИТЕНТ А,АКЦИИ,KZ0000000001,1,Перевод основной (получатель без смены прав собственности),10,1,KZT,10,ЦД ЦБ,0
02.02.2023,ЭМИТЕНТ А,АКЦИИ,KZ0000000001,1,Перевод основной (поставщик без смены прав собственности),10,1,KZT,10,ЦД ЦБ,0
"""


CASH_REPORT = """ОТЧЕТ ДВИЖЕНИЯ ДЕНЕЖНЫХ СРЕДСТВ
,
,ФИО/Наименование клиента:,ТЕСТОВЫЙ КЛИЕНТ
,№ лицевого счета:,123
,Отчет составлен на:,01.01.2024-31.12.2024
,
Входящий остаток на начало периода:,100 KZT
Исходящий остаток на конец периода:,141 KZT
,
Дата проведения операции/сделки,Содержание операции/сделки,Тип операции/вид сделки,Эмитент,ISIN,Входящий остаток,Приход,Расход,Исходящий остаток,Код валюты,Тип ЦБ,Код страны регистрации эмитента,Наименование рынка
01.06.2024,Зачисление денежных средств,Зачисление вознаграждения (дивиденды),ЭМИТЕНТ А,KZ0000000001,100,10,0,110,KZT,АКЦИИ,KZ,KASE
01.07.2024,Зачисление денежных средств,Зачисление вознаграждения (купон),ЭМИТЕНТ Б,KZ2000000002,110,1,0,111,KZT,ОБЛИГАЦИИ,KZ,KASE
01.08.2024,Зачисление денежных средств по сделке,Погашение ЦБ KZ2000000002,ЭМИТЕНТ Б,KZ2000000002,111,30,0,141,KZT,ОБЛИГАЦИИ,KZ,KASE
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

    def test_historical_securities_and_cash_reports_are_combined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp)
            broker_root = raw_root / "alatay"
            broker_root.mkdir()
            (broker_root / "00123 ОДЦБ.csv").write_text(HISTORICAL_SECURITIES_REPORT, encoding="utf-8")
            (broker_root / "00123 ОДДС.csv").write_text(CASH_REPORT, encoding="utf-8")

            parser = AlatayParser()
            reports = parser.discover_reports(raw_root, "00123")
            result = parser.parse_reports(reports, "00123")

        dataset = result.dataset
        validate_dataset_for_tax_forms(dataset)
        self.assertEqual(dataset.warnings, [])
        self.assertEqual(dataset.tables["Trades"][0]["commission"], "2")
        self.assertEqual(len(dataset.tables["Transfers"]), 2)
        self.assertEqual(dataset.tables["Unprocessed"], [])
        self.assertEqual(dataset.tables["Dividends"][0]["gross_amount"], "10.00")
        self.assertEqual(dataset.tables["Coupons"][0]["gross_amount"], "1.00")
        self.assertEqual(dataset.tables["CashBalances"][0]["ending_cash"], "141.00")
        self.assertEqual(dataset.tables["CorporateActions"][0]["proceeds"], "30.00")
        self.assertEqual(
            [row["year"] for row in dataset.tables["Years_Results"] if row["table"] == "Yearly Dividends"],
            [2024],
        )

        reconciliation = ReconciliationEngine().reconcile_dataset(dataset)
        self.assertEqual(
            [row for row in reconciliation if row.severity == ReconciliationSeverity.ERROR],
            [],
        )

    def test_xlsx_closing_positions_are_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "00123 2025 ОДЦБ.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            for row in (
                ("Отчет движения ценных бумаг",),
                (None, "№ лицевого счета:", "00123"),
                (None, "Отчет составлен на:", "01.01.2025-31.12.2025"),
                ("Ценные бумаги в портфеле клиента на конец отчетного периода:",),
                ("№", "Эмитент", "Вид ЦБ", "ISIN", "Номинал", "Количество ЦБ"),
                ("1", "ЭМИТЕНТ А", "АКЦИИ", "KZ0000000001", "1", 10),
            ):
                sheet.append(row)
            workbook.save(path)
            workbook.close()

            report = parse_alatay_xlsx(path)

        self.assertEqual(report.period_end.isoformat(), "2025-12-31")
        self.assertEqual(report.positions[0]["quantity"], "10")
        self.assertEqual(report.positions[0]["_snapshot_kind"], "closing")

    def test_registry_exposes_alatay(self) -> None:
        self.assertIn("alatay", default_registry().broker_codes())


if __name__ == "__main__":
    unittest.main()
