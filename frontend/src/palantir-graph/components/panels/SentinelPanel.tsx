import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { v4 as uuidv4 } from 'uuid'
import {
  ShieldExclamationIcon, PlusIcon, TrashIcon, XMarkIcon, BoltIcon,
} from '@heroicons/react/24/outline'
import { useOntologyStore } from '../../store/ontologyStore'
import { sentinelApi, type Sentinel, type SentinelFiring } from '../../../api/sentinelApi'
import { apiClientV2 } from '../../../api/client'

interface Props {
  isOpen: boolean
  onClose: () => void
}

// 一行结构化条件
interface CondRow {
  leftAlias: string
  leftProp: string
  op: string
  rightKind: 'property' | 'value'   // 右侧:对象属性 / 常量值
  rightAlias?: string
  rightProp?: string
  rightValue?: string
}

interface Draft {
  id?: string
  displayName: string
  description?: string
  bindings: { alias: string; objectTypeId: string }[]
  condRows: CondRow[]
  condLogic: 'and' | 'or'
  advanced: boolean
  conditionRaw: string              // 高级模式直写
  actionIds: string[]
  onChange: boolean
  onSchedule: boolean
  scanIntervalSeconds: number
  triggerMode: 'on_enter' | 'on_enter_leave' | 'run_on_all'
  muted: boolean
  enabled: boolean
}

const _OPS = ['==', '!=', '>', '>=', '<', '<=', 'contains']

// 运算符的中文称谓
const OP_LABEL: Record<string, string> = {
  '==': '等于', '!=': '不等于', '>': '大于', '>=': '大于等于',
  '<': '小于', '<=': '小于等于', 'contains': '包含',
}
// 数值/时间类属性(用于判断可用运算符与输入提示)
const NUM_TYPES = ['number', 'integer', 'int', 'float', 'double', 'decimal', 'currency', 'date', 'datetime', 'time']
const isNumeric = (t?: string) => !!t && NUM_TYPES.includes(t.toLowerCase())
function opsForType(t?: string): string[] {
  if (!t) return ['==', '!=', '>', '>=', '<', '<=']
  const tl = t.toLowerCase()
  if (isNumeric(t)) return ['>', '>=', '<', '<=', '==', '!=']
  if (tl === 'boolean' || tl === 'bool') return ['==', '!=']
  return ['==', '!=', 'contains'] // 文本类
}

const aliasOf = (i: number) => String.fromCharCode(97 + i)
/** 生成首个未占用的代号——删掉中间绑定再添加时不能撞车（后端按 alias 作键） */
const nextAlias = (bindings: { alias: string }[]) => {
  const used = new Set(bindings.map(b => b.alias))
  for (let i = 0; i < 26; i++) {
    const a = aliasOf(i)
    if (!used.has(a)) return a
  }
  return `x${bindings.length}`
}

const emptyRow = (alias: string): CondRow => ({
  leftAlias: alias, leftProp: '', op: '>=', rightKind: 'value', rightValue: '',
})

const emptyDraft = (): Draft => ({
  displayName: '', description: '',
  bindings: [{ alias: 'a', objectTypeId: '' }],
  condRows: [], condLogic: 'and', advanced: false, conditionRaw: '',
  actionIds: [], onChange: true, onSchedule: false, scanIntervalSeconds: 300,
  triggerMode: 'on_enter', muted: false, enabled: true,
})

// 把一行编译成表达式片段
function compileRow(r: CondRow): string | null {
  if (!r.leftAlias || !r.leftProp) return null
  const left = `${r.leftAlias}.${r.leftProp}`
  let right: string
  if (r.rightKind === 'property') {
    if (!r.rightAlias || !r.rightProp) return null
    right = `${r.rightAlias}.${r.rightProp}`
  } else {
    const v = (r.rightValue ?? '').trim()
    if (v === '') return null
    // 数字/布尔保持裸值，其余加引号
    right = /^-?\d+(\.\d+)?$/.test(v) || v === 'true' || v === 'false' ? v : `'${v.replace(/'/g, "\\'")}'`
  }
  if (r.op === 'contains') return `${right} in ${left}`
  return `${left} ${r.op} ${right}`
}

function compileCondition(rows: CondRow[], logic: 'and' | 'or'): string {
  const parts = rows.map(compileRow).filter(Boolean) as string[]
  return parts.join(` ${logic} `)
}

