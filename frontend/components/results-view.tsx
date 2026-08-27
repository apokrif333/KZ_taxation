'use client'

import { Archive, CheckCircle2, Download, FileJson, FileSpreadsheet, RotateCcw } from 'lucide-react'
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
    <header className="flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-center sm:justify-between"><div className="flex items-start gap-4"><span className="flex size-11 items-center justify-center rounded-full bg-primary/10 text-primary"><CheckCircle2 aria-hidden="true" /></span><div><p className="text-sm font-medium text-primary">РАСЧЁТ ЗАВЕРШЁН</p><h1 id="result-title" className="text-2xl font-semibold tracking-tight">Файлы готовы</h1><p className="mt-1 text-sm text-muted-foreground">Налоговый год: {result.tax_year}. Скачайте audit-файлы и черновик формы 270.00.</p></div></div><Button variant="outline" onClick={onRestart}><RotateCcw data-icon="inline-start" />Начать новый расчёт</Button></header>

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
