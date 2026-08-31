'use client'

import Link from 'next/link'
import { Menu } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface SiteHeaderProps {
  onPrivacy: () => void
  onStart: () => void
  privacyActive: boolean
}

export function SiteHeader({ onPrivacy, onStart, privacyActive }: SiteHeaderProps) {
  return (
    <header className="border-b bg-card">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
        <button className="flex items-center gap-3 text-left" onClick={onStart} aria-label="Перейти к расчёту">
          <img
            src="https://hebbkx1anhila5yf.public.blob.vercel-storage.com/logo%20square%20transparent-ebQXbj2TGuwVhsyCHv97JFCPKRieg0.png"
            alt=""
            className="size-10 object-contain"
          />
          <span className="flex flex-col">
            <span className="font-sans text-lg font-semibold leading-tight tracking-tight">QCM Tax 270</span>
            <span className="text-xs font-normal text-muted-foreground">by Quantum Cross Management</span>
          </span>
        </button>
        <nav className="hidden items-center gap-6 text-sm md:flex" aria-label="Основная навигация">
          <button className={cn('border-b-2 py-5', privacyActive ? 'border-transparent text-muted-foreground hover:text-foreground' : 'border-primary font-semibold text-primary')} onClick={onStart} aria-current={privacyActive ? undefined : 'page'}>Расчёт</button>
          <Link className="text-muted-foreground hover:text-foreground" href="/faq">FAQ</Link>
          <button className={cn('border-b-2 py-5', privacyActive ? 'border-primary font-semibold text-primary' : 'border-transparent text-muted-foreground hover:text-foreground')} onClick={onPrivacy} aria-current={privacyActive ? 'page' : undefined}>Конфиденциальность</button>
        </nav>
        <Button variant="ghost" size="icon" className="md:hidden" aria-label="Открыть меню"><Menu /></Button>
      </div>
    </header>
  )
}
