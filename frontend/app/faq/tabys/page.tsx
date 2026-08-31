import type { Metadata } from 'next'
import Link from 'next/link'
import { ArrowLeft, FileSpreadsheet, UploadCloud } from 'lucide-react'
import { FaqHeader } from '@/components/faq-header'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const metadata: Metadata = {
  title: 'Как скачать отчёты для Tabys — QCM Tax 270',
  description: 'Краткая инструкция по подготовке отчётов Tabys для QCM Tax 270.',
}

export default function TabysGuidePage() {
  return (
    <div className="min-h-screen bg-background">
      <FaqHeader article />
      <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
        <Link href="/faq" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" aria-hidden="true" />Все инструкции</Link>

        <header className="mt-6 max-w-3xl">
          <p className="text-sm font-semibold text-primary">TABYS</p>
          <h1 className="mt-2 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">Как скачать отчёты для Tabys</h1>
          <p className="mt-3 text-pretty leading-relaxed text-muted-foreground">Для расчёта нужны отчёты <span className="font-medium text-foreground">PDF</span> на русском языке за все годы существования счёта.</p>
        </header>

        <Card className="mt-8 border-primary/20 bg-accent/25">
          <CardContent className="flex gap-3 pt-4"><FileSpreadsheet className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" /><p className="text-sm leading-relaxed">Загрузите отчёты об операциях в формате PDF. В них должны быть данные по счёту, периоду и совершённым операциям.</p></CardContent>
        </Card>

        <ol className="mt-10 grid gap-8" aria-label="Инструкция по отчётам Tabys">
          <li className="grid gap-4 lg:grid-cols-[3rem_minmax(0,1fr)]">
            <span className="flex size-10 items-center justify-center rounded-full bg-primary font-mono text-sm font-semibold text-primary-foreground">1</span>
            <Card className="border-border/80"><CardHeader><CardTitle className="text-lg">Подготовьте отчёты за все годы</CardTitle><CardDescription className="leading-relaxed">Скачайте русскоязычные PDF-отчёты за каждый год существования вашего счёта Tabys.</CardDescription></CardHeader></Card>
          </li>
          <li className="grid gap-4 lg:grid-cols-[3rem_minmax(0,1fr)]">
            <span className="flex size-10 items-center justify-center rounded-full bg-primary font-mono text-sm font-semibold text-primary-foreground">2</span>
            <Card className="border-border/80"><CardHeader><CardTitle className="text-lg">Проверьте формат отчёта</CardTitle><CardDescription className="leading-relaxed">Нужен PDF «Отчёт по операциям», аналогичный примеру ниже.</CardDescription></CardHeader><CardContent><figure className="overflow-hidden rounded-lg border border-border bg-muted/20"><img src="/faq/tabys/report-example.png" alt="Пример PDF-отчёта по операциям Tabys" className="h-auto w-full" /><figcaption className="border-t bg-card px-3 py-2 text-xs text-muted-foreground">Пример подходящего отчёта Tabys</figcaption></figure></CardContent></Card>
          </li>
          <li className="grid gap-4 lg:grid-cols-[3rem_minmax(0,1fr)]">
            <span className="flex size-10 items-center justify-center rounded-full bg-primary font-mono text-sm font-semibold text-primary-foreground">3</span>
            <Card className="border-border/80"><CardHeader><CardTitle className="text-lg">Загрузите файлы в QCM Tax 270</CardTitle><CardDescription className="leading-relaxed">Добавьте все подготовленные PDF в блок Tabys на главной странице и продолжите расчёт.</CardDescription></CardHeader><CardContent><figure className="overflow-hidden rounded-lg border border-border bg-muted/20"><img src="/faq/tabys/step-3-upload.png" alt="Блок Tabys на странице загрузки QCM Tax 270" className="h-auto w-full" /><figcaption className="border-t bg-card px-3 py-2 text-xs text-muted-foreground">Загрузка отчётов Tabys</figcaption></figure></CardContent></Card>
          </li>
        </ol>

        <Card className="mt-10 border-primary/25 bg-accent/25"><CardHeader><div className="flex size-10 items-center justify-center rounded-md bg-primary/10 text-primary"><UploadCloud aria-hidden="true" /></div><CardTitle>Перед загрузкой</CardTitle><CardDescription>Убедитесь, что выбраны только PDF-отчёты Tabys за все нужные годы.</CardDescription></CardHeader><CardContent><Link href="/" className="inline-flex items-center gap-2 text-sm font-medium text-primary underline-offset-4 hover:underline">Перейти к загрузке отчётов <ArrowLeft className="size-4 rotate-180" aria-hidden="true" /></Link></CardContent></Card>
      </main>
    </div>
  )
}
