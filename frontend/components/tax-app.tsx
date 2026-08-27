'use client'

import { useCallback, useEffect, useState } from 'react'
import { LoaderCircle, RefreshCw, TriangleAlert } from 'lucide-react'
import { DiscoveredAccounts, accountKey } from '@/components/discovered-accounts'
import { MissingTransfers } from '@/components/missing-transfers'
import { PrivacyView } from '@/components/privacy-view'
import { ProcessingProgress } from '@/components/processing-progress'
import { ResultsView } from '@/components/results-view'
import { SiteHeader } from '@/components/site-header'
import { UploadWorkflow } from '@/components/upload-workflow'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { ApiClientError, createJob, deleteJob, discoverAccounts, getConfig, processJob, uploadReports } from '@/lib/api-client'
import type {
  AccountSelection,
  ApiConfig,
  AppStep,
  BrokerConfig,
  DiscoveredAccount,
  InvalidReportPeriod,
  ManualAccountGroup,
  ProcessResponse,
  SelectedReport,
} from '@/lib/types'

const FIXED_TAXPAYER = { fio1: 'Ivanov', fio2: 'Ivanov', fio3: 'Ivanov', iin: '1' } as const
const SUPPORTED_TAX_YEAR = '2025'

export function TaxApp() {
  const [config, setConfig] = useState<ApiConfig | null>(null)
  const [configError, setConfigError] = useState<string | null>(null)
  const [step, setStep] = useState<AppStep>('upload')
  const [privacy, setPrivacy] = useState(false)
  const taxYear = SUPPORTED_TAX_YEAR
  const [autoFiles, setAutoFiles] = useState<Record<string, SelectedReport[]>>({})
  const [manualGroups, setManualGroups] = useState<ManualAccountGroup[]>([])
  const [jobId, setJobId] = useState<string | null>(null)
  const [accounts, setAccounts] = useState<DiscoveredAccount[]>([])
  const [selections, setSelections] = useState<Record<string, AccountSelection>>({})
  const [result, setResult] = useState<ProcessResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [invalidReports, setInvalidReports] = useState<InvalidReportPeriod[]>([])

  const loadConfig = useCallback(async () => {
    setConfigError(null)
    try {
      setConfig(await getConfig())
    } catch (caught) {
      setConfigError(errorMessage(caught))
    }
  }, [])

  useEffect(() => { void loadConfig() }, [loadConfig])

  const clearError = () => { setError(null); setInvalidReports([]) }

  const addReports = (broker: BrokerConfig, files: File[]): SelectedReport[] => {
    if (!config) return []
    const allowed = new Set(broker.upload_extensions.map(normalizeExtension))
    return files.map((file) => {
      const extension = normalizeExtension(file.name.includes('.') ? `.${file.name.split('.').pop()}` : '')
      let reportError: string | undefined
      if (!allowed.has(extension)) reportError = `Формат не поддерживается для ${broker.display_name}`
      else if (file.size > config.max_upload_bytes) reportError = `Файл превышает лимит ${config.max_upload_mb} МБ`
      return { id: crypto.randomUUID(), file, status: reportError ? 'invalid' : 'valid', error: reportError, uploaded: false }
    })
  }

  const handleAddAutoFiles = (broker: BrokerConfig, files: File[]) => {
    clearError()
    setAutoFiles((current) => ({ ...current, [broker.code]: [...(current[broker.code] || []), ...addReports(broker, files)] }))
  }

  const handleAddManualFiles = (groupId: string, broker: BrokerConfig, files: File[]) => {
    clearError()
    const incoming = addReports(broker, files)
    setManualGroups((current) => current.map((group) => group.id === groupId ? { ...group, files: [...group.files, ...incoming] } : group))
  }

  const handleContinue = async () => {
    if (!config) return
    clearError()
    const totalFiles = [...Object.values(autoFiles).flat(), ...manualGroups.flatMap((group) => group.files)].length
    if (totalFiles > config.max_job_files) {
      setError(`В одном расчёте допускается не более ${config.max_job_files} файлов.`)
      return
    }
    setBusy(true)
    let currentJobId = jobId
    try {
      if (!currentJobId) {
        const created = await createJob()
        currentJobId = created.job_id
        setJobId(currentJobId)
      }

      for (const broker of config.brokers.filter((item) => item.account_id_mode === 'auto')) {
        const pending = (autoFiles[broker.code] || []).filter((report) => !report.uploaded && report.status === 'valid')
        for (const batch of chunks(pending, config.max_files)) {
          await uploadReports(currentJobId, broker.code, batch.map((report) => report.file))
          markAutoUploaded(broker.code, new Set(batch.map((report) => report.id)))
        }
      }

      for (const group of manualGroups) {
        const pending = group.files.filter((report) => !report.uploaded && report.status === 'valid')
        for (const batch of chunks(pending, config.max_files)) {
          await uploadReports(currentJobId, group.broker, batch.map((report) => report.file), group.accountId.trim())
          markManualUploaded(group.id, new Set(batch.map((report) => report.id)))
        }
      }

      const discovered = await discoverAccounts(currentJobId)
      setAccounts(discovered.accounts)
      setSelections((current) => Object.fromEntries(discovered.accounts.map((account) => [accountKey(account), current[accountKey(account)] || { joint: false, excluded: false }])))
      setStep('accounts')
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (caught) {
      applyError(caught)
    } finally {
      setBusy(false)
    }
  }

  const runProcess = async (allowApproximate: boolean) => {
    if (!jobId) return
    clearError()
    setBusy(true)
    setStep('processing')
    window.scrollTo({ top: 0, behavior: 'smooth' })
    try {
      const processed = await processJob(jobId, {
        tax_year: Number(taxYear),
        taxpayer: FIXED_TAXPAYER,
        joint_accounts: accounts.filter((account) => selections[accountKey(account)]?.joint).map((account) => account.account_id),
        acc_not_included_for_merged: accounts.filter((account) => selections[accountKey(account)]?.excluded).map((account) => account.account_id),
        form270_05: false,
        allow_approximate_transfer_basis: allowApproximate,
      })
      setResult(processed)
      setStep(processed.status === 'completed' ? 'results' : 'missing')
    } catch (caught) {
      applyError(caught)
      setStep(allowApproximate && result?.status === 'needs_additional_reports' ? 'missing' : 'accounts')
    } finally {
      setBusy(false)
    }
  }

  const resetLocalState = () => {
    setStep('upload'); setPrivacy(false)
    setAutoFiles({}); setManualGroups([]); setJobId(null); setAccounts([]); setSelections({}); setResult(null); setBusy(false); clearError()
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const restart = async () => {
    const currentJobId = jobId
    resetLocalState()
    if (currentJobId) {
      try { await deleteJob(currentJobId) } catch { /* TTL cleanup remains the fallback. */ }
    }
  }

  const applyError = (caught: unknown) => {
    setError(errorMessage(caught))
    setInvalidReports(caught instanceof ApiClientError ? caught.reports : [])
  }

  const markAutoUploaded = (brokerCode: string, ids: Set<string>) => setAutoFiles((current) => ({ ...current, [brokerCode]: (current[brokerCode] || []).map((report) => ids.has(report.id) ? { ...report, uploaded: true } : report) }))
  const markManualUploaded = (groupId: string, ids: Set<string>) => setManualGroups((current) => current.map((group) => group.id === groupId ? { ...group, files: group.files.map((report) => ids.has(report.id) ? { ...report, uploaded: true } : report) } : group))

  return <div className="min-h-screen bg-background">
    <SiteHeader privacyActive={privacy} onPrivacy={() => setPrivacy(true)} onStart={() => { void restart() }} />
    {privacy ? <PrivacyView onBack={() => setPrivacy(false)} /> : <main className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
      {step === 'upload' && <>
        <section className="grid gap-6 py-10 lg:grid-cols-[1fr_auto] lg:items-end"><div><p className="text-sm font-semibold text-primary">ФОРМА 270.00 · КАЗАХСТАН</p><h1 className="mt-2 max-w-3xl text-balance text-3xl font-semibold tracking-tight sm:text-4xl">Подготовка формы 270.00 по брокерским отчётам</h1><p className="mt-3 max-w-2xl text-pretty leading-relaxed text-muted-foreground">Соберите отчёты нескольких брокеров и счетов в одном расчёте, проверьте обнаруженные счета и скачайте итоговые файлы.</p></div><div id="how-it-works" className="flex gap-5 border-l border-primary/25 pl-5 text-sm"><WorkflowItem number="1" label="Добавьте отчёты" /><WorkflowItem number="2" label="Настройте счета" /><WorkflowItem number="3" label="Скачайте результаты" /></div></section>
        {!config && !configError && <LoadingConfig />}
        {configError && <ConfigError message={configError} onRetry={() => { void loadConfig() }} />}
        {config && <UploadWorkflow config={config} taxYear={taxYear} autoFiles={autoFiles} manualGroups={manualGroups} hasJob={Boolean(jobId)} busy={busy} error={error} invalidReports={invalidReports} onAddAutoFiles={handleAddAutoFiles} onRemoveAutoFile={(brokerCode, reportId) => setAutoFiles((current) => ({ ...current, [brokerCode]: (current[brokerCode] || []).filter((report) => report.id !== reportId || report.uploaded) }))} onAddManualGroup={(broker) => setManualGroups((current) => [...current, { id: crypto.randomUUID(), broker, accountId: '', files: [] }])} onRemoveManualGroup={(groupId) => setManualGroups((current) => current.filter((group) => group.id !== groupId || group.files.some((report) => report.uploaded)))} onManualAccountChange={(groupId, value) => setManualGroups((current) => current.map((group) => group.id === groupId ? { ...group, accountId: value } : group))} onAddManualFiles={handleAddManualFiles} onRemoveManualFile={(groupId, reportId) => setManualGroups((current) => current.map((group) => group.id === groupId ? { ...group, files: group.files.filter((report) => report.id !== reportId || report.uploaded) } : group))} onContinue={() => { void handleContinue() }} onAbandon={() => { void restart() }} />}
        <footer className="flex flex-col items-center gap-1 border-t border-primary/10 py-10 text-center text-xs text-muted-foreground"><span className="font-semibold text-primary">QCM Tax 270</span><span>by Quantum Cross Management · Результаты требуют проверки специалистом.</span></footer>
      </>}
      {step === 'accounts' && config && <DiscoveredAccounts accounts={accounts} brokers={config.brokers} selections={selections} busy={busy} error={error} invalidReports={invalidReports} onSelectionChange={(account, field, value) => setSelections((current) => ({ ...current, [accountKey(account)]: { ...(current[accountKey(account)] || { joint: false, excluded: false }), [field]: value } }))} onBack={() => { clearError(); setStep('upload') }} onProcess={() => { void runProcess(false) }} />}
      {step === 'processing' && <ProcessingProgress />}
      {step === 'missing' && result && <MissingTransfers items={result.missing_transfer_basis} busy={busy} error={error} onAddReports={() => { clearError(); setStep('upload') }} onApproximate={() => { void runProcess(true) }} />}
      {step === 'results' && result && config && <ResultsView result={result} brokers={config.brokers} onRestart={() => { void restart() }} />}
    </main>}
  </div>
}

function normalizeExtension(extension: string) { return extension.trim().toLowerCase().replace(/^([^.]|$)/, '.$1') }
function chunks<T>(items: T[], size: number): T[][] { return Array.from({ length: Math.ceil(items.length / size) }, (_, index) => items.slice(index * size, (index + 1) * size)) }
function errorMessage(caught: unknown) { return caught instanceof Error ? caught.message : 'Произошла неизвестная ошибка.' }
function WorkflowItem({ number, label }: { number: string; label: string }) { return <div className="flex max-w-24 flex-col gap-2"><span className="flex size-6 items-center justify-center rounded-full bg-accent font-mono text-xs font-semibold text-primary ring-1 ring-primary/20">{number}</span><span className="text-muted-foreground">{label}</span></div> }
function LoadingConfig() { return <div className="flex min-h-52 items-center justify-center gap-3 text-muted-foreground"><LoaderCircle className="animate-spin" />Загружаем список брокеров…</div> }
function ConfigError({ message, onRetry }: { message: string; onRetry: () => void }) { return <Alert variant="destructive"><TriangleAlert /><AlertDescription><p>{message}</p><Button className="mt-3" variant="outline" onClick={onRetry}><RefreshCw data-icon="inline-start" />Повторить</Button></AlertDescription></Alert> }
