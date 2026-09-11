'use client'

import Link from 'next/link'
import { useRef, useState, type DragEvent } from 'react'
import { BookOpen, CheckCircle2, ChevronDown, FileSpreadsheet, Info, LockKeyhole, Plus, Trash2, UploadCloud } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { cn } from '@/lib/utils'
import type { AlatayReportKind, ApiConfig, BrokerConfig, InvalidReportPeriod, ManualAccountGroup, SelectedReport } from '@/lib/types'

interface UploadWorkflowProps {
  config: ApiConfig
  taxYear: string
  autoFiles: Record<string, SelectedReport[]>
  manualGroups: ManualAccountGroup[]
  form27005: boolean
  hasJob: boolean
  busy: boolean
  error: string | null
  invalidReports: InvalidReportPeriod[]
  onAddAutoFiles: (broker: BrokerConfig, files: File[], alatayReportKind?: AlatayReportKind) => void
  onRemoveAutoFile: (brokerCode: string, reportId: string) => void
  onAddManualGroup: (brokerCode: string) => void
  onRemoveManualGroup: (groupId: string) => void
  onManualAccountChange: (groupId: string, value: string) => void
  onAddManualFiles: (groupId: string, broker: BrokerConfig, files: File[]) => void
  onRemoveManualFile: (groupId: string, reportId: string) => void
  onForm27005Change: (value: boolean) => void
  onContinue: () => void
  onAbandon: () => void
}

