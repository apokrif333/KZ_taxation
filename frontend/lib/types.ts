export type AppStep = 'upload' | 'processing' | 'results'

export type Broker = 'Interactive Brokers' | 'Freedom Broker' | 'Exante' | 'Tsifra Broker' | 'Tabys'

export interface UploadedReport {
  id: string
  name: string
  size: number
  status: 'valid' | 'invalid'
  error?: string
}

export type ReconciliationStatus = 'match' | 'warning' | 'error'

export interface ReconciliationRow {
  label: string
  broker: string
  calculated: string
  difference: string
  status: ReconciliationStatus
}

export interface ProcessingWarning {
  id: string
  severity: 'info' | 'warning' | 'error'
  title: string
  details: string
}

export interface TaxRow {
  category: string
  amount: string
  taxable: string
  withheld: string
  note: string
}

export interface ProcessingResult {
  taxYear: string
  operations: number
  instruments: number
  warningCount: number
  reconciliationErrors: number
  reconciliation: ReconciliationRow[]
  warnings: ProcessingWarning[]
  taxSummary: TaxRow[]
}
