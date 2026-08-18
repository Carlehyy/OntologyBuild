import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, BellRing, CheckCircle2, ChevronLeft, FlaskConical,
  Loader2, Pencil, Power, Save, Trash2, X,
} from 'lucide-react'
import {
  agentApi, type DynamicSentinel, type DynamicSentinelDefinition,
} from '@/api/agent'
import type { Action, LinkType, ObjectType } from '../../palantir-graph/types/ontology'

interface Props {
  open: boolean
  onClose: () => void
  oid: string
  releaseId: string
  objectTypes: ObjectType[]
  linkTypes: LinkType[]
  actions: Action[]
}

const errorText = (error: any) => {
  const detail = error?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message || Array.isArray(detail?.errors)) {
    const errors = (detail?.errors || []).map((item: any) => {
      if (typeof item === 'string') return item
      const path = Array.isArray(item?.loc) ? item.loc.join('.') : item?.field
      const message = item?.message || item?.msg || JSON.stringify(item)
      return path ? `${path}：${message}` : message
    }).filter(Boolean)
    return [detail?.message, ...errors].filter(Boolean).join('；')
  }
  return error?.message || '操作失败'
}

export function DynamicSentinelDrawer({
  open, onClose, oid, releaseId, objectTypes, linkTypes, actions,
}: Props) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<DynamicSentinel | null>(null)
  const [draft, setDraft] = useState<DynamicSentinelDefinition | null>(null)
  const [busyId, setBusyId] = useState('')
  const [error, setError] = useState('')
  const queryKey = ['agent-dynamic-sentinels', oid, releaseId]
  const { data: rows = [], isLoading, refetch } = useQuery({
    queryKey,
    queryFn: () => agentApi.dynamicSentinels(oid, releaseId),
    enabled: open && !!oid && !!releaseId,
  })

  useEffect(() => {
    if (!open) {
      setEditing(null)
      setDraft(null)
      setError('')
    }
  }, [open])

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['agent-dynamic-sentinels', oid] })
    await refetch()
  }

  const run = async (id: string, action: () => Promise<unknown>) => {
    setBusyId(id)
    setError('')
    try {
      await action()
      await refresh()
    } catch (requestError) {
      setError(errorText(requestError))
    } finally {
      setBusyId('')
    }
  }

  const startEdit = (row: DynamicSentinel) => {
    setEditing(row)
    setDraft({
      name: row.name, displayName: row.displayName, description: row.description,
      bindings: row.bindings.map(item => ({ ...item })),
      links: row.links.map(item => ({ ...item })), condition: row.condition,
      conditionRows: row.conditionRows || [], conditionLogic: row.conditionLogic || 'and',
      primaryAlias: row.primaryAlias, actionIds: [...row.actionIds],
      actionParameters: { ...row.actionParameters }, onChange: row.onChange,
      onSchedule: row.onSchedule, scanIntervalSeconds: row.scanIntervalSeconds,
      triggerMode: row.triggerMode, muted: row.muted,
    })
    setError('')
  }

  const save = async () => {
    if (!editing || !draft) return
    await run(editing.id, async () => {
      await agentApi.updateDynamicSentinel(oid, releaseId, editing, draft)
      setEditing(null)
      setDraft(null)
    })
  }

  const renameBinding = (index: number, alias: string) => {
    if (!draft) return
    const previousAlias = draft.bindings[index]?.alias
    setDraft({
      ...draft,
      bindings: draft.bindings.map((item, itemIndex) => itemIndex === index ? { ...item, alias } : item),
      links: draft.links.map(link => ({
        ...link,
        from: link.from === previousAlias ? alias : link.from,
        to: link.to === previousAlias ? alias : link.to,
      })),
      primaryAlias: draft.primaryAlias === previousAlias ? alias : draft.primaryAlias,
    })
  }

  const removeBinding = (index: number) => {
    if (!draft || draft.bindings.length === 1) return
    const removedAlias = draft.bindings[index]?.alias
    const bindings = draft.bindings.filter((_, itemIndex) => itemIndex !== index)
    setDraft({
      ...draft,
      bindings,
      links: draft.links.filter(link => link.from !== removedAlias && link.to !== removedAlias),
      primaryAlias: draft.primaryAlias === removedAlias
        ? (bindings[0]?.alias || '')
        : draft.primaryAlias,
    })
  }

  const addBinding = () => {
    if (!draft) return
    const aliases = new Set(draft.bindings.map(item => item.alias))
    let suffix = draft.bindings.length + 1
    while (aliases.has(`x${suffix}`)) suffix += 1
    setDraft({
      ...draft,
      bindings: [...draft.bindings, {
        alias: `x${suffix}`, objectTypeId: objectTypes[0]?.id || '', filter: null,
      }],
    })
  }

  const objectName = (id: string) => objectTypes.find(item => item.id === id)?.displayName || id
  const actionName = (id: string) => actions.find(item => item.id === id)?.displayName || id
  const trialActions = useMemo(
    () => editing?.lastTrialReport?.plannedActions || [], [editing])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[80] flex justify-end bg-slate-950/20 backdrop-blur-[1px]" role="dialog" aria-label="动态哨兵管理">
      <button type="button" className="flex-1 cursor-default" onClick={onClose} aria-label="关闭动态哨兵管理" />
      <aside className="flex h-full w-[min(560px,96vw)] flex-col border-l border-slate-200 bg-white shadow-2xl">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 px-5">
          <div className="flex min-w-0 items-center gap-2.5">
            {editing && (
              <button type="button" onClick={() => { setEditing(null); setDraft(null); setError('') }}
                className="flex h-7 w-7 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100" aria-label="返回动态哨兵列表">
                <ChevronLeft size={16} />
              </button>
            )}
            <span className="flex h-8 w-8 items-center justify-center rounded-md bg-teal-50 text-teal-700"><BellRing size={16} /></span>
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold text-slate-900">{editing ? `编辑 · ${editing.displayName}` : '动态哨兵'}</h2>
              <p className="truncate text-[11px] text-slate-500">仅管理智能助手后天创建的哨兵 · 公共哨兵不在此处</p>
            </div>
          </div>
          <button type="button" onClick={onClose} className="flex h-8 w-8 items-center justify-center rounded-md text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="关闭"><X size={16} /></button>
        </header>

        {error && (
          <div className="mx-5 mt-4 flex items-start gap-2 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-600">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />{error}
          </div>
        )}

        {editing && draft ? (
          <div className="scrollbar-thin flex-1 space-y-5 overflow-y-auto px-5 py-5">
            <section className="grid grid-cols-2 gap-3">
              <label className="col-span-1 text-xs text-slate-600">显示名称
                <input value={draft.displayName} onChange={event => setDraft({ ...draft, displayName: event.target.value })}
                  className="mt-1 h-9 w-full rounded-md border border-slate-200 px-3 text-sm outline-none focus:border-teal-400 focus:ring-2 focus:ring-teal-100" />
              </label>
              <label className="col-span-1 text-xs text-slate-600">技术名称
                <input value={draft.name} readOnly className="mt-1 h-9 w-full rounded-md border border-slate-200 bg-slate-50 px-3 text-sm text-slate-500" />
              </label>
              <label className="col-span-2 text-xs text-slate-600">说明
                <textarea value={draft.description || ''} onChange={event => setDraft({ ...draft, description: event.target.value })}
                  className="mt-1 min-h-20 w-full rounded-md border border-slate-200 px-3 py-2 text-sm outline-none focus:border-teal-400 focus:ring-2 focus:ring-teal-100" />
              </label>
            </section>

            <section>
              <div className="mb-2 flex items-center justify-between"><h3 className="text-xs font-semibold text-slate-800">对象绑定</h3>
                <button type="button" onClick={addBinding}
                  className="text-xs font-medium text-teal-700 hover:text-teal-900">添加绑定</button>
              </div>
              <div className="space-y-2">
                {draft.bindings.map((binding, index) => (
                  <div key={index} className="grid grid-cols-[72px_1fr_1.3fr_28px] gap-2">
                    <input value={binding.alias} onChange={event => renameBinding(index, event.target.value)}
                      aria-label={`绑定${index + 1}别名`} className="h-8 rounded-md border border-slate-200 px-2 text-xs" />
                    <select value={binding.objectTypeId} onChange={event => setDraft({ ...draft, bindings: draft.bindings.map((item, itemIndex) => itemIndex === index ? { ...item, objectTypeId: event.target.value } : item) })}
                      aria-label={`绑定${index + 1}对象类型`} className="h-8 rounded-md border border-slate-200 px-2 text-xs">
                      {objectTypes.map(item => <option key={item.id} value={item.id}>{item.displayName}</option>)}
                    </select>
                    <input value={binding.filter || ''} placeholder="可选过滤，如 a.amount > 100"
                      onChange={event => setDraft({ ...draft, bindings: draft.bindings.map((item, itemIndex) => itemIndex === index ? { ...item, filter: event.target.value || null } : item) })}
                      aria-label={`绑定${index + 1}过滤条件`} className="h-8 rounded-md border border-slate-200 px-2 text-xs" />
                    <button type="button" disabled={draft.bindings.length === 1} onClick={() => removeBinding(index)}
                      className="flex h-8 items-center justify-center rounded-md text-slate-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-30" aria-label={`删除绑定${index + 1}`}><X size={14} /></button>
                  </div>
                ))}
              </div>
              <label className="mt-3 block text-xs text-slate-600">主对象别名
                <select value={draft.primaryAlias} onChange={event => setDraft({ ...draft, primaryAlias: event.target.value })}
                  className="mt-1 h-8 w-full rounded-md border border-slate-200 bg-white px-2 text-xs">
                  {draft.bindings.map((item, index) => <option key={index} value={item.alias}>{item.alias} · {objectName(item.objectTypeId)}</option>)}
                </select>
              </label>
            </section>

            <section>
              <div className="mb-2 flex items-center justify-between"><h3 className="text-xs font-semibold text-slate-800">绑定关系</h3>
                <button type="button" disabled={draft.bindings.length < 2 || linkTypes.length === 0}
                  onClick={() => setDraft({ ...draft, links: [...draft.links, { from: draft.bindings[0]?.alias || '', linkTypeId: linkTypes[0]?.id || '', to: draft.bindings[1]?.alias || '' }] })}
                  className="text-xs font-medium text-teal-700 hover:text-teal-900 disabled:opacity-30">添加关系</button>
              </div>
              <div className="space-y-2">
                {draft.links.map((link, index) => (
                  <div key={`${link.linkTypeId}-${index}`} className="grid grid-cols-[1fr_1.5fr_1fr_28px] gap-2">
                    <select value={link.from} onChange={event => setDraft({ ...draft, links: draft.links.map((item, itemIndex) => itemIndex === index ? { ...item, from: event.target.value } : item) })}
                      aria-label={`关系${index + 1}起点`} className="h-8 rounded-md border border-slate-200 px-2 text-xs">
                      {draft.bindings.map((item, itemIndex) => <option key={itemIndex} value={item.alias}>{item.alias}</option>)}
                    </select>
                    <select value={link.linkTypeId} onChange={event => setDraft({ ...draft, links: draft.links.map((item, itemIndex) => itemIndex === index ? { ...item, linkTypeId: event.target.value } : item) })}
                      aria-label={`关系${index + 1}类型`} className="h-8 rounded-md border border-slate-200 px-2 text-xs">
                      {linkTypes.map(item => <option key={item.id} value={item.id}>{item.displayName}</option>)}
                    </select>
                    <select value={link.to} onChange={event => setDraft({ ...draft, links: draft.links.map((item, itemIndex) => itemIndex === index ? { ...item, to: event.target.value } : item) })}
                      aria-label={`关系${index + 1}终点`} className="h-8 rounded-md border border-slate-200 px-2 text-xs">
                      {draft.bindings.map((item, itemIndex) => <option key={itemIndex} value={item.alias}>{item.alias}</option>)}
                    </select>
                    <button type="button" onClick={() => setDraft({ ...draft, links: draft.links.filter((_, itemIndex) => itemIndex !== index) })}
                      className="flex h-8 items-center justify-center rounded-md text-slate-400 hover:bg-red-50 hover:text-red-600" aria-label={`删除关系${index + 1}`}><X size={14} /></button>
                  </div>
                ))}
                {draft.links.length === 0 && <p className="text-[11px] text-slate-400">单对象哨兵无需关系；多对象无关系时会进行组合匹配。</p>}
              </div>
            </section>

            <section>
              <h3 className="mb-2 text-xs font-semibold text-slate-800">最终触发条件</h3>
              <textarea value={draft.condition || ''} onChange={event => setDraft({ ...draft, condition: event.target.value || null, conditionRows: [] })}
                placeholder="例如 a.status == 'pending' and a.amount > 1000" aria-label="动态哨兵触发条件"
                className="min-h-24 w-full rounded-md border border-slate-200 bg-slate-950 px-3 py-2 font-mono text-xs leading-5 text-slate-100 outline-none focus:border-teal-400" />
            </section>

            <section>
              <h3 className="mb-2 text-xs font-semibold text-slate-800">触发动作</h3>
              <div className="grid grid-cols-2 gap-2">
                {actions.map(action => {
                  const checked = draft.actionIds.includes(action.id)
                  return <label key={action.id} className={`flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-xs ${checked ? 'border-teal-300 bg-teal-50 text-teal-800' : 'border-slate-200 text-slate-600'}`}>
                    <input type="checkbox" checked={checked} onChange={() => {
                      const actionParameters = { ...draft.actionParameters }
                      if (checked) delete actionParameters[action.id]
                      setDraft({
                        ...draft,
                        actionIds: checked ? draft.actionIds.filter(id => id !== action.id) : [...draft.actionIds, action.id],
                        actionParameters,
                      })
                    }} />
                    {action.displayName}
                  </label>
                })}
                {actions.length === 0 && <p className="col-span-2 text-xs text-slate-400">当前助手授权边界内没有可用动作。</p>}
              </div>
            </section>

            <section className="grid grid-cols-2 gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
              <label className="flex items-center gap-2 text-xs text-slate-700"><input type="checkbox" checked={draft.onChange} onChange={event => setDraft({ ...draft, onChange: event.target.checked })} />对象变化时评估</label>
              <label className="flex items-center gap-2 text-xs text-slate-700"><input type="checkbox" checked={draft.onSchedule} onChange={event => setDraft({ ...draft, onSchedule: event.target.checked })} />定时全量扫描</label>
              <label className="text-xs text-slate-600">触发模式
                <select value={draft.triggerMode} onChange={event => setDraft({ ...draft, triggerMode: event.target.value as DynamicSentinelDefinition['triggerMode'] })}
                  className="mt-1 h-8 w-full rounded-md border border-slate-200 bg-white px-2 text-xs">
                  <option value="on_enter">仅新进入时</option><option value="on_enter_leave">进入和离开</option><option value="run_on_all">每轮全部命中</option>
                </select>
              </label>
              <label className="text-xs text-slate-600">扫描间隔（秒）
                <input type="number" min={60} max={86400} value={draft.scanIntervalSeconds} onChange={event => setDraft({ ...draft, scanIntervalSeconds: Number(event.target.value) })}
                  className="mt-1 h-8 w-full rounded-md border border-slate-200 bg-white px-2 text-xs" />
              </label>
            </section>

            <div className="sticky bottom-0 flex justify-end gap-2 border-t border-slate-200 bg-white py-3">
              <button type="button" onClick={() => { setEditing(null); setDraft(null) }} className="h-8 rounded-md border border-slate-200 px-4 text-xs text-slate-600 hover:bg-slate-50">取消</button>
              <button type="button" onClick={() => void save()} disabled={busyId === editing.id}
                className="inline-flex h-8 items-center gap-1.5 rounded-md bg-teal-600 px-4 text-xs font-medium text-white disabled:opacity-40">
                {busyId === editing.id ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}保存并停用
              </button>
            </div>

            {trialActions.length > 0 && (
              <section className="rounded-lg border border-sky-100 bg-sky-50/50 p-3 text-xs text-slate-600">
                <h3 className="mb-2 font-semibold text-slate-800">最近试跑计划动作</h3>
                {trialActions.slice(0, 20).map((item, index) => <div key={index} className="py-1">· {item.actionName} → {item.targetInstanceId || '无目标'}</div>)}
              </section>
            )}
          </div>
        ) : (
          <div className="scrollbar-thin flex-1 overflow-y-auto px-5 py-5">
            {isLoading ? (
              <div className="flex h-40 items-center justify-center gap-2 text-xs text-slate-500"><Loader2 size={15} className="animate-spin text-teal-600" />加载动态哨兵…</div>
            ) : rows.length === 0 ? (
              <div className="flex h-56 flex-col items-center justify-center text-center">
                <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-teal-50 text-teal-600"><BellRing size={22} /></span>
                <p className="text-sm font-medium text-slate-700">还没有动态哨兵</p>
                <p className="mt-1 max-w-xs text-xs leading-5 text-slate-400">在对话中描述监听条件和触发动作，助手会生成经过强校验的创建提案。</p>
              </div>
            ) : (
              <div className="space-y-3">
                {rows.map(row => {
                  const trial = row.lastTrialReport
                  const busy = busyId === row.id
                  return (
                    <article key={row.id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="truncate text-sm font-semibold text-slate-900">{row.displayName}</h3>
                          <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${row.enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>{row.enabled ? '已启用' : '已停用'}</span>
                          {row.validationReport?.compatibility === 'review_required' && <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] text-amber-700">版本变化待复核</span>}
                        </div><p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">{row.description || row.condition || '未填写说明'}</p></div>
                        <button type="button" onClick={() => startEdit(row)} className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label={`编辑${row.displayName}`}><Pencil size={14} /></button>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
                        <span>监听：{row.bindings.map(item => objectName(item.objectTypeId)).join('、')}</span>
                        <span>动作：{row.actionIds.map(actionName).join('、') || '仅监测'}</span>
                      </div>
                      {trial && (
                        <div className={`mt-3 rounded-lg px-3 py-2 text-xs ${trial.passed ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-600'}`}>
                          <div className="flex items-center gap-1.5 font-medium">{trial.passed ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
                            全量试跑{trial.passed ? '通过' : '失败'} · 命中 {trial.matchCount} · 计划动作 {trial.plannedActionCount} · 未执行动作
                          </div>
                          {trial.errors.length > 0 && <p className="mt-1">{trial.errors.join('；')}</p>}
                        </div>
                      )}
                      <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-3">
                        <div className="flex gap-1.5">
                          <button type="button" onClick={() => void run(row.id, () => agentApi.trialDynamicSentinel(oid, releaseId, row.id))} disabled={busy}
                            className="inline-flex h-7 items-center gap-1.5 rounded-md border border-sky-200 bg-sky-50 px-2.5 text-[11px] font-medium text-sky-700 hover:bg-sky-100 disabled:opacity-40">
                            {busy ? <Loader2 size={11} className="animate-spin" /> : <FlaskConical size={11} />}全量试跑
                          </button>
                          <button type="button" onClick={() => void run(row.id, () => agentApi.setDynamicSentinelEnabled(oid, releaseId, row, !row.enabled))}
                            disabled={busy || (!row.enabled && !row.canEnable)} title={!row.enabled && !row.canEnable ? '请先完成通过的全量试跑' : undefined}
                            className={`inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[11px] font-medium disabled:cursor-not-allowed disabled:opacity-35 ${row.enabled ? 'border border-amber-200 bg-amber-50 text-amber-700' : 'bg-teal-600 text-white'}`}>
                            <Power size={11} />{row.enabled ? '停用' : '启用'}
                          </button>
                        </div>
                        <button type="button" onClick={() => { if (window.confirm(`删除动态哨兵“${row.displayName}”？执行历史会保留。`)) void run(row.id, () => agentApi.deleteDynamicSentinel(oid, releaseId, row)) }} disabled={busy}
                          className="flex h-7 w-7 items-center justify-center rounded-md text-slate-400 hover:bg-red-50 hover:text-red-600" aria-label={`删除${row.displayName}`}><Trash2 size={13} /></button>
                      </div>
                    </article>
                  )
                })}
              </div>
            )}
          </div>
        )}
      </aside>
    </div>
  )
}
