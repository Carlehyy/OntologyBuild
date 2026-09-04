// 实例值/来源的渲染组件,表格与实例详情抽屉共用,保证两处口径一致。
import { instanceSourceLabel, resolveInstanceValueDisplay } from './instanceValueDisplay'

export function FullValue({ value, type }: { value: unknown; type?: string }) {
  const display = resolveInstanceValueDisplay(value, type)
  switch (display.kind) {
    case 'empty':
      return <span className="text-[var(--color-text-tertiary)]">—</span>
    case 'array':
      return (
        <pre className="m-0 whitespace-nowrap font-mono text-[11px] leading-5 text-muted-foreground">
          {display.text}
        </pre>
      )
    case 'object':
      return (
        <pre className="m-0 max-w-[30rem] whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-muted-foreground">
          {display.text}
        </pre>
      )
    case 'number':
      return (
        <span className="block max-w-[30rem] whitespace-pre-wrap break-words tabular-nums text-foreground">
          {display.text}
        </span>
      )
    case 'date':
    case 'datetime':
      return (
        <span
          className="block max-w-[30rem] whitespace-nowrap tabular-nums text-foreground"
          title={display.raw}
        >
          {display.text}
        </span>
      )
    default:
      return <span className="block max-w-[30rem] whitespace-pre-wrap break-words text-foreground">{display.text}</span>
  }
}

const SOURCE_TONE: Record<string, string> = {
  pipeline: 'bg-brand-soft text-brand-ink',
  collector: 'bg-[var(--color-info-bg)] text-[var(--color-info)]',
  action: 'bg-viz-violet-soft text-viz-violet',
  import: 'bg-[var(--color-info-bg)] text-[var(--color-info)]',
  manual: 'bg-muted text-muted-foreground',
}

export function SourceChip({ source }: { source?: string | null }) {
  const key = (source ?? '').trim()
  const tone = SOURCE_TONE[key] || 'bg-muted text-muted-foreground'
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded px-1.5 py-0.5 text-[10px] font-medium ${tone}`}>
      {instanceSourceLabel(source)}
    </span>
  )
}
