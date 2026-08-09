/* 事实流 →「全量留痕」:每一个变化的出处与因果,追加不修改。
   渲染逻辑与原治理页一致,仅排版融入叙事时间线风格。 */
import { formatDecisionValue, formatFactSource } from '../tabs/governanceFormat'

export interface FactRow {
  id: string
  subjectLabel: string
  propertyName: string
  value: unknown
  present?: boolean
  kind: string
  source: string
  actorId?: string | null
  causedBy?: string | null
  supersedesId?: string | null
  recordedAt: string | null
  ontologyVersion?: string | null
}

export const KIND_META: Record<string, { label: string; cls: string; title: string }> = {
  property: { label: '属性', cls: 'bg-blue-50 text-blue-600 border-blue-200', title: '数据源/人工写入的存储属性变化' },
  derived: { label: '派生', cls: 'bg-purple-50 text-purple-600 border-purple-200', title: '函数自动重算的派生值(可溯源到输入事实)' },
  link: { label: '链接', cls: 'bg-cyan-50 text-cyan-600 border-cyan-200', title: '关系的建立/解除' },
  object: { label: '存在', cls: 'bg-red-50 text-red-600 border-red-200', title: '实例存在性(删除留墓碑)' },
  decision: { label: '决策', cls: 'bg-amber-50 text-amber-700 border-amber-200', title: '人的审批决策(批准/拒绝都记录)' },
  absence: { label: '缺席', cls: 'bg-gray-100 text-gray-500 border-gray-300', title: '查询结果为空/非空的翻转快照——"没有"也有出处' },
}

const fmtTime = (iso?: string | null) => iso
  ? new Date(iso).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  : '-'
const fmtVal = (v: unknown) => {
  if (v === null || v === undefined) return '∅'
  const s = typeof v === 'object' ? JSON.stringify(v) : String(v)
  return s.length > 30 ? `${s.slice(0, 30)}…` : s
}

export default function FactStream({
  facts,
  kindFilter,
  onKindFilterChange,
}: {
  facts: FactRow[]
  kindFilter: string
  onKindFilterChange: (kind: string) => void
}) {
  return (
    <>
      <div className="mb-3 flex flex-wrap gap-1">
        <button onClick={() => onKindFilterChange('')}
          className={`rounded-full border px-2 py-0.5 text-[10px] ${!kindFilter ? 'border-indigo-300 bg-indigo-50 text-indigo-600' : 'border-gray-200 text-gray-400 hover:text-gray-600'}`}>
          全部
        </button>
        {Object.entries(KIND_META).map(([k, m]) => (
          <button key={k} onClick={() => onKindFilterChange(k)} title={m.title}
            className={`rounded-full border px-2 py-0.5 text-[10px] ${kindFilter === k ? m.cls : 'border-gray-200 text-gray-400 hover:text-gray-600'}`}>
            {m.label}
          </button>
        ))}
      </div>
      {facts.length === 0 ? (
        <p className="py-3 text-center text-xs text-gray-400">暂无{kindFilter ? `「${KIND_META[kindFilter]?.label}」类` : ''}事实。</p>
      ) : (
        <div className="max-h-96 space-y-0.5 overflow-y-auto">
          {facts.map(f => {
            const decision = f.kind === 'decision' ? formatDecisionValue(f.value) : null
            const rawValue = f.value === null || f.value === undefined
              ? ''
              : typeof f.value === 'object' ? JSON.stringify(f.value) : String(f.value)
            return (
              <div key={f.id} className="flex items-center gap-2 border-b border-gray-50 py-1 text-xs last:border-0">
                <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${KIND_META[f.kind]?.cls ?? KIND_META.property.cls}`}
                  title={KIND_META[f.kind]?.title}>
                  {KIND_META[f.kind]?.label ?? f.kind}
                </span>
                <span className="max-w-[180px] truncate text-gray-600" title={f.subjectLabel}>{f.subjectLabel}</span>
                <span className="max-w-[110px] truncate font-mono text-gray-400">{f.propertyName}</span>
                <span className="text-gray-300">=</span>
                {decision ? (
                  <span className="flex-1 truncate" title={rawValue}>
                    <span className={decision.decision === 'approved' ? 'font-medium text-emerald-600' : 'font-medium text-red-600'}>
                      {decision.decision === 'approved' ? '✓ 批准' : '✗ 拒绝'}
                    </span>
                    {decision.reason && <span className="text-gray-600">:{decision.reason}</span>}
                  </span>
                ) : (
                  <span
                    className="flex-1 truncate font-mono text-gray-700"
                    title={f.present === false ? '属性已删除' : String(fmtVal(f.value))}
                  >
                    {f.present === false ? '(已删除)' : fmtVal(f.value)}
                  </span>
                )}
                {f.causedBy && (
                  <span className="shrink-0 text-[10px] text-gray-400"
                    title={`该变化由一次已批准的动作执行引起(指针 ${f.causedBy})`}>因果</span>
                )}
                {f.supersedesId && (
                  <span className="shrink-0 rounded bg-violet-50 px-1 text-[10px] text-violet-500"
                    title="该事实覆盖了同属性的旧值">覆盖</span>
                )}
                <span className="max-w-[110px] shrink-0 truncate text-gray-400" title={f.source}>{formatFactSource(f.source)}</span>
                <span className="shrink-0 text-gray-400">{fmtTime(f.recordedAt)}</span>
              </div>
            )
          })}
        </div>
      )}
    </>
  )
}
