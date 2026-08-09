/* 待审批 →「待你裁决的故事」。
   每条待审批可就地展开为三段式叙事:
   ① 起因 · 哪个数据变了(命中实例、关键属性值、最近事实、同批命中统计)
   ② 判定 · 哨兵为什么认为要动作(监听对象、条件片、扫描/命中明细)
   ③ 后果 · 批准会发生什么(动作定义 rules + 参数渲染的效果句、通知模板预填)
   裁决按钮(批准/拒绝)保留在折叠行与故事末尾,协议与文案不变。 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowRight, Bolt, CheckCircle2, ChevronDown, Database,
  Eye, Loader2, ShieldAlert, Sparkles, XCircle,
} from 'lucide-react'
import { apiClientV2 } from '@/api/client'
import { readableTargetSummary } from '../tabs/governanceFormat'
import type {
  FiringLike, PendingLogLike, SentinelLike, WorkspaceActionLike,
} from './storyModel'
import {
  buildBindingSentence, buildConditionSentence, buildEffectPreview,
  findTriggerFiring, firingMatchedInstanceIds,
} from './storyModel'

export interface PendingLog extends PendingLogLike {
  objectTypeId?: string | null
  status?: string | null
}

export type WorkspaceAction = WorkspaceActionLike

interface InstanceLite {
  id: string
  objectTypeId: string
  properties: Record<string, unknown>
  externalId?: string | null
}

interface InstanceFactLite {
  id: string
  propertyName: string
  value: unknown
  present: boolean
  kind: string
  source?: string | null
  recordedAt?: string | null
}

const fmtTime = (iso?: string | null) => iso
  ? new Date(iso).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  : '-'

const fmtVal = (v: unknown) => {
  if (v === null || v === undefined) return '∅'
  const s = typeof v === 'object' ? JSON.stringify(v) : String(v)
  return s.length > 30 ? `${s.slice(0, 30)}…` : s
}

function TriggerSourceChip({ source }: { source?: string | null }) {
  const meta = source === 'sentinel'
    ? { label: '哨兵触发', cls: 'border-rose-200 bg-rose-50 text-rose-600' }
    : source === 'manual'
      ? { label: '人工发起', cls: 'border-blue-200 bg-blue-50 text-blue-600' }
      : { label: '系统触发', cls: 'border-gray-200 bg-gray-50 text-gray-500' }
  return <span className={`rounded border px-1.5 py-0.5 text-[11px] ${meta.cls}`}>{meta.label}</span>
}

function Chapter({ icon: Icon, tone, title, children }: {
  icon: any
  tone: string
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="gov-chapter relative pl-9" aria-label={title}>
      <span className={`absolute left-0 top-0 flex h-6 w-6 items-center justify-center rounded-full border bg-white text-[10px] font-semibold ${tone}`}>
        <Icon size={12} />
      </span>
      <p className="text-xs font-semibold text-gray-700">{title}</p>
      <div className="mt-1.5 space-y-1.5 text-xs leading-5 text-gray-600">{children}</div>
    </section>
  )
}

function StoryCard({
  ontologyId,
  log,
  firing,
  sentinel,
  actionDef,
  objectTypeName,
  expanded,
  onToggle,
  canDecide,
  busy,
  onApprove,
  onReject,
}: {
  ontologyId: string
  log: PendingLog
  firing: FiringLike | null
  sentinel: SentinelLike | null
  actionDef: WorkspaceAction | null
  objectTypeName: (objectTypeId: string) => string
  expanded: boolean
  onToggle: () => void
  canDecide: boolean
  busy: boolean
  onApprove: (log: PendingLog) => void
  onReject: (log: PendingLog) => void
}) {
  const paramEntries = Object.entries(log.parameters || {})
  const sentinelTriggered = Boolean(firing) || log.triggerSource === 'sentinel'

  // 展开后才加载:目标类型实例(取值与同批命中标签)与目标实例最近事实。
  const typeInstancesQuery = useQuery<{ items?: InstanceLite[] } | InstanceLite[]>({
    queryKey: ['gov-type-instances', ontologyId, log.objectTypeId],
    queryFn: () => apiClientV2.get(
      `/formal/ontologies/${ontologyId}/instance-browser/objects`,
      { params: { object_type_id: log.objectTypeId, page: 1, page_size: 100 } },
    ) as any,
    enabled: expanded && Boolean(log.objectTypeId),
    staleTime: 30_000,
  })
  const targetFactsQuery = useQuery<InstanceFactLite[]>({
    queryKey: ['instance-facts', ontologyId, log.objectInstanceId],
    queryFn: () => apiClientV2.get(
      `/formal/ontologies/${ontologyId}/instances/${log.objectInstanceId}/facts`,
      { params: { limit: 5 } },
    ) as any,
    enabled: expanded && Boolean(log.objectInstanceId),
    staleTime: 30_000,
  })

  const rawInstances = typeInstancesQuery.data
  const instances: InstanceLite[] = Array.isArray(rawInstances)
    ? rawInstances
    : rawInstances?.items || []
  const targetInstance = instances.find(item => item.id === log.objectInstanceId) || null
  const objectValues = targetInstance?.properties || null
  const conditionSentence = buildConditionSentence(sentinel)
  const matched = firing ? firingMatchedInstanceIds(firing) : { entered: [], others: [] }
  const effectItems = buildEffectPreview({
    action: actionDef,
    parameters: log.parameters || {},
    targetLabel: readableTargetSummary(log),
    typeName: objectTypeName,
    objectValues,
  })

  return (
    <div
      className={`rounded-lg border transition-colors ${
        expanded ? 'border-teal-300 bg-white shadow-sm' : 'border-blue-200 bg-blue-50/50'
      }`}
    >
      <div
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={onToggle}
        onKeyDown={event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            onToggle()
          }
        }}
        className="cursor-pointer px-4 py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 rounded-lg"
      >
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <ChevronDown
                size={14}
                className={`shrink-0 text-gray-400 transition-transform duration-300 ${expanded ? 'rotate-180 text-teal-600' : ''}`}
              />
              <p className="text-sm font-semibold text-gray-800">{log.actionName || log.actionId}</p>
              <TriggerSourceChip source={log.triggerSource ?? (log.actorId ? 'manual' : 'sentinel')} />
              {log.status === 'executing' && (
                <span
                  className="rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-600"
                  title="上次批准后的执行未完成（持久检查点），再次批准将幂等继续执行"
                >
                  执行中 · 可重试批准
                </span>
              )}
              <span className="text-[11px] text-teal-600">
                {expanded ? '收起前因后果' : '展开前因后果'}
              </span>
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
              {log.objectInstanceId && (
                <span title={`实例 ID：${log.objectInstanceId}`}>
                  目标 <span className="font-medium text-gray-700">{readableTargetSummary(log, `${log.objectInstanceId.slice(0, 10)}…`)}</span>
                </span>
              )}
              {paramEntries.length === 0 ? (
                <span className="text-gray-400">无参数</span>
              ) : (
                <>
                  {paramEntries.slice(0, 3).map(([k, v]) => (
                    <span key={k} className="rounded border border-gray-200 bg-white px-1.5 py-0.5 font-mono text-[11px] text-gray-500">
                      {k}={fmtVal(v)}
                    </span>
                  ))}
                  {paramEntries.length > 3 && (
                    <span className="text-[11px] text-gray-400" title={JSON.stringify(log.parameters, null, 2)}>
                      共 {paramEntries.length} 个参数
                    </span>
                  )}
                </>
              )}
              <span className="text-gray-400">{fmtTime(log.executedAt)}</span>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2 self-end sm:self-auto">
            <button
              onClick={event => { event.stopPropagation(); onApprove(log) }}
              disabled={busy || !canDecide}
              title={canDecide ? undefined : '仅管理员可执行审批'}
              className="flex shrink-0 items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {busy ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />}
              批准并执行
            </button>
            <button
              onClick={event => { event.stopPropagation(); onReject(log) }}
              disabled={busy || !canDecide}
              title={canDecide ? undefined : '仅管理员可执行审批'}
              className="flex shrink-0 items-center gap-1 rounded-lg border border-red-300 px-3 py-1.5 text-xs text-red-600 hover:bg-red-50 disabled:opacity-50"
            >
              <XCircle size={12} /> 拒绝
            </button>
          </div>
        </div>
      </div>

      <div className="gov-expand" data-open={expanded}>
        <div className="gov-expand-inner">
          {expanded && (
            <div className="mx-4 mb-4 rounded-xl border border-slate-100 bg-slate-50/60 px-4 py-3.5">
              <div className="relative space-y-4 before:absolute before:bottom-2 before:left-[11px] before:top-2 before:w-px before:bg-slate-200">
                <Chapter icon={Database} tone="border-sky-200 text-sky-600" title="起因 · 哪个数据变了">
                  {sentinelTriggered && firing ? (
                    <>
                      <p>
                        {fmtTime(firing.createdAt)},哨兵「<span className="font-medium text-gray-800">{firing.sentinelName}</span>」侦测到数据变化:
                        本批扫描命中 <span className="font-semibold tabular-nums text-gray-800">{firing.matchCount}</span> 个实例
                        {matched.entered.length > 0 && (
                          <>,其中 <span className="font-semibold text-rose-600">{matched.entered.length} 个新进入</span>命中集合</>
                        )}
                        {matched.others.length > 0 && <>(另有 {matched.others.length} 个持续命中)</>}。
                      </p>
                    </>
                  ) : log.triggerSource === 'manual' ? (
                    <p>由 {log.actorId || '用户'} 在 {fmtTime(log.executedAt)} 手动发起。</p>
                  ) : sentinelTriggered ? (
                    <p>由哨兵在 {fmtTime(log.executedAt)} 侦测到数据变化后发起。</p>
                  ) : (
                    <p>由系统在 {fmtTime(log.executedAt)} 发起。</p>
                  )}
                  {log.objectInstanceId && (
                    <p>
                      目标实例 <span className="font-medium text-gray-800">{readableTargetSummary(log)}</span>
                      {objectValues && (
                        <>
                          {Object.entries(objectValues).slice(0, 4).map(([key, value]) => (
                            <span key={key} className="ml-1.5 rounded border border-slate-200 bg-white px-1.5 py-0.5 font-mono text-[11px] text-slate-500">
                              {key}={fmtVal(value)}
                            </span>
                          ))}
                        </>
                      )}
                      {typeInstancesQuery.isLoading && <Loader2 size={11} className="ml-1 inline animate-spin text-slate-400" />}
                    </p>
                  )}
                  {targetFactsQuery.data && targetFactsQuery.data.length > 0 && (
                    <p className="text-gray-500">
                      最近变化:
                      {targetFactsQuery.data.slice(0, 3).map(fact => (
                        <span key={fact.id} className="ml-1.5 inline-flex items-center gap-1 rounded bg-white px-1.5 py-0.5 text-[11px] text-slate-500 ring-1 ring-slate-200">
                          <span className="font-mono">{fact.propertyName}</span>
                          <ArrowRight size={9} className="text-slate-300" />
                          <span>{fact.present === false ? '(已删除)' : fmtVal(fact.value)}</span>
                          <span className="text-slate-300">{fmtTime(fact.recordedAt)}</span>
                        </span>
                      ))}
                    </p>
                  )}
                </Chapter>

                <Chapter icon={ShieldAlert} tone="border-rose-200 text-rose-600" title="判定 · 哨兵为什么认为要动作">
                  {sentinel ? (
                    <>
                      <p>
                        <span className={`mr-1.5 inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] ${
                          sentinel.muted
                            ? 'border-amber-200 bg-amber-50 text-amber-600'
                            : 'border-emerald-200 bg-emerald-50 text-emerald-600'
                        }`}>
                          {sentinel.muted ? <Eye size={10} /> : <Bolt size={10} />}
                          {sentinel.muted ? '影子(只记录不执行)' : '在线'}
                        </span>
                        <span className="font-medium text-gray-800">{sentinel.displayName || sentinel.name}</span>
                        <span className="ml-1.5 text-gray-500">{buildBindingSentence(sentinel, objectTypeName)}</span>
                      </p>
                      <p className="flex flex-wrap items-center gap-1.5">
                        命中条件
                        <span className="rounded-md border border-rose-200 bg-rose-50 px-2 py-0.5 font-mono text-[11px] text-rose-600">
                          {conditionSentence}
                        </span>
                      </p>
                      {firing && (
                        <p className="text-gray-500">
                          本次评估:命中 <span className="font-semibold tabular-nums text-gray-700">{firing.matchCount}</span> 组
                          {matched.entered.length > 0 && `,新进入 ${matched.entered.length} 组`}
                          {firing.durationMs != null && `,评估用时 ${firing.durationMs}ms`}
                          ,因此把该动作提交给你裁决。
                        </p>
                      )}
                    </>
                  ) : (
                    <p className="text-gray-500">
                      {log.triggerSource === 'manual'
                        ? '人工发起,不经过哨兵条件判定,由你直接评估是否执行。'
                        : '未找到触发它的哨兵定义(可能已在后续版本中调整),请结合参数与目标评估。'}
                    </p>
                  )}
                </Chapter>

                <Chapter icon={Sparkles} tone="border-amber-200 text-amber-600" title="后果 · 批准会发生什么">
                  <ul className="space-y-1">
                    {effectItems.map((item, index) => (
                      <li key={`${item.type}:${index}`} className="flex items-start gap-1.5">
                        <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />
                        <span>
                          <span className="text-gray-700">{item.sentence}</span>
                          {item.detail && (
                            <span className="block break-all rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-[11px] leading-4 text-slate-500 mt-1">
                              {item.detail}
                            </span>
                          )}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <p className="text-[11px] text-gray-400">批准后立即执行;执行结果与本次决策都会写入事实流,可全程追溯。拒绝则只记录决策,不改动任何数据。</p>
                  <div className="flex items-center gap-2 pt-1">
                    <button
                      onClick={() => onApprove(log)}
                      disabled={busy || !canDecide}
                      className="flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                    >
                      {busy ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />}
                      批准并执行
                    </button>
                    <button
                      onClick={() => onReject(log)}
                      disabled={busy || !canDecide}
                      className="flex items-center gap-1 rounded-lg border border-red-300 px-3 py-1.5 text-xs text-red-600 hover:bg-red-50 disabled:opacity-50"
                    >
                      <XCircle size={12} /> 拒绝
                    </button>
                  </div>
                </Chapter>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default function PendingStoryList({
  ontologyId,
  pending,
  firings,
  sentinels,
  actions,
  objectTypeName,
  canDecide,
  busyId,
  onApprove,
  onReject,
}: {
  ontologyId: string
  pending: PendingLog[]
  firings: FiringLike[]
  sentinels: SentinelLike[]
  actions: WorkspaceAction[]
  objectTypeName: (objectTypeId: string) => string
  canDecide: boolean
  busyId: string | null
  onApprove: (log: PendingLog) => void
  onReject: (log: PendingLog) => void
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null)
  return (
    <div className="space-y-2.5">
      {pending.map(log => {
        const firing = findTriggerFiring(log, firings)
        const sentinel = firing
          ? sentinels.find(item => item.id === firing.sentinelId) || null
          : sentinels.find(item => (item.actionIds || []).includes(log.actionId)) || null
        const actionDef = actions.find(item => item.id === log.actionId) || null
        return (
          <StoryCard
            key={log.id}
            ontologyId={ontologyId}
            log={log}
            firing={firing}
            sentinel={sentinel}
            actionDef={actionDef}
            objectTypeName={objectTypeName}
            expanded={expandedId === log.id}
            onToggle={() => setExpandedId(current => (current === log.id ? null : log.id))}
            canDecide={canDecide}
            busy={busyId === log.id}
            onApprove={onApprove}
            onReject={onReject}
          />
        )
      })}
      {pending.length > 0 && (
        <p className="flex items-center gap-1.5 pt-1 text-[11px] text-gray-400">
          <AlertTriangle size={11} />
          点击任意条目可展开「前因后果」:数据变化、哨兵判定依据与执行效果,看明白再裁决。
        </p>
      )}
    </div>
  )
}
