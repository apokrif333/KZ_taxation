import type { Metadata } from 'next'
import Link from 'next/link'
import { ArrowLeft, FileSpreadsheet, UploadCloud } from 'lucide-react'
import { FaqHeader } from '@/components/faq-header'
import { ZoomableImage } from '@/components/zoomable-image'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const metadata: Metadata = {
  title: 'Как скачать отчёты для Paidax — QCM Tax 270',
  description: 'Пошаговая инструкция по подготовке Excel-отчётов Paidax для QCM Tax 270.',
}

const steps = [
  {
    title: 'Откройте «Неторговое поручение»',
    text: 'Войдите в приложение Paidax, нажмите на значок меню слева вверху и выберите «Неторговое поручение».',
    image: '/faq/paidax/step-1-menu.png',
    alt: 'Пункт «Неторговое поручение» в меню приложения Paidax',
  },
  {
    title: 'Выберите «Брокерский отчёт»',
    text: 'В разделе «Неторговое поручение» выберите «Брокерский отчёт».',
    image: '/faq/paidax/step-2-broker-report.png',
    alt: 'Экран «Брокерский отчёт» в приложении Paidax',
  },
  {
    title: 'Скачайте Excel за каждый год',
    text: 'Укажите период с 1 января по 31 декабря и формат Excel. Скачайте отдельный отчёт за каждый год существования счёта.',
    image: '/faq/paidax/step-3-download-excel.png',
    alt: 'Выбор периода и формата Excel для брокерского отчёта Paidax',
  },
  {
    title: 'Загрузите файлы в QCM Tax 270',
    text: 'Вернитесь на главную страницу QCM Tax 270 и добавьте все скачанные XLSX-файлы в блок Paidax. После этого продолжите расчёт.',
    image: '/faq/paidax/step-4-upload.png',
    alt: 'Блок Paidax на странице загрузки QCM Tax 270',
  },
] as const

export default function PaidaxGuidePage() {
  return (
    <div className="min-h-screen bg-background">
      <FaqHeader article />
      <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
        <Link href="/faq" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" aria-hidden="true" />Все инструкции</Link>

        <header className="mt-6 max-w-3xl">
          <p className="text-sm font-semibold text-primary">PAIDAX</p>
          <h1 className="mt-2 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">Как скачать отчёты для Paidax</h1>
          <p className="mt-3 text-pretty leading-relaxed text-muted-foreground">Для расчёта нужны отчёты <span className="font-medium text-foreground">XLSX</span> за все годы существования счёта.</p>
        </header>

        <Card className="mt-8 border-primary/20 bg-accent/25">
          <CardContent className="flex gap-3 pt-4"><FileSpreadsheet className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" /><p className="text-sm leading-relaxed">Скачайте отдельный Excel-отчёт за каждый год с 1 января по 31 декабря. Затем загрузите все файлы в блок Paidax на главной странице.</p></CardContent>
        </Card>

        <ol className="mt-10 grid gap-8" aria-label="Инструкция по отчётам Paidax">
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

        <Card className="mt-10 border-primary/25 bg-accent/25"><CardHeader><div className="flex size-10 items-center justify-center rounded-md bg-primary/10 text-primary"><UploadCloud aria-hidden="true" /></div><CardTitle>Перед загрузкой</CardTitle><CardDescription>Проверьте, что подготовлены XLSX-файлы за все годы существования счёта Paidax.</CardDescription></CardHeader><CardContent><Link href="/" className="inline-flex items-center gap-2 text-sm font-medium text-primary underline-offset-4 hover:underline">Перейти к загрузке отчётов <ArrowLeft className="size-4 rotate-180" aria-hidden="true" /></Link></CardContent></Card>
      </main>
    </div>
  )
}
