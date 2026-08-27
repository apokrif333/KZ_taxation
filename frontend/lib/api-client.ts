import type {
  ApiConfig,
  ApiErrorResponse,
  CreateJobResponse,
  DiscoverResponse,
  InvalidReportPeriod,
  ProcessJobRequest,
  ProcessResponse,
  UploadBatchResponse,
} from '@/lib/types'

const DEFAULT_API_URL = 'http://localhost:8000'

export const apiBaseUrl = (process.env.NEXT_PUBLIC_API_URL || DEFAULT_API_URL).replace(/\/$/, '')

export class ApiClientError extends Error {
  readonly code: string
  readonly status: number
  readonly reports: InvalidReportPeriod[]

  constructor(message: string, code = 'network_error', status = 0, reports: InvalidReportPeriod[] = []) {
    super(message)
    this.name = 'ApiClientError'
    this.code = code
    this.status = status
    this.reports = reports
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(resolveApiUrl(path), init)
  } catch {
    throw new ApiClientError('Не удалось подключиться к серверу расчёта. Проверьте, что FastAPI запущен.')
  }

  if (!response.ok) {
    let error: ApiErrorResponse | null = null
    try {
      error = (await response.json()) as ApiErrorResponse
    } catch {
      // Infrastructure errors do not necessarily use the FastAPI error schema.
    }
    throw new ApiClientError(
      error?.detail?.message || 'Сервер не смог выполнить запрос.',
      error?.detail?.code || 'request_failed',
      response.status,
      error?.detail?.reports || [],
    )
  }

  return (await response.json()) as T
}

export function getConfig(): Promise<ApiConfig> {
  return requestJson<ApiConfig>('/api/config', { cache: 'no-store' })
}

export function createJob(): Promise<CreateJobResponse> {
  return requestJson<CreateJobResponse>('/api/jobs', { method: 'POST' })
}

export function uploadReports(
  jobId: string,
  broker: string,
  files: File[],
  accountId?: string,
): Promise<UploadBatchResponse> {
  const body = new FormData()
  body.append('broker', broker)
  if (accountId !== undefined) body.append('account_id', accountId)
  for (const file of files) body.append('files', file, file.name)
  return requestJson<UploadBatchResponse>(`/api/jobs/${encodeURIComponent(jobId)}/reports`, {
    method: 'POST',
    body,
  })
}

export function discoverAccounts(jobId: string): Promise<DiscoverResponse> {
  return requestJson<DiscoverResponse>(`/api/jobs/${encodeURIComponent(jobId)}/discover`, { method: 'POST' })
}

export function processJob(jobId: string, request: ProcessJobRequest): Promise<ProcessResponse> {
  return requestJson<ProcessResponse>(`/api/jobs/${encodeURIComponent(jobId)}/process`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
}

export async function deleteJob(jobId: string): Promise<void> {
  await requestJson(`/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' })
}

export function resolveApiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path
  return `${apiBaseUrl}${path.startsWith('/') ? path : `/${path}`}`
}

export function artifactUrl(downloadUrl: string): string {
  return resolveApiUrl(downloadUrl)
}

export function downloadAllUrl(jobId: string): string {
  return resolveApiUrl(`/api/jobs/${encodeURIComponent(jobId)}/all`)
}
