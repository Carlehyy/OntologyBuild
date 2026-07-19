import * as React from 'react'
import { AlertTriangle, Info, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from './Button'

interface ModalProps {
  open: boolean
  onClose: () => void
  title?: string
  description?: string
  children: React.ReactNode
  footer?: React.ReactNode
  size?: 'sm' | 'md' | 'lg' | 'xl' | '2xl' | '3xl'
  headerIcon?: React.ReactNode
  panelClassName?: string
  contentClassName?: string
}

const sizeMap = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-xl',
  '2xl': 'max-w-2xl',
  '3xl': 'max-w-3xl',
}

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = 'md',
  headerIcon,
  panelClassName,
  contentClassName,
}: ModalProps) {
  const titleId = React.useId()
  const descriptionId = React.useId()

  React.useEffect(() => {
    if (!open) return undefined
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose, open])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[var(--z-modal)] flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-slate-950/30 backdrop-blur-[2px] animate-fade-in"
        onClick={onClose}
        aria-hidden="true"
      />
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-describedby={description ? descriptionId : undefined}
        className={cn(
          'relative flex max-h-[calc(100dvh-2rem)] w-full flex-col overflow-hidden rounded-2xl border border-white/80 bg-white/95 shadow-[0_24px_72px_rgba(15,23,42,0.18)] backdrop-blur-xl animate-slide-up',
          sizeMap[size],
          panelClassName,
        )}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭弹窗"
          className="absolute right-4 top-4 z-10 flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"
        >
          <X size={16} />
        </button>

        {(title || description) && (
          <header className="flex shrink-0 items-start gap-3 px-6 pb-3 pt-5 pr-14">
            {headerIcon && (
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-600">
                {headerIcon}
              </div>
            )}
            <div className="min-w-0 pt-0.5">
              {title && (
                <h3 id={titleId} className="text-base font-semibold tracking-[-0.01em] text-slate-900">
                  {title}
                </h3>
              )}
              {description && (
                <p id={descriptionId} className="mt-1 text-sm leading-6 text-slate-500">
                  {description}
                </p>
              )}
            </div>
          </header>
        )}

        <div className={cn(
          'min-h-0 flex-1 overflow-y-auto px-6 py-4',
          !(title || description) && 'pt-6',
          contentClassName,
        )}>{children}</div>
        {footer && (
          <footer className="flex shrink-0 justify-end gap-2 border-t border-slate-100 bg-slate-50/70 px-6 py-4">
            {footer}
          </footer>
        )}
      </section>
    </div>
  )
}

interface ConfirmModalProps {
  open: boolean
  onClose: () => void
  onConfirm: () => void
  title: string
  description?: string
  confirmText?: string
  cancelText?: string
  variant?: 'danger' | 'default'
  loading?: boolean
}

export function ConfirmModal({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmText = '确认',
  cancelText = '取消',
  variant = 'default',
  loading,
}: ConfirmModalProps) {
  const danger = variant === 'danger'
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      headerIcon={danger
        ? <AlertTriangle size={19} className="text-red-600" />
        : <Info size={19} className="text-teal-600" />}
      footer={(
        <>
          <Button variant="outline" onClick={onClose} disabled={loading}>{cancelText}</Button>
          <Button
            variant={danger ? 'danger' : 'default'}
            onClick={onConfirm}
            loading={loading}
            className={danger ? 'shadow-sm shadow-red-900/10' : undefined}
          >
            {confirmText}
          </Button>
        </>
      )}
    >
      {description ? (
        <div className={cn(
          'rounded-xl border px-4 py-3 text-sm leading-6',
          danger
            ? 'border-red-100 bg-red-50/70 text-red-800'
            : 'border-teal-100 bg-teal-50/70 text-slate-600',
        )}>
          {description}
        </div>
      ) : <div />}
    </Modal>
  )
}
