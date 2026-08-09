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
    iconClass: 'bg-[var(--color-success-bg)] text-[var(--color-success)] ring-[var(--color-success-bg)]',
    accentClass: 'bg-[var(--color-success)]',
  },
  error: {
    icon: AlertCircle,
    iconClass: 'bg-[var(--color-danger-bg)] text-[var(--color-danger)] ring-[var(--color-danger-bg)]',
    accentClass: 'bg-[var(--color-danger)]',
  },
  warning: {
    icon: TriangleAlert,
    iconClass: 'bg-[var(--color-warning-bg)] text-[var(--color-warning)] ring-[var(--color-warning-bg)]',
    accentClass: 'bg-[var(--color-warning)]',
  },
  info: {
    icon: Info,
    iconClass: 'bg-[var(--color-info-bg)] text-[var(--color-info)] ring-[var(--color-info-bg)]',
    accentClass: 'bg-[var(--color-info)]',
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
      className="pointer-events-auto relative w-[min(390px,calc(100vw-32px))] overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-popover)] p-4 shadow-[var(--shadow-lg)] animate-slide-up"
    >
      <span className={`absolute inset-y-3 left-0 w-1 rounded-r-full ${meta.accentClass}`} />
      <div className="flex items-start gap-3">
        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ring-1 ${meta.iconClass}`}>
          <Icon size={17} />
        </span>
        <div className="min-w-0 flex-1 pt-0.5">
          <p className="text-sm font-semibold text-[var(--color-text-primary)]">{item.title}</p>
          {item.description && <p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">{item.description}</p>}
        </div>
        <button
          type="button"
          onClick={() => onDismiss(item.id)}
          aria-label="关闭提示"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
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
