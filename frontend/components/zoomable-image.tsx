'use client'

import { useEffect, useRef, useState } from 'react'
import { X, ZoomIn } from 'lucide-react'

interface ZoomableImageProps {
  src: string
  alt: string
}

export function ZoomableImage({ src, alt }: ZoomableImageProps) {
  const [isOpen, setIsOpen] = useState(false)
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!isOpen) return
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    const frame = window.requestAnimationFrame(() => closeButtonRef.current?.focus())
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', onKeyDown)
      window.cancelAnimationFrame(frame)
    }
  }, [isOpen])

  return <><button type="button" onClick={() => setIsOpen(true)} className="group relative block w-full cursor-zoom-in overflow-hidden focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50" aria-label={`Увеличить изображение: ${alt}`}><img src={src} alt={alt} className="h-auto w-full transition-transform duration-200 group-hover:scale-[1.01]" /><span className="pointer-events-none absolute right-3 top-3 flex size-8 items-center justify-center rounded-full bg-background/90 text-foreground shadow-sm"><ZoomIn className="size-4" aria-hidden="true" /></span></button>{isOpen && <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/80 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label="Увеличенное изображение" onMouseDown={() => setIsOpen(false)}><div className="relative max-h-full max-w-full" onMouseDown={(event) => event.stopPropagation()}><img src={src} alt={alt} className="max-h-[calc(100vh-2rem)] max-w-full rounded-lg object-contain shadow-2xl" /><button ref={closeButtonRef} type="button" onClick={() => setIsOpen(false)} className="absolute right-2 top-2 flex size-10 items-center justify-center rounded-full bg-background/90 text-foreground shadow-sm hover:bg-background focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring" aria-label="Закрыть увеличенное изображение"><X className="size-5" aria-hidden="true" /></button></div></div>}</>
}
