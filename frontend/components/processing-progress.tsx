'use client'

import { Check, LoaderCircle } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { PROCESSING_STAGES } from '@/lib/mock-service'
import { cn } from '@/lib/utils'

interface ProcessingProgressProps { activeStage: number; fileName: string }

export function ProcessingProgress({ activeStage, fileName }: ProcessingProgressProps) {
  return (
    <section className="mx-auto max-w-2xl py-12" aria-labelledby="processing-title" aria-live="polite">
      <Card className="shadow-sm">
        <CardHeader className="text-center"><div className="mx-auto flex size-12 items-center justify-center rounded-full bg-primary/10 text-primary"><LoaderCircle className="animate-spin" aria-hidden="true" /></div><CardTitle id="processing-title" className="text-xl">Обрабатываем отчёт</CardTitle><CardDescription>{fileName}<br />Не закрывайте эту страницу до завершения обработки.</CardDescription></CardHeader>
        <CardContent className="flex flex-col gap-6"><Progress value={((activeStage + 1) / PROCESSING_STAGES.length) * 100} aria-label="Ход обработки" /><ol className="flex flex-col">{PROCESSING_STAGES.map((stage, index) => { const complete = index < activeStage; const active = index === activeStage; return <li key={stage} className="flex gap-4"><div className="flex flex-col items-center"><span className={cn('flex size-7 items-center justify-center rounded-full border text-xs font-semibold', complete && 'border-primary bg-primary text-primary-foreground', active && 'border-primary text-primary', index > activeStage && 'border-input text-muted-foreground')}>{complete ? <Check aria-hidden="true" /> : index + 1}</span>{index < PROCESSING_STAGES.length - 1 && <span className={cn('h-8 w-px', complete ? 'bg-primary' : 'bg-border')} />}</div><span className={cn('pt-1 text-sm', active && 'font-semibold text-foreground', complete && 'text-foreground', index > activeStage && 'text-muted-foreground')}>{stage}{active && <span className="sr-only"> — выполняется</span>}</span></li> })}</ol></CardContent>
      </Card>
    </section>
  )
}
