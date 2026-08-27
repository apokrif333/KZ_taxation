'use client'

import { Calculator, FilePlus2, TriangleAlert } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import type { MissingTransferBasis } from '@/lib/types'

interface MissingTransfersProps {
  items: MissingTransferBasis[]
  busy: boolean
  error: string | null
  onAddReports: () => void
  onApproximate: () => void
}

export function MissingTransfers({ items, busy, error, onAddReports, onApproximate }: MissingTransfersProps) {
  return <section className="mx-auto flex max-w-5xl flex-col gap-6 py-10" aria-labelledby="missing-title">
    <header className="flex items-start gap-4"><span className="flex size-11 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary"><TriangleAlert aria-hidden="true" /></span><div><p className="text-sm font-semibold text-primary">ТРЕБУЮТСЯ ДОПОЛНИТЕЛЬНЫЕ ДАННЫЕ</p><h1 id="missing-title" className="mt-1 text-2xl font-semibold tracking-tight">Не хватает данных о стоимости приобретения</h1><p className="mt-2 text-muted-foreground">Для некоторых переведённых ценных бумаг не удалось восстановить первоначальную FIFO-стоимость.</p></div></header>
    {error && <Alert variant="destructive"><TriangleAlert /><AlertDescription>{error}</AlertDescription></Alert>}
    <Card><CardHeader><CardTitle>Неразрешённые Transfer IN</CardTitle><CardDescription>Добавьте отчёты исходного счёта либо явно разрешите расчёт по доступным данным.</CardDescription></CardHeader><CardContent className="overflow-x-auto px-0"><Table><TableHeader><TableRow><TableHead className="pl-6">Дата</TableHead><TableHead>Тикер</TableHead><TableHead>ISIN</TableHead><TableHead className="text-right">Количество</TableHead><TableHead className="pr-6">Счёт</TableHead></TableRow></TableHeader><TableBody>{items.map((item, index) => <TableRow key={`${item.transfer_date}:${item.isin}:${item.destination_broker}:${item.destination_account}:${index}`}><TableCell className="pl-6 whitespace-nowrap">{item.transfer_date || '—'}</TableCell><TableCell className="font-medium">{item.symbol || '—'}</TableCell><TableCell className="font-mono text-xs">{item.isin || '—'}</TableCell><TableCell className="text-right tabular-nums">{item.quantity}</TableCell><TableCell className="pr-6 whitespace-nowrap">{item.destination_broker}:{item.destination_account}</TableCell></TableRow>)}</TableBody></Table></CardContent></Card>
    <Alert className="border-primary/25 bg-accent/35"><TriangleAlert /><AlertDescription>Для указанных переведённых бумаг будет использована доступная брокерская оценка. FIFO-стоимость приобретения может быть приблизительной.</AlertDescription></Alert>
    <div className="flex flex-col gap-3 sm:flex-row sm:justify-between"><Button variant="outline" onClick={onAddReports} disabled={busy}><FilePlus2 data-icon="inline-start" />Добавить брокерские отчёты</Button><Button onClick={onApproximate} disabled={busy}><Calculator data-icon="inline-start" />{busy ? 'Рассчитываем…' : 'Рассчитать по имеющимся данным'}</Button></div>
  </section>
}
