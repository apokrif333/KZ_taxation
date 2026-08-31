import type { Metadata } from 'next'
import Link from 'next/link'
import { ArrowLeft, ExternalLink, FileSpreadsheet, UploadCloud } from 'lucide-react'
import { FaqHeader } from '@/components/faq-header'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const metadata: Metadata = {
  title: 'Как скачать отчёты для Interactive Brokers — QCM Tax 270',
  description: 'Пошаговая инструкция по скачиванию Annual Activity Statement в CSV из Interactive Brokers.',
}

const steps = [
  {
    title: 'Войдите в личный кабинет Interactive Brokers',
    text: 'Откройте сайт брокера и авторизуйтесь в личном кабинете.',
    image: null,
  },
  {
    title: 'Выберите английский язык кабинета',
    text: 'Если английский язык не выбран заранее, откройте меню профиля, перейдите в настройки и выберите English.',
    image: '/faq/interactive-brokers/step-1-language.png',
    alt: 'Меню Interactive Brokers с выбором английского языка кабинета',
  },
  {
    title: 'Откройте Activity Statement',
    text: 'В верхнем меню выберите Performance & Reports, затем Statements и Activity Statement.',
    image: '/faq/interactive-brokers/step-2-statements.png',
    alt: 'Меню Performance & Reports со ссылкой на Statements и Activity Statement',
  },
  {
    title: 'Скачайте Annual CSV за каждый год',
    text: 'В форме Activity Statement укажите Period: Annual, выберите нужный год и нажмите Download CSV. Повторите для каждого года существования счёта.',
    image: '/faq/interactive-brokers/step-3-annual-csv.png',
    alt: 'Форма Activity Statement с выбранным периодом Annual и кнопкой Download CSV',
  },
  {
    title: 'Загрузите файлы в QCM Tax 270',
    text: 'Вернитесь на главную страницу, добавьте все скачанные CSV в блок Interactive Brokers и продолжите расчёт.',
    image: '/faq/interactive-brokers/step-4-upload.png',
    alt: 'Блок Interactive Brokers на странице загрузки QCM Tax 270',
  },
] as const

export default function InteractiveBrokersGuidePage() {
  return (
    <div className="min-h-screen bg-background">
      <FaqHeader article />
      <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
        <Link href="/faq" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" aria-hidden="true" />Все инструкции</Link>

        <header className="mt-6 max-w-3xl">
          <p className="text-sm font-semibold text-primary">INTERACTIVE BROKERS</p>
          <h1 className="mt-2 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">Как скачать отчёты для Interactive Brokers</h1>
          <p className="mt-3 text-pretty leading-relaxed text-muted-foreground">Для расчёта нужны отчёты <span className="font-medium text-foreground">CSV</span> на английском языке за все годы существования счёта.</p>
        </header>

        <Card className="mt-8 border-primary/20 bg-accent/25">
          <CardContent className="flex gap-3 pt-4"><FileSpreadsheet className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" /><p className="text-sm leading-relaxed">Скачивайте именно <span className="font-medium">Activity Statement</span> с периодом <span className="font-medium">Annual</span> и форматом <span className="font-medium">CSV</span>. PDF и HTML для загрузки не подходят.</p></CardContent>
        </Card>

        <ol className="mt-10 grid gap-8" aria-label="Шаги скачивания отчёта Interactive Brokers">
          {steps.map((step, index) => (
            <li key={step.title} className="grid gap-4 lg:grid-cols-[3rem_minmax(0,1fr)]">
              <span className="flex size-10 items-center justify-center rounded-full bg-primary font-mono text-sm font-semibold text-primary-foreground">{index + 1}</span>
              <Card className="border-border/80">
                <CardHeader>
                  <CardTitle className="text-lg">{step.title}</CardTitle>
                  <CardDescription className="leading-relaxed">{step.text}</CardDescription>
                </CardHeader>
                {index === 0 && <CardContent><a href="https://www.interactivebrokers.com" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm font-medium text-primary underline-offset-4 hover:underline">Открыть interactivebrokers.com <ExternalLink className="size-4" aria-hidden="true" /></a></CardContent>}
                {step.image && <CardContent><figure className="overflow-hidden rounded-lg border border-border bg-muted/20"><img src={step.image} alt={step.alt} className="h-auto w-full" /><figcaption className="border-t bg-card px-3 py-2 text-xs text-muted-foreground">Шаг {index + 1}</figcaption></figure></CardContent>}
              </Card>
            </li>
          ))}
        </ol>

        <Card className="mt-10 border-primary/25 bg-accent/25"><CardHeader><div className="flex size-10 items-center justify-center rounded-md bg-primary/10 text-primary"><UploadCloud aria-hidden="true" /></div><CardTitle>После скачивания</CardTitle><CardDescription>Убедитесь, что добавили CSV за все нужные годы, затем загрузите их одним или несколькими действиями в блок Interactive Brokers.</CardDescription></CardHeader><CardContent><Link href="/" className="inline-flex items-center gap-2 text-sm font-medium text-primary underline-offset-4 hover:underline">Перейти к загрузке отчётов <ArrowLeft className="size-4 rotate-180" aria-hidden="true" /></Link></CardContent></Card>
      </main>
    </div>
  )
}
