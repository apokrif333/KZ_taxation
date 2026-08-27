'use client'

import { LoaderCircle } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'

const PROCESSING_STAGES = [
  'Чтение брокерских отчётов',
  'Глобальное сопоставление Transfer и FIFO',
  'Формирование audit-файлов',
  'Объединение счетов и подготовка формы 270.00',
] as const

export function ProcessingProgress() {
  return (
    <section className="mx-auto max-w-2xl py-12" aria-labelledby="processing-title" aria-live="polite">
      <Card className="shadow-sm">
        <CardHeader className="text-center"><div className="mx-auto flex size-12 items-center justify-center rounded-full bg-primary/10 text-primary"><LoaderCircle className="animate-spin" aria-hidden="true" /></div><CardTitle id="processing-title" className="text-xl">Обрабатываем отчёты</CardTitle><CardDescription>Расчёт выполняется реальным сервером и может занять несколько минут.<br />Не закрывайте эту страницу до завершения обработки.</CardDescription></CardHeader>
        <CardContent className="flex flex-col gap-6"><Progress value={35} aria-label="Расчёт выполняется" className="animate-pulse" /><ol className="grid gap-3">{PROCESSING_STAGES.map((stage, index) => <li key={stage} className="flex items-center gap-3 text-sm text-muted-foreground"><span className="flex size-7 shrink-0 items-center justify-center rounded-full border border-primary/30 text-xs font-semibold text-primary">{index + 1}</span>{stage}</li>)}</ol></CardContent>
      </Card>
    </section>
  )
}
