// 数据集成域内分页大小选择：基于 vendored Radix Select 的同构复用件，
// 替换 structured 各视图的原生 select 每页条数控件。
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

export function PageSizeSelect({ value, onChange, sizes, ariaLabel, disabled, title, className }: {
  value: number
  onChange: (size: number) => void
  sizes: readonly number[]
  ariaLabel: string
  disabled?: boolean
  title?: string
  className?: string
}) {
  return (
    <Select value={String(value)} onValueChange={v => onChange(Number(v))}>
      <SelectTrigger
        disabled={disabled}
        title={title}
        aria-label={ariaLabel}
        className={className ?? 'h-8 w-20 rounded-lg bg-card px-2 text-xs'}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {sizes.map(size => <SelectItem key={size} value={String(size)}>{size}</SelectItem>)}
      </SelectContent>
    </Select>
  )
}
