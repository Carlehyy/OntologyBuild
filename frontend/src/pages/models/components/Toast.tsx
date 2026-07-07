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
    bg: 'bg-emerald-50',
    border: 'border-emerald-200',
    text: 'text-emerald-700',
    icon: 'text-emerald-500',
    progress: 'bg-emerald-400',
  },
  error: {
    bg: 'bg-red-50',
    border: 'border-red-200',
    text: 'text-red-700',
    icon: 'text-red-500',
    progress: 'bg-red-400',
  },
  warning: {
    bg: 'bg-amber-50',
    border: 'border-amber-200',
    text: 'text-amber-700',
    icon: 'text-amber-500',
    progress: 'bg-amber-400',
  },
  info: {
    bg: 'bg-blue-50',
    border: 'border-blue-200',
    text: 'text-blue-700',
    icon: 'text-blue-500',
    progress: 'bg-blue-400',
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
      className={`relative flex items-center gap-3 px-5 py-4 rounded-xl border shadow-lg backdrop-blur-sm min-w-[320px] max-w-[480px] ${colors.bg} ${colors.border} animate-slide-up`}
      style={{
        animation: 'slideUp 0.3s ease-out, fadeIn 0.3s ease-out',
      }}
    >
      <Icon size={20} className={`shrink-0 ${colors.icon}`} />
      <span className={`text-sm font-medium ${colors.text} flex-1`}>{toast.message}</span>
      <button
        onClick={() => onRemove(toast.id)}
        className={`shrink-0 p-1 rounded-lg hover:bg-black/5 transition-colors ${colors.text}`}
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
