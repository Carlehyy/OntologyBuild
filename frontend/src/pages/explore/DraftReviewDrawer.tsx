import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  X, Box, GitBranch, Play, CircleAlert, TriangleAlert, Loader2, CheckCircle2, ShieldCheck,
} from 'lucide-react'
import {
  explorationApi, type ApplyDraftResult, type BxDraft,
} from '@/api/exploration'

const CARDINALITY_LABEL: Record<string, string> = {
  'one-to-one': '1:1', 'one-to-many': '1:N', 'many-to-one': 'N:1', 'many-to-many': 'N:N',
}

/** 本体草稿人审抽屉：分组预览 + 逐项勾选 + 报告，应用后跳图谱编辑器 */
export default function DraftReviewDrawer({ draft, onClose, onApplied }: {
  draft: BxDraft
  onClose: () => void
  onApplied?: (result: ApplyDraftResult) => void
}) {
  const allItems = useMemo(() => [
    ...draft.draft.objectTypes, ...draft.draft.linkTypes, ...draft.draft.actions,
  ], [draft])
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(allItems.filter(i => !i.conflict).map(i => i.key)))
  const [newName, setNewName] = useState('')
  const [newDomain, setNewDomain] = useState('业务探索')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<ApplyDraftResult | null>(null)

  const toggle = (key: string, conflict?: boolean) => {
    if (conflict || result) return
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const apply = async () => {
    setError('')
    if (selected.size === 0) { setError('请至少勾选一个草稿元素'); return }
    if (!draft.targetOntologyId && !newName.trim()) { setError('请填写新本体名称'); return }
    setBusy(true)
    try {
      const res = await explorationApi.applyDraft(draft.id, {
        selectedKeys: [...selected],
        newOntology: draft.targetOntologyId ? undefined
          : { name: newName.trim(), domain: newDomain.trim() || '业务探索' },
      })
      setResult(res)
      onApplied?.(res)
    } catch (e: any) {
      setError(e?.detail || e?.message || '应用失败')
    } finally {
      setBusy(false)
    }
  }

  const report = draft.report || { warnings: [], conflicts: [], scenarioCoverage: [], llmRefined: false }

  const checkbox = (key: string, conflict?: boolean) => (
    <input
      type="checkbox"
      className="mt-1 accent-teal-600 shrink-0 disabled:opacity-40"
      checked={selected.has(key)}
      disabled={!!conflict || !!result}
      onChange={() => toggle(key, conflict)}
    />
  )

  const conflictTag = <span className="text-[10px] px-1.5 py-px rounded bg-rose-50 text-rose-600 border border-rose-200">同名冲突 · 将跳过</span>

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={onClose}>
      <div
        className="h-full w-[620px] max-w-[92vw] bg-[var(--color-bg-elevated)] shadow-2xl flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-border)]">
          <div>
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">本体草稿审阅</div>
            <div className="text-xs text-[var(--color-text-tertiary)] mt-0.5">
              {draft.targetOntologyId ? '保守合并到已有本体（只新增，同名跳过）' : '应用时将新建本体'}
              {report.llmRefined ? ' · LLM 已补缺' : ' · 纯确定性映射'}
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-md hover:bg-[var(--color-bg-hover)] text-[var(--color-text-tertiary)]">
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {result ? (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50/60 px-4 py-3.5">
              <div className="flex items-center gap-2 text-sm font-medium text-emerald-700">
                <CheckCircle2 size={16} /> 已应用到本体「{result.ontologyName}」
              </div>
              <div className="text-xs text-emerald-800/80 mt-1.5">
                新建 对象类型 {result.created.objectTypes} · 链接 {result.created.linkTypes} · 动作 {result.created.actions}
                {result.skipped.length > 0 && ` · 跳过 ${result.skipped.length} 项`}
              </div>
              {result.skipped.length > 0 && (
                <ul className="mt-1.5 space-y-0.5">
                  {result.skipped.map((s, i) => (
                    <li key={i} className="text-[11px] text-emerald-900/70">· {s.reason}</li>
                  ))}
                </ul>
              )}
              <Link
                to={`/ontologies/${result.ontologyId}/graph`}
                className="inline-block mt-2.5 text-xs font-medium text-teal-700 underline underline-offset-2"
              >
                前往图谱编辑器继续完善 →
              </Link>
            </div>
          ) : (
            <>
              {(report.warnings.length > 0 || report.conflicts.length > 0) && (
                <div className="rounded-lg border border-amber-200 bg-amber-50/60 px-3.5 py-2.5">
                  <div className="flex items-center gap-1.5 text-xs font-medium text-amber-700 mb-1">
                    <TriangleAlert size={13} /> 转化报告（{report.warnings.length + report.conflicts.length}）
                  </div>
                  <ul className="space-y-0.5 max-h-36 overflow-y-auto">
                    {[...report.conflicts, ...report.warnings].map((w, i) => (
                      <li key={i} className="text-[11px] leading-relaxed text-amber-800/90">· {w}</li>
                    ))}
                  </ul>
                </div>
              )}
              {report.scenarioCoverage.length > 0 && (
                <div className="rounded-lg border border-rose-200 bg-rose-50/50 px-3.5 py-2.5">
                  <div className="flex items-center gap-1.5 text-xs font-medium text-rose-700 mb-1">
                    <CircleAlert size={13} /> 场景可表达性检查未通过
                  </div>
                  {report.scenarioCoverage.map((c, i) => (
                    <div key={i} className="text-[11px] leading-relaxed text-rose-800/90">
                      · 场景「{c.scenario}」缺少
                      {c.missingObjects.length > 0 && ` 对象: ${c.missingObjects.join('、')}`}
                      {c.missingBehaviors.length > 0 && ` 行为: ${c.missingBehaviors.join('、')}`}
                      —— 建议回到对话补齐后重新生成
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {/* 对象类型 */}
          <section>
            <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--color-text-secondary)] mb-2">
              <Box size={13} className="text-sky-600" /> 对象类型（{draft.draft.objectTypes.length}）
            </div>
            <div className="space-y-2">
              {draft.draft.objectTypes.map(ot => (
                <label key={ot.key} className="flex items-start gap-2.5 rounded-lg border border-[var(--color-border)] px-3 py-2.5 cursor-pointer hover:bg-[var(--color-bg-hover)]/50">
                  {checkbox(ot.key, ot.conflict)}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: ot.color || '#4C6EF5' }} />
                      <span className="text-xs font-medium text-[var(--color-text-primary)]">{ot.displayName}</span>
                      <span className="text-[11px] font-mono text-[var(--color-text-tertiary)]">{ot.name}</span>
                      {ot.origin === 'actor' && (
                        <span className="text-[10px] px-1.5 py-px rounded bg-violet-50 text-violet-600">来自主体模型</span>
                      )}
                      {ot.conflict && conflictTag}
                    </div>
                    <div className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">
                      主键 <span className="font-mono">{ot.primaryKey}</span> · {ot.properties.length} 属性：
                      {ot.properties.slice(0, 8).map(p => `${p.displayName || p.name}(${p.type})`).join('、')}
                      {ot.properties.length > 8 && ' …'}
                    </div>
                    {ot.description && (
                      <div className="mt-0.5 text-[11px] text-[var(--color-text-tertiary)] line-clamp-2">{ot.description}</div>
                    )}
                  </div>
                </label>
              ))}
            </div>
          </section>

          {/* 链接类型 */}
          <section>
            <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--color-text-secondary)] mb-2">
              <GitBranch size={13} className="text-teal-600" /> 链接类型（{draft.draft.linkTypes.length}）
            </div>
            <div className="space-y-2">
              {draft.draft.linkTypes.map(lt => (
                <label key={lt.key} className="flex items-start gap-2.5 rounded-lg border border-[var(--color-border)] px-3 py-2.5 cursor-pointer hover:bg-[var(--color-bg-hover)]/50">
                  {checkbox(lt.key, lt.conflict)}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs font-medium text-[var(--color-text-primary)]">{lt.displayName}</span>
                      <span className="text-[11px] text-[var(--color-text-tertiary)]">
                        {lt.sourceName} → {lt.targetName} · {CARDINALITY_LABEL[lt.cardinality] || lt.cardinality}
                      </span>
                      {lt.conflict && conflictTag}
                    </div>
                  </div>
                </label>
              ))}
              {draft.draft.linkTypes.length === 0 && (
                <div className="text-[11px] text-[var(--color-text-tertiary)] px-1">（无）</div>
              )}
            </div>
          </section>

          {/* 动作 */}
          <section>
            <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--color-text-secondary)] mb-2">
              <Play size={13} className="text-amber-600" /> 动作（{draft.draft.actions.length}）
            </div>
            <div className="space-y-2">
              {draft.draft.actions.map(a => (
                <label key={a.key} className="flex items-start gap-2.5 rounded-lg border border-[var(--color-border)] px-3 py-2.5 cursor-pointer hover:bg-[var(--color-bg-hover)]/50">
                  {checkbox(a.key, a.conflict)}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs font-medium text-[var(--color-text-primary)]">{a.displayName}</span>
                      <span className="text-[11px] font-mono text-[var(--color-text-tertiary)]">{a.name}</span>
                      {a.requiresApproval && (
                        <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-px rounded bg-teal-50 text-teal-700">
                          <ShieldCheck size={10} /> 需审批
                        </span>
                      )}
                      {a.conflict && conflictTag}
                    </div>
                    <div className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">
                      {a.parameters.length > 0 && `参数: ${a.parameters.map(p => p.displayName || p.name).join('、')} · `}
                      {a.rules.length > 0 && `${a.rules.length} 条待形式化规则（disabled） · `}
                      {a.description && <span className="line-clamp-2">{a.description}</span>}
                    </div>
                  </div>
                </label>
              ))}
              {draft.draft.actions.length === 0 && (
                <div className="text-[11px] text-[var(--color-text-tertiary)] px-1">（无）</div>
              )}
            </div>
          </section>
        </div>

        {!result && (
          <div className="border-t border-[var(--color-border)] px-5 py-3.5 space-y-2.5">
            {!draft.targetOntologyId && (
              <div className="flex gap-2">
                <input
                  value={newName}
                  onChange={e => setNewName(e.target.value)}
                  placeholder="新本体名称（必填）"
                  className="flex-1 px-3 py-1.5 text-xs rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] outline-none focus:border-teal-500"
                />
                <input
                  value={newDomain}
                  onChange={e => setNewDomain(e.target.value)}
                  placeholder="领域"
                  className="w-32 px-3 py-1.5 text-xs rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] outline-none focus:border-teal-500"
                />
              </div>
            )}
            {error && <div className="text-xs text-[var(--color-danger)]">{error}</div>}
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-[var(--color-text-tertiary)]">
                已勾选 {selected.size} / {allItems.length} 项（冲突项不可选，应用时自动跳过）
              </span>
              <button
                onClick={apply}
                disabled={busy}
                className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-md text-xs font-medium text-white bg-teal-600 hover:bg-teal-700 disabled:opacity-50"
              >
                {busy && <Loader2 size={12} className="animate-spin" />}
                {draft.targetOntologyId ? '应用到本体' : '新建本体并应用'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
