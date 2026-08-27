'use client'

import { ArrowLeft, Calculator, Info } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import type { AccountSelection, BrokerConfig, DiscoveredAccount, InvalidReportPeriod } from '@/lib/types'

interface DiscoveredAccountsProps {
  accounts: DiscoveredAccount[]
  brokers: BrokerConfig[]
  selections: Record<string, AccountSelection>
  busy: boolean
  error: string | null
  invalidReports: InvalidReportPeriod[]
  onSelectionChange: (account: DiscoveredAccount, field: keyof AccountSelection, value: boolean) => void
  onBack: () => void
  onProcess: () => void
}

export function accountKey(account: Pick<DiscoveredAccount, 'broker' | 'account_id'>) {
  return `${account.broker}:${account.account_id}`
}

export function DiscoveredAccounts({ accounts, brokers, selections, busy, error, invalidReports, onSelectionChange, onBack, onProcess }: DiscoveredAccountsProps) {
  const brokerNames = new Map(brokers.map((broker) => [broker.code, broker.display_name]))
  return <section className="mx-auto flex max-w-4xl flex-col gap-6 py-10" aria-labelledby="accounts-title">
    <header><p className="text-sm font-semibold text-primary">ШАГ 2</p><h1 id="accounts-title" className="mt-1 text-3xl font-semibold tracking-tight">Обнаруженные брокерские счета</h1><p className="mt-2 text-muted-foreground">Проверьте счета и выберите параметры, которые backend применит при формировании общего audit и формы 270.00.</p></header>

    {(error || invalidReports.length > 0) && <Alert variant="destructive"><Info /><AlertDescription>{error && <p className="font-medium">{error}</p>}{invalidReports.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-5">{invalidReports.map((report, index) => <li key={`${report.broker}:${report.report_name}:${index}`}>{brokerNames.get(report.broker) || report.broker}{report.account_id ? ` · ${report.account_id}` : ''} · {report.report_name} · окончание периода: {report.period_end || 'не определено'}</li>)}</ul>}</AlertDescription></Alert>}

    <div className="grid gap-4">
      {accounts.map((account) => {
        const selection = selections[accountKey(account)] || { joint: false, excluded: false }
        return <Card key={accountKey(account)}>
          <CardHeader><CardTitle className="text-lg">{brokerNames.get(account.broker) || account.broker} · <span className="font-mono">{account.account_id}</span></CardTitle><CardDescription>{account.report_count} {pluralReports(account.report_count)}</CardDescription></CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <label className="flex cursor-pointer items-start gap-3 rounded-md border p-4"><Checkbox checked={selection.joint} onCheckedChange={(value) => onSelectionChange(account, 'joint', Boolean(value))} /><span><span className="block text-sm font-medium">Совместный счёт</span><span className="mt-1 block text-xs text-muted-foreground">Выберите, если у вас совместный счёт с супругом(ой)/родственником.</span></span></label>
            <label className="flex cursor-pointer items-start gap-3 rounded-md border p-4"><Checkbox checked={selection.excluded} onCheckedChange={(value) => onSelectionChange(account, 'excluded', Boolean(value))} /><span><span className="block text-sm font-medium">Не включать в итоговую декларацию</span><span className="mt-1 block text-xs text-muted-foreground">Выберите, если не хотите, чтобы данный счёт учитывался при формировании налоговой декларации.</span></span></label>
          </CardContent>
        </Card>
      })}
    </div>

    <div className="flex flex-col-reverse gap-3 border-t pt-6 sm:flex-row sm:justify-between"><Button variant="outline" onClick={onBack} disabled={busy}><ArrowLeft data-icon="inline-start" />Назад к отчётам</Button><Button size="lg" onClick={onProcess} disabled={busy || accounts.length === 0}><Calculator data-icon="inline-start" />{busy ? 'Запускаем…' : 'Рассчитать'}</Button></div>
  </section>
}

function pluralReports(count: number) {
  if (count % 10 === 1 && count % 100 !== 11) return 'отчёт'
  if ([2, 3, 4].includes(count % 10) && ![12, 13, 14].includes(count % 100)) return 'отчёта'
  return 'отчётов'
}
