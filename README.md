# KZ Taxation Form 270 ETL

Проект строит единый ETL-пайплайн для отчётов IB, Exante, Freedom, Tsifra и Tabys и подготовки черновиков формы 270.00 Республики Казахстан.

Главное правило архитектуры: `legacy/` остаётся рабочим источником текущей логики, а новый пакет `src/kztax270` задаёт стабильные интерфейсы, канонические схемы и pipeline. Общая логика постепенно выносится из legacy в `kztax270.calculations`, `kztax270.reference`, `kztax270.reconciliation` и `kztax270.form270`.

IB уже имеет native parser в `src/kztax270/brokers/ib.py`. Старый IB adapter доступен как broker code `ib_legacy`.

## Целевой поток

1. `discover raw reports` - найти отчёты в `data/raw/{broker}/` по одному брокерскому счёту.
2. `parse broker reports` - распарсить отчёты брокера через native parser или legacy adapter.
3. `enrich instruments from reference data` - обогатить инструменты справочниками.
4. `apply corporate actions` - применить split/merge/redemption/buyback/spin-off и прочие события.
5. `calculate FIFO` - рассчитать реализации методом FIFO с комиссиями.
6. `calculate income categories` - классифицировать дивиденды, купоны, interest, transfers.
7. `apply tax rules` - сформировать налоговую сводную. На текущем этапе это stub.
8. `generate broker-level Excel audit workbook` - один workbook на один брокерский счёт.
9. `run reconciliation` - сравнить raw totals брокера с каноническими таблицами.
10. `create joint-owner Excel workbook` - при необходимости создать 50%-ную копию audit workbook.
11. `generate account-level Form270 draft JSON` - заполнить JSON из `data/templates/270 new template.json`.
12. `merge multiple broker/account JSON files` - объединить несколько счетов клиента.

## Структура

```text
src/kztax270/
  brokers/          # parser interfaces, discovery, lazy adapters to legacy
  canonical/        # canonical dataset and workbook schema
  calculations/     # shared FIFO, corporate actions, income, tax rule contracts
  excel/            # canonical audit writer, merge and joint-account share
  form270/          # JSON builder and merge
  reconciliation/   # raw-vs-canonical discrepancy engine
  reference/        # CSV-backed reference data stores and updater stubs
  pipeline.py       # account/client orchestration
  cli.py            # command-line interface
legacy/             # existing working code, not rewritten in this iteration
data/raw/           # raw broker reports, ignored by Git
reference/          # versionable reference tables and schemas
configs/            # account/client TOML examples
tests/              # unit-test scaffold
```

## Canonical Excel Workbook

Каждый брокерский счёт должен давать workbook с одинаковым набором листов:

```text
Instruments
CorporateActions
Dividends
Transfers
Trades
Fifo
Positions
Interest
Coupons
CashBalances
Years_Results
Unprocessed
Reconciliation
```

Подробные поля описаны в `docs/CANONICAL_SCHEMA.md` и зафиксированы в `src/kztax270/canonical/workbook_schema.py`.

## Reconciliation

Новый слой поддерживает проверки:

```text
total_trades_gross_amount
total_commissions
total_dividends_gross
total_dividends_net
total_dividends_tax
total_interest
total_coupons
total_deposits_withdrawals_transfers
ending_cash
ending_position_quantity
realized_pl
```

Каждое расхождение получает severity: `info`, `warning`, `error`. Начальные правила лежат в `src/kztax270/reconciliation/engine.py`.

Для IB parser извлекает raw totals из broker CSV и сверяет их с canonical tables: trades gross amount, commissions, dividends gross/net/tax, cash interest, coupons, deposits/withdrawals, ending cash, ending positions and broker-provided realized P/L.

Known IB behavior: broker realized P/L can remain `warning`, because IB performance summary, trade-level realized P/L and tax FIFO are different controls. Tax FIFO must use opening lots/transfers/corporate actions before this warning can be treated as a filing blocker.

## Reference Data

Справочники хранятся отдельно от raw-data:

```text
reference/fx_rates/       # среднегодовые официальные курсы НБ РК
reference/instruments/    # instrument master table
reference/jurisdictions/  # countries, preferential tax/offshore flags
reference/kase_aix/       # KASE/AIX official list snapshots
```

Создать CSV с заголовками:

```powershell
$env:PYTHONPATH="src"
python -m kztax270 init-reference
```

## Установка для разработки

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[dev,legacy]
```

Если нужно только импортировать новый каркас и запускать unit-тесты без legacy ETL, тяжёлые зависимости можно не ставить.

## Примеры CLI

Найти raw reports:

```powershell
$env:PYTHONPATH="src"
python -m kztax270 discover ib U1717377
```

Запустить клиента из конфига:

```powershell
$env:PYTHONPATH="src"
python -m kztax270 run-client configs/accounts.example.toml client_demo
```

Все рабочие сценарии запускаются через один конфигурационный файл:

```powershell
$env:PYTHONPATH="src"
python -m kztax270 run-270 .\configs\form270.toml
```

В `[[form270.jobs]]` тип задания задаётся полем `id`:

- `excel` — создать audit Excel из raw-отчётов одного счёта;
- `merge_excel` — объединить несколько готовых audit Excel;
- `joint_excel` — создать 50%-ную копию готового audit Excel для одного владельца совместного счёта;
- `270_json` — создать 270.00 JSON из одного audit Excel;

Задания выполняются сверху вниз: можно сначала указать `excel`, затем
`joint_excel`, а потом `270_json` с именем файла вида
`ib_U22777472_joint_audit.xlsx`. При создании совместного Excel все количества,
денежные суммы, комиссии, удержания, PnL и остатки делятся на два; цены, курсы и
мультипликаторы сохраняются. `Years_Results` агрегируется и рассчитывается заново.
В заданиях
объединения имена файлов без пути ищутся в
`data/processed`; объединённый Excel сохраняется как
`merged_Имя_Фамилия.xlsx`. Детальные листы объединяются построчно, а
пересекающиеся строки `Years_Results` суммируются по виду дохода, году, флагу,
стране, бирже и валюте. Полный пример находится в
[`configs/form270.toml`](configs/form270.toml).

Для Excel нужны `pandas` и `openpyxl`.

На текущем этапе запуск полного клиента требует legacy-зависимостей и корректных raw-файлов. Налоговый движок пока stub: он сохраняет структуру pipeline, но не заявляет готовность финального расчёта формы 270.00.

## New Tax Rule Baseline

The new code follows these supplied business rules:

1. FIFO acquisition cost includes opening trade commission via `Fifo.acquisition_cost_with_commission`; liquidation commission is not deducted from tax `Fifo.pnl`.
2. Foreign-currency income uses annual average official NBK FX rate by income year from `reference/fx_rates/nbk_average_annual_rates.csv`.
3. Instrument tax flags are explicit canonical fields: `offshore_flag`, `issuer_outside_kz_flag`, `preferential_tax_flag`.
