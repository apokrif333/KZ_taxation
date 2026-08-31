import type { Metadata } from 'next'
import Link from 'next/link'
import { ArrowRight, BookOpen, FileSpreadsheet } from 'lucide-react'
import { FaqHeader } from '@/components/faq-header'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const metadata: Metadata = {
  title: 'FAQ — QCM Tax 270',
  description: 'Инструкции по подготовке брокерских отчётов для QCM Tax 270.',
}

export default function FaqPage() {
  return (
    <div className="min-h-screen bg-background">
      <FaqHeader />
      <main className="mx-auto max-w-5xl px-4 py-10 sm:px-6 lg:px-8">
        <header className="max-w-3xl">
          <p className="text-sm font-semibold text-primary">QCM TAX 270 · ПОМОЩЬ</p>
          <h1 className="mt-2 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">FAQ</h1>
          <p className="mt-3 text-pretty leading-relaxed text-muted-foreground">Пошаговые инструкции по подготовке и загрузке брокерских отчётов для расчёта формы 270.00.</p>
        </header>

        <section className="mt-8" aria-labelledby="broker-guides-title">
          <div className="mb-4 flex items-center gap-2"><BookOpen className="size-5 text-primary" aria-hidden="true" /><h2 id="broker-guides-title" className="text-xl font-semibold">Инструкции для брокеров</h2></div>
          <Link href="/faq/interactive-brokers" className="group block rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
            <Card className="border-primary/15 transition-colors group-hover:bg-accent/30">
              <CardHeader className="sm:flex-row sm:items-start sm:justify-between"><div><div className="mb-3 flex size-10 items-center justify-center rounded-md bg-primary/10 text-primary"><FileSpreadsheet aria-hidden="true" /></div><CardTitle className="text-lg">Как скачать отчёты для Interactive Brokers</CardTitle><CardDescription className="mt-2 max-w-2xl">Скачайте Annual Activity Statement в CSV за каждый год существования счёта и загрузите файлы в расчёт.</CardDescription></div><ArrowRight className="mt-1 hidden size-5 text-primary transition-transform group-hover:translate-x-1 sm:block" aria-hidden="true" /></CardHeader>
              <CardContent><span className="inline-flex items-center gap-1 text-sm font-medium text-primary">Открыть инструкцию <ArrowRight className="size-4" aria-hidden="true" /></span></CardContent>
            </Card>
          </Link>
          <Link href="/faq/freedom-broker" className="group mt-4 block rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
            <Card className="border-primary/15 transition-colors group-hover:bg-accent/30">
              <CardHeader className="sm:flex-row sm:items-start sm:justify-between"><div><div className="mb-3 flex size-10 items-center justify-center rounded-md bg-primary/10 text-primary"><FileSpreadsheet aria-hidden="true" /></div><CardTitle className="text-lg">Как скачать отчёты для Freedom Broker</CardTitle><CardDescription className="mt-2 max-w-2xl">Скачайте отчёт брокера в XLSX на русском языке за весь срок счёта и отдельно добавьте отчёты для каждого номера счёта.</CardDescription></div><ArrowRight className="mt-1 hidden size-5 text-primary transition-transform group-hover:translate-x-1 sm:block" aria-hidden="true" /></CardHeader>
              <CardContent><span className="inline-flex items-center gap-1 text-sm font-medium text-primary">Открыть инструкцию <ArrowRight className="size-4" aria-hidden="true" /></span></CardContent>
            </Card>
          </Link>
          <Link href="/faq/exante" className="group mt-4 block rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
            <Card className="border-primary/15 transition-colors group-hover:bg-accent/30">
              <CardHeader className="sm:flex-row sm:items-start sm:justify-between"><div><div className="mb-3 flex size-10 items-center justify-center rounded-md bg-primary/10 text-primary"><FileSpreadsheet aria-hidden="true" /></div><CardTitle className="text-lg">Как скачать отчёты для Exante</CardTitle><CardDescription className="mt-2 max-w-2xl">Создайте Custom Report в CSV на английском языке за каждый год существования счёта и загрузите все готовые файлы.</CardDescription></div><ArrowRight className="mt-1 hidden size-5 text-primary transition-transform group-hover:translate-x-1 sm:block" aria-hidden="true" /></CardHeader>
              <CardContent><span className="inline-flex items-center gap-1 text-sm font-medium text-primary">Открыть инструкцию <ArrowRight className="size-4" aria-hidden="true" /></span></CardContent>
            </Card>
          </Link>
          <Link href="/faq/freedom-bank" className="group mt-4 block rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
            <Card className="border-primary/15 transition-colors group-hover:bg-accent/30">
              <CardHeader className="sm:flex-row sm:items-start sm:justify-between"><div><div className="mb-3 flex size-10 items-center justify-center rounded-md bg-primary/10 text-primary"><FileSpreadsheet aria-hidden="true" /></div><CardTitle className="text-lg">Как скачать отчёты для Freedom Bank</CardTitle><CardDescription className="mt-2 max-w-2xl">Запросите русскоязычный PDF «Отчёт о брокерских сделках» в Freedom SuperApp за каждый год существования счёта.</CardDescription></div><ArrowRight className="mt-1 hidden size-5 text-primary transition-transform group-hover:translate-x-1 sm:block" aria-hidden="true" /></CardHeader>
              <CardContent><span className="inline-flex items-center gap-1 text-sm font-medium text-primary">Открыть инструкцию <ArrowRight className="size-4" aria-hidden="true" /></span></CardContent>
            </Card>
          </Link>
          <Link href="/faq/tabys" className="group mt-4 block rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
            <Card className="border-primary/15 transition-colors group-hover:bg-accent/30">
              <CardHeader className="sm:flex-row sm:items-start sm:justify-between"><div><div className="mb-3 flex size-10 items-center justify-center rounded-md bg-primary/10 text-primary"><FileSpreadsheet aria-hidden="true" /></div><CardTitle className="text-lg">Как скачать отчёты для Tabys</CardTitle><CardDescription className="mt-2 max-w-2xl">Подготовьте русскоязычные PDF-отчёты об операциях за все годы существования счёта.</CardDescription></div><ArrowRight className="mt-1 hidden size-5 text-primary transition-transform group-hover:translate-x-1 sm:block" aria-hidden="true" /></CardHeader>
              <CardContent><span className="inline-flex items-center gap-1 text-sm font-medium text-primary">Открыть инструкцию <ArrowRight className="size-4" aria-hidden="true" /></span></CardContent>
            </Card>
          </Link>
        </section>
      </main>
    </div>
  )
}
