import type { Metadata } from 'next'
import Link from 'next/link'
import { ArrowLeft, ExternalLink, FileSpreadsheet, UploadCloud } from 'lucide-react'
import { FaqHeader } from '@/components/faq-header'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const metadata: Metadata = {
  title: 'Как скачать отчёты для Exante — QCM Tax 270',
  description: 'Пошаговая инструкция по созданию и скачиванию Custom Reports в CSV из Exante.',
}

const steps = [
  {
    title: 'Войдите в личный кабинет Exante',
    text: 'Откройте сайт брокера и авторизуйтесь в личном кабинете.',
    image: null,
  },
  {
    title: 'Выберите английский язык',
    text: 'В правом верхнем углу переключите язык кабинета на English.',
    image: '/faq/exante/step-1-language.png',
    alt: 'Переключатель английского языка в личном кабинете Exante',
  },
  {
    title: 'Откройте создание Custom Report',
    text: 'В боковом меню выберите Reports, откройте вкладку Custom Reports и нажмите Add Custom Report.',
    image: '/faq/exante/step-2-add-custom-report.png',
    alt: 'Раздел Reports в Exante с вкладкой Custom Reports и кнопкой Add Custom Report',
  },
  {
    title: 'Настройте формат и состав отчёта',
    text: 'Выберите номер счёта и формат CSV. Добавьте четыре раздела: Account Summary, Financial Transactions, Trades и Costs and Charges Report.',
    image: '/faq/exante/step-3-report-settings.png',
    alt: 'Форма Add Custom Report в Exante с выбором счёта и формата CSV',
  },
  {
    title: 'Укажите годовой период для каждого раздела',
    text: 'Для каждого из четырёх разделов укажите период с 01.01 по 31.12 нужного года. Создавайте отдельный отчёт для каждого года существования счёта.',
    image: '/faq/exante/step-4-report-period.png',
    alt: 'Поля дат отчёта Costs and Charges Report в Exante',
  },
  {
    title: 'Сохраните и запросите отчёт',
    text: 'Нажмите Save and Request. Формирование обычно занимает 10–15 минут.',
    image: null,
  },
  {
    title: 'Скачайте готовый CSV',
    text: 'Вернитесь в Custom Reports и нажмите иконку скачивания напротив готового отчёта. Повторите для всех сформированных годовых отчётов.',
    image: '/faq/exante/step-5-download-report.png',
    alt: 'Список Custom Reports в Exante с кнопками скачивания готовых CSV',
  },
  {
    title: 'Загрузите отчёты в QCM Tax 270',
    text: 'Вернитесь на главную страницу и добавьте все скачанные CSV в блок Exante, затем продолжите расчёт.',
    image: '/faq/exante/step-6-upload.png',
    alt: 'Блок Exante на странице загрузки QCM Tax 270',
  },
] as const

export default function ExanteGuidePage() {
  return (
    <div className="min-h-screen bg-background">
      <FaqHeader article />
      <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
        <Link href="/faq" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" aria-hidden="true" />Все инструкции</Link>

        <header className="mt-6 max-w-3xl">
          <p className="text-sm font-semibold text-primary">EXANTE</p>
          <h1 className="mt-2 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">Как скачать отчёты для Exante</h1>
          <p className="mt-3 text-pretty leading-relaxed text-muted-foreground">Для расчёта нужны отчёты <span className="font-medium text-foreground">CSV</span> на английском языке за все годы существования счёта.</p>
        </header>

        <Card className="mt-8 border-primary/20 bg-accent/25">
          <CardContent className="flex gap-3 pt-4"><FileSpreadsheet className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" /><p className="text-sm leading-relaxed">В каждом годовом Custom Report должны быть выбраны <span className="font-medium">Account Summary</span>, <span className="font-medium">Financial Transactions</span>, <span className="font-medium">Trades</span> и <span className="font-medium">Costs and Charges Report</span> в формате <span className="font-medium">CSV</span>.</p></CardContent>
        </Card>

        <ol className="mt-10 grid gap-8" aria-label="Шаги скачивания отчётов Exante">
          {steps.map((step, index) => (
            <li key={step.title} className="grid gap-4 lg:grid-cols-[3rem_minmax(0,1fr)]">
              <span className="flex size-10 items-center justify-center rounded-full bg-primary font-mono text-sm font-semibold text-primary-foreground">{index + 1}</span>
              <Card className="border-border/80">
                <CardHeader>
                  <CardTitle className="text-lg">{step.title}</CardTitle>
                  <CardDescription className="leading-relaxed">{step.text}</CardDescription>
                </CardHeader>
                {index === 0 && <CardContent><a href="https://exante.eu" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm font-medium text-primary underline-offset-4 hover:underline">Открыть exante.eu <ExternalLink className="size-4" aria-hidden="true" /></a></CardContent>}
                {step.image && <CardContent><figure className="overflow-hidden rounded-lg border border-border bg-muted/20"><img src={step.image} alt={step.alt} className="h-auto w-full" /><figcaption className="border-t bg-card px-3 py-2 text-xs text-muted-foreground">Шаг {index + 1}</figcaption></figure></CardContent>}
              </Card>
            </li>
          ))}
        </ol>

        <Card className="mt-10 border-primary/25 bg-accent/25"><CardHeader><div className="flex size-10 items-center justify-center rounded-md bg-primary/10 text-primary"><UploadCloud aria-hidden="true" /></div><CardTitle>Перед загрузкой</CardTitle><CardDescription>Убедитесь, что скачали готовые CSV за каждый год и для каждого нужного счёта. Затем добавьте все файлы в блок Exante на главной странице.</CardDescription></CardHeader><CardContent><Link href="/" className="inline-flex items-center gap-2 text-sm font-medium text-primary underline-offset-4 hover:underline">Перейти к загрузке отчётов <ArrowLeft className="size-4 rotate-180" aria-hidden="true" /></Link></CardContent></Card>
      </main>
    </div>
  )
}
