import { ArrowLeft, EyeOff, FileClock, ShieldCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export function PrivacyView({ onBack }: { onBack: () => void }) {
  const items = [
    { icon: FileClock, title: 'Временная обработка', text: 'Загруженные файлы предназначены только для выполнения текущей обработки и не предполагают постоянного хранения.' },
    { icon: EyeOff, title: 'Обезличивание', text: 'Перед загрузкой вы можете удалить или скрыть персональные данные, которые не нужны для обработки операций.' },
    { icon: ShieldCheck, title: 'Минимум данных', text: 'Сервис не должен запрашивать лишнюю персональную информацию, не связанную с подготовкой данных формы.' },
  ]
  return <main className="mx-auto min-h-[calc(100vh-4rem)] max-w-4xl px-4 py-12 sm:px-6"><Button variant="ghost" onClick={onBack}><ArrowLeft data-icon="inline-start" />Вернуться к расчёту</Button><div className="mt-8"><p className="text-sm font-semibold text-primary">КОНФИДЕНЦИАЛЬНОСТЬ</p><h1 className="mt-2 text-balance text-3xl font-semibold tracking-tight">Как обрабатываются ваши файлы</h1><p className="mt-3 max-w-2xl text-pretty text-muted-foreground">Краткая информация о предполагаемом подходе к данным. Этот раздел будет дополнен юридическими документами перед запуском сервиса.</p></div><div className="mt-8 grid gap-4 md:grid-cols-3">{items.map(({ icon: Icon, title, text }) => <Card key={title} className="border-primary/15 bg-accent/30"><CardHeader><span className="flex size-10 items-center justify-center rounded-md bg-secondary text-secondary-foreground"><Icon aria-hidden="true" /></span><CardTitle>{title}</CardTitle></CardHeader><CardContent><CardDescription className="leading-relaxed">{text}</CardDescription></CardContent></Card>)}</div></main>
}
