import * as React from "react"
import { cn } from "@/lib/utils"
import { Button } from "./Button"

interface ModalProps {
  open: boolean
  onClose: () => void
  title?: string
  description?: string
  children: React.ReactNode
  footer?: React.ReactNode
  size?: "sm" | "md" | "lg" | "xl" | "2xl" | "3xl"
}

const sizeMap = {
  sm: "max-w-sm",
  md: "max-w-md",
  lg: "max-w-lg",
  xl: "max-w-xl",
  "2xl": "max-w-2xl",
  "3xl": "max-w-3xl",
}

export function Modal({ open, onClose, title, description, children, footer, size = "md" }: ModalProps) {
  if (!open) return null

  return (
    <div className="fixed inset-0 z-[var(--z-modal)] flex items-center justify-center">
      <div className="absolute inset-0 bg-[var(--color-bg-overlay)] transition-opacity" onClick={onClose} />
      <div className={cn("relative w-full mx-4 bg-[var(--color-bg-elevated)] rounded-lg shadow-lg border border-[var(--color-border)]", sizeMap[size])}>
        {(title || description) && (
          <div className="px-6 pt-5 pb-2">
            {title && <h3 className="text-lg font-semibold text-[var(--color-text-primary)]">{title}</h3>}
            {description && <p className="mt-1 text-sm text-[var(--color-text-secondary)]">{description}</p>}
          </div>
        )}
        <div className="px-6 py-4">{children}</div>
        {footer && <div className="px-6 pb-5 pt-2 flex justify-end gap-2">{footer}</div>}
      </div>
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
  variant?: "danger" | "default"
  loading?: boolean
}

export function ConfirmModal({
  open, onClose, onConfirm, title, description, confirmText = "确认", cancelText = "取消", variant = "default", loading,
}: ConfirmModalProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={loading}>{cancelText}</Button>
          <Button variant={variant === "danger" ? "danger" : "default"} onClick={onConfirm} loading={loading}>
            {confirmText}
          </Button>
        </>
      }
    >
      <div />
    </Modal>
  )
}
