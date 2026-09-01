import type { Metadata } from 'next'
import Link from 'next/link'
import { AlertTriangle, ArrowLeft, FileSearch, TableProperties } from 'lucide-react'
import { FaqHeader } from '@/components/faq-header'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const metadata: Metadata = {
  title: 'Что находится в audit-файле — QCM Tax 270',
  description: 'Описание листов, колонок и расчётных показателей audit Excel в QCM Tax 270.',
}

type Field = readonly [name: string, description: string]

const yearsResultFields: Field[] = [
  ['Year', 'Год дохода или даты выхода из позиции.'],
  ['Flag', 'Налоговая классификация соответствующей группы операций.'],
  ['Country', 'Страна инструмента или эмитента.'],
  ['Tax_Exchange', 'Налоговая классификация с учётом биржи: например AIX, KASE или outofKZ.'],
  ['Currency', 'Валюта исходных операций.'],
  ['PnL', 'Сумма реализованного финансового результата в валюте группы: сумма PnL детальных строк FIFO либо доходов, отнесённых к деривативам. Комиссии за покупку учтены.'],
  ['PnL_KZT', 'PnL в тенге. Для офшорных сделок указывается вся сумма реализации, а не обычный результат FIFO.'],
  ['Amount', 'Валовая сумма дивидендов, процентов или купонов в исходной валюте.'],
  ['Amount_KZT', 'Amount в тенге, то есть сумма Gross_Amount_KZT. Для льготных дивидендов, не попадающих в соответствующий годовой блок, может быть 0.'],
  ['OnlyProfit', 'Часть дохода, которая учитывается как положительный доход для налоговой базы. Для деривативов это положительный результат до комиссий; для Interest — только положительные проценты; для Coupons — доход с учётом правил по НКД и сторно.'],
  ['OnlyProfit_KZT', 'OnlyProfit в тенге.'],
  ['Withhold_KZT', 'Сумма иностранного налога, удержанного у источника, в тенге. Удержание отрицательно; возвраты уменьшают ранее удержанную сумму.'],
  ['Tax_KZT', 'Расчётный налог Казахстана до зачёта иностранного удержания. В общем случае: положительная налоговая база × 10%; для льготных групп, валютных сделок и погашений облигаций предусмотрены исключения.'],
  ['Tax_KZT_Withhold', 'Налог к оплате после допустимого зачёта удержания у источника: не менее нуля. Для одной группы это max(Tax_KZT − иностранный удержанный налог, 0). В объединённом отчёте зачёт может перераспределяться между брокерами внутри одной страны, года и вида дохода.'],
]

