import type { Metadata } from 'next'
import Link from 'next/link'
import { ArrowLeft, FileSpreadsheet, UploadCloud } from 'lucide-react'
import { FaqHeader } from '@/components/faq-header'
import { ZoomableImage } from '@/components/zoomable-image'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const metadata: Metadata = {
  title: 'Как скачать отчёты для Alatau City Invest — QCM Tax 270',
  description: 'Пошаговая инструкция по подготовке Excel-отчётов ОДДС и ОДЦБ Alatau City Invest для QCM Tax 270.',
}

const steps = [
  {
    title: 'Откройте «Декларация за 2025»',
    text: 'Войдите в приложение Alatau City Invest и нажмите «Декларация за 2025».',
    image: '/faq/alatay/step-1-declaration.png',
    alt: 'Раздел «Декларация за 2025» в приложении Alatau City Invest',
  },
  {
    title: 'Откройте оба вида отчётов',
    text: 'Скачайте по очереди отчёты из двух блоков: «Ценные бумаги» и «Денежные средства».',
    image: '/faq/alatay/step-2-report-types.png',
    alt: 'Блоки «Ценные бумаги» и «Денежные средства» в приложении Alatau City Invest',
  },
  {
    title: 'Выберите период за весь срок счёта',
    text: 'Все отчёты скачайте в формате Excel (.xlsx) за все годы существования счёта — из обоих блоков.',
    image: '/faq/alatay/step-3-period.jpeg',
    alt: 'Выбор периода для отчёта «Ценные бумаги» в приложении Alatau City Invest',
  },
  {
    title: 'Загрузите отчёты в QCM Tax 270',
    text: 'Добавьте отчёты ОДДС и ОДЦБ в одноимённые блоки Alatau City Invest на главной странице. Количество файлов в обоих блоках должно совпадать; затем нажмите «Продолжить».',
    image: '/faq/alatay/step-4-upload.png',
    alt: 'Кнопка «Продолжить» на странице загрузки QCM Tax 270',
  },
] as const

export default function AlatayGuidePage() {
  return (
    <div className="min-h-screen bg-background">
      <FaqHeader article />
      <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
        <Link href="/faq" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" aria-hidden="true" />Все инструкции</Link>

        <header className="mt-6 max-w-3xl">
          <p className="text-sm font-semibold text-primary">ALATAU CITY INVEST</p>
          <h1 className="mt-2 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">Как скачать отчёты для Alatau City Invest</h1>
          <p className="mt-3 text-pretty leading-relaxed text-muted-foreground">Для расчёта нужны два вида отчётов <span className="font-medium text-foreground">XLSX</span>: ОДДС и ОДЦБ — за все годы существования счёта.</p>
        </header>

        <Card className="mt-8 border-primary/20 bg-accent/25">
          <CardContent className="flex gap-3 pt-4"><FileSpreadsheet className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" /><p className="text-sm leading-relaxed">Для каждого периода скачайте оба отчёта: о движении денежных средств и о движении ценных бумаг. Перед расчётом количество файлов ОДДС и ОДЦБ должно совпадать.</p></CardContent>
        </Card>

        <ol className="mt-10 grid gap-8" aria-label="Инструкция по отчётам Alatau City Invest">
          {steps.map((step, index) => (
            <li key={step.title} className="grid gap-4 lg:grid-cols-[3rem_minmax(0,1fr)]">
              <span className="flex size-10 items-center justify-center rounded-full bg-primary font-mono text-sm font-semibold text-primary-foreground">{index + 1}</span>
              <Card className="border-border/80">
                <CardHeader><CardTitle className="text-lg">{step.title}</CardTitle><CardDescription className="leading-relaxed">{step.text}</CardDescription></CardHeader>
                <CardContent><figure className="overflow-hidden rounded-lg border border-border bg-muted/20"><ZoomableImage src={step.image} alt={step.alt} /><figcaption className="border-t bg-card px-3 py-2 text-xs text-muted-foreground">Шаг {index + 1}</figcaption></figure></CardContent>
              </Card>
            </li>
          ))}
        </ol>

        <Card className="mt-10 border-primary/25 bg-accent/25"><CardHeader><div className="flex size-10 items-center justify-center rounded-md bg-primary/10 text-primary"><UploadCloud aria-hidden="true" /></div><CardTitle>Перед загрузкой</CardTitle><CardDescription>Проверьте, что для каждого периода подготовлены оба Excel-отчёта: ОДДС и ОДЦБ.</CardDescription></CardHeader><CardContent><Link href="/" className="inline-flex items-center gap-2 text-sm font-medium text-primary underline-offset-4 hover:underline">Перейти к загрузке отчётов <ArrowLeft className="size-4 rotate-180" aria-hidden="true" /></Link></CardContent></Card>
      </main>
    </div>
  )
}
