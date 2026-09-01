export type JobStatus =
  | 'collecting'
  | 'awaiting_options'
  | 'processing'
  | 'needs_additional_reports'
  | 'completed'

export type AccountIdMode = 'auto' | 'manual'

export interface BrokerConfig {
  code: string
  display_name: string
  upload_extensions: string[]
  account_id_mode: AccountIdMode
}

export interface ApiConfig {
  brokers: BrokerConfig[]
  max_upload_bytes: number
  max_upload_mb: number
  max_files: number
  max_job_files: number
  pending_job_ttl_seconds: number
  job_ttl_seconds: number
}

export interface CreateJobResponse {
  job_id: string
  status: 'collecting'
}

export interface UploadBatchResponse {
  job_id: string
  status: 'collecting'
  accepted_files: number
  total_files: number
  reports: UploadedReport[]
}

export interface UploadedReport {
  report_id: string
  filename: string
}

export interface DeleteReportResponse {
  job_id: string
  status: 'collecting'
  total_files: number
}

export interface DiscoveredAccount {
  broker: string
  account_id: string
  report_count: number
}

export interface DiscoverResponse {
  job_id: string
  status: 'awaiting_options'
  accounts: DiscoveredAccount[]
}

export interface TaxpayerPayload {
  fio1: string
  fio2: string
  fio3: string
  iin: string
}

export interface ProcessJobRequest {
  tax_year: number
  taxpayer: TaxpayerPayload
  joint_accounts: string[]
  acc_not_included_for_merged: string[]
  form270_05: boolean
  allow_approximate_transfer_basis: boolean
}

export interface MissingTransferBasis {
  transfer_date: string | null
  symbol: string | null
  isin: string | null
  quantity: string
  currency: string | null
  destination_broker: string
  destination_account: string
  reason: string
}

export type ArtifactKind = 'account_audit' | 'joint_audit' | 'merged_audit' | 'form270'

export interface Artifact {
  id: string
  kind: ArtifactKind
  filename: string
  download_url: string
  broker: string | null
  account_id: string | null
}

export interface ProcessResponse {
  job_id: string
  status: 'needs_additional_reports' | 'completed'
  tax_year: number
  missing_transfer_basis: MissingTransferBasis[]
  used_approximate_transfer_basis: boolean
  artifacts: Artifact[]
}

export interface InvalidReportPeriod {
  broker: string
  account_id: string | null
  report_name: string
  period_end: string | null
}

export interface ApiErrorDetail {
  code: string
  message: string
  reports?: InvalidReportPeriod[]
}

export interface ApiErrorResponse {
  detail: ApiErrorDetail
}

export type AppStep = 'upload' | 'accounts' | 'processing' | 'missing' | 'results'

export interface SelectedReport {
  id: string
  file: File
  status: 'valid' | 'invalid'
  error?: string
  uploaded: boolean
  serverReportId?: string
}

export interface ManualAccountGroup {
  id: string
  broker: string
  accountId: string
  files: SelectedReport[]
}

export interface AccountSelection {
  joint: boolean
  excluded: boolean
}