export default function SentinelPanel({ isOpen, onClose }: Props) {
  const { id: ontologyId } = useParams<{ id: string }>()
  const { ontology } = useOntologyStore()
  const workspaceMode = useOntologyStore(s => s.workspaceMode)
  const workspaceVersionId = useOntologyStore(s => s.workspaceVersionId)
  const workspaceSentinels = useOntologyStore(s => s.workspaceSentinels)
  const workspaceTrialRun = useOntologyStore(s => s.workspaceTrialRun)
  const revision = useOntologyStore(s => s.revision)
  const runtimeAccessible = workspaceMode === 'runtime'
  const definitionEditable = runtimeAccessible || workspaceMode === 'draft'
  const [list, setList] = useState<Sentinel[]>([])
  const [firings, setFirings] = useState<SentinelFiring[]>([])
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState<'list' | 'firings'>('list')
  const [error, setError] = useState<string | null>(null)

  const errText = (e: any) =>
    typeof e?.detail === 'string' ? e.detail : (e?.detail?.message || e?.message || '请求失败')

  const objectTypes = ontology?.objectTypes || []
  const linkTypes = ontology?.linkTypes || []
  const actions = ontology?.actions || []

  const otName = (id: string) => objectTypes.find(o => o.id === id)?.displayName || '未选择'
  const propsOf = (objectTypeId: string) =>
    objectTypes.find(o => o.id === objectTypeId)?.properties || []
  // 条件里对象的可读称谓：用对象类型中文名；同类型多个时附代号区分
  const subjectLabel = (alias: string) => {
    const b = draft?.bindings.find(x => x.alias === alias)
    if (!b) return alias
    const name = otName(b.objectTypeId)
    if (name === '未选择') return `对象 ${alias}`
    const dup = (draft?.bindings.filter(x => x.objectTypeId === b.objectTypeId).length || 0) > 1
    return dup ? `${name}(${alias})` : name
  }
  const propType = (alias: string, propName?: string) => {
    const b = draft?.bindings.find(x => x.alias === alias)
    return propsOf(b?.objectTypeId || '').find(p => p.name === propName)?.type as string | undefined
  }

  const refresh = async () => {
    if (!ontologyId) return
    if (!runtimeAccessible) {
      setList(workspaceSentinels)
      const results = workspaceMode === 'trial'
        ? (workspaceTrialRun?.result?.sentinels || [])
        : []
      setFirings(results.map((item, index): SentinelFiring => ({
        id: `trial-${item.id || index}`,
        sentinelId: item.id || `trial-${index}`,
        sentinelName: item.name || '未命名哨兵',
        triggerSource: 'trial',
        status: (item.errors || []).length > 0 ? 'error' : item.skipped ? 'skipped' : 'evaluated',
        matchCount: item.matched || 0,
        matches: [],
        actionResults: [],
        error: (item.errors || []).join('；') || undefined,
        durationMs: 0,
      })))
      setError(null)
      return
    }
    // 失败必须可见：静默吞错会把"后端挂了"伪装成"没有哨兵"
    try {
      const [s, f] = await Promise.all([
        sentinelApi.list(ontologyId),
        sentinelApi.firings(ontologyId),
      ])
      setList((s || []) as Sentinel[]); setFirings((f || []) as SentinelFiring[])
      setError(null)
    } catch (e: any) {
      setError(`加载哨兵失败：${errText(e)}`)
    }
  }

  useEffect(() => { if (isOpen) void refresh() }, [isOpen, ontologyId, runtimeAccessible, workspaceSentinels, workspaceTrialRun])

  // 自动推断关系约束:依据 bindings 两两之间唯一的实体关系
  const inferLinks = (bindings: Draft['bindings']) => {
    const links: { from: string; linkTypeId: string; to: string }[] = []
    for (let i = 0; i < bindings.length; i++) {
      for (let j = i + 1; j < bindings.length; j++) {
        const a = bindings[i], b = bindings[j]
        if (!a.objectTypeId || !b.objectTypeId) continue
        const cands = linkTypes.filter(lt =>
          (lt.sourceObjectTypeId === a.objectTypeId && lt.targetObjectTypeId === b.objectTypeId) ||
          (lt.sourceObjectTypeId === b.objectTypeId && lt.targetObjectTypeId === a.objectTypeId))
        if (cands.length >= 1) {
          const lt = cands[0]
          const fwd = lt.sourceObjectTypeId === a.objectTypeId
          links.push({ from: fwd ? a.alias : b.alias, linkTypeId: lt.id, to: fwd ? b.alias : a.alias })
        }
      }
    }
    return links
  }

  // 推断关系的可读描述/歧义提示
  const relationHint = useMemo(() => {
    if (!draft || draft.bindings.length < 2) return null
    const results: { text: string; ambiguous: boolean }[] = []
    for (let i = 0; i < draft.bindings.length; i++) {
      for (let j = i + 1; j < draft.bindings.length; j++) {
        const a = draft.bindings[i], b = draft.bindings[j]
        if (!a.objectTypeId || !b.objectTypeId) continue
        const cands = linkTypes.filter(lt =>
          (lt.sourceObjectTypeId === a.objectTypeId && lt.targetObjectTypeId === b.objectTypeId) ||
          (lt.sourceObjectTypeId === b.objectTypeId && lt.targetObjectTypeId === a.objectTypeId))
        if (cands.length === 0)
          results.push({ text: `${otName(a.objectTypeId)} 与 ${otName(b.objectTypeId)} 之间未建立关系（将按全组合匹配）`, ambiguous: false })
        else if (cands.length === 1)
          results.push({ text: `已自动关联：${otName(a.objectTypeId)} —${cands[0].displayName}→ ${otName(b.objectTypeId)}`, ambiguous: false })
        else
          results.push({ text: `${otName(a.objectTypeId)} 与 ${otName(b.objectTypeId)} 间有多条关系，默认用「${cands[0].displayName}」`, ambiguous: true })
      }
    }
    return results
  }, [draft?.bindings, linkTypes])

  if (!isOpen) return null

  // 后端 Sentinel → 前端 Draft（回显）
  const toDraft = (s: Sentinel): Draft => ({
    id: s.id, displayName: s.displayName, description: s.description,
    bindings: (s.bindings || []).map(b => ({ alias: b.alias, objectTypeId: b.objectTypeId })),
    condRows: ((s as any).conditionRows || []) as CondRow[],
    condLogic: ((s as any).conditionLogic || 'and') as 'and' | 'or',
    advanced: !((s as any).conditionRows?.length) && !!s.condition,
    conditionRaw: s.condition || '',
    actionIds: s.actionIds || [],
    onChange: s.onChange, onSchedule: s.onSchedule,
    scanIntervalSeconds: s.scanIntervalSeconds,
    triggerMode: ((s as any).triggerMode || 'on_enter'),
    muted: !!(s as any).muted, enabled: s.enabled,
  })

  const save = async () => {
    if (!ontologyId || !draft || !definitionEditable) return
    setBusy(true)
    try {
      const condition = draft.advanced ? draft.conditionRaw : compileCondition(draft.condRows, draft.condLogic)
      const body: any = {
        name: draft.displayName, displayName: draft.displayName, description: draft.description,
        bindings: draft.bindings.map(b => ({ alias: b.alias, objectTypeId: b.objectTypeId, filter: null })),
        links: inferLinks(draft.bindings),
        condition,
        conditionRows: draft.advanced ? [] : draft.condRows,
        conditionLogic: draft.condLogic,
        primaryAlias: draft.bindings[0]?.alias,
        actionIds: draft.actionIds,
        onChange: draft.onChange, onSchedule: draft.onSchedule,
        scanIntervalSeconds: draft.scanIntervalSeconds,
        triggerMode: draft.triggerMode, muted: draft.muted,
        enabled: draft.enabled,
        status: 'published',
      }
      if (runtimeAccessible) {
        if (draft.id) await sentinelApi.update(ontologyId, draft.id, body)
        else await sentinelApi.create(ontologyId, body)
      } else {
        if (!workspaceVersionId) throw new Error('缺少草稿版本标识')
        const id = draft.id || uuidv4()
        const previous = list.find(item => item.id === id)
        const nextSentinel: Sentinel = {
          ...(previous || {} as Sentinel),
          ...body,
          id,
          ontologyId,
          name: previous?.name || draft.displayName,
          status: 'draft',
        }
        const nextList = previous
          ? list.map(item => item.id === id ? nextSentinel : item)
          : [...list, nextSentinel]
        const result = await apiClientV2.put<{ revision: string }>(
          `/ontologies/${ontologyId}/versions/${workspaceVersionId}/workspace/mappings`,
          { baseRevision: revision, sentinels: nextList },
        )
        setList(nextList)
        useOntologyStore.setState({ workspaceSentinels: nextList, revision: result.revision })
      }
      setDraft(null)
      setError(null)
      await refresh()
    } catch (e: any) {
      setError(`保存哨兵失败：${errText(e)}`)
    } finally { setBusy(false) }
  }

  const runNow = async () => {
    if (!ontologyId || !runtimeAccessible) return
    setBusy(true)
    try {
      await sentinelApi.run(ontologyId)
      setError(null)
      await refresh()
      setTab('firings')
    } catch (e: any) {
      setError(`手动触发失败：${errText(e)}`)
    } finally { setBusy(false) }
  }

  const toggleSentinel = async (sentinel: Sentinel) => {
    if (!ontologyId || !definitionEditable) return
    try {
      if (runtimeAccessible) {
        await sentinelApi.toggle(ontologyId, sentinel.id)
        await refresh()
        return
      }
      if (!workspaceVersionId) throw new Error('缺少草稿版本标识')
      const nextList = list.map(item => item.id === sentinel.id ? { ...item, enabled: !item.enabled } : item)
      const result = await apiClientV2.put<{ revision: string }>(
        `/ontologies/${ontologyId}/versions/${workspaceVersionId}/workspace/mappings`,
        { baseRevision: revision, sentinels: nextList },
      )
      setList(nextList)
      useOntologyStore.setState({ workspaceSentinels: nextList, revision: result.revision })
      setError(null)
    } catch (e: any) { setError(`切换启停失败：${errText(e)}`) }
  }

  const removeSentinel = async (sentinel: Sentinel) => {
    if (!ontologyId || !definitionEditable || !confirm('删除该哨兵？触发历史会保留。')) return
    try {
      if (runtimeAccessible) {
        await sentinelApi.remove(ontologyId, sentinel.id)
        await refresh()
        return
      }
      if (!workspaceVersionId) throw new Error('缺少草稿版本标识')
      const nextList = list.filter(item => item.id !== sentinel.id)
      const result = await apiClientV2.put<{ revision: string }>(
        `/ontologies/${ontologyId}/versions/${workspaceVersionId}/workspace/mappings`,
        { baseRevision: revision, sentinels: nextList },
      )
      setList(nextList)
      useOntologyStore.setState({ workspaceSentinels: nextList, revision: result.revision })
      setError(null)
    } catch (e: any) { setError(`删除失败：${errText(e)}`) }
  }

  const setBinding = (i: number, patch: Partial<Draft['bindings'][0]>) => {
    if (!draft) return
    const bs = draft.bindings.map((b, j) => j === i ? { ...b, ...patch } : b)
    setDraft({ ...draft, bindings: bs })
  }
  const setRow = (i: number, patch: Partial<CondRow>) => {
    if (!draft) return
    const rows = draft.condRows.map((r, j) => j === i ? { ...r, ...patch } : r)
    setDraft({ ...draft, condRows: rows })
  }

  return (
    <>
      <div className="fixed inset-0 z-[60] bg-black/30" onClick={onClose} />
      <div className="fixed right-0 top-0 bottom-0 w-[640px] z-[70] glass border-l border-surface-700 animate-slide-in-right flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-surface-700 flex-shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-rose-500/20 flex items-center justify-center">
              <ShieldExclamationIcon className="w-5 h-5 text-rose-400" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-surface-100">哨兵引擎</h2>
              <p className="text-[11px] text-surface-400">监听对象变化 → 条件判断 → 执行动作</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={runNow} disabled={busy || !runtimeAccessible}
              title={!runtimeAccessible ? '只有当前发布态可以手动触发哨兵' : undefined}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-rose-500/90 hover:bg-rose-500 text-white text-xs disabled:opacity-50">
              <BoltIcon className="w-4 h-4" /> 手动触发
            </button>
            <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-surface-800" aria-label="关闭哨兵引擎" title="关闭哨兵引擎">
              <XMarkIcon className="w-5 h-5 text-surface-400" />
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-surface-700 text-xs flex-shrink-0">
          {(['list', 'firings'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-4 py-2.5 ${tab === t ? 'text-rose-400 border-b-2 border-rose-400' : 'text-surface-400'}`}>
              {t === 'list' ? `哨兵 (${list.length})` : `触发日志 (${firings.length})`}
            </button>
          ))}
        </div>

        {/* 唯一滚动容器 */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {!runtimeAccessible && (
            <div role="status" className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs leading-5 text-amber-200">
              {workspaceMode === 'draft'
                ? '草稿态可编辑哨兵定义，但不会评估条件或执行动作。'
                : workspaceMode === 'trial'
                  ? '正在查看冻结的哨兵定义和隔离试跑评估；触发与修改均不可操作。'
                  : '哨兵定义完整可见；历史或归档版本不读取当前正式触发记录，也不可修改。'}
            </div>
          )}
          {error && (
            <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">
              {error}
            </div>
          )}
          {tab === 'list' && !draft && (
            <>
              <button onClick={() => setDraft(emptyDraft())} disabled={!definitionEditable}
                title={!definitionEditable ? '当前状态不可新建哨兵' : undefined}
                className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg border border-dashed border-surface-600 text-surface-300 hover:border-rose-400 hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-40 text-sm">
                <PlusIcon className="w-4 h-4" /> 新建哨兵
              </button>
              {list.map(s => (
                <div key={s.id} className="rounded-lg border border-surface-700 bg-surface-800/40 p-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full ${s.enabled ? 'bg-emerald-400' : 'bg-surface-500'}`} />
                      <span className="text-sm text-surface-100">{s.displayName}</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <button disabled={!definitionEditable} onClick={() => setDraft(toDraft(s))} className="text-[11px] text-surface-400 hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-35 px-1">编辑</button>
                      <button disabled={!definitionEditable} onClick={() => void toggleSentinel(s)}
                        className="text-[11px] text-surface-400 hover:text-emerald-400 disabled:cursor-not-allowed disabled:opacity-35 px-1">{s.enabled ? '停用' : '启用'}</button>
                      <button disabled={!definitionEditable} onClick={() => void removeSentinel(s)}
                        className="p-1 text-surface-400 hover:text-red-400 disabled:cursor-not-allowed disabled:opacity-35"><TrashIcon className="w-3.5 h-3.5" /></button>
                    </div>
                  </div>
                  <div className="mt-2 text-[11px] text-surface-400 space-y-0.5">
                    <div>监听：{(s.bindings || []).map(b => `${otName(b.objectTypeId)}(${b.alias})`).join('、')}</div>
                    {s.condition && <div>条件：<code className="text-amber-300">{s.condition}</code></div>}
                    <div>动作：{s.actionIds?.length || 0} 个 · 时机：{[s.onChange && '变化', s.onSchedule && `扫描${s.scanIntervalSeconds}s`].filter(Boolean).join(' / ') || '仅手动'}</div>
                  </div>
                </div>
              ))}
              {list.length === 0 && <p className="text-center text-xs text-surface-500 py-6">还没有哨兵</p>}
            </>
          )}

          {tab === 'list' && draft && (
            <div className="space-y-4 text-xs">
              {/* 名称 */}
              <div>
                <label className="block text-surface-300 mb-1">哨兵名称</label>
                <input className="inp" value={draft.displayName} placeholder="如：大额订单超信用额度"
                  onChange={e => setDraft({ ...draft, displayName: e.target.value })} />
              </div>

              {/* 1. 监听对象 — 清晰结构 */}
              <div className="rounded-lg border border-surface-700 p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-surface-200">监听对象</div>
                    <div className="text-[10px] text-surface-500">给每个对象类型起个代号，下方条件里用代号引用它的属性</div>
                  </div>
                  <button className="text-rose-400 whitespace-nowrap"
                    onClick={() => setDraft({ ...draft, bindings: [...draft.bindings, { alias: nextAlias(draft.bindings), objectTypeId: '' }] })}>+ 加对象</button>
                </div>
                {draft.bindings.map((b, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <span className="flex items-center gap-1 text-surface-400">
                      代号
                      <span className="w-6 h-6 rounded bg-rose-500/15 text-rose-300 flex items-center justify-center font-mono font-semibold">{b.alias}</span>
                      =
                    </span>
                    <select className="inp flex-1" value={b.objectTypeId}
                      onChange={e => setBinding(i, { objectTypeId: e.target.value })}>
                      <option value="">选择对象类型…</option>
                      {objectTypes.map(o => <option key={o.id} value={o.id}>{o.displayName}</option>)}
                    </select>
                    {draft.bindings.length > 1 && (
                      <button className="text-red-400 px-1" onClick={() => setDraft({ ...draft, bindings: draft.bindings.filter((_, j) => j !== i) })}>×</button>
                    )}
                  </div>
                ))}
              </div>

              {/* 2. 关系约束 — 自动推断，仅展示/歧义时可调 */}
              {relationHint && relationHint.length > 0 && (
                <div className="rounded-lg border border-surface-700 p-3 space-y-1">
                  <div className="text-surface-200">对象关联<span className="text-[10px] text-surface-500 ml-1">（依据本体已有实体关系自动推断）</span></div>
                  {relationHint.map((h, i) => (
                    <div key={i} className={`text-[11px] flex items-start gap-1 ${h.ambiguous ? 'text-amber-300' : 'text-surface-400'}`}>
                      <span>{h.ambiguous ? '⚠' : '↳'}</span><span>{h.text}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* 3. 触发条件 — 句子式逐行，AND/OR，属性比常量/属性 */}
              <div className="rounded-lg border border-surface-700 p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="text-surface-200">触发条件 <span className="text-[10px] text-surface-500 ml-1">满足下列条件时触发</span></div>
                  <label className="flex items-center gap-1 text-[10px] text-surface-400">
                    <input type="checkbox" checked={draft.advanced} onChange={e => setDraft({ ...draft, advanced: e.target.checked })} />
                    高级模式
                  </label>
                </div>

                {draft.advanced ? (
                  <textarea className="inp font-mono h-20 resize-none" placeholder="如 a.amount > b.credit_limit and a.status == 'submitted'"
                    value={draft.conditionRaw} onChange={e => setDraft({ ...draft, conditionRaw: e.target.value })} />
                ) : (
                  <>
                    {draft.condRows.length > 1 && (
                      <div className="flex items-center gap-2 text-[11px] text-surface-300">
                        满足
                        <div className="flex rounded overflow-hidden border border-surface-600">
                          {(['and', 'or'] as const).map(l => (
                            <button key={l} onClick={() => setDraft({ ...draft, condLogic: l })}
                              className={`px-2 py-0.5 ${draft.condLogic === l ? 'bg-rose-500 text-white' : 'text-surface-300'}`}>
                              {l === 'and' ? '以下全部' : '以下任一'}
                            </button>
                          ))}
                        </div>
                        条件
                      </div>
                    )}

                    {draft.condRows.length === 0 && (
                      <p className="text-[11px] text-surface-500 leading-relaxed">
                        还没有条件。点下方「添加条件」，像填一句话一样设置，例如：<br />
                        <span className="text-surface-400">当 订单 的 金额 大于 1000</span>。
                      </p>
                    )}

                    {draft.condRows.map((r, i) => {
                      const leftProps = propsOf(draft.bindings.find(b => b.alias === r.leftAlias)?.objectTypeId || '')
                      const rightProps = propsOf(draft.bindings.find(b => b.alias === r.rightAlias)?.objectTypeId || '')
                      const lt = propType(r.leftAlias, r.leftProp)
                      const ops = opsForType(lt)
                      return (
                        <div key={i} className="rounded border border-surface-700/60 bg-surface-800/40 p-2.5">
                          <div className="flex flex-nowrap items-center gap-1.5">
                            <span className="text-surface-500 shrink-0">当</span>
                            <select className="inp-inline" value={r.leftAlias}
                              onChange={e => setRow(i, { leftAlias: e.target.value, leftProp: '' })}>
                              {draft.bindings.map(b => <option key={b.alias} value={b.alias}>{subjectLabel(b.alias)}</option>)}
                            </select>
                            <span className="text-surface-500">的</span>
                            <select className="inp-inline" value={r.leftProp}
                              onChange={e => setRow(i, { leftProp: e.target.value, op: opsForType(propType(r.leftAlias, e.target.value))[0] })}>
                              <option value="">选择属性</option>
                              {leftProps.map(p => <option key={p.id} value={p.name}>{p.displayName}</option>)}
                            </select>
                            <select className="inp-inline font-medium text-rose-300" value={r.op}
                              onChange={e => setRow(i, { op: e.target.value })}>
                              {ops.map(o => <option key={o} value={o}>{OP_LABEL[o]}</option>)}
                            </select>
                            {r.rightKind === 'value' ? (
                              <input className="inp-inline min-w-[88px]"
                                placeholder={isNumeric(lt) ? '如 1000' : '如 已提交'}
                                value={r.rightValue || ''} onChange={e => setRow(i, { rightValue: e.target.value })} />
                            ) : (
                              <span className="inline-flex items-center gap-1.5">
                                <select className="inp-inline" value={r.rightAlias || ''}
                                  onChange={e => setRow(i, { rightAlias: e.target.value, rightProp: '' })}>
                                  <option value="">对象</option>
                                  {draft.bindings.map(b => <option key={b.alias} value={b.alias}>{subjectLabel(b.alias)}</option>)}
                                </select>
                                <span className="text-surface-500">的</span>
                                <select className="inp-inline" value={r.rightProp || ''}
                                  onChange={e => setRow(i, { rightProp: e.target.value })}>
                                  <option value="">属性</option>
                                  {rightProps.map(p => <option key={p.id} value={p.name}>{p.displayName}</option>)}
                                </select>
                              </span>
                            )}
                            <button className="ml-auto text-surface-500 hover:text-red-400 px-1"
                              onClick={() => setDraft({ ...draft, condRows: draft.condRows.filter((_, j) => j !== i) })}>×</button>
                          </div>
                          <div className="mt-1.5 pl-5">
                            <button className="text-[10px] text-surface-500 hover:text-rose-300"
                              onClick={() => setRow(i, { rightKind: r.rightKind === 'value' ? 'property' : 'value' })}>
                              {r.rightKind === 'value' ? '↔ 改为对比另一个对象的属性' : '↔ 改为对比固定值'}
                            </button>
                          </div>
                        </div>
                      )
                    })}

                    <button className="flex items-center gap-1 text-rose-400 text-[11px]"
                      onClick={() => setDraft({ ...draft, condRows: [...draft.condRows, emptyRow(draft.bindings[0]?.alias || 'a')] })}>
                      <PlusIcon className="w-3.5 h-3.5" /> 添加条件
                    </button>

                    {draft.condRows.length > 0 && (
                      <div className="text-[10px] text-surface-500 break-all border-t border-surface-700/50 pt-1.5">
                        生成的规则：<code className="text-amber-300/80">{compileCondition(draft.condRows, draft.condLogic) || '（条件不完整）'}</code>
                      </div>
                    )}
                  </>
                )}
              </div>

              {/* 命中后执行的动作 — 无内部滚轮 */}
              <div className="rounded-lg border border-surface-700 p-3 space-y-1">
                <div className="text-surface-200 mb-1">命中后执行的动作<span className="text-[10px] text-surface-500 ml-1">（可多选，依次执行）</span></div>
                {actions.map(a => {
                  const requiredParams = (a.parameters || []).filter(p => p.required)
                  const checked = draft.actionIds.includes(a.id)
                  return (
                    <div key={a.id} className="py-0.5">
                      <label className="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" checked={checked}
                          onChange={e => {
                            const set = new Set(draft.actionIds)
                            if (e.target.checked) set.add(a.id)
                            else set.delete(a.id)
                            setDraft({ ...draft, actionIds: [...set] })
                          }} />
                        <span className="text-surface-200">{a.displayName}</span>
                      </label>
                      {checked && requiredParams.length > 0 && (
                        <div className="ml-5 mt-1 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[10px] text-amber-200">
                          该动作有必填参数：{requiredParams.map(p => p.displayName || p.name).join('、')}。哨兵触发时不会弹窗填写参数，执行可能校验失败。
                        </div>
                      )}
                    </div>
                  )
                })}
                {actions.length === 0 && <span className="text-surface-500">本体里还没有动作</span>}
              </div>

              {/* 触发时机 */}
              <div className="rounded-lg border border-surface-700 p-3 space-y-3">
                <div>
                  <div className="text-surface-200 mb-2">触发时机</div>
                  <div className="flex flex-wrap items-center gap-4">
                    <label className="flex items-center gap-1.5"><input type="checkbox" checked={draft.onChange} onChange={e => setDraft({ ...draft, onChange: e.target.checked })} /> 数据变化时</label>
                    <label className="flex items-center gap-1.5"><input type="checkbox" checked={draft.onSchedule} onChange={e => setDraft({ ...draft, onSchedule: e.target.checked })} /> 定期扫描</label>
                    {draft.onSchedule && (
                      <span className="flex items-center gap-1">每
                        <input type="number" className="inp w-16" value={draft.scanIntervalSeconds}
                          onChange={e => setDraft({ ...draft, scanIntervalSeconds: Number(e.target.value) })} /> 秒
                      </span>
                    )}
                  </div>
                </div>
                <div>
                  <div className="text-surface-200 mb-1">触发方式 <span className="text-[10px] text-surface-500 ml-1">条件持续满足时是否重复触发</span></div>
                  <select className="inp" value={draft.triggerMode}
                    onChange={e => setDraft({ ...draft, triggerMode: e.target.value as Draft['triggerMode'] })}>
                    <option value="on_enter">仅在"刚满足"时触发一次（推荐，避免重复）</option>
                    <option value="on_enter_leave">满足时触发 + 条件消除时也触发（用于收尾）</option>
                    <option value="run_on_all">每次都对所有满足的对象执行（电平/批量）</option>
                  </select>
                </div>
                <label className="flex items-center gap-1.5 text-surface-300">
                  <input type="checkbox" checked={draft.muted} onChange={e => setDraft({ ...draft, muted: e.target.checked })} />
                  静默（仍评估并记录命中，但不执行动作——可用于上线前观察）
                </label>
              </div>

              <div className="flex items-center gap-2 pt-1">
                <button onClick={save} disabled={busy || !draft.displayName}
                  className="flex-1 py-2 rounded-lg bg-rose-500 hover:bg-rose-600 text-white disabled:opacity-50">{draft.id ? '保存' : '创建'}</button>
                <button onClick={() => setDraft(null)} className="px-4 py-2 rounded-lg border border-surface-600 text-surface-300">取消</button>
              </div>
            </div>
          )}

          {tab === 'firings' && (
            <div className="space-y-2 text-xs">
              {firings.length === 0 && <p className="text-center text-surface-500 py-6">还没有触发记录</p>}
              {firings.map(f => (
                <div key={f.id} className="rounded-lg border border-surface-700 bg-surface-800/40 p-3">
                  <div className="flex items-center justify-between">
                    <span className="text-surface-100">{f.sentinelName}</span>
                    <span className={`px-1.5 py-0.5 rounded text-[10px] ${f.status === 'fired' ? 'bg-emerald-500/20 text-emerald-300' : f.status === 'error' ? 'bg-red-500/20 text-red-300' : 'bg-surface-600/40 text-surface-300'}`}>{f.status}</span>
                  </div>
                  <div className="mt-1 text-[11px] text-surface-400">
                    来源：{f.triggerSource} · 命中 {f.matchCount} · 动作 {f.actionResults?.length || 0} · {f.durationMs}ms
                  </div>
                  {(f.actionResults || []).map((r: any, i: number) => (
                    <div key={i} className="mt-1 rounded bg-surface-900/50 px-2 py-1.5 text-[11px] text-surface-300">
                      <div>→ {r.status} {(r.effects || []).map((e: any) => e.description).join('; ')}</div>
                      {r.errorMessage && <div className="mt-0.5 text-red-300">{r.errorMessage}</div>}
                      {(r.validationErrors || []).length > 0 && (
                        <div className="mt-0.5 text-red-300">
                          {(r.validationErrors || []).join('；')}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      <style>{`.inp{background:rgb(30 30 38);border:1px solid rgb(60 60 72);border-radius:6px;padding:5px 8px;color:#e5e5ea;font-size:12px;width:100%}.inp:focus{outline:none;border-color:#fb7185}.inp-inline{background:rgb(38 38 48);border:1px solid rgb(63 63 76);border-radius:6px;padding:3px 7px;color:#e5e5ea;font-size:12px;max-width:160px}.inp-inline:focus{outline:none;border-color:#fb7185}select.inp-inline{cursor:pointer}`}</style>
    </>
  )
}
