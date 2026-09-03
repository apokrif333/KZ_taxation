import type { Metadata } from 'next'
import Link from 'next/link'
import { AlertTriangle, ArrowLeft, ExternalLink, FileJson, Landmark, Send, WalletCards } from 'lucide-react'
import { FaqHeader } from '@/components/faq-header'
import { ZoomableImage } from '@/components/zoomable-image'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const metadata: Metadata = {
  title: 'Загрузка JSON формы 270.00 и оплата налога — QCM Tax 270',
  description: 'Как проверить данные КГД, загрузить JSON формы 270.00, отправить декларацию и оплатить ИПН.',
}

function GuideImage({ src, alt, caption }: { src: string; alt: string; caption: string }) {
  return <figure className="overflow-hidden rounded-lg border border-border bg-muted/20"><ZoomableImage src={src} alt={alt} /><figcaption className="border-t bg-card px-3 py-2 text-xs text-muted-foreground">{caption}</figcaption></figure>
}

export default function Form270UploadPage() {
  return (
    <div className="min-h-screen bg-background">
      <FaqHeader article />
      <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
        <Link href="/faq" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" aria-hidden="true" />Все материалы FAQ</Link>

        <header className="mt-6 max-w-3xl">
          <p className="text-sm font-semibold text-primary">НАЛОГИ И ЗАКОНЫ</p>
          <h1 className="mt-2 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">Загрузка JSON формы 270.00 и оплата налога</h1>
          <p className="mt-3 text-pretty leading-relaxed text-muted-foreground">Пошаговая инструкция: проверка предзаполненных сведений КГД, загрузка JSON, отправка декларации и оплата ИПН.</p>
        </header>

        <Alert className="mt-8 border-amber-500/35 bg-amber-500/10 text-foreground"><AlertTriangle className="text-amber-700 dark:text-amber-400" /><AlertDescription className="text-sm leading-relaxed">Перед отправкой декларации проверьте данные, которые КГД уже добавил в форму. JSON из сервиса заполняет расчёт по брокерским отчётам, но не заменяет сведения о другом имуществе, доходах или обязательствах.</AlertDescription></Alert>

        <section className="mt-10" aria-labelledby="check-data-title">
          <div className="flex items-center gap-2"><Landmark className="size-5 text-primary" aria-hidden="true" /><h2 id="check-data-title" className="text-2xl font-semibold tracking-tight">1. Проверьте имеющиеся данные КГД</h2></div>
          <Card className="mt-4 border-border/80"><CardContent className="pt-6"><ol className="list-decimal space-y-3 pl-5 text-sm leading-relaxed text-muted-foreground"><li>Откройте <a href="https://knp.kgd.gov.kz" target="_blank" rel="noreferrer" className="font-medium text-primary underline-offset-4 hover:underline">Кабинет налогоплательщика КГД <ExternalLink className="inline-block size-3.5" aria-hidden="true" /></a> и выберите <strong className="text-foreground">«Подать документ»</strong>.<div className="mt-4"><GuideImage src="/faq/form270-upload/step-7-submitted.png" alt="Раздел «Мои документы» КНП с принятыми декларациями 270.00" caption="Раздел «Мои документы» в кабинете КГД." /></div></li><li>Найдите форму 270.00 «Декларация о доходах и имуществе физического лица». Если формы нет в списке, возможно вы являетесь ИП и вам нужно скорректировать ваши доступы к налоговым формам, позвонив на 1414.</li><li>Выберите налоговый год, нажмите <strong className="text-foreground">«Подать»</strong>, затем <strong className="text-foreground">«Предзаполнить декларацию»</strong>.<div className="mt-4"><GuideImage src="/faq/form270-upload/step-2-prepopulate.png" alt="Окно формы 270.00 с выбором года и кнопкой загрузки JSON" caption="Выберите год и нажмите «Подать» или «Предзаполнить декларацию»." /></div></li><li>Пролистайте форму до конца, но пока не отправляйте её. Запишите поля, которые КГД уже заполнил: доходы, активы, имущество, операции или задолженности. Их нужно будет дополнить после загрузки JSON.</li></ol></CardContent></Card>
          <div className="mt-4 grid gap-4 lg:grid-cols-2"><GuideImage src="/faq/form270-upload/step-3-prefilled-data.png" alt="Страница формы 270.00 с предзаполненными полями" caption="Пример предзаполненных сведений в форме." /><GuideImage src="/faq/form270-upload/step-4-prefilled-data-details.png" alt="Раздел C формы 270.00 с предзаполненными сведениями об имуществе" caption="Пример данных об имуществе, которые потребуется сохранить в декларации." /></div>
        </section>

        <section className="mt-12" aria-labelledby="upload-title">
          <div className="flex items-center gap-2"><FileJson className="size-5 text-primary" aria-hidden="true" /><h2 id="upload-title" className="text-2xl font-semibold tracking-tight">2. Загрузите JSON и отправьте декларацию</h2></div>
          <div className="mt-4 grid gap-4">
            <Card className="border-border/80"><CardHeader><CardTitle className="text-lg">Загрузите файл</CardTitle><CardDescription className="leading-relaxed">Вернитесь в КНП: «Подать документ» → форма 270.00 → выберите налоговый год → «Загрузить (JSON)». Выберите JSON-файл, который сформировал сервис.</CardDescription></CardHeader></Card>
            <Card className="border-border/80"><CardHeader><CardTitle className="text-lg">Не предзаполняйте декларацию повторно</CardTitle><CardDescription className="leading-relaxed">На вопрос «Предзаполнить декларацию?» выберите «Нет», чтобы загрузить подготовленный JSON без повторного предзаполнения.</CardDescription></CardHeader><CardContent><GuideImage src="/faq/form270-upload/step-5-decline-prepopulate.png" alt="Диалог предзаполнения формы 270.00 с выбранной кнопкой «Нет»" caption="После загрузки JSON выберите «Нет»." /></CardContent></Card>
            <Card className="border-border/80"><CardHeader><CardTitle className="text-lg">Проверьте налог к уплате и УГД</CardTitle><CardDescription className="leading-relaxed">В приложении 270.01 найдите строку K — это сумма налога к уплате. Запомните её, дополните сведения, выявленные на первом шаге, затем в конце формы проверьте УГД по месту прописки. Нажмите «Сформировать», подпишите и отправьте декларацию.</CardDescription></CardHeader><CardContent><GuideImage src="/faq/form270-upload/step-6-tax-payable.png" alt="Строка K приложения 270.01 с суммой индивидуального подоходного налога к уплате" caption="Сумма налога к уплате указана в строке K приложения 270.01." /></CardContent></Card>
            <Card className="border-primary/20 bg-accent/20"><CardContent className="flex gap-4 pt-5"><Send className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" /><p className="text-sm leading-relaxed text-muted-foreground">После отправки проверьте, что декларация появилась в разделе «Мои документы» и имеет статус принятой.</p></CardContent></Card>
          </div>
        </section>

        <section className="mt-12" aria-labelledby="payment-title">
          <div className="flex items-center gap-2"><WalletCards className="size-5 text-primary" aria-hidden="true" /><h2 id="payment-title" className="text-2xl font-semibold tracking-tight">3. Оплатите налог</h2></div>
          <Card className="mt-4 border-border/80"><CardHeader><CardTitle className="text-lg">Варианты оплаты</CardTitle><CardDescription>Налог можно оплатить <a href="https://kgd.gov.kz/ru/app/pshep-payment-web" target="_blank" rel="noreferrer" className="font-medium text-primary underline-offset-4 hover:underline">через шлюз КГД <ExternalLink className="inline-block size-3.5" aria-hidden="true" /></a>, приложение e-Salyq Azamat, мобильные приложения банков и другими доступными способами.</CardDescription></CardHeader><CardContent className="text-sm leading-relaxed text-muted-foreground"><p className="font-medium text-foreground">Пример оплаты через Kaspi:</p><ol className="mt-3 list-decimal space-y-2 pl-5"><li>В поиске выберите «Налог для физ. лиц по реквизитам».</li><li>Укажите тип платежа <strong className="text-foreground">101202 «ИПН с доходов, не облагаемых у источника выплаты»</strong>.</li><li>Выберите УГД по месту прописки, укажите ФИО и ИИН.</li><li>Оплатите сумму из строки K приложения 270.01.</li></ol></CardContent></Card>
        </section>

        <Card className="mt-10 border-primary/25 bg-accent/25"><CardContent className="pt-5 text-sm leading-relaxed text-muted-foreground">Окончательная налоговая обязанность зависит от конкретной ситуации налогоплательщика. Перед отправкой декларации и оплатой налога проверьте введённые сведения и реквизиты платежа.</CardContent></Card>
      </main>
    </div>
  )
}
