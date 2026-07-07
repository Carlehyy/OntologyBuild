import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { apiClientV2 } from '@/api/client'
import { sentinelApi, type Sentinel, type SentinelFiring } from '@/api/sentinelApi'
import {
  HandMetal, Rocket, ShieldAlert, ScrollText, Loader2, CheckCircle2, XCircle,
  Eye, Bolt, ArrowUpCircle, ArrowDownCircle, ExternalLink, AlertTriangle,
} from 'lucide-react'

/* 治理与推演驾驶舱：
   ① 待审批 —— 人是最终裁决者，批准/拒绝都是事实
   ② 自治等级 —— 按人工批准率逐级放权（影子→人审→自动）
   ③ 哨兵 —— 平台正在替你盯什么、最近命中了什么
   ④ 事实流 —— 每一个变化的出处与因果，全量留痕 */

interface PendingLog {
  id: string; actionId: string; actionName: string | null
  objectInstanceId: string | null; parameters: Record<string, unknown>
  actorId: string | null; executedAt: string
}

interface AutonomyStat {
  actionId: string; actionName: string; requiresApproval: boolean
  level: 'L0' | 'L1' | 'L2'; shadow: boolean
  sentinels: { id: string; name: string; muted: boolean; enabled: boolean }[]
  decisions: { approved: number; rejected: number; total: number; recentCount: number; recentApprovalRate: number | null }
  autoRuns: { total: number; failed: number }
  pending: number
  recommendation: 'promote' | 'demote' | 'observe' | null
  recommendationReason: string | null
  thresholds: { promoteMinDecisions: number; promoteRate: number }
}

interface FactRow {
  id: string; subjectLabel: string; propertyName: string; value: unknown
  kind: string; source: string; actorId?: string | null
  causedBy?: string | null; supersedesId?: string | null; recordedAt: string | null
}

const KIND_META: Record<string, { label: string; cls: string; title: string }> = {
  property: { label: '属性', cls: 'bg-blue-50 text-blue-600 border-blue-200', title: '数据源/人工写入的存储属性变化' },
  derived: { label: '派生', cls: 'bg-purple-50 text-purple-600 border-purple-200', title: '函数自动重算的派生值（可溯源到输入事实）' },
  link: { label: '链接', cls: 'bg-cyan-50 text-cyan-600 border-cyan-200', title: '关系的建立/解除' },
  object: { label: '存在', cls: 'bg-red-50 text-red-600 border-red-200', title: '实例存在性（删除留墓碑）' },
  decision: { label: '决策', cls: 'bg-amber-50 text-amber-700 border-amber-200', title: '人的审批决策（批准/拒绝都记录）' },
  absence: { label: '缺席', cls: 'bg-gray-100 text-gray-500 border-gray-300', title: '查询结果为空/非空的翻转快照——"没有"也有出处' },
}

const LEVEL_META: Record<string, { label: string; icon: any; cls: string; desc: string }> = {
  L0: { label: 'L0 影子', icon: Eye, cls: 'bg-gray-100 text-gray-600 border-gray-300', desc: '哨兵全部静默，只观察不动手' },
  L1: { label: 'L1 人审', icon: HandMetal, cls: 'bg-blue-50 text-blue-700 border-blue-300', desc: '每次执行等人批准' },
  L2: { label: 'L2 自动', icon: Bolt, cls: 'bg-emerald-50 text-emerald-700 border-emerald-300', desc: '命中即执行' },
}

function SectionHead({ icon: Icon, iconCls, title, sub, extra }: {
  icon: any; iconCls: string; title: string; sub: string; extra?: React.ReactNode
}) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <Icon size={15} className={iconCls} />
      <p className="text-sm font-medium text-gray-700">{title}</p>
      <span className="text-xs text-gray-400">{sub}</span>
      {extra && <div className="ml-auto">{extra}</div>}
    </div>
  )
}

