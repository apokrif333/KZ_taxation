'use client'

import Link from 'next/link'
import { useRef, useState, type DragEvent } from 'react'
import { BookOpen, CheckCircle2, FileSpreadsheet, Info, LockKeyhole, Plus, Trash2, UploadCloud } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { cn } from '@/lib/utils'
import type { ApiConfig, BrokerConfig, InvalidReportPeriod, ManualAccountGroup, SelectedReport } from '@/lib/types'

interface UploadWorkflowProps {
  config: ApiConfig
  taxYear: string
  autoFiles: Record<string, SelectedReport[]>
  manualGroups: ManualAccountGroup[]
  hasJob: boolean
  busy: boolean
  error: string | null
  invalidReports: InvalidReportPeriod[]
  onAddAutoFiles: (broker: BrokerConfig, files: File[]) => void
  onRemoveAutoFile: (brokerCode: string, reportId: string) => void
  onAddManualGroup: (brokerCode: string) => void
  onRemoveManualGroup: (groupId: string) => void
  onManualAccountChange: (groupId: string, value: string) => void
  onAddManualFiles: (groupId: string, broker: BrokerConfig, files: File[]) => void
  onRemoveManualFile: (groupId: string, reportId: string) => void
  onContinue: () => void
  onAbandon: () => void
}

