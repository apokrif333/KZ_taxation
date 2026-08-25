'use client'

import { useRef, useState } from 'react'
import { CheckCircle2, FileSpreadsheet, Info, LockKeyhole, Trash2, UploadCloud } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cn } from '@/lib/utils'
import type { Broker, UploadedReport } from '@/lib/types'

const brokers: Broker[] = ['Interactive Brokers', 'Freedom Broker', 'Exante', 'Tsifra Broker', 'Tabys']
const years = ['2025', '2024', '2023']

interface UploadWorkflowProps {
  onProcess: (data: { broker: Broker; files: UploadedReport[]; taxYear: string; jointAccount: boolean }) => void
}

export function UploadWorkflow({ onProcess }: UploadWorkflowProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [broker, setBroker] = useState<Broker>('Interactive Brokers')
  const [files, setFiles] = useState<UploadedReport[]>([])
  const [taxYear, setTaxYear] = useState('2025')
  const [jointAccount, setJointAccount] = useState(false)
  const [dragging, setDragging] = useState(false)

  const addFiles = (list: FileList | null) => {
    if (!list) return
    const incoming = Array.from(list).map((file) => {
      const valid = /\.(csv|xlsx|xls|pdf|html?)$/i.test(file.name)
      return { id: crypto.randomUUID(), name: file.name, size: file.size, status: valid ? 'valid' as const : 'invalid' as const, error: valid ? undefined : 'Неподдерживаемый формат файла' }
    })
    setFiles((current) => [...current, ...incoming])
  }

  const removeFile = (id: string) => setFiles((current) => current.filter((file) => file.id !== id))
  const canSubmit = files.some((file) => file.status === 'valid')

  return (
    <section aria-labelledby="calculation-title" className="grid gap-6 lg:grid-cols-[1fr_19rem]">
      <Card className="border-border/80 shadow-sm">
        <CardHeader className="border-b">
          <div className="flex items-center gap-3"><span className="flex size-7 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">1</span><div><CardTitle id="calculation-title">Выберите брокера</CardTitle><CardDescription>Укажите источник загружаемого отчёта.</CardDescription></div></div>
        </CardHeader>
        <CardContent className="flex flex-col gap-7">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5" role="radiogroup" aria-label="Брокер">
            {brokers.map((item) => (
              <button key={item} role="radio" aria-checked={broker === item} onClick={() => setBroker(item)} className={cn('min-h-16 rounded-md border px-3 py-2 text-left text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring', broker === item ? 'border-primary bg-accent/60 text-primary shadow-sm ring-1 ring-primary/15' : 'bg-background text-muted-foreground hover:border-primary/40 hover:bg-muted')}>
                {item}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-3"><span className="flex size-7 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">2</span><div><h2 className="font-semibold">Загрузите брокерский отчёт</h2><p className="text-sm text-muted-foreground">Можно добавить несколько файлов.</p></div></div>
          <div onDragOver={(event) => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); addFiles(event.dataTransfer.files) }} className={cn('flex min-h-52 flex-col items-center justify-center gap-4 rounded-lg border border-dashed p-6 text-center transition-colors', dragging ? 'border-primary bg-accent/50 ring-2 ring-primary/10' : 'border-input bg-muted/40 hover:border-primary/50 hover:bg-accent/20')}>
            <span className="flex size-12 items-center justify-center rounded-full bg-background text-primary shadow-sm"><UploadCloud aria-hidden="true" /></span>
            <div><p className="font-semibold">Перетащите файлы сюда</p><p className="mt-1 text-sm text-muted-foreground">или выберите их на компьютере</p></div>
            <input ref={inputRef} className="sr-only" type="file" multiple accept=".csv,.xlsx,.xls,.pdf,.html,.htm" onChange={(event) => addFiles(event.target.files)} aria-label="Выбрать брокерские отчёты" />
            <Button variant="outline" onClick={() => inputRef.current?.click()}>Выбрать файлы</Button>
            <p className="text-xs text-muted-foreground">CSV, XLSX, XLS, PDF или HTML</p>
          </div>

          {files.length > 0 && <div className="flex flex-col gap-2" aria-live="polite">{files.map((file) => <div key={file.id} className="flex items-center gap-3 rounded-md border bg-background p-3"><FileSpreadsheet className="text-muted-foreground" aria-hidden="true" /><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{file.name}</p><p className="text-xs text-muted-foreground">{formatBytes(file.size)}</p></div><span className={cn('flex items-center gap-1 text-xs font-medium', file.status === 'valid' ? 'text-primary' : 'text-destructive')}>{file.status === 'valid' ? <><CheckCircle2 aria-hidden="true" />Проверен</> : file.error}</span><Button variant="ghost" size="icon-sm" onClick={() => removeFile(file.id)} aria-label={`Удалить ${file.name}`}><Trash2 /></Button></div>)}</div>}

          <Alert className="border-secondary-foreground/20 bg-secondary/45 text-secondary-foreground"><LockKeyhole /><AlertDescription><span className="font-semibold">Файлы используются только для выполнения расчёта</span> и не сохраняются после завершения обработки. При необходимости вы можете предварительно обезличить брокерский отчёт.</AlertDescription></Alert>
        </CardContent>
      </Card>

      <aside>
        <Card className="sticky top-6 border-border bg-card shadow-md shadow-primary/5">
          <CardHeader><div className="flex items-center gap-3"><span className="flex size-7 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">3</span><CardTitle>Параметры</CardTitle></div></CardHeader>
          <CardContent>
            <FieldGroup>
              <Field><FieldLabel htmlFor="tax-year">Налоговый год</FieldLabel><Select value={taxYear} onValueChange={(value) => setTaxYear(value ?? '2025')} items={years.map((year) => ({ label: year, value: year }))}><SelectTrigger id="tax-year" className="w-full"><SelectValue /></SelectTrigger><SelectContent><SelectGroup>{years.map((year) => <SelectItem key={year} value={year}>{year}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>
              <Field orientation="horizontal"><Checkbox id="joint-account" checked={jointAccount} onCheckedChange={(value) => setJointAccount(Boolean(value))} /><FieldContent><FieldLabel htmlFor="joint-account">Совместный брокерский счёт</FieldLabel><FieldDescription>Суммы, относящиеся к налогоплательщику, могут потребовать пропорционального распределения.</FieldDescription></FieldContent></Field>
            </FieldGroup>
          </CardContent>
          <CardContent className="border-t pt-5"><Button className="w-full shadow-sm shadow-primary/20" size="lg" disabled={!canSubmit} onClick={() => onProcess({ broker, files, taxYear, jointAccount })}>Обработать отчёт</Button>{!canSubmit && <p className="mt-3 flex items-start gap-2 text-xs text-muted-foreground"><Info aria-hidden="true" />Добавьте хотя бы один поддерживаемый файл.</p>}</CardContent>
        </Card>
      </aside>
    </section>
  )
}

function formatBytes(bytes: number) {
  if (!bytes) return '0 КБ'
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} МБ` : `${Math.ceil(bytes / 1024)} КБ`
}
