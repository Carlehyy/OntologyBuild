// 实例值/来源的渲染组件,表格与实例详情抽屉共用,保证两处口径一致。
import { instanceSourceLabel, resolveInstanceValueDisplay } from './instanceValueDisplay'

export function FullValue({ value, type }: { value: unknown; type?: string }) {
  const display = resolveInstanceValueDisplay(value, type)
  switch (display.kind) {
    case 'empty':
      return <span className="text-slate-300">—</span>
    case 'array':
      return (
        <pre className="m-0 whitespace-nowrap font-mono text-[11px] leading-5 text-slate-600">
          {display.text}
        </pre>
      )
    case 'object':
      return (
        <pre className="m-0 max-w-[30rem] whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-slate-600">
          {display.text}
        </pre>
      )
    case 'number':
      return (
        <span className="block max-w-[30rem] whitespace-pre-wrap break-words tabular-nums text-slate-700">
          {display.text}
        </span>
      )
    case 'date':
    case 'datetime':
      return (
        <span
          className="block max-w-[30rem] whitespace-nowrap tabular-nums text-slate-700"
          title={display.raw}
        >
          {display.text}
        </span>
      )
    default:
      return <span className="block max-w-[30rem] whitespace-pre-wrap break-words text-slate-700">{display.text}</span>
  }
}

const SOURCE_TONE: Record<string, string> = {
  pipeline: 'bg-teal-50 text-teal-700',
  collector: 'bg-sky-50 text-sky-700',
  action: 'bg-violet-50 text-violet-700',
  import: 'bg-blue-50 text-blue-700',
  manual: 'bg-slate-100 text-slate-600',
}

export function SourceChip({ source }: { source?: string | null }) {
  const key = (source ?? '').trim()
  const tone = SOURCE_TONE[key] || 'bg-slate-100 text-slate-500'
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded px-1.5 py-0.5 text-[10px] font-medium ${tone}`}>
      {instanceSourceLabel(source)}
    </span>
  )
}