export function UploadWorkflow({
  config, taxYear, autoFiles, manualGroups, hasJob, busy, error, invalidReports,
  onAddAutoFiles, onRemoveAutoFile, onAddManualGroup, onRemoveManualGroup,
  onManualAccountChange, onAddManualFiles, onRemoveManualFile, onContinue, onAbandon,
}: UploadWorkflowProps) {
  const autoBrokers = config.brokers.filter((broker) => broker.account_id_mode === 'auto')
  const manualBrokers = config.brokers.filter((broker) => broker.account_id_mode === 'manual')
  const allReports = [...Object.values(autoFiles).flat(), ...manualGroups.flatMap((group) => group.files)]
  const hasAcceptedOrValidReport = allReports.some((report) => report.uploaded || report.status === 'valid')
  const hasInvalidReport = allReports.some((report) => report.status === 'invalid')
  const missingManualAccount = manualGroups.some(
    (group) => group.files.some((report) => report.uploaded || report.status === 'valid') && !group.accountId.trim(),
  )
  const canSubmit = hasAcceptedOrValidReport && !hasInvalidReport && !missingManualAccount && !busy

  return (
    <section aria-labelledby="calculation-title" className="grid gap-6 lg:grid-cols-[1fr_19rem]">
      <Card className="border-border/80 shadow-sm">
        <CardHeader className="border-b">
          <div className="flex items-center gap-3">
            <span className="flex size-7 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">1</span>
            <div><CardTitle id="calculation-title">Брокерские отчёты</CardTitle><CardDescription>Добавьте отчёты только тех брокеров, которые участвуют в расчёте.</CardDescription></div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          {hasJob && <Alert className="border-primary/25 bg-accent/35"><Info /><AlertDescription>Ранее принятые файлы уже находятся в этом расчёте. Добавьте только новые отчёты — повторно они не отправятся.</AlertDescription></Alert>}

          {autoBrokers.map((broker) => <BrokerReportCard key={broker.code} broker={broker} reports={autoFiles[broker.code] || []} onFiles={(files) => onAddAutoFiles(broker, files)} onRemove={(reportId) => onRemoveAutoFile(broker.code, reportId)} />)}

          {manualBrokers.map((broker) => {
            const groups = manualGroups.filter((group) => group.broker === broker.code)
            return <div key={broker.code} className="rounded-lg border bg-muted/20 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold">{broker.display_name}</h2><p className="text-sm text-muted-foreground">Для каждого счёта укажите номер и добавьте его отчёты отдельно.</p></div><Button variant="outline" onClick={() => onAddManualGroup(broker.code)}><Plus data-icon="inline-start" />Добавить счёт</Button></div>
              <div className="mt-4 flex flex-col gap-4">
                {groups.length === 0 && <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">Счета не добавлены.</p>}
                {groups.map((group, index) => {
                  const locked = group.files.some((report) => report.uploaded)
                  return <div key={group.id} className="rounded-md border bg-card p-4">
                    <div className="flex flex-wrap items-end gap-3">
                      <label className="min-w-52 flex-1 text-sm font-medium">Номер счёта {groups.length > 1 ? index + 1 : ''}<input className="mt-2 h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60" value={group.accountId} disabled={locked} onChange={(event) => onManualAccountChange(group.id, event.target.value)} placeholder="Например, 759023" /></label>
                      <FilePicker className="min-w-64 flex-1" broker={broker} onFiles={(files) => onAddManualFiles(group.id, broker, files)} />
                      <Button variant="ghost" size="icon" disabled={locked} onClick={() => onRemoveManualGroup(group.id)} aria-label="Удалить счёт" title={locked ? 'Принятый backend счёт можно удалить только вместе со всем расчётом' : 'Удалить счёт'}><Trash2 /></Button>
                    </div>
                    {!group.accountId.trim() && group.files.length > 0 && <p className="mt-2 text-xs text-destructive">Укажите номер счёта Freedom.</p>}
                    <ReportList reports={group.files} onRemove={(reportId) => onRemoveManualFile(group.id, reportId)} />
                  </div>
                })}
              </div>
              {broker.code === 'freedom' && <div className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-primary/10 pt-3 text-sm"><BookOpen className="size-4 text-primary" aria-hidden="true" /><span className="font-medium">Инструкция:</span><Link href="/faq/freedom-broker" className="text-primary underline-offset-4 hover:underline">как скачать отчёты Freedom Broker</Link></div>}
            </div>
          })}

          <Alert className="border-secondary-foreground/20 bg-secondary/45 text-secondary-foreground"><LockKeyhole /><AlertDescription><span className="font-semibold">Исходные отчёты удаляются сразу после успешного расчёта.</span> Незавершённое задание хранится временно до {Math.round(config.pending_job_ttl_seconds / 60)} минут.</AlertDescription></Alert>
        </CardContent>
      </Card>

      <aside><Card className="sticky top-6 border-border bg-card shadow-md shadow-primary/5">
        <CardHeader><div className="flex items-center gap-3"><span className="flex size-7 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">2</span><CardTitle>Параметры</CardTitle></div></CardHeader>
        <CardContent><FieldGroup><Field><FieldLabel htmlFor="tax-year">Налоговый год</FieldLabel><div id="tax-year" className="flex h-9 w-full items-center rounded-md border border-input bg-muted px-3 text-sm font-medium" aria-readonly="true">{taxYear}</div><p className="text-xs text-muted-foreground">Сейчас расчёт поддерживает только налоговый период 2025 года.</p></Field></FieldGroup><div className="mt-5 rounded-md bg-muted p-3 text-xs text-muted-foreground">До {config.max_files} файлов в одной загрузке, {config.max_job_files} файлов на расчёт, до {config.max_upload_mb} МБ на файл.</div></CardContent>
        <CardContent className="border-t pt-5"><Button className="w-full shadow-sm shadow-primary/20" size="lg" disabled={!canSubmit} onClick={onContinue}>{busy ? 'Загружаем…' : 'Продолжить'}</Button>{!canSubmit && !busy && <p className="mt-3 flex items-start gap-2 text-xs text-muted-foreground"><Info aria-hidden="true" />Добавьте поддерживаемые файлы и заполните номера ручных счетов.</p>}{hasJob && <Button className="mt-2 w-full" variant="ghost" onClick={onAbandon} disabled={busy}>Отменить этот расчёт</Button>}</CardContent>
      </Card></aside>

      {(error || invalidReports.length > 0) && <Alert variant="destructive" className="lg:col-span-2"><Info /><AlertDescription>{error && <p className="font-medium">{error}</p>}{invalidReports.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-5">{invalidReports.map((report, index) => <li key={`${report.broker}:${report.report_name}:${index}`}>{report.broker}{report.account_id ? ` · ${report.account_id}` : ''} · {report.report_name} · окончание периода: {report.period_end || 'не определено'}</li>)}</ul>}</AlertDescription></Alert>}
    </section>
  )
}

function BrokerReportCard({ broker, reports, onFiles, onRemove }: { broker: BrokerConfig; reports: SelectedReport[]; onFiles: (files: File[]) => void; onRemove: (reportId: string) => void }) {
  const guide = broker.code === 'ib'
    ? { href: '/faq/interactive-brokers', label: 'как скачать отчёты Interactive Brokers' }
    : broker.code === 'exante'
      ? { href: '/faq/exante', label: 'как скачать отчёты Exante' }
      : broker.code === 'freedom_bank'
        ? { href: '/faq/freedom-bank', label: 'как скачать отчёты Freedom Bank' }
        : broker.code === 'tabys'
          ? { href: '/faq/tabys', label: 'как скачать отчёты Tabys' }
          : null

  return <div className="rounded-lg border bg-card p-4"><div><h2 className="font-semibold">{broker.display_name}</h2><p className="text-sm text-muted-foreground">{reports.length ? `${reports.length} ${pluralFiles(reports.length)}` : 'Файлы не добавлены'}</p></div><FilePicker className="mt-3 w-full" broker={broker} onFiles={onFiles} /><ReportList reports={reports} onRemove={onRemove} />{guide && <div className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-primary/10 pt-3 text-sm"><BookOpen className="size-4 text-primary" aria-hidden="true" /><span className="font-medium">Инструкция:</span><Link href={guide.href} className="text-primary underline-offset-4 hover:underline">{guide.label}</Link></div>}</div>
}

function FilePicker({ broker, onFiles, className }: { broker: BrokerConfig; onFiles: (files: File[]) => void; className?: string }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragActive, setDragActive] = useState(false)
  const accept = broker.upload_extensions.map((extension) => extension.startsWith('.') ? extension : `.${extension}`).join(',')

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragActive(false)
    const files = Array.from(event.dataTransfer.files)
    if (files.length > 0) onFiles(files)
  }

  return <div
    className={cn(
      'flex flex-wrap items-center justify-center gap-3 rounded-md border border-dashed p-3 transition-colors',
      dragActive ? 'border-primary bg-primary/10' : 'border-input bg-muted/20',
      className,
    )}
    onDragEnter={(event) => { event.preventDefault(); setDragActive(true) }}
    onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy' }}
    onDragLeave={(event) => { event.preventDefault(); setDragActive(false) }}
    onDrop={handleDrop}
  >
    <input ref={inputRef} className="sr-only" type="file" multiple accept={accept} onChange={(event) => { onFiles(Array.from(event.target.files || [])); event.target.value = '' }} aria-label={`Выбрать отчёты ${broker.display_name}`} />
    <UploadCloud className="size-5 text-primary" aria-hidden="true" />
    <span className="text-center text-sm text-muted-foreground">Перетащите файлы сюда</span>
    <Button type="button" variant="outline" size="sm" onClick={() => inputRef.current?.click()}>Добавить файлы</Button>
  </div>
}

function ReportList({ reports, onRemove }: { reports: SelectedReport[]; onRemove: (reportId: string) => void }) {
  if (reports.length === 0) return null
  return <div className="mt-3 flex flex-col gap-2" aria-live="polite">{reports.map((report) => <div key={report.id} className="flex items-center gap-3 rounded-md border bg-background p-3"><FileSpreadsheet className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" /><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{report.file.name}</p><p className="text-xs text-muted-foreground">{formatBytes(report.file.size)}</p></div><span className={cn('flex items-center gap-1 text-xs font-medium', report.status === 'invalid' ? 'text-destructive' : 'text-primary')}>{report.uploaded ? <><CheckCircle2 aria-hidden="true" />Загружен</> : report.status === 'valid' ? 'Готов' : report.error}</span><Button variant="ghost" size="icon-sm" disabled={report.uploaded} onClick={() => onRemove(report.id)} aria-label={`Удалить ${report.file.name}`}><Trash2 /></Button></div>)}</div>
}

function formatBytes(bytes: number) {
  if (!bytes) return '0 КБ'
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} МБ` : `${Math.ceil(bytes / 1024)} КБ`
}

function pluralFiles(count: number) {
  if (count % 10 === 1 && count % 100 !== 11) return 'файл'
  if ([2, 3, 4].includes(count % 10) && ![12, 13, 14].includes(count % 100)) return 'файла'
  return 'файлов'
}
