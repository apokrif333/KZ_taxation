import type { Metadata } from 'next'
import Link from 'next/link'
import { ArrowRight, BookOpen, Calculator, FileJson, FileSearch, FileSpreadsheet, Landmark } from 'lucide-react'
import { FaqHeader } from '@/components/faq-header'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const metadata: Metadata = {
  title: 'FAQ — QCM Tax 270',
  description: 'Инструкции по подготовке брокерских отчётов и методология расчёта QCM Tax 270.',
}

const brokerGuides = [
  { href: '/faq/interactive-brokers', title: 'Как скачать отчёты для Interactive Brokers', description: 'Скачайте Annual Activity Statement в CSV за каждый год существования счёта и загрузите файлы в расчёт.' },
  { href: '/faq/freedom-broker', title: 'Как скачать отчёты для Freedom Broker', description: 'Скачайте отчёт брокера в XLSX на русском языке за весь срок счёта и отдельно добавьте отчёты для каждого номера счёта.' },
  { href: '/faq/exante', title: 'Как скачать отчёты для Exante', description: 'Создайте Custom Report в CSV на английском языке за каждый год существования счёта и загрузите все готовые файлы.' },
  { href: '/faq/freedom-bank', title: 'Как скачать отчёты для Freedom Bank', description: 'Запросите русскоязычный PDF «Отчёт о брокерских сделках» в Freedom SuperApp за каждый год существования счёта.' },
  { href: '/faq/tabys', title: 'Как скачать отчёты для Tabys', description: 'Подготовьте русскоязычные PDF-отчёты об операциях за все годы существования счёта.' },
] as const

export default function FaqPage() {
  return (
    <div className="min-h-screen bg-background">
      <FaqHeader />
      <main className="mx-auto max-w-5xl px-4 py-10 sm:px-6 lg:px-8">
        <header className="max-w-3xl">
          <p className="text-sm font-semibold text-primary">QCM TAX 270 · ПОМОЩЬ</p>
          <h1 className="mt-2 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">FAQ</h1>
          <p className="mt-3 text-pretty leading-relaxed text-muted-foreground">Инструкции по подготовке брокерских отчётов и материалы, которые помогут проверить расчёт формы 270.00.</p>
        </header>

        <Accordion className="mt-8 gap-4" multiple>
          <AccordionItem value="broker-guides" className="rounded-xl border border-primary/15 bg-card px-5">
            <AccordionTrigger className="items-center gap-4 py-5 text-left text-xl font-semibold hover:no-underline sm:text-2xl">
              <span className="flex items-center gap-3"><BookOpen className="size-5 text-primary" aria-hidden="true" />Инструкции для брокеров</span>
            </AccordionTrigger>
            <AccordionContent className="pb-5 pt-1">
              <div className="grid gap-4">
                {brokerGuides.map((guide) => <GuideCard key={guide.href} {...guide} icon={<FileSpreadsheet aria-hidden="true" />} />)}
              </div>
            </AccordionContent>
          </AccordionItem>

          <AccordionItem value="methodology" className="rounded-xl border border-primary/15 bg-card px-5">
            <AccordionTrigger className="items-center gap-4 py-5 text-left text-xl font-semibold hover:no-underline sm:text-2xl">
              <span className="flex items-center gap-3"><Calculator className="size-5 text-primary" aria-hidden="true" />Методология и расчёты</span>
            </AccordionTrigger>
            <AccordionContent className="pb-5 pt-1">
              <div className="grid gap-4">
                <GuideCard href="/faq/audit-file" title="Что находится в audit-файле" description="Описание всех листов audit Excel, колонок, расчётных показателей и связи детальных операций с Years_Results." icon={<FileSearch aria-hidden="true" />} />
              </div>
            </AccordionContent>
          </AccordionItem>

          <AccordionItem value="tax-and-law" className="rounded-xl border border-primary/15 bg-card px-5">
            <AccordionTrigger className="items-center gap-4 py-5 text-left text-xl font-semibold hover:no-underline sm:text-2xl">
              <span className="flex items-center gap-3"><Landmark className="size-5 text-primary" aria-hidden="true" />Налоги и законы</span>
            </AccordionTrigger>
            <AccordionContent className="pb-5 pt-1">
              <div className="grid gap-4">
                <GuideCard href="/faq/form270-upload" title="Загрузка JSON формы 270.00 и оплата налога" description="Как проверить предзаполненные данные КГД, загрузить JSON, отправить декларацию и оплатить ИПН." icon={<FileJson aria-hidden="true" />} />
              </div>
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      </main>
    </div>
  )
}

function GuideCard({ href, title, description, icon }: { href: string; title: string; description: string; icon: React.ReactNode }) {
  return <Link href={href} className="group block rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50"><Card className="border-primary/15 transition-colors group-hover:bg-accent/30"><CardHeader className="sm:flex-row sm:items-start sm:justify-between"><div><div className="mb-3 flex size-10 items-center justify-center rounded-md bg-primary/10 text-primary">{icon}</div><CardTitle className="text-lg">{title}</CardTitle><CardDescription className="mt-2 max-w-2xl">{description}</CardDescription></div><ArrowRight className="mt-1 hidden size-5 text-primary transition-transform group-hover:translate-x-1 sm:block" aria-hidden="true" /></CardHeader><CardContent><span className="inline-flex items-center gap-1 text-sm font-medium text-primary">Открыть статью <ArrowRight className="size-4" aria-hidden="true" /></span></CardContent></Card></Link>
}
