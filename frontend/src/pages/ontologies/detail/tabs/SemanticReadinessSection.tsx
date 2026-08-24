import { AlertTriangle, Check, FileText } from 'lucide-react'
import type { OntologySemanticOverview } from '@/api/v2/ontology-versions'
import {
  canvasCountRows,
  consistencyView,
  documentStateView,
  normalizeSemanticOverview,
  structureCountRows,
  type SemanticCountRow,
} from './semanticReadiness'

function CountRow({ label, rows }: { label: string; rows: SemanticCountRow[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <span className="w-14 shrink-0 text-xs text-slate-500">{label}</span>
      {rows.map(row => (
        <span key={row.key} className="text-xs text-slate-600">
          {row.label} <b className="font-semibold tabular-nums text-slate-800">{row.count}</b>
        </span>
      ))}
    </div>
  )
}

/** 发布前检查弹窗的「业务语义」区块：需求文档状态、画布/结构计数与三面一致性。
 *  视觉层级沿用弹窗内既有区块（结构影响/运行态冲突）的边框浅底卡片语汇。
 *  入口先经 normalizeSemanticOverview 归一，脏数据/旧后端载荷不渲染。 */
export default function SemanticReadinessSection({ overview }: { overview: OntologySemanticOverview }) {
  const normalized = normalizeSemanticOverview(overview)
  if (!normalized) return null
  const consistency = consistencyView(normalized)
  const documentState = documentStateView(normalized)
  return (
    <section aria-labelledby="semantic-readiness-heading" data-testid="semantic-readiness-section">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h3 id="semantic-readiness-heading" className="font-semibold text-slate-800">业务语义</h3>
        <span
          data-testid="semantic-consistency-badge"
          className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${consistency.tone === 'consistent'
            ? 'bg-emerald-100 text-emerald-700'
            : 'bg-amber-100 text-amber-800'}`}
        >
          {consistency.tone === 'consistent' ? <Check size={12} /> : <AlertTriangle size={12} />}
          {consistency.text}
        </span>
      </div>
      <div className="space-y-2 rounded-lg border border-slate-200 bg-slate-50/60 p-3">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
          <FileText size={13} className="shrink-0 text-slate-400" aria-hidden="true" />
          <span className="text-slate-500">需求文档</span>
          <span
            data-testid="semantic-document-state"
            className={documentState.tone === 'stale'
              ? 'font-medium text-amber-700'
              : documentState.tone === 'ok'
                ? 'text-slate-700'
                : 'text-slate-400'}
          >
            {documentState.text}
          </span>
          {!normalized.hasSemanticLayer && (
            <span className="text-slate-400">（该版本尚未沉淀业务语义层）</span>
          )}
        </div>
        <CountRow label="画布模型" rows={canvasCountRows(normalized)} />
        <CountRow label="本体结构" rows={structureCountRows(normalized)} />
        {consistency.details.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1" aria-label="语义一致性明细">
            {consistency.details.map(item => (
              <code
                key={item.code}
                className="rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] text-amber-800"
              >
                {item.label} ×{item.count}
              </code>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
