import * as React from "react"
import { cn } from "@/lib/utils"

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  error?: string
  label?: string
}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, error, label, ...props }, ref) => {
    return (
      <div className="w-full">
        {label && (
          <label className="block text-sm font-medium text-[var(--color-text-primary)] mb-1.5">
            {label}
            {props.required && <span className="text-[var(--color-danger)] ml-0.5">*</span>}
          </label>
        )}
        <input
          type={type}
          className={cn(
            "flex h-9 w-full rounded-md border bg-[var(--color-bg-elevated)] px-3 py-2 text-sm shadow-sm transition-colors",
            "placeholder:text-[var(--color-text-tertiary)]",
            "focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]",
            "disabled:cursor-not-allowed disabled:opacity-50 disabled:bg-[var(--color-bg-hover)]",
            error
              ? "border-[var(--color-danger)] focus:ring-[var(--color-danger)] focus:border-[var(--color-danger)]"
              : "border-[var(--color-border)]",
            className
          )}
          ref={ref}
          {...props}
        />
        {error && (
          <p className="mt-1 text-xs text-[var(--color-danger)]">{error}</p>
        )}
      </div>
    )
  }
)
Input.displayName = "Input"

export { Input }
