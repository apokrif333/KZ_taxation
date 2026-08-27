import { ArrowLeft, EyeOff, FileClock, ShieldCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export function PrivacyView({ onBack }: { onBack: () => void }) {
  const items = [
    { icon: FileClock, title: 'Временная обработка', text: 'Если вы загрузили файлы в окно браузера, но забыли его закрыть, то через час мы автоматически удалим ваши файлы с сервера, чтобы не хранить персональные данные.' },
    { icon: EyeOff, title: 'Обезличивание', text: 'Перед загрузкой вы можете удалить или скрыть персональные данные из брокерских отчётов: ФИО, номер паспорта, удостоверение личности и т. д. Эта информация не нужна для расчётов.' },
    { icon: ShieldCheck, title: 'Минимум данных', text: 'Загруженные файлы существуют на нашем сервере, пока вы выполняете работу с ними: загрузили их в окно браузера, ждёте завершения расчёта. Как только расчёт закончен, ваши файлы автоматически удаляются.' },
  ]
  return <main className="mx-auto min-h-[calc(100vh-4rem)] max-w-4xl px-4 py-12 sm:px-6"><Button variant="ghost" onClick={onBack}><ArrowLeft data-icon="inline-start" />Вернуться к расчёту</Button><div className="mt-8"><p className="text-sm font-semibold text-primary">КОНФИДЕНЦИАЛЬНОСТЬ</p><h1 className="mt-2 text-balance text-3xl font-semibold tracking-tight">Как обрабатываются ваши файлы</h1><p className="mt-3 max-w-2xl text-pretty text-muted-foreground">Краткая информация о работе с загруженными данными.</p></div><div className="mt-8 grid gap-4 md:grid-cols-3">{items.map(({ icon: Icon, title, text }) => <Card key={title} className="border-primary/15 bg-accent/30"><CardHeader><span className="flex size-10 items-center justify-center rounded-md bg-secondary text-secondary-foreground"><Icon aria-hidden="true" /></span><CardTitle>{title}</CardTitle></CardHeader><CardContent><CardDescription className="leading-relaxed">{text}</CardDescription></CardContent></Card>)}</div></main>
}
