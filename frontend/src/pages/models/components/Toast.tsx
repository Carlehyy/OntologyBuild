import { useEffect, useState } from 'react'
import { CheckCircle2, XCircle, AlertCircle, Info, X } from 'lucide-react'

export type ToastType = 'success' | 'error' | 'warning' | 'info'

export interface ToastItem {
  id: string
  type: ToastType
  message: string
  duration?: number
}

interface ToastProps {
  toasts: ToastItem[]
  onRemove: (id: string) => void
}

const iconMap = {
  success: CheckCircle2,
  error: XCircle,
  warning: AlertCircle,
  info: Info,
}

const colorMap = {
  success: {
    bg: 'bg-[var(--color-success-bg)]',
    border: 'border-[color-mix(in_srgb,var(--color-success)_35%,transparent)]',
    text: 'text-[var(--color-success)]',
    icon: 'text-[var(--color-success)]',
    progress: 'bg-[var(--color-success-bg)]',
  },
  error: {
    bg: 'bg-[var(--color-danger-bg)]',
    border: 'border-[color-mix(in_srgb,var(--color-danger)_35%,transparent)]',
    text: 'text-[var(--color-danger)]',
    icon: 'text-[var(--color-danger)]',
    progress: 'bg-[var(--color-danger-bg)]',
  },
  warning: {
    bg: 'bg-[var(--color-warning-bg)]',
    border: 'border-[color-mix(in_srgb,var(--color-warning)_35%,transparent)]',
    text: 'text-[var(--color-warning)]',
    icon: 'text-[var(--color-warning)]',
    progress: 'bg-[var(--color-warning-bg)]',
  },
  info: {
    bg: 'bg-[var(--color-info-bg)]',
    border: 'border-[color-mix(in_srgb,var(--color-info)_35%,transparent)]',
    text: 'text-[var(--color-info)]',
    icon: 'text-[var(--color-info)]',
    progress: 'bg-[var(--color-info-bg)]',
  },
}

function ToastItemComponent({ toast, onRemove }: { toast: ToastItem; onRemove: (id: string) => void }) {
  const [progress, setProgress] = useState(100)
  const duration = toast.duration || 3000
  const colors = colorMap[toast.type]
  const Icon = iconMap[toast.type]

  useEffect(() => {
    const startTime = Date.now()
    const interval = setInterval(() => {
      const elapsed = Date.now() - startTime
      const remaining = Math.max(0, 100 - (elapsed / duration) * 100)
      setProgress(remaining)
      if (remaining <= 0) {
        clearInterval(interval)
        onRemove(toast.id)
      }
    }, 50)
    return () => clearInterval(interval)
  }, [toast.id, duration, onRemove])

  return (
    <div
      className={`relative flex items-center gap-3 px-5 py-4 rounded-xl border shadow-lg overflow-hidden min-w-[320px] max-w-[480px] ${colors.bg} ${colors.border} animate-slide-up`}
      style={{
        animation: 'slideUp 0.3s ease-out, fadeIn 0.3s ease-out',
      }}
    >
      <Icon size={20} className={`shrink-0 ${colors.icon}`} />
      <span className={`text-sm font-medium ${colors.text} flex-1`}>{toast.message}</span>
      <button
        onClick={() => onRemove(toast.id)}
        className={`shrink-0 p-1 rounded-lg hover:bg-[var(--color-bg-overlay)] transition-colors ${colors.text}`}
      >
        <X size={14} />
      </button>
      {/* Progress bar */}
      <div className="absolute bottom-0 left-0 right-0 h-[2px] rounded-b-xl overflow-hidden">
        <div
          className={`h-full ${colors.progress} transition-all duration-100 ease-linear`}
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  )
}

export default function ToastContainer({ toasts, onRemove }: ToastProps) {
  if (toasts.length === 0) return null

  return (
    <div className="fixed inset-0 z-[9999] pointer-events-none flex items-center justify-center">
      <div className="flex flex-col gap-3 pointer-events-auto">
        {toasts.map((toast) => (
          <ToastItemComponent key={toast.id} toast={toast} onRemove={onRemove} />
        ))}
      </div>
    </div>
  )
}

// Hook for using toast
let toastCallback: ((toast: Omit<ToastItem, 'id'>) => void) | null = null

export function setToastCallback(cb: typeof toastCallback) {
  toastCallback = cb
}

export function showToast(toast: Omit<ToastItem, 'id'>) {
  if (toastCallback) {
    toastCallback(toast)
  }
}
