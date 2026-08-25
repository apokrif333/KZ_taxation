'use client'

import { Menu } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface SiteHeaderProps {
  onPrivacy: () => void
  onStart: () => void
}

export function SiteHeader({ onPrivacy, onStart }: SiteHeaderProps) {
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
          <button className="border-b-2 border-primary py-5 font-semibold text-primary" onClick={onStart}>Расчёт</button>
          <a className="text-muted-foreground hover:text-foreground" href="#how-it-works">Как это работает</a>
          <button className="text-muted-foreground hover:text-foreground" onClick={onPrivacy}>Конфиденциальность</button>
        </nav>
        <Button variant="ghost" size="icon" className="md:hidden" aria-label="Открыть меню"><Menu /></Button>
      </div>
    </header>
  )
}
