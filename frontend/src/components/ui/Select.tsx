import * as React from "react"
import { cn } from "@/lib/utils"

interface SelectOption {
  value: string
  label: string
}

interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  options: SelectOption[]
  label?: string
  error?: string
}

const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, options, label, error, ...props }, ref) => (
    <div className="w-full">
      {label && (
        <label className="block text-sm font-medium text-[var(--color-text-primary)] mb-1.5">
          {label}
          {props.required && <span className="text-[var(--color-danger)] ml-0.5">*</span>}
        </label>
      )}
      <select
        className={cn(
          "flex h-9 w-full rounded-md border bg-[var(--color-bg-elevated)] px-3 py-2 text-sm shadow-sm",
          "focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]",
          "disabled:cursor-not-allowed disabled:opacity-50",
          error ? "border-[var(--color-danger)]" : "border-[var(--color-border)]",
          className
        )}
        ref={ref}
        {...props}
      >
        {options.map(opt => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>
      {error && <p className="mt-1 text-xs text-[var(--color-danger)]">{error}</p>}
    </div>
  )
)
Select.displayName = "Select"

export { Select }
export type { SelectOption }
