'use client'

import { Archive, ArrowRight, CheckCircle2, Download, FileJson, FileSearch, FileSpreadsheet, RotateCcw } from 'lucide-react'
import Link from 'next/link'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { artifactUrl, downloadAllUrl } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import type { Artifact, BrokerConfig, ProcessResponse } from '@/lib/types'

interface ResultsViewProps {
  result: ProcessResponse
  brokers: BrokerConfig[]
  onRestart: () => void
}

export function ResultsView({ result, brokers, onRestart }: ResultsViewProps) {
  const brokerNames = new Map(brokers.map((broker) => [broker.code, broker.display_name]))
  const accountArtifacts = result.artifacts.filter((artifact) => artifact.kind === 'account_audit' || artifact.kind === 'joint_audit')
  const grouped = new Map<string, Artifact[]>()
  for (const artifact of accountArtifacts) {
    const key = `${artifact.broker || ''}:${artifact.account_id || ''}`
    grouped.set(key, [...(grouped.get(key) || []), artifact])
  }
  const merged = result.artifacts.filter((artifact) => artifact.kind === 'merged_audit')
  const forms = result.artifacts.filter((artifact) => artifact.kind === 'form270')

  return <section className="flex flex-col gap-8 py-8" aria-labelledby="result-title">
    <header className="flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-start sm:justify-between"><div className="flex items-start gap-4"><span className="flex size-11 items-center justify-center rounded-full bg-primary/10 text-primary"><CheckCircle2 aria-hidden="true" /></span><div><p className="text-sm font-medium text-primary">РАСЧЁТ ЗАВЕРШЁН</p><h1 id="result-title" className="text-2xl font-semibold tracking-tight">Файлы готовы</h1><p className="mt-1 text-sm text-muted-foreground">Налоговый год: {result.tax_year}. Скачайте audit-файлы и черновик формы 270.00.</p><div className="mt-4 grid max-w-4xl gap-3 md:grid-cols-2"><GuideLink href="/faq/audit-file" icon={<FileSearch className="size-4 shrink-0" aria-hidden="true" />} title="Что находится в audit-файле" description="Описание audit XLSX-файлов и их структуры." /><GuideLink href="/faq/form270-upload" icon={<FileJson className="size-4 shrink-0" aria-hidden="true" />} title="Загрузка JSON формы 270.00 и оплата налога" description="Как проверить данные КГД, загрузить JSON, отправить декларацию и оплатить ИПН." /></div></div></div><Button variant="outline" onClick={onRestart}><RotateCcw data-icon="inline-start" />Начать новый расчёт</Button></header>

    <div>
      <h2 className="text-xl font-semibold">Audit по счетам</h2>
      <p className="mt-1 text-sm text-muted-foreground">Отдельный audit доступен и для счетов, исключённых из итогового объединения.</p>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        {Array.from(grouped.values()).map((artifacts) => {
          const first = artifacts[0]
          return <Card key={`${first.broker}:${first.account_id}`}><CardHeader><CardTitle className="text-lg">{brokerNames.get(first.broker || '') || first.broker} · <span className="font-mono">{first.account_id}</span></CardTitle><CardDescription>{artifacts.some((artifact) => artifact.kind === 'joint_audit') ? 'Доступны обычный и совместный audit.' : 'Индивидуальный audit счёта.'}</CardDescription></CardHeader><CardContent className="flex flex-wrap gap-2">{artifacts.map((artifact) => <ArtifactLink key={artifact.id} artifact={artifact} />)}</CardContent></Card>
        })}
      </div>
    </div>

    <div className="grid gap-4 md:grid-cols-2">
      {merged.map((artifact) => <ResultCard key={artifact.id} artifact={artifact} title="Общий audit" description="Итоговый merged workbook для всех счетов, включённых в декларацию." />)}
      {forms.map((artifact) => <ResultCard key={artifact.id} artifact={artifact} title="Форма 270.00" description="JSON-документ для загрузки в налоговый кабинет." />)}
    </div>

    <Card className="border-primary/25 bg-accent/25"><CardHeader><CardTitle>Все результаты одним архивом</CardTitle><CardDescription>ZIP-файл с audit-расчётами по всем счетам, единым merged-файлом и формой 270.00.</CardDescription></CardHeader><CardContent><a className={cn(buttonVariants({ size: 'lg' }))} href={downloadAllUrl(result.job_id)}><Archive data-icon="inline-start" />Скачать все файлы ZIP</a></CardContent></Card>
  </section>
}

function ResultCard({ artifact, title, description }: { artifact: Artifact; title: string; description: string }) {
  const Icon = artifact.kind === 'form270' ? FileJson : FileSpreadsheet
  return <Card><CardHeader><div className="flex size-10 items-center justify-center rounded-md bg-primary/10 text-primary"><Icon aria-hidden="true" /></div><CardTitle>{title}</CardTitle><CardDescription>{description}</CardDescription></CardHeader><CardContent><ArtifactLink artifact={artifact} /></CardContent></Card>
}

function ArtifactLink({ artifact }: { artifact: Artifact }) {
  const label = artifact.kind === 'joint_audit' ? 'Скачать joint audit XLSX' : artifact.kind === 'account_audit' ? 'Скачать audit XLSX' : artifact.kind === 'merged_audit' ? 'Скачать merged XLSX' : 'Скачать JSON'
  return <a className={cn(buttonVariants({ variant: artifact.kind === 'joint_audit' ? 'outline' : 'default' }))} href={artifactUrl(artifact.download_url)} download={artifact.filename}><Download data-icon="inline-start" />{label}</a>
}

function GuideLink({ href, icon, title, description }: { href: string; icon: React.ReactNode; title: string; description: string }) {
  return <Link href={href} className="group flex h-full flex-col rounded-lg border border-primary/15 bg-accent/25 px-4 py-3.5 text-sm transition-colors hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"><span className="flex items-start gap-2 text-primary">{icon}<span className="flex-1 font-semibold leading-snug">{title}</span><ArrowRight className="mt-0.5 size-4 shrink-0 transition-transform group-hover:translate-x-0.5" aria-hidden="true" /></span><span className="mt-2 leading-relaxed text-muted-foreground">{description}</span><span className="mt-3 border-t border-primary/10 pt-2 text-xs leading-relaxed text-primary/80">Нажав на эту ссылку, текущий расчёт будет удалён, и вы не сможете вернуться на эту страницу.</span></Link>
}