const sections: { number: string; title: string; description: string; fields: Field[]; note?: string }[] = [
  {
    number: '2.2', title: 'Unprocessed', description: 'Строки, которые система сохранила, но не смогла полностью превратить в операцию расчёта. Warning или error могут означать, что для полного расчёта требуется уточнение отчёта.', fields: [
      ['Severity', 'Уровень: info, warning или error.'], ['Reason', 'Машинная причина, почему строка не обработана.'], ['Details', 'Читаемое пояснение причины и контекст.'], ['Source_Sheet', 'Раздел исходного отчёта, откуда пришла строка.'], ['Source_Report', 'Имя или путь исходного загруженного файла.'], ['Trade_ID', 'Идентификатор операции у брокера, если он есть.'], ['Date_Time', 'Дата и время операции из отчёта.'], ['Symbol', 'Тикер инструмента, если определён.'], ['ISIN', 'Международный идентификатор, если определён.'], ['Asset_Type', 'Тип инструмента из отчёта или нормализации.'], ['Currency', 'Валюта строки.'], ['Quantity', 'Количество.'], ['Price', 'Цена за единицу.'], ['Amount', 'Денежная сумма строки.'], ['Commission', 'Комиссия строки.'],
    ], note: 'Поля Date_Time, Symbol, ISIN, Asset_Type, Currency, Quantity, Price, Amount и Commission не рассчитываются отдельно: это сохранённые значения исходной строки, которые помогают найти её у брокера.',
  },
  {
    number: '2.3', title: 'Reconciliation', description: 'Контрольные сверки между итогами, прочитанными непосредственно из брокерского отчёта, и итогами нормализованных листов audit-файла.', fields: [
      ['Metric', 'Что сверяется: оборот сделок, комиссии, дивиденды, проценты, купоны, переводы, реализованный P/L, остаток денежных средств, позиция и т. п.'], ['Severity', 'Info, если разница в пределах допуска; warning или error, если нет.'], ['Year', 'Год проверяемой величины, если применимо.'], ['Currency', 'Валюта, если применимо.'], ['Instrument_Key', 'Ключ инструмента: приоритетно ISIN, иначе Symbol; используется при сверке по инструменту.'], ['Broker_Value', 'Значение, извлечённое из исходного брокерского отчёта.'], ['Canonical_Value', 'Значение, повторно собранное из листов audit-файла.'], ['Difference', 'Canonical_Value − Broker_Value.'], ['Tolerance', 'Допустимая абсолютная разница: обычно 0,01, для количества позиции — 0,0001.'], ['Source', 'Дополнительный идентификатор источника, если доступен.'], ['Details', 'Пояснение структуры сверки.'],
    ], note: 'Difference по pnl_after_all_commissions_by_instrument и realized_pl встречается часто: расчёт использует FIFO и учитывает только комиссии за покупку, поэтому PnL может отличаться от данных брокера. Это нормально.',
  },
  {
    number: '2.4', title: 'CorporateActions', description: 'События эмитента: split, merger/merged, spinoff, maturity, full_call, redemption, buyback и другие. Лист объясняет, почему инструмент, количество или FIFO могли измениться без обычной сделки.', fields: [
      ['Date', 'Дата корпоративного события.'], ['Symbol', 'Исходный тикер инструмента.'], ['ISIN', 'ISIN исходного инструмента.'], ['Action_Type', 'Определённый тип события по описанию брокера.'], ['Description', 'Исходное текстовое описание события.'], ['Quantity', 'Количество, указанное брокером по событию.'], ['Proceeds', 'Поступление или списание, указанное брокером.'], ['Value', 'Стоимость, указанная брокером.'], ['Currency', 'Валюта сумм события.'], ['Realized_PL', 'Реализованный P/L, указанный брокером для события, если есть.'], ['Source_Report', 'Файл брокерского отчёта.'],
    ], note: 'Quantity, Proceeds, Value и Realized_PL переносятся из отчёта после нормализации числового формата; это не самостоятельная налоговая формула.',
  },
  {
    number: '2.5', title: 'Dividends', description: 'Детальные дивиденды и удержание налога. Строка сверяется с группой Yearly Dividends по году, валюте, стране и Flag.', fields: [
      ['Date', 'Дата дивидендного события; в поддерживаемых отчётах обычно совпадает с Pay_Date.'], ['Pay_Date', 'Дата выплаты, по которой определяется год дохода.'], ['Symbol', 'Тикер бумаги.'], ['ISIN', 'ISIN бумаги.'], ['Country', 'Страна, определённая для бумаги или эмитента.'], ['Flag', 'Налоговая классификация дивиденда.'], ['Currency', 'Валюта выплаты.'], ['Gross_Amount', 'Валовой дивиденд до удержания.'], ['Withholding_Tax', 'Удержанный иностранный налог; обычно отрицательное число.'], ['Net_Amount', 'Gross_Amount + Withholding_Tax, то есть фактически зачисленная сумма до иных возможных банковских движений.'], ['KZT_Rate', 'Среднегодовой курс валюты выплаты.'], ['Gross_Amount_KZT', 'Gross_Amount × KZT_Rate.'], ['Tax', 'Ориентировочный казахстанский налог в валюте выплаты: Gross_Amount × 10%. Вспомогательная детальная величина.'], ['Tax_KZT', 'Tax × KZT_Rate.'], ['Offshore_Flag', 'Признак офшорности из справочника, если он доступен.'], ['Kase_Aix_Preferential_Flag', 'Признак льготности дивиденда AIX/KASE, если он определён справочником.'], ['Source_Report', 'Файл брокерского отчёта.'],
    ],
  },
  {
    number: '2.6', title: 'Transfers', description: 'Все вводы, выводы и переводы денег или ценных бумаг. Входящий перевод бумаги сам по себе не является продажей, но может быть источником лота для FIFO.', fields: [
      ['Date', 'Дата перевода или зачисления.'], ['Transfer_Type', 'cash или security.'], ['Direction', 'in (входящий) либо out (исходящий).'], ['Asset_Type', 'cash либо тип переводимой ценной бумаги.'], ['Symbol', 'Тикер переводимой бумаги; для денег пусто.'], ['ISIN', 'ISIN переводимой бумаги, если определён.'], ['Currency', 'Валюта денег или бумаги.'], ['Quantity', 'Количество бумаг; для cash-перевода пусто.'], ['Price', 'Цена или себестоимость единицы, если её сообщил брокер либо она получена из связанного исходящего FIFO. Для входящего перевода без источника может быть пусто.'], ['Enter_Date', 'Дата приобретения переносимого лота, если она известна из связанного исходящего отчёта; иначе пусто.'], ['Amount', 'Сумма cash-перевода со знаком отчёта. Для security-перевода обычно пусто, поскольку перевод не трактуется как сделка.'], ['Broker_Comment', 'Описание из отчёта брокера.'], ['Counterparty', 'Счёт или контрагент перевода, если указан.'], ['Source_Report', 'Файл брокерского отчёта.'],
    ],
  },
  {
    number: '2.7', title: 'Trades', description: 'Нормализованные исходные сделки: покупки, продажи и в отдельных отчётах сделки с валютой. Результат FIFO находится на листе Fifo.', fields: [
      ['Date_Time', 'Дата и время исполнения.'], ['Trade_ID', 'Идентификатор сделки у брокера или технический идентификатор.'], ['Trade_Type', 'Нормализованный вид или сторона операции.'], ['Symbol', 'Тикер.'], ['ISIN', 'ISIN, если найден.'], ['Asset_Type', 'Акции, облигации, опционы, фьючерсы, Forex и т. п.'], ['Quantity', 'Подписанное количество по направлению сделки.'], ['Price', 'Цена за единицу.'], ['Multiplier', 'Множитель контракта; для обычной акции обычно 1. Для сложных облигаций и фьючерсов значение может отличаться.'], ['Amount', 'Валовая сумма сделки без комиссии.'], ['Commission', 'Комиссия сделки по данным брокера.'], ['Amount_With_Commission', 'Amount с учётом Commission.'], ['KZT_Rate', 'Среднегодовой курс валюты сделки в год исполнения.'], ['Amount_KZT', 'Amount × KZT_Rate.'], ['Source_Of_Expense', 'Источник или характер расхода, если брокер его сообщает.'], ['Currency', 'Валюта сделки.'], ['Exchange', 'Биржа или площадка из отчёта либо карточки инструмента.'], ['Country', 'Страна инструмента либо страна, используемая для классификации.'], ['Source_Report', 'Файл брокерского отчёта.'],
    ],
  },
  {
    number: '2.8', title: 'Fifo', description: 'Главный детальный лист по реализованным позициям. Каждая строка связывает закрывающую операцию с конкретным открывающим лотом по принципу FIFO: первым закрывается самый ранний доступный лот. Одна продажа может дать несколько строк Fifo.', fields: [
      ['Asset_Type', 'Тип инструмента.'], ['Symbol', 'Тикер на момент расчёта; при merger может быть уже тикер новой бумаги.'], ['ISIN', 'ISIN инструмента.'], ['Currency', 'Валюта позиции.'], ['Country', 'Страна инструмента или эмитента, применённая для классификации.'], ['Exchange', 'Биржа или площадка из операции либо карточки инструмента.'], ['Tax_Exchange', 'Налоговая классификация с учётом биржи: например AIX, KASE или outofKZ.'], ['Flag', 'Налоговая классификация строки.'], ['Operation_Type', 'Тип закрывающего события: trade, derivative_trade, fx_trade, bond_redemption, option_expiration либо corporate_action:<тип>.'], ['Years_Result_Table', 'Таблица Years_Results, в которую попадает строка: Yearly Trades, Yearly Derivatives, Yearly Bonds Redemption или Yearly FX Trades.'], ['Position_Type', 'long, short или fx.'], ['Enter_Date', 'Дата открытия FIFO-лота.'], ['Enter_Quantity', 'Количество из открывающего лота, использованное этой строкой FIFO.'], ['Enter_Price', 'Цена открытия за единицу.'], ['Enter_Multiplier', 'Множитель открывающего контракта. У одной сделки открывающий и закрывающий множители могут различаться, например для сложных облигаций и фьючерсов.'], ['Enter_Amount', 'Валовая стоимость использованной части открывающего лота: abs(Enter_Quantity) × Enter_Price × Enter_Multiplier.'], ['Enter_Commission', 'Распределённая на эту часть лота комиссия открытия.'], ['Exit_Date', 'Дата закрытия лота; её год определяет год результата.'], ['Exit_Quantity', 'Количество, закрытое этой строкой.'], ['Exit_Price', 'Цена закрытия за единицу.'], ['Exit_Multiplier', 'Множитель закрывающего контракта.'], ['Exit_Amount', 'Валовая стоимость выхода без комиссии: abs(Exit_Quantity) × Exit_Price × Exit_Multiplier.'], ['Exit_Commission', 'Часть комиссии закрывающей операции, распределённая на данную FIFO-строку.'], ['PnL_Before_Commission', 'Разница валовых сумм до комиссий: для long Exit_Amount − Enter_Amount, для short Enter_Amount − Exit_Amount.'], ['PnL_After_All_Commissions', 'Сверочный результат после распределения всех комиссий открытия и закрытия. Используется на листе Reconciliation при сверке с брокерским realised P/L.'], ['PnL', 'Реализованный результат для агрегации Years_Results. Для обычного long: Exit_Amount − (Enter_Amount + Enter_Commission); для short учитывается специфика короткой позиции. Для деривативов налоговая база дополнительно использует PnL_Before_Commission.'], ['KZT_Rate', 'Среднегодовой курс валюты в год Exit_Date.'], ['Exit_Amount_KZT', 'Exit_Amount × KZT_Rate.'], ['PnL_KZT', 'PnL × KZT_Rate.'], ['Source_Trade_ID', 'Идентификатор закрывающей сделки или технический ID корпоративного события, например с префиксом CA:.'],
    ],
  },
  {
    number: '2.9', title: 'Positions', description: 'Снимок открытых позиций на конец отчётного периода или года.', fields: [
      ['Year', 'Год снимка.'], ['Date', 'Отчётная дата снимка.'], ['Asset_Type', 'Тип инструмента.'], ['Symbol', 'Тикер.'], ['ISIN', 'ISIN, если найден.'], ['Currency', 'Валюта оценки.'], ['Country', 'Страна инструмента или эмитента.'], ['Quantity', 'Остаточное количество.'], ['Price', 'Цена закрытия или оценки из отчёта.'], ['Multiplier', 'Множитель инструмента; для обычных бумаг обычно 1.'], ['Amount', 'Стоимость позиции по отчёту: как правило Quantity × Price × Multiplier, с учётом соглашения брокера о знаке.'], ['KZT_Rate', 'Среднегодовой курс валюты для Year.'], ['Amount_KZT', 'Amount × KZT_Rate.'],
    ],
  },
  {
    number: '2.10', title: 'Interest', description: 'Процентные доходы по денежному остатку, финансированию или аналогичным операциям. Доход по ценной бумаге может быть отнесён в Coupons, а swap — в Yearly Derivatives.', fields: [
      ['Date', 'Дата начисления или зачисления.'], ['Description', 'Описание брокера.'], ['Financing_Kind', 'Вид финансирования, если брокер его предоставляет.'], ['Flag', 'Налоговая классификация; для обычного процентного дохода обычно non-preferential.'], ['Years_Result_Table', 'Yearly Interest либо Yearly Derivatives для swap-подобной записи.'], ['Currency', 'Валюта дохода.'], ['Gross_Amount', 'Валовая сумма процента.'], ['Withholding_Tax', 'Удержанный налог, если он есть.'], ['Net_Amount', 'Gross_Amount + Withholding_Tax.'], ['KZT_Rate', 'Среднегодовой курс валюты дохода.'], ['Gross_Amount_KZT', 'Gross_Amount × KZT_Rate.'], ['Withholding_Tax_KZT', 'Withholding_Tax × KZT_Rate.'], ['Net_Amount_KZT', 'Net_Amount × KZT_Rate.'], ['Commission', 'Комиссия, если она явно относится к строке.'], ['Source_Report', 'Файл брокерского отчёта.'],
    ],
  },
  {
    number: '2.11', title: 'Coupons', description: 'Купонные выплаты по облигациям. Лист отделён от Interest, чтобы купонный доход можно было проверить отдельно.', fields: [
      ['Date', 'Дата начисления или выплаты.'], ['Symbol', 'Тикер облигации.'], ['ISIN', 'ISIN облигации.'], ['Country', 'Страна инструмента или эмитента.'], ['Flag', 'Налоговая классификация бумаги.'], ['Currency', 'Валюта купона.'], ['Gross_Amount', 'Валовая сумма купона.'], ['Withholding_Tax', 'Удержанный налог со знаком отчёта.'], ['Net_Amount', 'Gross_Amount + Withholding_Tax.'], ['KZT_Rate', 'Среднегодовой курс валюты дохода.'], ['Gross_Amount_KZT', 'Gross_Amount × KZT_Rate.'], ['Withholding_Tax_KZT', 'Withholding_Tax × KZT_Rate.'], ['Net_Amount_KZT', 'Net_Amount × KZT_Rate.'], ['Is_Revert', 'true, если текст брокера прямо указывает на отмену или сторно дохода; такое сторно уменьшает соответствующий положительный доход.'], ['Offshore_Flag', 'Признак офшорности, если определён.'], ['Source_Report', 'Файл брокерского отчёта.'],
    ], note: 'В Yearly Coupons Amount показывает все купонные строки, а OnlyProfit отделяет сумму, считающуюся вознаграждением. Отрицательный НКД может оставаться в Amount, но не уменьшать OnlyProfit.',
  },
  {
    number: '2.12', title: 'CashBalances', description: 'Остатки денег по валютам на отчётную дату. Используются в контрольной сверке ending cash.', fields: [
      ['Broker', 'Код брокера. Если исходная строка его не содержит, подставляется код текущего обрабатываемого счёта.'], ['Account_ID', 'Номер счёта. Если в строке отсутствует, подставляется текущий счёт.'], ['Year', 'Год остатка.'], ['Date', 'Отчётная дата остатка.'], ['Currency', 'Валюта остатка.'], ['Ending_Cash', 'Конечный денежный остаток в валюте.'], ['Ending_Cash_KZT', 'Ending_Cash × среднегодовой курс соответствующего года.'], ['Source_Report', 'Файл брокерского отчёта.'],
    ],
  },
  {
    number: '2.13', title: 'Instruments', description: 'Справочник всех инструментов, встреченных в загруженных отчётах. Он нужен для идентификации, классификации и объяснения того, откуда взялись Symbol, ISIN, страна и признаки льготности в других листах.', fields: [
      ['Symbol', 'Тикер или символ инструмента.'], ['Description', 'Название или описание из брокерского отчёта.'], ['Conid', 'Внутренний идентификатор Interactive Brokers, если доступен.'], ['Security_ID', 'Идентификатор инструмента у брокера.'], ['Underlying', 'Базовый актив для дериватива, если есть.'], ['Listing_Exchange', 'Площадка листинга инструмента.'], ['Multiplier', 'Множитель контракта.'], ['Type', 'Тип инструмента.'], ['Code', 'Код инструмента в отчёте или справочнике.'], ['Year', 'Год, к которому относится запись справочника, если он важен для идентификации.'], ['Expiry', 'Дата экспирации дериватива.'], ['Delivery_Month', 'Месяц поставки фьючерса, если есть.'], ['Strike', 'Страйк опциона.'], ['Issuer', 'Эмитент.'], ['Maturity', 'Дата погашения облигации.'], ['CUSIP', 'Идентификатор CUSIP.'], ['Country', 'Страна инструмента или эмитента, используемая в классификации.'], ['ISIN', 'Международный идентификатор ценной бумаги.'], ['FIGI', 'Идентификатор FIGI.'], ['Issuer_Country', 'Страна эмитента, если отдельна от Country.'], ['Offshore_Flag', 'Результат проверки офшорности страны эмитента.'], ['Issuer_Outside_KZ_Flag', 'Признак, что эмитент находится вне Казахстана.'], ['Preferential_Tax_Flag', 'Признак налоговой льготы из справочника.'], ['Source_Broker', 'Брокер, от которого получена запись.'], ['Source_Account', 'Счёт, от которого получена запись.'], ['Source_Report', 'Файл, где инструмент обнаружен.'], ['As_Of_Date', 'Дата актуальности записи или снимка.'],
    ],
  },
]

