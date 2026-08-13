import { useCallback, useEffect, useState } from 'react'
import {
  Brain, Check, GitBranch, Loader2, Pin, PinOff, Plus, Sparkles, Trash2, X,
} from 'lucide-react'

import {
  superAssistantApi,
  type ReflectionCandidate,
  type ReflectionSettings,
  type SuperMemory,
} from '@/api/superAssistant'
import { useToast } from '@/components/ui/Toast'
import { errorText } from './assistantPanelUtils'
import {
  ZONE_LABELS,
  candidateActions,
  filterMemories,
  memoryConflictDescription,
  zoneLabel,
} from './evolutionLogic'

function ZoneTag({ zone }: { zone: string }) {
  return (
    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">{zoneLabel(zone)}</span>
  )
}

function ConfidenceTag({ confidence }: { confidence: string }) {
  const tone = confidence === 'high' ? 'bg-emerald-50 text-emerald-700'
    : confidence === 'low' ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-600'
  return <span className={`rounded px-1.5 py-0.5 text-[10px] ${tone}`}>{confidence}</span>
}

/** 待审批：反思产出的 memory/skill/conflict 候选 */
export function ApprovalTab({ conversationId }: { conversationId: string | null }) {
  const { toast } = useToast()
  const [candidates, setCandidates] = useState<ReflectionCandidate[]>([])
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [reflecting, setReflecting] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setCandidates(await superAssistantApi.reflectionCandidates('pending'))
    } catch (error) {
      toast({ tone: 'error', title: '加载待审批候选失败', description: errorText(error) })
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => {
    setLoading(true)
    refresh()
    const timer = window.setInterval(refresh, 5000)
    return () => window.clearInterval(timer)
  }, [refresh])

  const decide = async (candidate: ReflectionCandidate, decision: string) => {
    if (busyId) return
    setBusyId(candidate.id)
    try {
      await superAssistantApi.decideReflectionCandidate(candidate.id, decision)
      toast({ tone: 'success', title: decision === 'accept' || decision === 'new_supersedes' ? '已接受' : '已处理' })
      await refresh()
    } catch (error) {
      toast({ tone: 'error', title: '审批操作失败', description: errorText(error) })
    } finally {
      setBusyId(null)
    }
  }

  const runFullReflection = async () => {
    if (!conversationId || reflecting) return
    setReflecting(true)
    try {
      await superAssistantApi.runFullReflection(conversationId)
      toast({ tone: 'success', title: '反思已在后台执行', description: '产出的候选会稍后出现在这里' })
      window.setTimeout(refresh, 3000)
    } catch (error) {
      toast({ tone: 'error', title: '触发反思失败', description: errorText(error) })
    } finally {
      setReflecting(false)
    }
  }

  const actions = (candidate: ReflectionCandidate) => (
    <div className="mt-3 flex flex-wrap gap-2">
      {candidateActions(candidate.kind).map(option => (
        <button
          key={option.decision}
          type="button"
          disabled={busyId === candidate.id}
          onClick={() => decide(candidate, option.decision)}
          className={option.primary
            ? 'flex min-h-8 items-center gap-1 rounded-lg bg-teal-600 px-3 text-xs font-medium text-white transition-colors hover:bg-teal-700 disabled:opacity-50'
            : 'flex min-h-8 items-center gap-1 rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)] disabled:opacity-50'}
        >
          {busyId === candidate.id ? <Loader2 size={12} className="animate-spin" /> : option.primary ? <Check size={12} /> : <X size={12} />}
          {option.label}
        </button>
      ))}
    </div>
  )

  return (
    <div className="grid gap-3" data-testid="approval-tab">
      <button
        type="button"
        onClick={runFullReflection}
        disabled={!conversationId || reflecting}
        className="flex min-h-9 items-center justify-center gap-1.5 rounded-lg border border-teal-200 bg-teal-50 text-xs font-medium text-teal-700 transition-colors hover:bg-teal-100 disabled:cursor-not-allowed disabled:opacity-50"
        title={conversationId ? '对当前会话执行一次完整反思' : '请先选择一个会话'}
      >
        {reflecting ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
        立即反思当前会话
      </button>
      {loading && candidates.length === 0 && (
        <div className="p-10 text-center text-xs text-[var(--color-text-tertiary)]"><Loader2 size={18} className="mx-auto animate-spin" /></div>
      )}
      {!loading && candidates.length === 0 && (
        <div className="rounded-xl border border-dashed border-[var(--color-border)] p-10 text-center text-xs text-[var(--color-text-tertiary)]">
          <Check size={22} className="mx-auto mb-2" />没有待审批的候选
        </div>
      )}
      {candidates.map(candidate => {
        const payload = candidate.payload || {}
        return (
          <article key={candidate.id} data-testid={`candidate-${candidate.kind}`} className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-4">
            <div className="flex items-center gap-1.5">
              {candidate.kind === 'memory' && <Brain size={14} className="shrink-0 text-teal-600" />}
              {candidate.kind === 'skill' && <Sparkles size={14} className="shrink-0 text-teal-600" />}
              {candidate.kind === 'conflict' && <GitBranch size={14} className="shrink-0 text-amber-600" />}
              <span className="text-xs font-semibold text-[var(--color-text-primary)]">
                {candidate.kind === 'memory' ? '新记忆' : candidate.kind === 'skill' ? '新 Skill' : '记忆冲突'}
              </span>
              <ConfidenceTag confidence={candidate.confidence} />
              {candidate.kind === 'memory' && payload.zone ? <ZoneTag zone={String(payload.zone)} /> : null}
            </div>

            {candidate.kind === 'memory' && (
              <>
                <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-[var(--color-text-primary)]">{String(payload.content || '')}</p>
                {Array.isArray(payload.supersedes) && payload.supersedes.length > 0 && (
                  <p className="mt-1 text-[10px] text-amber-600">接受后将取代 {payload.supersedes.length} 条旧记忆</p>
                )}
                {actions(candidate)}
              </>
            )}

            {candidate.kind === 'skill' && (
              <>
                <p className="mt-2 font-mono text-xs font-semibold text-[var(--color-text-primary)]">{String(payload.name || '')}</p>
                <p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">{String(payload.description || '')}</p>
                {payload.skill_md ? (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-[10px] text-teal-700 hover:underline">预览 SKILL.md</summary>
                    <pre className="mt-1 max-h-48 overflow-auto rounded-lg bg-slate-50 p-2 text-[10px] leading-4 whitespace-pre-wrap">{String(payload.skill_md)}</pre>
                  </details>
                ) : null}
                {actions(candidate)}
              </>
            )}

            {candidate.kind === 'conflict' && (
              <>
                <p className="mt-2 text-xs leading-5 text-[var(--color-text-primary)]">{String(payload.explain || '与现有记忆冲突')}</p>
                <p className="mt-1 text-[10px] text-[var(--color-text-tertiary)]">冲突类型：{String(payload.conflict_kind || '-')}</p>
                {payload.candidate_content ? (
                  <p className="mt-2 rounded-lg bg-slate-50 p-2 text-[11px] leading-4 text-[var(--color-text-secondary)]">建议新内容：{String(payload.candidate_content)}</p>
                ) : null}
                {actions(candidate)}
              </>
            )}
          </article>
        )
      })}
    </div>
  )
}

