import Link from 'next/link'
import { ArrowLeft } from 'lucide-react'

interface FaqHeaderProps {
  article?: boolean
}

export function FaqHeader({ article = false }: FaqHeaderProps) {
  return (
    <header className="border-b bg-card">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
        <Link className="flex items-center gap-3 text-left" href="/" aria-label="Перейти к расчёту">
          <img
            src="https://hebbkx1anhila5yf.public.blob.vercel-storage.com/logo%20square%20transparent-ebQXbj2TGuwVhsyCHv97JFCPKRieg0.png"
            alt=""
            className="size-10 object-contain"
          />
          <span className="flex flex-col">
            <span className="font-sans text-lg font-semibold leading-tight tracking-tight">QCM Tax 270</span>
            <span className="text-xs font-normal text-muted-foreground">by Quantum Cross Management</span>
          </span>
        </Link>
        <nav className="hidden items-center gap-6 text-sm md:flex" aria-label="Основная навигация">
          <Link className="border-b-2 border-transparent py-5 text-muted-foreground hover:text-foreground" href="/">Расчёт</Link>
          <Link className="border-b-2 border-primary py-5 font-semibold text-primary" href="/faq" aria-current="page">FAQ</Link>
        </nav>
        <Link href={article ? '/faq' : '/'} className="flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground md:hidden" aria-label={article ? 'Вернуться к FAQ' : 'Вернуться к расчёту'}><ArrowLeft className="size-4" /></Link>
      </div>
    </header>
  )
}