export default function AuditFilePage() {
  return <div className="min-h-screen bg-background">
    <FaqHeader article />
    <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
      <Link href="/faq" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" aria-hidden="true" />Все материалы FAQ</Link>

      <header className="mt-6 max-w-3xl">
        <p className="text-sm font-semibold text-primary">МЕТОДОЛОГИЯ И РАСЧЁТЫ</p>
        <h1 className="mt-2 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">Что находится в audit-файле</h1>
        <p className="mt-3 text-pretty leading-relaxed text-muted-foreground">Audit Excel — это обработка загруженных брокерских отчётов. На его основании заполняется форма 270.00; файл помогает проверить путь от операции до годового результата.</p>
      </header>

      <Card className="mt-8 border-primary/20 bg-accent/25"><CardContent className="flex gap-3 pt-5"><FileSearch className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" /><div className="text-sm leading-relaxed"><p>Суммы указаны в валюте операции, если в названии нет суффикса <strong>_KZT</strong>. Поля с суффиксом <strong>_KZT</strong> переведены в тенге.</p><p className="mt-2"><strong>KZT_Rate</strong> — среднегодовой курс соответствующей валюты к тенге за год операции. Для KZT он равен 1; основной источник курсов — справочные среднегодовые курсы НБК.</p></div></CardContent></Card>

      <section className="mt-10" aria-labelledby="classification-title">
        <div className="flex items-center gap-2"><TableProperties className="size-5 text-primary" aria-hidden="true" /><h2 id="classification-title" className="text-2xl font-semibold tracking-tight">Как проверять расчёт</h2></div>
        <div className="mt-4 grid gap-4 lg:grid-cols-2"><Card><CardHeader><CardTitle className="text-lg">Flag</CardTitle><CardDescription className="leading-relaxed">Признак налоговой классификации строки.</CardDescription></CardHeader><CardContent><ul className="space-y-2 text-sm leading-relaxed text-muted-foreground"><li><strong className="text-foreground">non-preferential</strong> — обычный, не льготный источник.</li><li><strong className="text-foreground">preferential</strong> — льготный инструмент.</li><li><strong className="text-foreground">preferential_aix / preferential_kase</strong> — доход или сделка, отнесённые к льготному режиму AIX или KASE.</li><li><strong className="text-foreground">offshore</strong> — инструмент с офшорной классификацией.</li></ul></CardContent></Card><Card><CardHeader><CardTitle className="text-lg">Связь детальных данных и итогов</CardTitle></CardHeader><CardContent className="text-sm leading-relaxed text-muted-foreground">Классификация формируется по справочнику инструмента, стране эмитента, признакам AIX/KASE и справочнику офшорных юрисдикций. Найдите детальную операцию на листах Dividends, Fifo, Interest или Coupons, посмотрите её Flag, Tax_Exchange и Years_Result_Table, затем найдите строку с теми же признаками в агрегированном Years_Results.</CardContent></Card></div>
      </section>

      <section className="mt-12" aria-labelledby="sheets-title">
        <h2 id="sheets-title" className="text-2xl font-semibold tracking-tight">Листы audit-файла</h2>

        <ArticleSection number="2.1" title="Years_Results" description="Главный лист проверки: отдельные таблицы с итогами по году, налоговой классификации, стране, бирже и валюте. Его показатели используются при подготовке Form 270.00.">
          <p className="text-sm leading-relaxed text-muted-foreground">Возможные таблицы: <strong className="text-foreground">Yearly Trades</strong> — обычные ценные бумаги; <strong className="text-foreground">Yearly Derivatives</strong> — деривативы, опционы, фьючерсы и свопы; <strong className="text-foreground">Yearly Bonds Redemption</strong> — погашение облигаций; <strong className="text-foreground">Yearly FX Trades</strong> — операции с валютой; <strong className="text-foreground">Yearly Dividends</strong>, <strong className="text-foreground">Yearly Interest</strong> и <strong className="text-foreground">Yearly Coupons</strong> — соответствующие виды дохода.</p>
          <FieldList fields={yearsResultFields} />
        </ArticleSection>

        {sections.map((section) => <ArticleSection key={section.number} {...section}><FieldList fields={section.fields} />{section.note && <p className="mt-5 rounded-lg border border-primary/15 bg-muted/30 p-4 text-sm leading-relaxed text-muted-foreground">{section.note}</p>}{section.title === 'Reconciliation' && <AuditWarning>Если Difference есть по <strong>ending_position_quantity</strong> или <strong>unprocessed_rows</strong>, отправьте audit-файл в <a href="https://t.me/aleksei_ash" target="_blank" rel="noreferrer">Telegram</a>: налоговый отчёт может быть неполным.</AuditWarning>}{section.title === 'Unprocessed' && <AuditWarning>При появлении записи в этом листе рекомендуем отправить audit-файл в <a href="https://t.me/aleksei_ash" target="_blank" rel="noreferrer">Telegram</a>, так как налоговый отчёт может быть неполным.</AuditWarning>}</ArticleSection>)}
      </section>

      <Card className="mt-10 border-primary/25 bg-accent/25"><CardContent className="pt-5 text-sm leading-relaxed text-muted-foreground">Окончательная налоговая обязанность зависит от конкретной ситуации налогоплательщика; результат необходимо проверить со специалистом.</CardContent></Card>
    </main>
  </div>
}

function ArticleSection({ number, title, description, children }: { number: string; title: string; description: string; children: React.ReactNode }) {
  return <Card className="mt-6 border-border/80"><CardHeader><div className="text-sm font-semibold text-primary">{number}</div><CardTitle className="text-xl">{title}</CardTitle><CardDescription className="max-w-3xl leading-relaxed">{description}</CardDescription></CardHeader><CardContent>{children}</CardContent></Card>
}

function FieldList({ fields }: { fields: Field[] }) {
  return <dl className="mt-5 grid gap-x-6 gap-y-4 border-t pt-5 sm:grid-cols-2">{fields.map(([name, description]) => <div key={name}><dt className="font-mono text-sm font-semibold text-primary">{name}</dt><dd className="mt-1 text-sm leading-relaxed text-muted-foreground">{description}</dd></div>)}</dl>
}

function AuditWarning({ children }: { children: React.ReactNode }) {
  return <Alert className="mt-5 border-amber-500/35 bg-amber-500/10 text-foreground"><AlertTriangle className="text-amber-700 dark:text-amber-400" /><AlertDescription className="text-sm leading-relaxed">{children}</AlertDescription></Alert>
}