export default function GovernanceTab({ ontologyId }: { ontologyId: string }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [kindFilter, setKindFilter] = useState('')

  const { data: pending = [] } = useQuery<PendingLog[]>({
    queryKey: ['gov-pending', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/pending-actions`) as any,
    refetchInterval: 15000,
  })
  const { data: autonomy = [] } = useQuery<AutonomyStat[]>({
    queryKey: ['gov-autonomy', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/autonomy`) as any,
  })
  const { data: sentinels = [] } = useQuery<Sentinel[]>({
    queryKey: ['gov-sentinels', ontologyId],
    queryFn: () => sentinelApi.list(ontologyId) as any,
  })
  const { data: firings = [] } = useQuery<SentinelFiring[]>({
    queryKey: ['gov-firings', ontologyId],
    queryFn: () => sentinelApi.firings(ontologyId) as any,
  })
  const { data: facts = [] } = useQuery<FactRow[]>({
    queryKey: ['gov-facts', ontologyId, kindFilter],
    queryFn: () => apiClientV2.get(
      `/formal/ontologies/${ontologyId}/facts/recent?limit=50${kindFilter ? `&kind=${kindFilter}` : ''}`) as any,
  })

  const refreshAll = () => {
    qc.invalidateQueries({ queryKey: ['gov-pending', ontologyId] })
    qc.invalidateQueries({ queryKey: ['gov-autonomy', ontologyId] })
    qc.invalidateQueries({ queryKey: ['gov-facts', ontologyId] })
    qc.invalidateQueries({ queryKey: ['formal-overview', ontologyId] })
  }

  const decide = async (log: PendingLog, decision: 'approved' | 'rejected') => {
    let reason: string | undefined
    if (decision === 'rejected') {
      const input = window.prompt('拒绝原因（会记录进决策事实，可留空）：')
      if (input === null) return
      reason = input || undefined
    }
    setBusy(log.id)
    setMsg(null)
    try {
      await apiClientV2.post(`/formal/ontologies/${ontologyId}/action-logs/${log.id}/decide`,
        { decision, reason })
      setMsg({ ok: true, text: decision === 'approved' ? '已批准并执行，决策已写入事实流。' : '已拒绝，决策已写入事实流。' })
      refreshAll()
    } catch (e: any) {
      setMsg({ ok: false, text: e?.detail?.message || e?.detail || e?.message || '决策失败' })
    } finally {
      setBusy(null)
    }
  }

  const toggleGate = async (s: AutonomyStat, requiresApproval: boolean) => {
    const verb = requiresApproval ? '降级到 L1（加回人工审批）' : '晋升到 L2（关闭审批，命中即自动执行）'
    if (!window.confirm(`确定把「${s.actionName}」${verb}？`)) return
    setBusy(s.actionId)
    try {
      await apiClientV2.put(`/formal/ontologies/${ontologyId}/actions/${s.actionId}`,
        { requiresApproval })
      refreshAll()
    } catch (e: any) {
      setMsg({ ok: false, text: e?.detail || e?.message || '切换失败' })
    } finally {
      setBusy(null)
    }
  }

  const fmtTime = (iso?: string | null) => iso
    ? new Date(iso).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : '-'
  const fmtVal = (v: unknown) => {
    if (v === null || v === undefined) return '∅'
    const s = typeof v === 'object' ? JSON.stringify(v) : String(v)
    return s.length > 30 ? s.slice(0, 30) + '…' : s
  }
  const pct = (v: number | null) => (v === null ? '—' : `${Math.round(v * 100)}%`)

  return (
    <div className="space-y-4">
      {msg && (
        <div className={`px-3 py-2 rounded-lg border text-xs ${
          msg.ok ? 'bg-green-50 border-green-200 text-green-700' : 'bg-red-50 border-red-200 text-red-600'
        }`}>{msg.text}</div>
      )}

      {/* ① 待审批 */}
      <div className="rounded-xl border bg-white p-4">
        <SectionHead icon={HandMetal} iconCls="text-blue-500" title="待审批"
          sub="人是最终裁决者 · 批准/拒绝都写入决策事实" />
        {pending.length === 0 ? (
          <p className="text-xs text-gray-400 py-3 text-center">没有等待审批的动作。开启动作的「需人工审批」后，真实执行会先在这里等你拍板。</p>
        ) : (
          <div className="space-y-2">
            {pending.map(l => (
              <div key={l.id} className="flex items-center gap-3 rounded-lg border border-blue-200 bg-blue-50/50 px-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-gray-800 font-medium">{l.actionName || l.actionId}</p>
                  <p className="text-xs text-gray-500 truncate">
                    {l.objectInstanceId && <>目标 <code className="text-gray-600">{l.objectInstanceId.slice(0, 10)}…</code> · </>}
                    {Object.entries(l.parameters || {}).slice(0, 3).map(([k, v]) => `${k}=${fmtVal(v)}`).join('，') || '无参数'}
                    <span className="text-gray-400"> · {l.actorId ? '人工发起' : '哨兵触发'} · {fmtTime(l.executedAt)}</span>
                  </p>
                </div>
                <button onClick={() => void decide(l, 'approved')} disabled={busy === l.id}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50 shrink-0">
                  {busy === l.id ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />}
                  批准并执行
                </button>
                <button onClick={() => void decide(l, 'rejected')} disabled={busy === l.id}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs border border-red-300 text-red-600 hover:bg-red-50 disabled:opacity-50 shrink-0">
                  <XCircle size={12} /> 拒绝
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ② 自治等级 */}
      <div className="rounded-xl border bg-white p-4">
        <SectionHead icon={Rocket} iconCls="text-amber-500" title="自治等级"
          sub="影子 → 人审 → 自动：自治是按批准率挣来的" />
        {autonomy.length === 0 ? (
          <p className="text-xs text-gray-400 py-3 text-center">还没有动作。在图谱编辑器创建动作并绑定哨兵后，这里管理每个动作的放权等级。</p>
        ) : (
          <div className="space-y-2">
            {autonomy.map(s => {
              const meta = LEVEL_META[s.level]
              const r = s.decisions.recentApprovalRate
              return (
                <div key={s.actionId} className={`rounded-lg border px-3 py-2.5 ${
                  s.recommendation === 'promote' ? 'border-emerald-300 bg-emerald-50/40'
                  : s.recommendation === 'demote' ? 'border-red-300 bg-red-50/40' : 'border-gray-200'
                }`}>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-medium ${meta.cls}`} title={meta.desc}>
                      <meta.icon size={11} /> {meta.label}
                    </span>
                    <span className="text-sm text-gray-800 font-medium">{s.actionName}</span>
                    {s.pending > 0 && <span className="text-[10px] px-1.5 rounded bg-blue-100 text-blue-700">{s.pending} 待审批</span>}
                    {s.sentinels.map(sn => (
                      <span key={sn.id} className={`text-[10px] px-1.5 rounded ${sn.muted ? 'bg-gray-100 text-gray-400' : 'bg-rose-50 text-rose-500'}`}>
                        {sn.name}{sn.muted ? '·影子' : ''}
                      </span>
                    ))}
                    <div className="ml-auto flex gap-1.5">
                      {s.level === 'L1' && (
                        <button onClick={() => void toggleGate(s, false)}
                          disabled={busy === s.actionId || s.recommendation !== 'promote'}
                          title={s.recommendation === 'promote' ? '批准率达标，可放权自动执行'
                            : `晋升条件：近 ${s.thresholds.promoteMinDecisions} 次批准率 ≥ ${Math.round(s.thresholds.promoteRate * 100)}%（当前 ${s.decisions.recentCount} 次 / ${pct(r)}）`}
                          className={`inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] border ${
                            s.recommendation === 'promote'
                              ? 'bg-emerald-600 text-white border-emerald-600 hover:bg-emerald-700'
                              : 'border-gray-200 text-gray-300 cursor-not-allowed'
                          }`}>
                          <ArrowUpCircle size={12} /> 晋升 L2
                        </button>
                      )}
                      {s.level === 'L2' && (
                        <button onClick={() => void toggleGate(s, true)} disabled={busy === s.actionId}
                          className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] border border-blue-300 text-blue-600 hover:bg-blue-50">
                          <ArrowDownCircle size={12} /> 降级 L1
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="mt-2 flex items-center gap-2 text-[11px] text-gray-500">
                    <span className="w-16 shrink-0">近期批准率</span>
                    <div className="flex-1 h-1.5 rounded-full bg-gray-100 overflow-hidden">
                      <div className={`h-full rounded-full ${
                        r !== null && r >= s.thresholds.promoteRate ? 'bg-emerald-400'
                        : r !== null && r >= 0.5 ? 'bg-amber-400' : 'bg-red-300'
                      }`} style={{ width: `${Math.round((r ?? 0) * 100)}%` }} />
                    </div>
                    <span className="font-mono">{pct(r)}</span>
                    <span className="text-gray-400">({s.decisions.recentCount}/{s.thresholds.promoteMinDecisions})</span>
                    <span className="text-gray-400 ml-2">累计 👍{s.decisions.approved} 👎{s.decisions.rejected} · 自动 {s.autoRuns.total}{s.autoRuns.failed ? `（失败 ${s.autoRuns.failed}）` : ''}</span>
                  </div>
                  {s.recommendationReason && (
                    <p className={`mt-1.5 text-[11px] ${
                      s.recommendation === 'promote' ? 'text-emerald-600'
                      : s.recommendation === 'demote' ? 'text-red-600' : 'text-gray-500'
                    }`}>{s.recommendationReason}</p>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* ③ 哨兵与触发 */}
      <div className="rounded-xl border bg-white p-4">
        <SectionHead icon={ShieldAlert} iconCls="text-rose-500" title="哨兵"
          sub="平台正在替你盯什么"
          extra={<button onClick={() => navigate(`/ontologies/${ontologyId}/graph`)}
            className="text-xs text-rose-500 hover:underline inline-flex items-center gap-1">
            去图谱编辑器管理 <ExternalLink size={11} /></button>} />
        {sentinels.length === 0 ? (
          <p className="text-xs text-gray-400 py-3 text-center">还没有哨兵。哨兵 = 常驻监听条件 + 命中执行动作，是治理与推演的发动机。</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-3">
            {sentinels.map(s => (
              <div key={s.id} className="flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-xs">
                <span className={`w-2 h-2 rounded-full shrink-0 ${!s.enabled ? 'bg-gray-300' : (s as any).muted ? 'bg-amber-400' : 'bg-emerald-500'}`}
                  title={!s.enabled ? '已停用' : (s as any).muted ? '影子（只记录不执行）' : '在线'} />
                <span className="text-gray-800 font-medium truncate">{s.displayName}</span>
                {(s as any).muted && <span className="text-[10px] px-1 rounded bg-amber-50 text-amber-600 border border-amber-200">影子</span>}
                {s.condition && <code className="text-gray-400 truncate flex-1" title={s.condition}>{s.condition}</code>}
              </div>
            ))}
          </div>
        )}
        {firings.length > 0 && (
          <div className="space-y-1 pt-2 border-t">
            <p className="text-xs text-gray-400 mb-1.5">最近触发</p>
            {firings.slice(0, 8).map(f => (
              <div key={f.id} className="flex items-center gap-2 text-xs py-0.5">
                {f.status === 'error'
                  ? <AlertTriangle size={12} className="text-red-400 shrink-0" />
                  : <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                      f.status === 'fired' ? 'bg-rose-400' : f.status === 'no_match' ? 'bg-gray-300' : 'bg-amber-300'}`} />}
                <span className="text-gray-600 truncate max-w-[140px]">{f.sentinelName}</span>
                <span className={`px-1 rounded text-[10px] ${f.status === 'error' ? 'bg-red-50 text-red-500' : 'bg-gray-50 text-gray-400'}`}>{f.status}</span>
                <span className="text-gray-400">命中 {f.matchCount}</span>
                {f.error && <span className="text-red-400 truncate flex-1" title={f.error}>{f.error}</span>}
                <span className="ml-auto text-gray-400 shrink-0">{fmtTime(f.createdAt)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ④ 事实流 */}
      <div className="rounded-xl border bg-white p-4">
        <SectionHead icon={ScrollText} iconCls="text-indigo-500" title="事实流"
          sub="追加不修改 · 每个变化都有出处与因果"
          extra={
            <div className="flex gap-1">
              <button onClick={() => setKindFilter('')}
                className={`px-2 py-0.5 rounded-full text-[10px] border ${!kindFilter ? 'bg-indigo-50 border-indigo-300 text-indigo-600' : 'border-gray-200 text-gray-400 hover:text-gray-600'}`}>
                全部
              </button>
              {Object.entries(KIND_META).map(([k, m]) => (
                <button key={k} onClick={() => setKindFilter(k)} title={m.title}
                  className={`px-2 py-0.5 rounded-full text-[10px] border ${kindFilter === k ? m.cls : 'border-gray-200 text-gray-400 hover:text-gray-600'}`}>
                  {m.label}
                </button>
              ))}
            </div>
          } />
        {facts.length === 0 ? (
          <p className="text-xs text-gray-400 py-3 text-center">暂无{kindFilter ? `「${KIND_META[kindFilter]?.label}」类` : ''}事实。</p>
        ) : (
          <div className="space-y-0.5 max-h-96 overflow-y-auto">
            {facts.map(f => (
              <div key={f.id} className="flex items-center gap-2 text-xs py-1 border-b border-gray-50 last:border-0">
                <span className={`px-1.5 py-0.5 rounded border text-[10px] shrink-0 ${KIND_META[f.kind]?.cls ?? KIND_META.property.cls}`}
                  title={KIND_META[f.kind]?.title}>
                  {KIND_META[f.kind]?.label ?? f.kind}
                </span>
                <span className="text-gray-600 truncate max-w-[140px]" title={f.subjectLabel}>{f.subjectLabel}</span>
                <span className="font-mono text-gray-400 truncate max-w-[110px]">{f.propertyName}</span>
                <span className="text-gray-300">=</span>
                <span className="font-mono text-gray-700 truncate flex-1" title={String(fmtVal(f.value))}>{fmtVal(f.value)}</span>
                {f.causedBy && <span className="text-[10px] text-gray-400 shrink-0" title={`因果指针 → ${f.causedBy}`}>因果</span>}
                {f.supersedesId && <span className="text-[10px] text-violet-400 shrink-0" title="覆盖了旧事实">⤴</span>}
                <span className="text-gray-400 truncate max-w-[110px] shrink-0" title={f.source}>{f.source}</span>
                <span className="text-gray-400 shrink-0">{fmtTime(f.recordedAt)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