/** 记忆：浏览/搜索/pin/删除/手动新增 + auto-accept 开关 */
export function MemoryTab() {
  const { toast } = useToast()
  const [memories, setMemories] = useState<SuperMemory[]>([])
  const [settings, setSettings] = useState<ReflectionSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [zone, setZone] = useState('')
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState({ content: '', zone: 'general', pinned: false })
  const [saving, setSaving] = useState(false)
  const [togglingAuto, setTogglingAuto] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [memoryList, reflectionSettings] = await Promise.all([
        superAssistantApi.memories(),
        superAssistantApi.reflectionSettings(),
      ])
      setMemories(memoryList)
      setSettings(reflectionSettings)
    } catch (error) {
      toast({ tone: 'error', title: '加载记忆失败', description: errorText(error) })
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => {
    setLoading(true)
    refresh()
  }, [refresh])

  const toggleAutoAccept = async () => {
    if (!settings || togglingAuto) return
    setTogglingAuto(true)
    try {
      const updated = await superAssistantApi.updateReflectionSettings({ auto_accept_enabled: !settings.auto_accept_enabled })
      setSettings(updated)
    } catch (error) {
      toast({ tone: 'error', title: '设置更新失败', description: errorText(error) })
    } finally {
      setTogglingAuto(false)
    }
  }

  const togglePin = async (memory: SuperMemory) => {
    try {
      await superAssistantApi.updateMemory(memory.id, { pinned: !memory.pinned })
      await refresh()
    } catch (error) {
      toast({ tone: 'error', title: '更新失败', description: errorText(error) })
    }
  }

  const removeMemory = async (memory: SuperMemory) => {
    if (!window.confirm('确定删除这条记忆？')) return
    try {
      await superAssistantApi.deleteMemory(memory.id)
      toast({ tone: 'success', title: '记忆已删除' })
      await refresh()
    } catch (error) {
      toast({ tone: 'error', title: '删除失败', description: errorText(error) })
    }
  }

  const createMemory = async () => {
    if (!draft.content.trim() || saving) return
    setSaving(true)
    try {
      await superAssistantApi.createMemory({
        content: draft.content.trim(),
        zone: draft.zone,
        pinned: draft.pinned,
      })
      toast({ tone: 'success', title: '记忆已保存' })
      setCreating(false)
      setDraft({ content: '', zone: 'general', pinned: false })
      await refresh()
    } catch (error: any) {
      const conflict = memoryConflictDescription(error)
      if (conflict) {
        toast({ tone: 'error', title: '与现有记忆过于相似', description: conflict })
      } else {
        toast({ tone: 'error', title: '保存失败', description: errorText(error) })
      }
    } finally {
      setSaving(false)
    }
  }

  const zones = Array.from(new Set(memories.map(memory => memory.zone)))
  const filtered = filterMemories(memories, { query, zone })

  return (
    <div className="grid gap-3" data-testid="memory-tab">
      <div className="flex items-center justify-between rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-3">
        <div className="min-w-0">
          <p className="text-xs font-medium text-[var(--color-text-primary)]">自动记住低风险记忆</p>
          <p className="mt-0.5 text-[10px] text-[var(--color-text-tertiary)]">
            共 {settings?.memory_count ?? memories.length} 条 · 待审批 {settings?.pending_count ?? 0} 条
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={settings?.auto_accept_enabled ?? true}
          data-testid="auto-accept-switch"
          onClick={toggleAutoAccept}
          disabled={!settings || togglingAuto}
          className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${settings?.auto_accept_enabled ? 'bg-teal-600' : 'bg-slate-300'}`}
        >
          <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${settings?.auto_accept_enabled ? 'left-[22px]' : 'left-0.5'}`} />
        </button>
      </div>

      <div className="flex gap-2">
        <input
          value={query}
          onChange={event => setQuery(event.target.value)}
          placeholder="搜索记忆内容或标签…"
          className="min-h-9 flex-1 rounded-lg border border-[var(--color-border)] bg-transparent px-3 text-xs outline-none focus:border-teal-500"
        />
        <select
          value={zone}
          onChange={event => setZone(event.target.value)}
          className="min-h-9 rounded-lg border border-[var(--color-border)] bg-transparent px-2 text-xs outline-none focus:border-teal-500"
        >
          <option value="">全部分区</option>
          {zones.map(item => <option key={item} value={item}>{zoneLabel(item)}</option>)}
        </select>
        <button
          type="button"
          onClick={() => setCreating(true)}
          aria-label="新增记忆"
          className="flex min-h-9 items-center gap-1 rounded-lg bg-teal-600 px-3 text-xs font-medium text-white hover:bg-teal-700"
        >
          <Plus size={13} />新增
        </button>
      </div>

      {loading && <div className="p-10 text-center text-xs text-[var(--color-text-tertiary)]"><Loader2 size={18} className="mx-auto animate-spin" /></div>}
      {!loading && filtered.length === 0 && (
        <div className="rounded-xl border border-dashed border-[var(--color-border)] p-10 text-center text-xs text-[var(--color-text-tertiary)]">
          <Brain size={22} className="mx-auto mb-2" />{memories.length === 0 ? '暂无记忆，助手会在对话中逐步学习' : '没有匹配的记忆'}
        </div>
      )}
      {filtered.map(memory => (
        <article key={memory.id} data-testid="memory-item" className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-4">
          <div className="flex items-start justify-between gap-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <ZoneTag zone={memory.zone} />
              <ConfidenceTag confidence={memory.confidence} />
              {memory.pinned ? <span className="rounded bg-teal-50 px-1.5 py-0.5 text-[10px] text-teal-700">常驻</span> : null}
              <span className="text-[10px] text-[var(--color-text-tertiary)]" title="命中 / 引用次数">
                {memory.match_count}/{memory.reference_count}
              </span>
            </div>
            <div className="flex shrink-0 gap-1">
              <button type="button" onClick={() => togglePin(memory)} aria-label={memory.pinned ? '取消常驻' : '设为常驻'}
                className="flex h-7 w-7 items-center justify-center rounded-md text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)]">
                {memory.pinned ? <PinOff size={13} /> : <Pin size={13} />}
              </button>
              <button type="button" onClick={() => removeMemory(memory)} aria-label="删除记忆"
                className="flex h-7 w-7 items-center justify-center rounded-md text-[var(--color-text-tertiary)] hover:bg-red-50 hover:text-red-600">
                <Trash2 size={13} />
              </button>
            </div>
          </div>
          <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-[var(--color-text-primary)]">{memory.content}</p>
          {memory.tags.length > 0 && (
            <p className="mt-1 text-[10px] text-[var(--color-text-tertiary)]">{memory.tags.map(tag => `#${tag}`).join(' ')}</p>
          )}
        </article>
      ))}

      {settings && (settings.palace_index || settings.profile) && (
        <details className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-4">
          <summary className="cursor-pointer text-xs font-medium text-[var(--color-text-primary)]">记忆宫殿索引</summary>
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-[10px] leading-4 text-[var(--color-text-secondary)]">
            {settings.palace_index || settings.profile}
          </pre>
        </details>
      )}

      {creating && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-label="新增记忆">
          <div className="w-full max-w-md rounded-2xl bg-[var(--color-bg-elevated)] p-5 shadow-xl">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">新增记忆</h3>
              <button type="button" onClick={() => setCreating(false)} aria-label="关闭"
                className="flex h-8 w-8 items-center justify-center rounded-lg text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]">
                <X size={15} />
              </button>
            </div>
            <textarea
              value={draft.content}
              onChange={event => setDraft({ ...draft, content: event.target.value })}
              rows={4}
              placeholder="要记住的事实，例如：用户偏好简洁的中文回答"
              className="mt-3 w-full rounded-lg border border-[var(--color-border)] bg-transparent p-3 text-xs leading-5 outline-none focus:border-teal-500"
            />
            <div className="mt-3 flex items-center gap-3">
              <select
                value={draft.zone}
                onChange={event => setDraft({ ...draft, zone: event.target.value })}
                className="min-h-9 rounded-lg border border-[var(--color-border)] bg-transparent px-2 text-xs outline-none focus:border-teal-500"
              >
                {Object.entries(ZONE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              <label className="flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
                <input
                  type="checkbox"
                  checked={draft.pinned}
                  onChange={event => setDraft({ ...draft, pinned: event.target.checked })}
                />
                常驻系统提示
              </label>
            </div>
            <button
              type="button"
              onClick={createMemory}
              disabled={!draft.content.trim() || saving}
              className="mt-4 flex min-h-9 w-full items-center justify-center gap-1.5 rounded-lg bg-teal-600 text-xs font-medium text-white hover:bg-teal-700 disabled:opacity-50"
            >
              {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
              保存记忆
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export function EvolutionPendingBadge() {
  const [count, setCount] = useState(0)
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const settings = await superAssistantApi.reflectionSettings()
        if (!cancelled) setCount(settings.pending_count)
      } catch { /* 徽标失败静默 */ }
    }
    load()
    const timer = window.setInterval(load, 15000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [])
  if (!count) return null
  return (
    <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700" data-testid="pending-badge">
      {count}
    </span>
  )
}
