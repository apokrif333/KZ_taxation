import type { Metadata } from 'next'
import Link from 'next/link'
import { ArrowLeft, ExternalLink, FileSpreadsheet, UploadCloud } from 'lucide-react'
import { FaqHeader } from '@/components/faq-header'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const metadata: Metadata = {
  title: 'Как скачать отчёты для Freedom Broker — QCM Tax 270',
  description: 'Пошаговая инструкция по скачиванию отчётов Freedom Broker в XLSX.',
}

const steps = [
  {
    title: 'Войдите в личный кабинет Tradernet',
    text: 'Откройте сайт Freedom Broker и авторизуйтесь в личном кабинете Tradernet.',
    image: null,
  },
  {
    title: 'Выберите счёт',
    text: 'Откройте список счетов в правом верхнем углу и выберите нужный. Если у вас есть обычный и D-счёт, подготовьте отчёт для каждого из них отдельно.',
    image: '/faq/freedom-broker/step-1-account.png',
    alt: 'Список счетов Freedom Broker в личном кабинете Tradernet',
  },
  {
    title: 'Откройте отчёт брокера и задайте период',
    text: 'Перейдите: Кабинет → Отчёты брокера → Отчёт брокера. Выберите «За период»: в «Дате начала» укажите самую раннюю доступную дату, а в «Дате экспирации» — 31 декабря прошлого года. Затем нажмите «Сформировать отчёт».',
    image: '/faq/freedom-broker/step-2-report-period.png',
    alt: 'Страница отчёта брокера Freedom с полями дат и кнопкой формирования отчёта',
  },
  {
    title: 'Скачайте отчёт в XLS',
    text: 'После формирования отчёта нажмите кнопку XLS. Для расчёта нужны отчёты в формате XLSX на русском языке за весь срок существования каждого счёта.',
    image: '/faq/freedom-broker/step-3-download-xls.png',
    alt: 'Сформированный отчёт Freedom Broker с выделенной кнопкой скачивания XLS',
  },
  {
    title: 'Загрузите отчёты в QCM Tax 270',
    text: 'На главной странице в блоке Freedom Broker нажмите «Добавить счёт», укажите его номер и добавьте относящийся к нему XLSX. Повторите для каждого счёта, включая D-счёт.',
    image: '/faq/freedom-broker/step-4-upload.png',
    alt: 'Блок Freedom Broker на странице загрузки QCM Tax 270 с добавлением нескольких счетов',
  },
] as const

export default function FreedomBrokerGuidePage() {
  return (
    <div className="min-h-screen bg-background">
      <FaqHeader article />
      <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
        <Link href="/faq" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" aria-hidden="true" />Все инструкции</Link>

        <header className="mt-6 max-w-3xl">
          <p className="text-sm font-semibold text-primary">FREEDOM BROKER</p>
          <h1 className="mt-2 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">Как скачать отчёты для Freedom Broker</h1>
          <p className="mt-3 text-pretty leading-relaxed text-muted-foreground">Для расчёта нужны отчёты <span className="font-medium text-foreground">XLSX</span> на русском языке за все годы существования каждого счёта.</p>
        </header>

        <Card className="mt-8 border-primary/20 bg-accent/25">
          <CardContent className="flex gap-3 pt-4"><FileSpreadsheet className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" /><p className="text-sm leading-relaxed">В отчёте Freedom Broker номер счёта не всегда указан. Поэтому при загрузке обязательно добавьте счёт вручную и привяжите к нему соответствующий файл.</p></CardContent>
        </Card>

        <ol className="mt-10 grid gap-8" aria-label="Шаги скачивания отчёта Freedom Broker">
          {steps.map((step, index) => (
            <li key={step.title} className="grid gap-4 lg:grid-cols-[3rem_minmax(0,1fr)]">
              <span className="flex size-10 items-center justify-center rounded-full bg-primary font-mono text-sm font-semibold text-primary-foreground">{index + 1}</span>
              <Card className="border-border/80">
                <CardHeader>
                  <CardTitle className="text-lg">{step.title}</CardTitle>
                  <CardDescription className="leading-relaxed">{step.text}</CardDescription>
                </CardHeader>
                {index === 0 && <CardContent><a href="https://tradernet.ru/terminal?site_lang=ru" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm font-medium text-primary underline-offset-4 hover:underline">Открыть Tradernet <ExternalLink className="size-4" aria-hidden="true" /></a></CardContent>}
                {step.image && <CardContent><figure className="overflow-hidden rounded-lg border border-border bg-muted/20"><img src={step.image} alt={step.alt} className="h-auto w-full" /><figcaption className="border-t bg-card px-3 py-2 text-xs text-muted-foreground">Шаг {index + 1}</figcaption></figure></CardContent>}
              </Card>
            </li>
          ))}
        </ol>

        <Card className="mt-10 border-primary/25 bg-accent/25"><CardHeader><div className="flex size-10 items-center justify-center rounded-md bg-primary/10 text-primary"><UploadCloud aria-hidden="true" /></div><CardTitle>Перед продолжением</CardTitle><CardDescription>Проверьте, что для каждого номера счёта добавлен свой XLSX. Если есть обычный и D-счёт, отчёты должны быть загружены в разные группы.</CardDescription></CardHeader><CardContent><Link href="/" className="inline-flex items-center gap-2 text-sm font-medium text-primary underline-offset-4 hover:underline">Перейти к загрузке отчётов <ArrowLeft className="size-4 rotate-180" aria-hidden="true" /></Link></CardContent></Card>
      </main>
    </div>
  )
}
