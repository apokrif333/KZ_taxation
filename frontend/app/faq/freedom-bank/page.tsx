import type { Metadata } from 'next'
import Link from 'next/link'
import { ArrowLeft, FileSpreadsheet, UploadCloud } from 'lucide-react'
import { FaqHeader } from '@/components/faq-header'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const metadata: Metadata = {
  title: 'Как скачать отчёты для Freedom Bank — QCM Tax 270',
  description: 'Пошаговая инструкция по запросу отчёта о брокерских сделках в Freedom SuperApp.',
}

const steps = [
  {
    title: 'Откройте Freedom SuperApp',
    text: 'Войдите в мобильное приложение Freedom SuperApp на Android или iPhone.',
    image: null,
  },
  {
    title: 'Выберите SuperCard',
    text: 'На главной странице приложения нажмите SuperCard в разделе «Мой банк».',
    image: '/faq/freedom-bank/step-1-super-card.png',
    alt: 'Главная страница Freedom SuperApp с выделенной картой SuperCard',
  },
  {
    title: 'Откройте Валюту Freedom',
    text: 'На странице SuperCard выберите «Валюта Freedom».',
    image: '/faq/freedom-bank/step-2-freedom-currency.png',
    alt: 'Страница SuperCard с выделенным разделом Валюта Freedom',
  },
  {
    title: 'Перейдите в справки и выписки',
    text: 'Прокрутите страницу вниз и нажмите «Справки и выписка».',
    image: '/faq/freedom-bank/step-3-statements.png',
    alt: 'Страница Валюта Freedom с выделенным пунктом Справки и выписка',
  },
  {
    title: 'Выберите отчёт о брокерских сделках',
    text: 'В списке справок нажмите «Отчёт о брокерских сделках».',
    image: '/faq/freedom-bank/step-4-broker-trades.png',
    alt: 'Список справок Freedom Bank с выделенным отчётом о брокерских сделках',
  },
  {
    title: 'Запросите отчёт за год',
    text: 'Выберите язык «На русском» и годовой период с 01.01 по 31.12. Запрашивайте отчёты по очереди за все годы существования счёта и нажимайте «Получить выписку».',
    image: '/faq/freedom-bank/step-5-period.png',
    alt: 'Выбор годового периода с 01 января по 31 декабря в Freedom SuperApp',
  },
  {
    title: 'Загрузите отчёты в QCM Tax 270',
    text: 'Сохраните полученные PDF и добавьте все годовые отчёты в блок Freedom Bank на главной странице QCM Tax 270. Затем продолжите расчёт.',
    image: '/faq/freedom-bank/step-6-upload.png',
    alt: 'Блок Freedom Bank на странице загрузки QCM Tax 270',
  },
] as const

export default function FreedomBankGuidePage() {
  return (
    <div className="min-h-screen bg-background">
      <FaqHeader article />
      <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
        <Link href="/faq" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" aria-hidden="true" />Все инструкции</Link>

        <header className="mt-6 max-w-3xl">
          <p className="text-sm font-semibold text-primary">FREEDOM BANK</p>
          <h1 className="mt-2 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">Как скачать отчёты для Freedom Bank</h1>
          <p className="mt-3 text-pretty leading-relaxed text-muted-foreground">Для расчёта нужны отчёты <span className="font-medium text-foreground">PDF</span> на русском языке за все годы существования счёта.</p>
        </header>

        <Card className="mt-8 border-primary/20 bg-accent/25">
          <CardContent className="flex gap-3 pt-4"><FileSpreadsheet className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" /><p className="text-sm leading-relaxed">Запрашивайте именно <span className="font-medium">«Отчёт о брокерских сделках»</span> с языком <span className="font-medium">«На русском»</span> и годовым периодом для каждого нужного года.</p></CardContent>
        </Card>

        <ol className="mt-10 grid gap-8" aria-label="Шаги скачивания отчётов Freedom Bank">
          {steps.map((step, index) => (
            <li key={step.title} className="grid gap-4 lg:grid-cols-[3rem_minmax(0,1fr)]">
              <span className="flex size-10 items-center justify-center rounded-full bg-primary font-mono text-sm font-semibold text-primary-foreground">{index + 1}</span>
              <Card className="border-border/80">
                <CardHeader>
                  <CardTitle className="text-lg">{step.title}</CardTitle>
                  <CardDescription className="leading-relaxed">{step.text}</CardDescription>
                </CardHeader>
                {step.image && <CardContent><figure className="overflow-hidden rounded-lg border border-border bg-muted/20"><img src={step.image} alt={step.alt} className="h-auto w-full" /><figcaption className="border-t bg-card px-3 py-2 text-xs text-muted-foreground">Шаг {index + 1}</figcaption></figure></CardContent>}
              </Card>
            </li>
          ))}
        </ol>

        <Card className="mt-10 border-primary/25 bg-accent/25"><CardHeader><div className="flex size-10 items-center justify-center rounded-md bg-primary/10 text-primary"><UploadCloud aria-hidden="true" /></div><CardTitle>Перед загрузкой</CardTitle><CardDescription>Проверьте, что получили русскоязычные PDF за все нужные годы. Добавьте файлы в блок Freedom Bank и продолжите расчёт.</CardDescription></CardHeader><CardContent><Link href="/" className="inline-flex items-center gap-2 text-sm font-medium text-primary underline-offset-4 hover:underline">Перейти к загрузке отчётов <ArrowLeft className="size-4 rotate-180" aria-hidden="true" /></Link></CardContent></Card>
      </main>
    </div>
  )
}
