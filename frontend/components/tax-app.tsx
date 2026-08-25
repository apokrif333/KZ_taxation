'use client'

import { useEffect, useState } from 'react'
import { SiteHeader } from '@/components/site-header'
import { UploadWorkflow } from '@/components/upload-workflow'
import { ProcessingProgress } from '@/components/processing-progress'
import { ResultsView } from '@/components/results-view'
import { PrivacyView } from '@/components/privacy-view'
import { mockProcessReport, PROCESSING_STAGES } from '@/lib/mock-service'
import type { AppStep, Broker, ProcessingResult, UploadedReport } from '@/lib/types'

export function TaxApp() {
  const [step, setStep] = useState<AppStep>('upload')
  const [privacy, setPrivacy] = useState(false)
  const [activeStage, setActiveStage] = useState(0)
  const [fileName, setFileName] = useState('')
  const [result, setResult] = useState<ProcessingResult | null>(null)
  const [request, setRequest] = useState<{ broker: Broker; files: UploadedReport[]; taxYear: string; jointAccount: boolean } | null>(null)

  useEffect(() => {
    if (step !== 'processing' || !request) return
    if (activeStage < PROCESSING_STAGES.length - 1) {
      const timer = window.setTimeout(() => setActiveStage((value) => value + 1), 620)
      return () => window.clearTimeout(timer)
    }
    const timer = window.setTimeout(async () => {
      const data = await mockProcessReport(request)
      setResult(data)
      setStep('results')
    }, 700)
    return () => window.clearTimeout(timer)
  }, [step, request, activeStage])

  const startProcessing = (data: { broker: Broker; files: UploadedReport[]; taxYear: string; jointAccount: boolean }) => {
    setRequest(data); setFileName(data.files[0]?.name ?? 'Брокерский отчёт'); setActiveStage(0); setStep('processing'); window.scrollTo({ top: 0, behavior: 'smooth' })
  }
  const restart = () => { setStep('upload'); setResult(null); setRequest(null); setPrivacy(false); window.scrollTo({ top: 0, behavior: 'smooth' }) }

  return <div className="min-h-screen bg-background"><SiteHeader onPrivacy={() => setPrivacy(true)} onStart={restart} />{privacy ? <PrivacyView onBack={() => setPrivacy(false)} /> : <main className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">{step === 'upload' && <><section className="grid gap-6 py-10 lg:grid-cols-[1fr_auto] lg:items-end"><div><p className="text-sm font-semibold text-primary">ФОРМА 270.00 · КАЗАХСТАН</p><h1 className="mt-2 max-w-3xl text-balance text-3xl font-semibold tracking-tight sm:text-4xl">Подготовка формы 270.00 по брокерским отчётам</h1><p className="mt-3 max-w-2xl text-pretty leading-relaxed text-muted-foreground">Загрузите брокерский отчёт, проверьте результаты обработки и получите audit-файл и данные для формы 270.00.</p></div><div id="how-it-works" className="flex gap-5 border-l border-primary/25 pl-5 text-sm"><WorkflowItem number="1" label="Загрузите отчёт" /><WorkflowItem number="2" label="Проверьте сверку" /><WorkflowItem number="3" label="Скачайте результаты" /></div></section><UploadWorkflow onProcess={startProcessing} /><footer className="flex flex-col items-center gap-1 border-t border-primary/10 py-10 text-center text-xs text-muted-foreground"><span className="font-semibold text-primary">QCM Tax 270</span><span>by Quantum Cross Management · Демонстрационный интерфейс. Результаты требуют проверки специалистом.</span></footer></>}{step === 'processing' && <ProcessingProgress activeStage={activeStage} fileName={fileName} />}{step === 'results' && result && <ResultsView result={result} onRestart={restart} />}</main>}</div>
}

function WorkflowItem({ number, label }: { number: string; label: string }) { return <div className="flex max-w-24 flex-col gap-2"><span className="flex size-6 items-center justify-center rounded-full bg-accent font-mono text-xs font-semibold text-primary ring-1 ring-primary/20">{number}</span><span className="text-muted-foreground">{label}</span></div> }