export function UploadWorkflow({
  config, taxYear, autoFiles, manualGroups, form27005, hasJob, busy, error, invalidReports,
  onAddAutoFiles, onRemoveAutoFile, onAddManualGroup, onRemoveManualGroup,
  onManualAccountChange, onAddManualFiles, onRemoveManualFile, onForm27005Change, onContinue, onAbandon,
}: UploadWorkflowProps) {
  const brokers = [...config.brokers].sort(compareBrokers)
  const carefullyVerifyBrokerCodes = ['tabys', 'halyk', 'paidax']
  const primaryBrokers = brokers.filter((broker) => !carefullyVerifyBrokerCodes.includes(broker.code))
  const carefullyVerifyBrokers = carefullyVerifyBrokerCodes
    .map((code) => brokers.find((broker) => broker.code === code))
    .filter((broker): broker is BrokerConfig => Boolean(broker))
  const allReports = [...Object.values(autoFiles).flat(), ...manualGroups.flatMap((group) => group.files)]
  const hasAcceptedOrValidReport = allReports.some((report) => report.uploaded || report.status === 'valid')
  const hasInvalidReport = allReports.some((report) => report.status === 'invalid')
  const alatayReports = autoFiles.alatay || []
  const alatayCashReports = alatayReports.filter((report) => report.alatayReportKind === 'cash')
  const alataySecuritiesReports = alatayReports.filter((report) => report.alatayReportKind === 'securities')
  const hasAlatayReports = alatayReports.length > 0
  const alatayReportsPaired = !hasAlatayReports || (
    alatayCashReports.length > 0 && alatayCashReports.length === alataySecuritiesReports.length
  )
  const missingManualAccount = manualGroups.some(
    (group) => group.files.some((report) => report.uploaded || report.status === 'valid') && !group.accountId.trim(),
  )
  const canSubmit = hasAcceptedOrValidReport && !hasInvalidReport && !missingManualAccount && alatayReportsPaired && !busy

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

          <Alert className="border-amber-300/80 bg-amber-50 text-amber-950 dark:border-amber-500/40 dark:bg-amber-950/35 dark:text-amber-100">
            <Info />
            <AlertDescription>
              <p className="font-semibold">Для корректного расчёта по методу FIFO загрузите отчёты за все годы существования счёта.</p>
              <p className="mt-1">Продажа в 2025 году может относиться к активу, купленному несколькими годами ранее. Если ранних отчётов нет, себестоимость и налог могут быть рассчитаны некорректно.</p>
            </AlertDescription>
          </Alert>

          {primaryBrokers.map((broker) => broker.code === 'alatay'
            ? <AlatayReportCard key={broker.code} broker={broker} cashReports={alatayCashReports} securitiesReports={alataySecuritiesReports} busy={busy} onFiles={(kind, files) => onAddAutoFiles(broker, files, kind)} onRemove={(reportId) => onRemoveAutoFile(broker.code, reportId)} />
            : broker.account_id_mode === 'auto'
              ? <BrokerReportCard key={broker.code} broker={broker} reports={autoFiles[broker.code] || []} busy={busy} onFiles={(files) => onAddAutoFiles(broker, files)} onRemove={(reportId) => onRemoveAutoFile(broker.code, reportId)} />
            : <ManualBrokerReportCard key={broker.code} broker={broker} groups={manualGroups.filter((group) => group.broker === broker.code)} busy={busy} onAddGroup={() => onAddManualGroup(broker.code)} onRemoveGroup={onRemoveManualGroup} onAccountChange={onManualAccountChange} onFiles={onAddManualFiles} onRemoveFile={onRemoveManualFile} />,
          )}

          {carefullyVerifyBrokers.length > 0 && <Alert className="border-amber-300/80 bg-amber-50 text-amber-950 dark:border-amber-500/40 dark:bg-amber-950/35 dark:text-amber-100"><Info /><AlertDescription>Для брокеров указанных ниже требуется внимательная перепроверка после формирования отчёта, так как код для них не тестировался на десятках тысяч сделок, как это было сделано для брокеров выше.</AlertDescription></Alert>}

          {carefullyVerifyBrokers.map((broker) => broker.account_id_mode === 'auto'
            ? <BrokerReportCard key={broker.code} broker={broker} reports={autoFiles[broker.code] || []} busy={busy} onFiles={(files) => onAddAutoFiles(broker, files)} onRemove={(reportId) => onRemoveAutoFile(broker.code, reportId)} />
            : <ManualBrokerReportCard key={broker.code} broker={broker} groups={manualGroups.filter((group) => group.broker === broker.code)} busy={busy} onAddGroup={() => onAddManualGroup(broker.code)} onRemoveGroup={onRemoveManualGroup} onAccountChange={onManualAccountChange} onFiles={onAddManualFiles} onRemoveFile={onRemoveManualFile} />,
          )}

          <Alert className="border-secondary-foreground/20 bg-secondary/45 text-secondary-foreground"><LockKeyhole /><AlertDescription><span className="font-semibold">Исходные отчёты удаляются сразу после успешного расчёта.</span> Незавершённое задание хранится временно до {Math.round(config.pending_job_ttl_seconds / 60)} минут.</AlertDescription></Alert>
        </CardContent>
      </Card>

      <aside><Card className="sticky top-6 border-border bg-card shadow-md shadow-primary/5">
        <CardHeader><div className="flex items-center gap-3"><span className="flex size-7 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">2</span><CardTitle>Параметры</CardTitle></div></CardHeader>
        <CardContent><FieldGroup><Field><FieldLabel htmlFor="tax-year">Налоговый год</FieldLabel><div id="tax-year" className="flex h-9 w-full items-center rounded-md border border-input bg-muted px-3 text-sm font-medium" aria-readonly="true">{taxYear}</div><p className="text-xs text-muted-foreground">Сейчас расчёт поддерживает только налоговый период 2025 года.</p></Field></FieldGroup><div className="mt-5 rounded-md bg-muted p-3 text-xs text-muted-foreground">До {config.max_files} файлов в одной загрузке, {config.max_job_files} файлов на расчёт, до {config.max_upload_mb} МБ на файл.</div><label className="mt-4 flex cursor-pointer items-start gap-2.5 border-t pt-4 text-xs leading-snug text-muted-foreground"><Checkbox className="mt-0.5" checked={form27005} onCheckedChange={(value) => onForm27005Change(Boolean(value))} aria-label="Я или супруг(а) владелец бизнеса или попадаю под закон о противодействии коррупции, или совокупная сумма приобретений в прошлом году превысила 20 000 МРП" /><span>Я или супруг(а) владелец бизнеса или попадаю под закон о противодействии коррупции, или совокупная сумма приобретений в прошлом году превысила 20 000 МРП</span></label></CardContent>
        <CardContent className="border-t pt-5"><Button className="w-full shadow-sm shadow-primary/20" size="lg" disabled={!canSubmit} onClick={onContinue}>{busy ? 'Загружаем…' : 'Продолжить'}</Button>{!canSubmit && !busy && <p className="mt-3 flex items-start gap-2 text-xs text-muted-foreground"><Info aria-hidden="true" />Добавьте поддерживаемые файлы и заполните номера ручных счетов.</p>}{hasJob && <Button className="mt-2 w-full" variant="ghost" onClick={onAbandon} disabled={busy}>Отменить этот расчёт</Button>}</CardContent>
      </Card></aside>

      {(error || invalidReports.length > 0) && <Alert variant="destructive" className="lg:col-span-2"><Info /><AlertDescription>{error && <p className="font-medium">{error}</p>}{invalidReports.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-5">{invalidReports.map((report, index) => <li key={`${report.broker}:${report.report_name}:${index}`}>{report.broker}{report.account_id ? ` · ${report.account_id}` : ''} · {report.report_name} · окончание периода: {report.period_end || 'не определено'}</li>)}</ul>}</AlertDescription></Alert>}
    </section>
  )
}

function AlatayReportCard({
  broker,
  cashReports,
  securitiesReports,
  busy,
  onFiles,
  onRemove,
}: {
  broker: BrokerConfig
  cashReports: SelectedReport[]
  securitiesReports: SelectedReport[]
  busy: boolean
  onFiles: (kind: AlatayReportKind, files: File[]) => void
  onRemove: (reportId: string) => void
}) {
  const reportsPaired = cashReports.length > 0 && cashReports.length === securitiesReports.length

  return <div className="rounded-lg border bg-card p-4">
    <BrokerTitle broker={broker} />
    <p className="mt-1 text-sm text-muted-foreground">Номера счетов определяются автоматически из отчётов.</p>
    <Alert className="mt-4 border-primary/20 bg-primary/5"><Info aria-hidden="true" /><AlertDescription>Для расчёта загрузите одинаковое количество отчётов ОДДС и ОДЦБ.</AlertDescription></Alert>
    <div className="mt-4 grid gap-4 xl:grid-cols-2">
      <AlatayUploadSection title="ОДДС" description="Отчёт движения денежных средств" broker={broker} reports={cashReports} busy={busy} onFiles={(files) => onFiles('cash', files)} onRemove={onRemove} />
      <AlatayUploadSection title="ОДЦБ" description="Отчёт движения ценных бумаг" broker={broker} reports={securitiesReports} busy={busy} onFiles={(files) => onFiles('securities', files)} onRemove={onRemove} />
    </div>
    {(cashReports.length > 0 || securitiesReports.length > 0) && <p className={cn('mt-4 text-sm font-medium', reportsPaired ? 'text-primary' : 'text-destructive')}>
      {reportsPaired
        ? `Добавлено пар отчётов: ${cashReports.length}. Можно продолжить расчёт.`
        : `ОДДС: ${cashReports.length}; ОДЦБ: ${securitiesReports.length}. Добавьте недостающие отчёты.`}
    </p>}
  </div>
}

function AlatayUploadSection({ title, description, broker, reports, busy, onFiles, onRemove }: {
  title: string
  description: string
  broker: BrokerConfig
  reports: SelectedReport[]
  busy: boolean
  onFiles: (files: File[]) => void
  onRemove: (reportId: string) => void
}) {
  return <div className="rounded-md border bg-muted/20 p-4">
    <div className="mb-3 flex items-start justify-between gap-3"><div><h3 className="font-semibold">{title}</h3><p className="mt-1 text-sm text-muted-foreground">{description}</p></div><span className="rounded-full bg-background px-2.5 py-1 text-xs font-medium text-muted-foreground ring-1 ring-border">{reports.length} {pluralFiles(reports.length)}</span></div>
    <FilePicker broker={broker} onFiles={onFiles} />
    <ReportList reports={reports} busy={busy} onRemove={onRemove} />
  </div>
}

function BrokerReportCard({ broker, reports, busy, onFiles, onRemove, collapsible = true }: { broker: BrokerConfig; reports: SelectedReport[]; busy: boolean; onFiles: (files: File[]) => void; onRemove: (reportId: string) => void; collapsible?: boolean }) {
  const hasReports = reports.length > 0
  const [isOpen, setIsOpen] = useState(false)
  const expanded = hasReports || isOpen
  const contentId = `broker-upload-${broker.code}`

  if (collapsible) {
    return <div className="rounded-lg border bg-card p-4">
      <button type="button" className="flex w-full items-center justify-between gap-4 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default" onClick={() => setIsOpen((value) => !value)} disabled={hasReports} aria-expanded={expanded} aria-controls={contentId}>
        <div><BrokerTitle broker={broker} /><p className="mt-1 text-sm text-muted-foreground">{hasReports ? `${reports.length} ${pluralFiles(reports.length)}` : 'Файлы не добавлены'}</p></div>
        {hasReports ? <CheckCircle2 className="size-5 shrink-0 text-primary" aria-label="Файлы добавлены" /> : <ChevronDown className={cn('size-5 shrink-0 text-muted-foreground transition-transform', expanded && 'rotate-180')} aria-hidden="true" />}
      </button>
      <BrokerWarning broker={broker} />
      {expanded && <div id={contentId} className="mt-4 border-t border-primary/10 pt-4"><FilePicker broker={broker} onFiles={onFiles} /><ReportList reports={reports} busy={busy} onRemove={onRemove} /><BrokerGuidance broker={broker} /></div>}
    </div>
  }

  const guide = broker.code === 'ib'
    ? { href: '/faq/interactive-brokers', label: 'как скачать отчёты Interactive Brokers' }
    : broker.code === 'exante'
      ? { href: '/faq/exante', label: 'как скачать отчёты Exante' }
      : broker.code === 'freedom_bank'
        ? { href: '/faq/freedom-bank', label: 'как скачать отчёты Freedom Bank' }
        : broker.code === 'tabys'
          ? { href: '/faq/tabys', label: 'как скачать отчёты Tabys' }
          : broker.code === 'halyk'
            ? { href: '/faq/halyk', label: 'как скачать отчёты Halyk Finance' }
            : broker.code === 'paidax'
              ? { href: '/faq/paidax', label: 'как скачать отчёты Paidax' }
          : null

  return <div className="rounded-lg border bg-card p-4"><BrokerTitle broker={broker} /><p className="mt-1 text-sm text-muted-foreground">{reports.length ? `${reports.length} ${pluralFiles(reports.length)}` : 'Файлы не добавлены'}</p><FilePicker className="mt-3 w-full" broker={broker} onFiles={onFiles} /><ReportList reports={reports} busy={busy} onRemove={onRemove} />{guide && <div className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-primary/10 pt-3 text-sm"><BookOpen className="size-4 text-primary" aria-hidden="true" /><span className="font-medium">Инструкция:</span><Link href={guide.href} className="text-primary underline-offset-4 hover:underline">{guide.label}</Link></div>}</div>
}

function ManualBrokerReportCard({ broker, groups, busy, onAddGroup, onRemoveGroup, onAccountChange, onFiles, onRemoveFile, collapsible = true }: { broker: BrokerConfig; groups: ManualAccountGroup[]; busy: boolean; onAddGroup: () => void; onRemoveGroup: (groupId: string) => void; onAccountChange: (groupId: string, value: string) => void; onFiles: (groupId: string, broker: BrokerConfig, files: File[]) => void; onRemoveFile: (groupId: string, reportId: string) => void; collapsible?: boolean }) {
  const reportCount = groups.reduce((count, group) => count + group.files.length, 0)
  const hasReports = reportCount > 0
  const [isOpen, setIsOpen] = useState(false)
  const expanded = hasReports || isOpen
  const contentId = `broker-upload-${broker.code}`

  if (collapsible) {
    return <div className="rounded-lg border bg-muted/20 p-4">
      <button type="button" className="flex w-full items-center justify-between gap-4 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default" onClick={() => setIsOpen((value) => !value)} disabled={hasReports} aria-expanded={expanded} aria-controls={contentId}>
        <div><BrokerTitle broker={broker} /><p className="mt-1 text-sm text-muted-foreground">{hasReports ? `${reportCount} ${pluralFiles(reportCount)}` : 'Файлы не добавлены'}</p></div>
        {hasReports ? <CheckCircle2 className="size-5 shrink-0 text-primary" aria-label="Файлы добавлены" /> : <ChevronDown className={cn('size-5 shrink-0 text-muted-foreground transition-transform', expanded && 'rotate-180')} aria-hidden="true" />}
      </button>
      {expanded && <div id={contentId} className="mt-4 border-t border-primary/10 pt-4"><ManualBrokerContent broker={broker} groups={groups} busy={busy} onAddGroup={onAddGroup} onRemoveGroup={onRemoveGroup} onAccountChange={onAccountChange} onFiles={onFiles} onRemoveFile={onRemoveFile} /></div>}
    </div>
  }

  return <div className="rounded-lg border bg-muted/20 p-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><BrokerTitle broker={broker} /><p className="mt-1 text-sm text-muted-foreground">Для каждого счёта укажите номер и добавьте его отчёты отдельно.</p></div><Button variant="outline" onClick={onAddGroup}><Plus data-icon="inline-start" />Добавить счёт</Button></div><div className="mt-4 flex flex-col gap-4">{groups.length === 0 && <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">Счета не добавлены.</p>}{groups.map((group, index) => { const locked = group.files.some((report) => report.uploaded); return <div key={group.id} className="rounded-md border bg-card p-4"><div className="flex flex-wrap items-end gap-3"><label className="min-w-52 flex-1 text-sm font-medium">Номер счёта {groups.length > 1 ? index + 1 : ''}<input className="mt-2 h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60" value={group.accountId} disabled={locked} onChange={(event) => onAccountChange(group.id, event.target.value)} placeholder="Например, 759023" /></label><FilePicker className="min-w-64 flex-1" broker={broker} onFiles={(files) => onFiles(group.id, broker, files)} /><Button variant="ghost" size="icon" disabled={locked} onClick={() => onRemoveGroup(group.id)} aria-label="Удалить счёт" title={locked ? 'Принятый backend счёт можно удалить только вместе со всем расчётом' : 'Удалить счёт'}><Trash2 /></Button></div>{!group.accountId.trim() && group.files.length > 0 && <p className="mt-2 text-xs text-destructive">Укажите номер счёта Freedom.</p>}<ReportList reports={group.files} busy={busy} onRemove={(reportId) => onRemoveFile(group.id, reportId)} /></div>})}</div>{broker.code === 'freedom' && <div className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-primary/10 pt-3 text-sm"><BookOpen className="size-4 text-primary" aria-hidden="true" /><span className="font-medium">Инструкция:</span><Link href="/faq/freedom-broker" className="text-primary underline-offset-4 hover:underline">как скачать отчёты Freedom Broker</Link></div>}</div>
}

function BrokerGuidance({ broker }: { broker: BrokerConfig }) {
  const guide = broker.code === 'ib'
    ? { href: '/faq/interactive-brokers', label: 'как скачать отчёты Interactive Brokers' }
    : broker.code === 'exante'
      ? { href: '/faq/exante', label: 'как скачать отчёты Exante' }
      : broker.code === 'freedom_bank'
        ? { href: '/faq/freedom-bank', label: 'как скачать отчёты Freedom Bank' }
        : broker.code === 'freedom'
          ? { href: '/faq/freedom-broker', label: 'как скачать отчёты Freedom Broker' }
          : broker.code === 'tabys'
            ? { href: '/faq/tabys', label: 'как скачать отчёты Tabys' }
            : broker.code === 'halyk'
              ? { href: '/faq/halyk', label: 'как скачать отчёты Halyk Finance' }
              : broker.code === 'paidax'
                ? { href: '/faq/paidax', label: 'как скачать отчёты Paidax' }
                : null

  if (!guide) return null
  return <div className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-primary/10 pt-3 text-sm"><BookOpen className="size-4 text-primary" aria-hidden="true" /><span className="font-medium">Инструкция:</span><Link href={guide.href} className="text-primary underline-offset-4 hover:underline">{guide.label}</Link></div>
}

function BrokerWarning({ broker }: { broker: BrokerConfig }) {
  if (broker.code !== 'paidax') return null
  return <Alert className="mt-3 border-amber-300/80 bg-amber-50 text-amber-950 dark:border-amber-500/40 dark:bg-amber-950/35 dark:text-amber-100"><Info className="mt-0.5" aria-hidden="true" /><AlertDescription><span className="font-semibold">Внимание!</span> Сделки с криптовалютой не обрабатываются и не учитываются!</AlertDescription></Alert>
}

function ManualBrokerContent({ broker, groups, busy, onAddGroup, onRemoveGroup, onAccountChange, onFiles, onRemoveFile }: { broker: BrokerConfig; groups: ManualAccountGroup[]; busy: boolean; onAddGroup: () => void; onRemoveGroup: (groupId: string) => void; onAccountChange: (groupId: string, value: string) => void; onFiles: (groupId: string, broker: BrokerConfig, files: File[]) => void; onRemoveFile: (groupId: string, reportId: string) => void }) {
  return <><div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">Для каждого счёта укажите номер и добавьте его отчёты отдельно.</p><Button variant="outline" onClick={onAddGroup}><Plus data-icon="inline-start" />Добавить счёт</Button></div><div className="mt-4 flex flex-col gap-4">{groups.length === 0 && <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">Счета не добавлены.</p>}{groups.map((group, index) => { const locked = group.files.some((report) => report.uploaded); return <div key={group.id} className="rounded-md border bg-card p-4"><div className="flex flex-wrap items-end gap-3"><label className="min-w-52 flex-1 text-sm font-medium">Номер счёта {groups.length > 1 ? index + 1 : ''}<input className="mt-2 h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60" value={group.accountId} disabled={locked} onChange={(event) => onAccountChange(group.id, event.target.value)} placeholder="Например, 759023" /></label><FilePicker className="min-w-64 flex-1" broker={broker} onFiles={(files) => onFiles(group.id, broker, files)} /><Button variant="ghost" size="icon" disabled={locked} onClick={() => onRemoveGroup(group.id)} aria-label="Удалить счёт" title={locked ? 'Принятый backend счёт можно удалить только вместе со всем расчётом' : 'Удалить счёт'}><Trash2 /></Button></div>{!group.accountId.trim() && group.files.length > 0 && <p className="mt-2 text-xs text-destructive">Укажите номер счёта Freedom.</p>}<ReportList reports={group.files} busy={busy} onRemove={(reportId) => onRemoveFile(group.id, reportId)} /></div>})}</div><BrokerGuidance broker={broker} /></>
}

const brokerLogos: Record<string, { src: string; alt: string }> = {
  alatay: { src: '/broker-logos/alatay.png', alt: 'Alatau City Invest' },
  exante: { src: '/broker-logos/exante.png', alt: 'Exante' },
  freedom: { src: '/broker-logos/freedom-broker.png', alt: 'Freedom Broker' },
  freedom_bank: { src: '/broker-logos/freedom-bank.png', alt: 'Freedom Bank' },
  halyk: { src: '/broker-logos/halyk-finance.png', alt: 'Halyk Finance' },
  ib: { src: '/broker-logos/interactive-brokers.png', alt: 'Interactive Brokers' },
  paidax: { src: '/broker-logos/paidax.png', alt: 'Paidax' },
  tabys: { src: '/broker-logos/tabys.png', alt: 'Tabys' },
  tsifra: { src: '/broker-logos/tsifra-broker.png', alt: 'Цифра Брокер' },
}

function BrokerTitle({ broker }: { broker: BrokerConfig }) {
  const logo = brokerLogos[broker.code]
  return <div className="flex items-center gap-2"><span className="flex size-8 shrink-0 items-center justify-center overflow-hidden rounded-md border border-border/80 bg-background">{logo && <img src={logo.src} alt={logo.alt} className="size-full object-contain" />}</span><h2 className="font-semibold">{broker.display_name}</h2></div>
}

function compareBrokers(left: BrokerConfig, right: BrokerConfig) {
  const leftIsLatin = /^[A-Za-z]/.test(left.display_name.trim())
  const rightIsLatin = /^[A-Za-z]/.test(right.display_name.trim())
  if (leftIsLatin !== rightIsLatin) return leftIsLatin ? -1 : 1
  return left.display_name.localeCompare(right.display_name, leftIsLatin ? 'en' : 'ru')
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
    <p className="w-full text-center text-xs leading-relaxed text-muted-foreground">Загружайте отчёты за весь период существования этого счёта, а не только за 2025 год.</p>
  </div>
}

function ReportList({ reports, busy, onRemove }: { reports: SelectedReport[]; busy: boolean; onRemove: (reportId: string) => void }) {
  if (reports.length === 0) return null
  return <div className="mt-3 flex flex-col gap-2" aria-live="polite">{reports.map((report) => <div key={report.id} className="flex items-center gap-3 rounded-md border bg-background p-3"><FileSpreadsheet className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" /><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{report.file.name}</p><p className="text-xs text-muted-foreground">{formatBytes(report.file.size)}</p></div><span className={cn('flex items-center gap-1 text-xs font-medium', report.status === 'invalid' ? 'text-destructive' : 'text-primary')}>{report.uploaded ? <><CheckCircle2 aria-hidden="true" />Загружен</> : report.status === 'valid' ? 'Готов' : report.error}</span><Button variant="ghost" size="icon-sm" disabled={busy} onClick={() => onRemove(report.id)} aria-label={`Удалить ${report.file.name}`}><Trash2 /></Button></div>)}</div>
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
