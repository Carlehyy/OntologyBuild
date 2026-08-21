/* 待审批「起因 → 判定 → 后果」三段式故事正文。
   从原 StoryCard 就地展开区抽取,供详情弹窗复用;
   数据查询(目标类型实例、目标实例最近事实)仅在 active 时发起。 */
import {
  ArrowRight, Bolt, Database, Eye, Loader2, ShieldAlert, Sparkles,
} from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { apiClientV2 } from '@/api/client'
import { readableTargetSummary } from '../tabs/governanceFormat'
import type {
  FiringLike, SentinelLike, WorkspaceActionLike,
} from './storyModel'
import {
  buildBindingSentence, buildConditionSentence, buildEffectPreview,
  firingMatchedInstanceIds,
} from './storyModel'
import type { PendingLog } from './PendingStoryList'

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

export default function PendingStoryChapters({
  ontologyId,
  log,
  firing,
  sentinel,
  actionDef,
  objectTypeName,
  active,
}: {
  ontologyId: string
  log: PendingLog
  firing: FiringLike | null
  sentinel: SentinelLike | null
  actionDef: WorkspaceActionLike | null
  objectTypeName: (objectTypeId: string) => string
  /** 仅在详情可见时为 true:控制展开后才发起的两个查询。 */
  active: boolean
}) {
  const typeInstancesQuery = useQuery<{ items?: InstanceLite[] } | InstanceLite[]>({
    queryKey: ['gov-type-instances', ontologyId, log.objectTypeId],
    queryFn: () => apiClientV2.get(
      `/formal/ontologies/${ontologyId}/instance-browser/objects`,
      { params: { object_type_id: log.objectTypeId, page: 1, page_size: 100 } },
    ) as any,
    enabled: active && Boolean(log.objectTypeId),
    staleTime: 30_000,
  })
  const targetFactsQuery = useQuery<InstanceFactLite[]>({
    queryKey: ['instance-facts', ontologyId, log.objectInstanceId],
    queryFn: () => apiClientV2.get(
      `/formal/ontologies/${ontologyId}/instances/${log.objectInstanceId}/facts`,
      { params: { limit: 5 } },
    ) as any,
    enabled: active && Boolean(log.objectInstanceId),
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
  const sentinelTriggered = Boolean(firing) || log.triggerSource === 'sentinel'

  return (
    <div className="relative space-y-4 before:absolute before:bottom-2 before:left-[11px] before:top-2 before:w-px before:bg-slate-200">
      <Chapter icon={Database} tone="border-sky-200 text-sky-600" title="起因 · 哪个数据变了">
        {sentinelTriggered && firing ? (
          <p>
            {fmtTime(firing.createdAt)},哨兵「<span className="font-medium text-gray-800">{firing.sentinelName}</span>」侦测到数据变化:
            本批扫描命中 <span className="font-semibold tabular-nums text-gray-800">{firing.matchCount}</span> 个实例
            {matched.entered.length > 0 && (
              <>,其中 <span className="font-semibold text-rose-600">{matched.entered.length} 个新进入</span>命中集合</>
            )}
            {matched.others.length > 0 && <>(另有 {matched.others.length} 个持续命中)</>}。
          </p>
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
      </Chapter>
    </div>
  )
}
