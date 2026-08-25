import type { Broker, ProcessingResult, UploadedReport } from '@/lib/types'

export interface ProcessReportRequest {
  broker: Broker
  files: UploadedReport[]
  taxYear: string
  jointAccount: boolean
}

export const PROCESSING_STAGES = [
  'Чтение отчёта',
  'Обработка операций',
  'Расчёт FIFO',
  'Классификация доходов',
  'Применение налоговых правил',
  'Сверка с отчётом брокера',
  'Формирование результатов',
] as const

const mockResult: ProcessingResult = {
  taxYear: '2025',
  operations: 248,
  instruments: 17,
  warningCount: 3,
  reconciliationErrors: 1,
  reconciliation: [
    { label: 'Сумма сделок', broker: '42 840 510 ₸', calculated: '42 840 510 ₸', difference: '0 ₸', status: 'match' },
    { label: 'Комиссии', broker: '128 420 ₸', calculated: '128 420 ₸', difference: '0 ₸', status: 'match' },
    { label: 'Дивиденды', broker: '684 210 ₸', calculated: '684 210 ₸', difference: '0 ₸', status: 'match' },
    { label: 'Удержанный налог', broker: '102 632 ₸', calculated: '102 632 ₸', difference: '0 ₸', status: 'match' },
    { label: 'Проценты', broker: '84 350 ₸', calculated: '84 120 ₸', difference: '−230 ₸', status: 'warning' },
    { label: 'Купоны', broker: '212 000 ₸', calculated: '212 000 ₸', difference: '0 ₸', status: 'match' },
    { label: 'Вводы и выводы средств', broker: '8 500 000 ₸', calculated: '8 500 000 ₸', difference: '0 ₸', status: 'match' },
    { label: 'Остаток денежных средств', broker: '1 284 090 ₸', calculated: '1 284 090 ₸', difference: '0 ₸', status: 'match' },
    { label: 'Позиции на конец периода', broker: '15', calculated: '15', difference: '0', status: 'match' },
    { label: 'Реализованный P/L', broker: '1 842 300 ₸', calculated: '1 791 860 ₸', difference: '−50 440 ₸', status: 'error' },
  ],
  warnings: [
    { id: 'w1', severity: 'info', title: 'Обнаружены операции, требующие проверки.', details: 'Две операции содержат неполное описание корпоративного действия. Проверьте лист «Предупреждения» в audit-файле.' },
    { id: 'w2', severity: 'warning', title: 'Реализованный P/L брокера отличается от налогового FIFO.', details: 'Расхождение может быть связано с методом определения себестоимости. Налоговый FIFO рассчитан отдельно от показателя брокера.' },
    { id: 'w3', severity: 'error', title: 'Не удалось определить страну эмитента для одного инструмента.', details: 'Инструмент с тикером EXAMPLE требует ручного уточнения страны эмитента перед подачей формы.' },
  ],
  taxSummary: [
    { category: 'Реализация ценных бумаг', amount: '1 791 860 ₸', taxable: '1 432 600 ₸', withheld: '0 ₸', note: 'По данным налогового FIFO' },
    { category: 'Дивиденды', amount: '684 210 ₸', taxable: '684 210 ₸', withheld: '102 632 ₸', note: 'Требуется проверка источника' },
    { category: 'Купоны', amount: '212 000 ₸', taxable: '212 000 ₸', withheld: '21 200 ₸', note: '—' },
    { category: 'Проценты', amount: '84 120 ₸', taxable: '84 120 ₸', withheld: '0 ₸', note: 'Есть расхождение 230 ₸' },
    { category: 'Прочие доходы', amount: '18 500 ₸', taxable: '18 500 ₸', withheld: '0 ₸', note: 'Проверить классификацию' },
  ],
}

export async function mockProcessReport(request: ProcessReportRequest): Promise<ProcessingResult> {
  await new Promise((resolve) => setTimeout(resolve, 700))
  return { ...mockResult, taxYear: request.taxYear }
}
