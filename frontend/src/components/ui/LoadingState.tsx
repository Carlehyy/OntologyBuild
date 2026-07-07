import { cn } from "@/lib/utils"

interface LoadingStateProps {
  className?: string
  message?: string
}

export function LoadingState({ className, message = "加载中..." }: LoadingStateProps) {
  return (
    <div className={cn("flex flex-col items-center justify-center py-12 text-[var(--color-text-tertiary)]", className)}>
      <svg className="animate-spin h-8 w-8 mb-3" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      <p className="text-sm">{message}</p>
    </div>
  )
}

interface EmptyStateProps {
  className?: string
  title?: string
  description?: string
  action?: React.ReactNode
}

export function EmptyState({ className, title = "暂无数据", description, action }: EmptyStateProps) {
  return (
    <div className={cn("flex flex-col items-center justify-center py-12 text-center", className)}>
      <div className="w-16 h-16 rounded-full bg-[var(--color-bg-hover)] flex items-center justify-center mb-4">
        <svg className="w-8 h-8 text-[var(--color-text-tertiary)]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
        </svg>
      </div>
      <h3 className="text-sm font-medium text-[var(--color-text-secondary)] mb-1">{title}</h3>
      {description && <p className="text-xs text-[var(--color-text-tertiary)] max-w-xs">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
