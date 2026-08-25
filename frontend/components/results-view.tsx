'use client'

import { AlertCircle, AlertTriangle, Check, CheckCircle2, Download, FileJson, FileSpreadsheet, Info, RotateCcw } from 'lucide-react'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import type { ProcessingResult, ReconciliationStatus } from '@/lib/types'

interface ResultsViewProps { result: ProcessingResult; onRestart: () => void }

const statusConfig: Record<ReconciliationStatus, { label: string; icon: typeof Check }> = {
  match: { label: 'Совпадает', icon: CheckCircle2 },
  warning: { label: 'Предупреждение', icon: AlertTriangle },
  error: { label: 'Ошибка', icon: AlertCircle },
}

export function ResultsView({ result, onRestart }: ResultsViewProps) {
  const metrics = [
    ['Налоговый год', result.taxYear], ['Количество операций', String(result.operations)], ['Количество инструментов', String(result.instruments)], ['Предупреждения', String(result.warningCount)], ['Ошибки сверки', String(result.reconciliationErrors)],
  ]
  return <section className="flex flex-col gap-8 py-8" aria-labelledby="result-title">
    <header className="flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-center sm:justify-between"><div className="flex items-start gap-4"><span className="flex size-11 items-center justify-center rounded-full bg-primary/10 text-primary"><CheckCircle2 aria-hidden="true" /></span><div><p className="text-sm font-medium text-primary">РАСЧЁТ ЗАВЕРШЁН</p><h1 id="result-title" className="text-2xl font-semibold tracking-tight">Обработка завершена</h1><p className="mt-1 text-sm text-muted-foreground">Проверьте сверку и предупреждения перед использованием результатов.</p></div></div><Button variant="outline" onClick={onRestart}><RotateCcw data-icon="inline-start" />Начать новый расчёт</Button></header>

    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border bg-border md:grid-cols-5">{metrics.map(([label, value]) => <div key={label} className="bg-card p-4"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-2 text-xl font-semibold tabular-nums">{value}</p></div>)}</div>

    <Card className="shadow-sm"><CardHeader><CardTitle>Сверка с отчётом брокера</CardTitle><CardDescription>Сопоставление показателей брокера с результатом обработки. Демонстрационные данные.</CardDescription></CardHeader><CardContent className="overflow-x-auto px-0"><Table><TableHeader><TableRow><TableHead className="pl-6">Показатель</TableHead><TableHead className="text-right">По отчёту брокера</TableHead><TableHead className="text-right">Рассчитано</TableHead><TableHead className="text-right">Расхождение</TableHead><TableHead className="pr-6">Статус</TableHead></TableRow></TableHeader><TableBody>{result.reconciliation.map((row) => { const StatusIcon = statusConfig[row.status].icon; return <TableRow key={row.label}><TableCell className="pl-6 font-medium">{row.label}</TableCell><TableCell className="text-right tabular-nums">{row.broker}</TableCell><TableCell className="text-right tabular-nums">{row.calculated}</TableCell><TableCell className="text-right tabular-nums">{row.difference}</TableCell><TableCell className="pr-6"><Badge variant={row.status === 'error' ? 'destructive' : row.status === 'warning' ? 'outline' : 'secondary'}><StatusIcon />{statusConfig[row.status].label}</Badge></TableCell></TableRow> })}</TableBody></Table></CardContent></Card>

    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <Card className="shadow-sm"><CardHeader><CardTitle>Налоговая сводка</CardTitle><CardDescription>Предварительные демонстрационные данные. Не являются налоговой консультацией.</CardDescription></CardHeader><CardContent className="overflow-x-auto px-0"><Table><TableHeader><TableRow><TableHead className="pl-6">Категория дохода</TableHead><TableHead className="text-right">Сумма</TableHead><TableHead className="text-right">Налогооблагаемая сумма</TableHead><TableHead className="text-right">Удержанный налог</TableHead><TableHead className="pr-6">Примечание</TableHead></TableRow></TableHeader><TableBody>{result.taxSummary.map((row) => <TableRow key={row.category}><TableCell className="pl-6 font-medium">{row.category}</TableCell><TableCell className="text-right tabular-nums">{row.amount}</TableCell><TableCell className="text-right tabular-nums">{row.taxable}</TableCell><TableCell className="text-right tabular-nums">{row.withheld}</TableCell><TableCell className="pr-6 text-muted-foreground">{row.note}</TableCell></TableRow>)}</TableBody></Table></CardContent></Card>
      <Card className="shadow-sm"><CardHeader><CardTitle>Предупреждения</CardTitle><CardDescription>Раскройте сообщение для просмотра деталей.</CardDescription></CardHeader><CardContent><Accordion>{result.warnings.map((warning) => { const Icon = warning.severity === 'error' ? AlertCircle : warning.severity === 'warning' ? AlertTriangle : Info; return <AccordionItem key={warning.id} value={warning.id}><AccordionTrigger><span className="flex items-start gap-2 text-left"><Icon className={warning.severity === 'error' ? 'text-destructive' : 'text-primary'} aria-hidden="true" /><span>{warning.title}</span></span></AccordionTrigger><AccordionContent><p className="leading-relaxed text-muted-foreground">{warning.details}</p></AccordionContent></AccordionItem> })}</Accordion></CardContent></Card>
    </div>

    <div><div className="mb-4"><h2 className="text-xl font-semibold">Скачать результаты</h2><p className="mt-1 text-sm text-muted-foreground">Файлы сформированы на основе демонстрационных данных.</p></div><div className="grid gap-4 md:grid-cols-2"><DownloadCard icon={FileSpreadsheet} title="Скачать audit Excel" description="Детальная расшифровка расчётов, операций, FIFO и сверок." fileName="audit_270_2025.xlsx" /><DownloadCard icon={FileJson} title="Скачать JSON для формы 270.00" description="Черновик данных для формы 270.00." fileName="form_270_2025.json" /></div></div>
  </section>
}

function DownloadCard({ icon: Icon, title, description, fileName }: { icon: typeof FileSpreadsheet; title: string; description: string; fileName: string }) {
  const download = () => { const blob = new Blob(['Демонстрационный файл QCM Tax 270'], { type: 'text/plain;charset=utf-8' }); const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = fileName; anchor.click(); URL.revokeObjectURL(url) }
  return <Card><CardHeader><div className="flex size-10 items-center justify-center rounded-md bg-primary/10 text-primary"><Icon aria-hidden="true" /></div><CardTitle>{title}</CardTitle><CardDescription>{description}</CardDescription></CardHeader><CardFooter><Button onClick={download}><Download data-icon="inline-start" />Скачать</Button></CardFooter></Card>
}
