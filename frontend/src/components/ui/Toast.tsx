import * as React from 'react'
import { AlertCircle, CheckCircle2, Info, TriangleAlert, X } from 'lucide-react'

export type ToastTone = 'success' | 'error' | 'warning' | 'info'

export interface ToastInput {
  tone: ToastTone
  title: string
  description?: string
  duration?: number
}

interface ToastRecord extends ToastInput {
  id: number
}

interface ToastContextValue {
  toast: (input: ToastInput) => void
}

const ToastContext = React.createContext<ToastContextValue | null>(null)

const toneMeta = {
  success: {
    icon: CheckCircle2,
    iconClass: 'bg-emerald-50 text-emerald-600 ring-emerald-100',
    accentClass: 'bg-emerald-500',
  },
  error: {
    icon: AlertCircle,
    iconClass: 'bg-red-50 text-red-600 ring-red-100',
    accentClass: 'bg-red-500',
  },
  warning: {
    icon: TriangleAlert,
    iconClass: 'bg-amber-50 text-amber-600 ring-amber-100',
    accentClass: 'bg-amber-500',
  },
  info: {
    icon: Info,
    iconClass: 'bg-sky-50 text-sky-600 ring-sky-100',
    accentClass: 'bg-sky-500',
  },
} satisfies Record<ToastTone, { icon: React.ElementType; iconClass: string; accentClass: string }>

function ToastCard({ item, onDismiss }: { item: ToastRecord; onDismiss: (id: number) => void }) {
  const meta = toneMeta[item.tone]
  const Icon = meta.icon

  React.useEffect(() => {
    const timer = window.setTimeout(() => onDismiss(item.id), item.duration ?? (item.tone === 'error' ? 6000 : 3600))
    return () => window.clearTimeout(timer)
  }, [item.duration, item.id, item.tone, onDismiss])

  return (
    <div
      role={item.tone === 'error' ? 'alert' : 'status'}
      className="pointer-events-auto relative w-[min(390px,calc(100vw-32px))] overflow-hidden rounded-2xl border border-white/90 bg-white/95 p-4 shadow-[0_18px_52px_rgba(15,23,42,0.16)] backdrop-blur-xl animate-slide-up"
    >
      <span className={`absolute inset-y-3 left-0 w-1 rounded-r-full ${meta.accentClass}`} />
      <div className="flex items-start gap-3">
        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ring-1 ${meta.iconClass}`}>
          <Icon size={17} />
        </span>
        <div className="min-w-0 flex-1 pt-0.5">
          <p className="text-sm font-semibold text-slate-900">{item.title}</p>
          {item.description && <p className="mt-1 text-xs leading-5 text-slate-500">{item.description}</p>}
        </div>
        <button
          type="button"
          onClick={() => onDismiss(item.id)}
          aria-label="关闭提示"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  )
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = React.useState<ToastRecord[]>([])
  const nextId = React.useRef(0)

  const dismiss = React.useCallback((id: number) => {
    setItems(current => current.filter(item => item.id !== id))
  }, [])

  const toast = React.useCallback((input: ToastInput) => {
    const record = { ...input, id: ++nextId.current }
    setItems(current => [...current.slice(-2), record])
  }, [])

  const value = React.useMemo(() => ({ toast }), [toast])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        aria-atomic="false"
        className="pointer-events-none fixed bottom-5 right-5 z-[var(--z-toast)] flex flex-col items-end gap-3"
      >
        {items.map(item => <ToastCard key={item.id} item={item} onDismiss={dismiss} />)}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const context = React.useContext(ToastContext)
  if (!context) throw new Error('useToast must be used inside ToastProvider')
  return context
}
