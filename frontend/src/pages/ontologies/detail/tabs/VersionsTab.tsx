import { useMemo, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle, ArrowRight, Check, ChevronDown, CircleAlert, Database,
  GitBranch, GitCommitHorizontal, LockKeyhole, MoreHorizontal, Plus, Rocket,
  ShieldCheck, Trash2, Wrench,
} from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { LoadingState } from '@/components/ui/LoadingState'
import { ConfirmModal, Modal } from '@/components/ui/Modal'
import {
  ontologyVersionApi,
  type OntologyImpactReport,
  type OntologyReleaseGateIssue,
  type OntologyTrialRun as Trial,
  type OntologyVersionNode as VersionNode,
} from '@/api/v2/ontology-versions'

type VersionStage = 'current' | 'release' | 'draft' | 'trial' | 'archived'

function errorText(error: any) {
  const detail = error?.response?.data?.detail ?? error?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail?.errors) && detail.errors.length > 0) {
    const issues = detail.errors.map((item: any) => item.message).filter(Boolean).join('；')
    return detail?.message && issues
      ? `${detail.message}：${issues}`
      : issues || detail?.message || '发布校验未通过'
  }
  if (detail?.message) return detail.message
  return error?.message || '操作失败'
}

function errorIssues(error: any): Array<{ message: string; kind?: string; field?: string }> {
  const detail = error?.response?.data?.detail ?? error?.detail
  return Array.isArray(detail?.errors) ? detail.errors.filter((item: any) => item?.message) : []
}

function stageOf(node: VersionNode, currentReleaseId?: string): VersionStage {
  if (node.id === currentReleaseId) return 'current'
  if (node.node_kind === 'release') return 'release'
  if (node.lifecycle_status === 'superseded') return 'archived'
  if (node.lifecycle_status === 'trial_ready' && node.latest_trial?.status === 'passed') return 'trial'
  return 'draft'
}

const STAGE_META: Record<VersionStage, { label: string; badge: 'success' | 'warning' | 'danger' | 'default'; dot: string; card: string }> = {
  current: {
    label: '当前发布', badge: 'success', dot: 'bg-teal-500 ring-teal-100',
    card: 'border-teal-200 bg-teal-50/55 hover:border-teal-300',
  },
  release: {
    label: '历史发布', badge: 'default', dot: 'bg-slate-400 ring-slate-100',
    card: 'border-slate-200 bg-white hover:border-slate-300',
  },
  draft: {
    label: '草稿态', badge: 'warning', dot: 'bg-sky-500 ring-sky-100',
    card: 'border-sky-200 bg-sky-50/45 hover:border-sky-300',
  },
  trial: {
    label: '试跑态', badge: 'warning', dot: 'bg-amber-500 ring-amber-100',
    card: 'border-amber-200 bg-amber-50/45 hover:border-amber-300',
  },
  archived: {
    label: '已晋级', badge: 'default', dot: 'bg-violet-400 ring-violet-100',
    card: 'border-violet-100 bg-violet-50/30 hover:border-violet-200',
  },
}

const VERSION_ACTION_BUTTON = {
  editor: 'border-violet-200 bg-violet-50 text-violet-700 shadow-sm hover:border-violet-300 hover:bg-violet-100 focus-visible:ring-2 focus-visible:ring-violet-500',
  mapping: 'border-sky-200 bg-sky-50 text-sky-700 shadow-sm hover:border-sky-300 hover:bg-sky-100 focus-visible:ring-2 focus-visible:ring-sky-500',
  trial: 'border-amber-700 bg-amber-700 text-white shadow-sm hover:border-amber-800 hover:bg-amber-800 focus-visible:ring-2 focus-visible:ring-amber-500',
  release: 'border-teal-700 bg-teal-700 text-white shadow-sm hover:border-teal-800 hover:bg-teal-800 focus-visible:ring-2 focus-visible:ring-teal-500',
} as const

function StageBadge({ stage }: { stage: VersionStage }) {
  const meta = STAGE_META[stage]
  return <Badge variant={meta.badge}>{meta.label}</Badge>
}

const PROPERTY_MAPPING_CODES = new Set([
  'mapping_property_missing',
  'link_mapping_property_missing',
])

const MAPPING_CODES = new Set([
  ...PROPERTY_MAPPING_CODES,
  'trial_object_mapping_required',
  'object_type_mapping_required',
  'link_type_mapping_required',
  'mapping_dataset_missing',
  'mapping_field_mapping_invalid',
  'mapping_object_type_not_found',
  'link_mapping_endpoint_missing',
  'link_mapping_field_mapping_invalid',
  'link_mapping_type_not_found',
  'link_mapping_source_object_mapping_missing',
  'link_mapping_target_object_mapping_missing',
])

interface ReleaseIssueGroup {
  key: string
  title: string
  subtitle: string
  issues: OntologyReleaseGateIssue[]
  fields: string[]
}

function groupReleaseIssues(issues: OntologyReleaseGateIssue[]): ReleaseIssueGroup[] {
  const groups = new Map<string, ReleaseIssueGroup>()
  issues.forEach((issue, index) => {
    const key = issue.targetId || `${issue.kind}:${issue.id || issue.name || issue.code}:${index}`
    const existing = groups.get(key)
    const title = issue.targetName || issue.name || (
      issue.kind === 'trialRun' ? '隔离试跑' : issue.kind === 'version' ? '版本基线' : '发布条件'
    )
    const kindLabel = issue.kind === 'linkMapping' || issue.kind === 'linkType'
      ? '实体关系'
      : issue.kind === 'mapping' || issue.kind === 'objectType'
        ? '对象实体'
        : '发布门禁'
    const subtitle = issue.name && issue.name !== title && MAPPING_CODES.has(issue.code)
      ? `${kindLabel} · 映射 ${issue.name}`
      : kindLabel
    if (existing) {
      existing.issues.push(issue)
      if (issue.field && PROPERTY_MAPPING_CODES.has(issue.code) && !existing.fields.includes(issue.field)) {
        existing.fields.push(issue.field)
      }
      return
    }
    groups.set(key, {
      key,
      title,
      subtitle,
      issues: [issue],
      fields: issue.field && PROPERTY_MAPPING_CODES.has(issue.code) ? [issue.field] : [],
    })
  })
  return [...groups.values()]
}

function concisePromotionError(error: any) {
  const detail = error?.response?.data?.detail ?? error?.detail
  if (Array.isArray(detail?.errors)) {
    return `发布条件在确认过程中发生变化，当前仍有 ${detail.errors.length} 项未满足，请关闭后重新检查。`
  }
  return typeof detail === 'string' ? detail : detail?.message || error?.message || '发布失败，请重新检查后再试。'
}

export default function VersionsTab({ ontologyId, onClose }: { ontologyId: string; onClose?: () => void }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [source, setSource] = useState<VersionNode | null>(null)
  const [label, setLabel] = useState('')
  const [description, setDescription] = useState('')
  const [trialDetail, setTrialDetail] = useState<Trial | null>(null)
  const [promotion, setPromotion] = useState<{ node: VersionNode; impact: OntologyImpactReport } | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<VersionNode | null>(null)
  const [gateIssues, setGateIssues] = useState<Array<{ message: string; kind?: string; field?: string }>>([])
  const [gateVersionId, setGateVersionId] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ tone: 'good' | 'bad'; text: string } | null>(null)
  const [openMenuId, setOpenMenuId] = useState<string | null>(null)

  const treeQuery = useQuery({
    queryKey: ['version-tree', ontologyId],
    queryFn: () => ontologyVersionApi.tree(ontologyId),
  })

  const nodes = treeQuery.data?.versions || []
  const currentReleaseId = treeQuery.data?.current_release_id
  const promotedFrom = useMemo(() => new Map(nodes.map(node => [node.id, node.version_number])), [nodes])
  const roots = useMemo(() => {
    const known = new Set(nodes.map(node => node.id))
    const children = new Map<string, VersionNode[]>()
    const rootNodes: VersionNode[] = []
    for (const node of nodes) {
      // 正式版本由某个试跑分支晋级产生时，演进图优先展示真实因果链：
      // v0 → v0.1（草稿/试跑）→ v1（发布），而不是把 v1 排在来源分支前面。
      const visualParentId = node.promoted_from_id && known.has(node.promoted_from_id)
        ? node.promoted_from_id
        : node.parent_version_id
      if (!visualParentId || !known.has(visualParentId)) {
        rootNodes.push(node)
        continue
      }
      children.set(visualParentId, [...(children.get(visualParentId) || []), node])
    }
    // 同一来源下发布节点优先形成主干，其余草稿与试跑分支按创建时间展开。
    const sort = (items: VersionNode[]) => [...items].sort((a, b) => {
      if (a.node_kind !== b.node_kind) return a.node_kind === 'release' ? -1 : 1
      return new Date(a.created_at || 0).getTime() - new Date(b.created_at || 0).getTime()
    })
    for (const [parent, items] of children) children.set(parent, sort(items))
    return { rootNodes: sort(rootNodes), children }
  }, [nodes])

  const refresh = () => qc.invalidateQueries({ queryKey: ['version-tree', ontologyId] })
  const refreshReleasedProjection = async () => {
    const keys = [
      ['ontologies'], ['ontology', ontologyId],
      ['formal-overview', ontologyId], ['recent-facts', ontologyId],
      ['overview-pending', ontologyId],
      ['ms-ot', ontologyId], ['ms-lt', ontologyId], ['ms-act', ontologyId],
      ['ms-fn', ontologyId], ['ms-inst', ontologyId],
      ['fi-ot', ontologyId], ['fi-inst', ontologyId],
      ['formal-object-types', ontologyId], ['formal-link-types', ontologyId],
      ['mappings', ontologyId], ['link-mappings', ontologyId],
      ['mapping-snapshot', ontologyId],
      ['current-release-workspace', ontologyId],
      ['mapping-object-instances', ontologyId], ['mapping-link-instances', ontologyId],
      ['gov-pending', ontologyId], ['gov-sentinels', ontologyId],
      ['gov-firings', ontologyId], ['gov-autonomy', ontologyId], ['gov-facts', ontologyId],
    ]
    await Promise.all(keys.map(queryKey => qc.invalidateQueries({ queryKey })))
  }

  const openVersion = (node: VersionNode) => {
    onClose?.()
    navigate(node.id === currentReleaseId
      ? `/ontologies/${ontologyId}/graph`
      : `/ontologies/${ontologyId}/graph?versionId=${node.id}`)
  }

  const openMapping = (node: VersionNode) => {
    onClose?.()
    navigate(`/ontologies/${ontologyId}/graph?versionId=${node.id}&view=mapping`)
  }

  const createDraft = useMutation({
    mutationFn: () => ontologyVersionApi.createDraft(
      ontologyId, source!.id, { versionLabel: label, description }),
    onSuccess: async node => {
      await refresh()
      setSource(null)
      setLabel('')
      setDescription('')
      setNotice({ tone: 'good', text: `${node.version_number} 已从完整快照创建，可直接点击节点进入编辑。` })
    },
  })

  const deleteVersion = useMutation({
    mutationFn: (node: VersionNode) => ontologyVersionApi.deleteVersion(ontologyId, node.id),
    onSuccess: async (result, node) => {
      setDeleteTarget(null)
      if (gateVersionId === node.id) {
        setGateIssues([])
        setGateVersionId(null)
      }
      await refresh()
      setNotice({ tone: 'good', text: `${result.version_number} 已删除。版本编号不会被复用。` })
    },
    onError: error => setNotice({ tone: 'bad', text: errorText(error) }),
  })

  const runTrial = useMutation({
    mutationFn: (node: VersionNode) => ontologyVersionApi.runTrial(ontologyId, node.id),
    onSuccess: async run => {
      await refresh()
      setGateIssues([])
      setGateVersionId(null)
      setTrialDetail(run)
      setNotice(run.status === 'passed'
        ? { tone: 'good', text: '草稿已进入试跑态：快照冻结，真实数据仅写入隔离空间。' }
        : { tone: 'bad', text: '试跑未通过，请根据错误修正结构或映射。' })
    },
    onError: (error, node) => {
      const issues = errorIssues(error)
      setGateIssues(issues)
      setGateVersionId(node.id)
      setNotice({
        tone: 'bad',
        text: issues.length > 0
          ? `暂时不能进入试跑态：仍有 ${issues.length} 项试跑门禁条件未满足。`
          : errorText(error),
      })
    },
  })

  const inspectImpact = useMutation({
    mutationFn: async (node: VersionNode) => ({
      node,
      impact: await ontologyVersionApi.impact(ontologyId, node.id),
    }),
    onSuccess: setPromotion,
    onError: error => setNotice({ tone: 'bad', text: errorText(error) }),
  })

  const promote = useMutation({
    mutationFn: () => ontologyVersionApi.promote(
      ontologyId,
      promotion!.node.id,
      {
        trialRunId: promotion!.node.latest_trial!.id,
        impactHash: promotion!.impact.impactHash,
        versionLabel: promotion!.node.version_label || '',
      },
    ),
    onSuccess: async release => {
      setPromotion(null)
      await refresh()
      await refreshReleasedProjection()
      setNotice({ tone: 'good', text: `${release.version_number} 已发布并成为唯一运行版本。` })
    },
    onError: error => setNotice({ tone: 'bad', text: concisePromotionError(error) }),
  })

  const createRepairDraft = useMutation({
    mutationFn: () => ontologyVersionApi.createDraft(
      ontologyId,
      promotion!.impact.releaseReadiness!.repairSourceVersionId || promotion!.node.id,
      {
        versionLabel: '发布修复',
        description: `修复 ${promotion!.node.version_number} 的发布前检查问题`,
      },
    ),
    onSuccess: async node => {
      setPromotion(null)
      await refresh()
      openMapping(node)
    },
  })

  if (treeQuery.isLoading) return <LoadingState />
  if (treeQuery.isError) return (
    <Card className="space-y-3 border-red-200 bg-red-50 p-4 text-sm text-red-800">
      <p>版本树加载失败：{errorText(treeQuery.error)}</p>
      <Button variant="outline" size="sm" onClick={() => treeQuery.refetch()}>重新加载</Button>
    </Card>
  )

  const renderNode = (node: VersionNode, depth = 0): ReactNode => {
    const stage = stageOf(node, currentReleaseId)
    const meta = STAGE_META[stage]
    const children = roots.children.get(node.id) || []
    const editing = stage === 'draft'
    const trial = stage === 'trial'
    const deletableStage = editing || trial
    const isLeaf = children.length === 0
    const promotedFromVersion = node.promoted_from_id ? promotedFrom.get(node.promoted_from_id) : null
    const menuItemClass = 'flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 disabled:cursor-not-allowed disabled:text-slate-300 disabled:hover:bg-transparent'

    return (
      <div key={node.id} role="treeitem" aria-level={depth + 1} aria-current={stage === 'current' ? 'true' : undefined}>
        <article
          data-testid={`version-node-${node.version_number}`}
          className={`group relative rounded-xl border px-3.5 py-2.5 transition-all duration-200 ${meta.card}`}
        >
          <span className={`absolute -left-[1.72rem] top-5 h-3 w-3 rounded-full ring-4 ${meta.dot}`} aria-hidden="true" />
          <div className="flex items-center justify-between gap-3">
            <button
              type="button"
              onClick={() => openVersion(node)}
              className="min-w-0 flex-1 rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2"
              aria-label={`打开 ${node.version_number} ${meta.label}`}
            >
              <div className="flex min-w-0 items-center gap-2">
                {node.node_kind === 'release'
                  ? <GitCommitHorizontal size={16} className={`shrink-0 ${stage === 'current' ? 'text-teal-700' : 'text-slate-500'}`} />
                  : <GitBranch size={16} className={`shrink-0 ${trial ? 'text-amber-600' : 'text-sky-600'}`} />}
                <span className="shrink-0 font-mono text-base font-semibold tabular-nums text-slate-800">{node.version_number}</span>
                <span className="shrink-0"><StageBadge stage={stage} /></span>
                {node.version_label && <span className="min-w-0 truncate text-xs font-medium text-slate-500">{node.version_label}</span>}
                {promotedFromVersion && <span className="shrink-0 whitespace-nowrap text-[11px] text-teal-600">由 {promotedFromVersion} 晋级</span>}
              </div>
            </button>

            <div className="flex shrink-0 items-center gap-1.5">
              {editing ? (
                <>
                  <Button variant="outline" size="sm" className={VERSION_ACTION_BUTTON.editor} onClick={() => openVersion(node)}>
                    打开编辑器
                  </Button>
                  <Button variant="outline" size="sm" className={VERSION_ACTION_BUTTON.mapping} onClick={() => openMapping(node)}>
                    <Database size={14} /> 数据映射
                  </Button>
                  <Button variant="outline" size="sm" className={VERSION_ACTION_BUTTON.trial} loading={runTrial.isPending} onClick={() => runTrial.mutate(node)}>
                    转为试跑态
                  </Button>
                </>
              ) : trial ? (
                <>
                  <Button variant="outline" size="sm" className={VERSION_ACTION_BUTTON.editor} onClick={() => openVersion(node)}>
                    打开编辑器
                  </Button>
                  <Button variant="outline" size="sm" className={VERSION_ACTION_BUTTON.mapping} onClick={() => openMapping(node)}>
                    <Database size={14} /> 数据映射
                  </Button>
                  <Button variant="outline" size="sm" className={VERSION_ACTION_BUTTON.release} onClick={() => {
                    promote.reset()
                    createRepairDraft.reset()
                    inspectImpact.mutate(node)
                  }}>转为发布态</Button>
                </>
              ) : (
                <>
                  <Button variant="outline" size="sm" className={VERSION_ACTION_BUTTON.editor} onClick={() => openVersion(node)}>
                    打开编辑器
                  </Button>
                  <Button variant="outline" size="sm" className={VERSION_ACTION_BUTTON.mapping} onClick={() => openMapping(node)}>
                    <Database size={14} /> 数据映射
                  </Button>
                </>
              )}

              <div className="relative">
                <button
                  type="button"
                  className="flex h-8 w-8 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-white/80 hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"
                  aria-label={`${node.version_number} 更多操作`}
                  aria-haspopup="true"
                  aria-expanded={openMenuId === node.id}
                  aria-controls={`version-actions-${node.id}`}
                  title="更多操作"
                  onClick={() => setOpenMenuId(current => current === node.id ? null : node.id)}
                >
                  <MoreHorizontal size={16} />
                </button>
                {openMenuId === node.id && (
                  <div
                    id={`version-actions-${node.id}`}
                    className="absolute right-0 top-9 z-30 min-w-36 rounded-lg border border-slate-200 bg-white p-1 shadow-lg shadow-slate-200/70"
                  >
                    <button type="button" className={menuItemClass} onClick={() => {
                      setOpenMenuId(null)
                      setSource(node)
                    }}>
                      <Plus size={13} /> 创建新版本
                    </button>
                    {deletableStage && (
                      <button
                        type="button"
                        className={menuItemClass}
                        disabled={!isLeaf}
                        title={!isLeaf ? '该版本下仍有分支，只有叶子节点可以删除' : undefined}
                        onClick={() => {
                          setOpenMenuId(null)
                          setDeleteTarget(node)
                        }}
                      >
                        <Trash2 size={13} /> {isLeaf ? '删除此分支' : '删除此分支（非叶子）'}
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        </article>

        {children.length > 0 && (
          <div role="group" className="ml-3 space-y-2.5 border-l border-slate-200 pl-6 pt-2.5">
            {children.map(child => renderNode(child, depth + 1))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 pb-1">
      <Card className="shrink-0 overflow-hidden p-0">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-800">
              <GitCommitHorizontal size={16} /> 从草稿到正式运行
            </h3>
            <p className="mt-0.5 text-xs text-slate-500">在秩序中演化：每次创建新版本都会生成独立、完整的草稿快照。</p>
          </div>
          <div className="flex items-center gap-2 rounded-full bg-teal-50 px-3 py-1.5 text-xs font-medium text-teal-700">
            <ShieldCheck size={14} /> 当前正式运行：{treeQuery.data?.current_release_version}
          </div>
        </div>

        <div className="grid grid-cols-[1fr_auto_1fr_auto_1fr] items-stretch px-4 py-3" aria-label="版本状态递进关系">
          <div className="rounded-xl border border-sky-100 bg-sky-50/70 p-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-sky-900"><span className="flex h-5 w-5 items-center justify-center rounded-full bg-sky-600 text-[11px] text-white">1</span>草稿态</div>
            <p className="mt-1.5 text-xs leading-5 text-sky-700">完整定义与映射可编辑；新版本始终从此状态开始，不产生运行数据或执行动作。</p>
          </div>
          <div className="flex w-9 items-center justify-center text-slate-300"><ArrowRight size={18} /></div>
          <div className="rounded-xl border border-amber-100 bg-amber-50/70 p-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-amber-900"><span className="flex h-5 w-5 items-center justify-center rounded-full bg-amber-500 text-[11px] text-white">2</span>试跑态</div>
            <p className="mt-1.5 text-xs leading-5 text-amber-700">门禁：基线仍为当前发布版、结构与哨兵有效，且至少一个对象实体完成数据映射；通过后冻结并隔离试跑。</p>
          </div>
          <div className="flex w-9 items-center justify-center text-slate-300"><ArrowRight size={18} /></div>
          <div className="rounded-xl border border-teal-100 bg-teal-50/70 p-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-teal-900"><span className="flex h-5 w-5 items-center justify-center rounded-full bg-teal-600 text-[11px] text-white"><Check size={12} /></span>发布态</div>
            <p className="mt-1.5 text-xs leading-5 text-teal-700">门禁：精确试跑已通过且未失效、数据版本未变化、影响已确认；发布后结构只读，仅当前版本正式执行。</p>
          </div>
        </div>
      </Card>

      {notice && (
        <div role="status" aria-live="polite" className={`shrink-0 rounded-lg border px-3 py-2 text-sm ${notice.tone === 'good'
          ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
          : 'border-red-200 bg-red-50 text-red-800'}`}>
          {notice.text}
        </div>
      )}

      {gateIssues.length > 0 && (
        <div role="alert" className="shrink-0 rounded-xl border border-red-200 bg-red-50/80 px-4 py-3 text-sm text-red-900">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="font-semibold">草稿尚未满足转为试跑态的硬性条件</p>
              <div className="mt-1 max-h-24 space-y-1 overflow-y-auto pr-2 text-xs leading-5 text-red-800">
                {gateIssues.map((item, index) => <p key={`${item.kind || ''}-${item.field || ''}-${index}`}>• {item.message}</p>)}
              </div>
            </div>
            <Button variant="outline" size="sm" onClick={() => {
              const node = nodes.find(item => item.id === gateVersionId)
              if (node) openMapping(node)
            }}>
              <Database size={14} /> 完善映射
            </Button>
          </div>
        </div>
      )}

      <section className="min-h-0 flex-1 overflow-y-auto overscroll-contain rounded-xl border border-slate-200 bg-slate-50/60 p-4" aria-label="本体版本树">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-slate-700">版本演化树</p>
            <p className="mt-0.5 text-xs text-slate-400">{nodes.length} 个完整快照节点</p>
          </div>
          <span className="rounded-md bg-teal-50 px-2.5 py-1 font-mono text-xs font-semibold text-teal-700">
            当前 {treeQuery.data?.current_release_version}
          </span>
        </div>
        {roots.rootNodes.length > 0 ? (
          <div role="tree" className="ml-2 space-y-2.5 border-l border-slate-200 pl-6" data-testid="version-tree">
            {roots.rootNodes.map(node => renderNode(node))}
          </div>
        ) : (
          <p className="rounded-lg bg-white px-4 py-8 text-center text-sm text-slate-400">暂无版本节点</p>
        )}
      </section>

      {source && (
        <Modal open onClose={() => setSource(null)} title={`从 ${source.version_number} 创建完整分支`} size="sm"
          footer={<>
            <Button variant="ghost" onClick={() => setSource(null)}>取消</Button>
            <Button loading={createDraft.isPending} onClick={() => createDraft.mutate()}>创建分支</Button>
          </>}>
          <div className="space-y-3">
            <p className="text-sm leading-6 text-gray-600">
              新版本固定为草稿态，并一次性复制对象实体、实体关系、执行动作、激活函数、哨兵、对象映射、关系映射及画布布局，不依赖祖先拼装。
            </p>
            <input value={label} onChange={event => setLabel(event.target.value)} placeholder="分支标签（可选）" className="w-full rounded-lg border px-3 py-2 text-sm" />
            <textarea value={description} onChange={event => setDescription(event.target.value)} placeholder="本次变化目标（可选）" className="h-20 w-full resize-none rounded-lg border px-3 py-2 text-sm" />
            {createDraft.isError && <p className="text-sm text-red-600">{errorText(createDraft.error)}</p>}
          </div>
        </Modal>
      )}

      {trialDetail && (
        <Modal open onClose={() => setTrialDetail(null)} title="隔离试跑结果" size="lg"
          footer={<>
            <Button variant="ghost" onClick={() => setTrialDetail(null)}>关闭</Button>
            {trialDetail.status === 'passed' && (
              <Button onClick={() => {
                const node = nodes.find(item => item.latest_trial?.id === trialDetail.id)
                if (node) openVersion(node)
              }}>打开试跑图谱</Button>
            )}
          </>}>
          <div className="space-y-4 text-sm">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {Object.entries(trialDetail.result?.counts || {}).map(([key, value]) => (
                <div key={key} className="rounded-lg border bg-gray-50 p-3"><b className="block text-lg">{String(value)}</b><span className="text-xs text-gray-500">{key}</span></div>
              ))}
            </div>
            <p className="flex items-center gap-2 text-emerald-700"><ShieldCheck size={16} /> 外部动作执行数：{trialDetail.result?.actionsExecuted ?? 0}；副作用：已阻断</p>
            {(trialDetail.result?.errors || []).length > 0 && <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-red-800">
              {(trialDetail.result?.errors || []).map((item, index) => <p key={index}>• {item.message}</p>)}
            </div>}
            {(trialDetail.result?.warnings || []).length > 0 && <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-800">
              {(trialDetail.result?.warnings || []).map((item, index) => <p key={index}>• {item.message}</p>)}
            </div>}
          </div>
        </Modal>
      )}

      {promotion && (
        (() => {
          const readiness = promotion.impact.releaseReadiness
          const blocked = promotion.impact.baseOutdated || readiness?.ready === false
          const issues = readiness?.errors || []
          const groups = groupReleaseIssues(issues)
          const mappingIssues = issues.filter(item => MAPPING_CODES.has(item.code))
          const propertyIssues = issues.filter(item => PROPERTY_MAPPING_CODES.has(item.code))
          const repairable = readiness?.repairStrategy === 'create_draft'

          return (
            <Modal
              open
              onClose={() => setPromotion(null)}
              title={`发布前检查 · ${promotion.node.version_number}`}
              size="xl"
              headerIcon={blocked
                ? <CircleAlert size={20} className="text-red-600" />
                : <ShieldCheck size={20} className="text-emerald-600" />}
              footer={<>
                <Button variant="ghost" onClick={() => setPromotion(null)}>取消</Button>
                {blocked ? (
                  <>
                    <Button variant="outline" disabled><LockKeyhole size={14} /> 暂不可发布</Button>
                    {repairable && (
                      <Button loading={createRepairDraft.isPending} onClick={() => createRepairDraft.mutate()}>
                        <Wrench size={14} /> 创建修复分支并完善映射
                      </Button>
                    )}
                  </>
                ) : (
                  <Button loading={promote.isPending} onClick={() => promote.mutate()}>
                    <Rocket size={14} /> 确认发布
                  </Button>
                )}
              </>}
            >
              <div className="space-y-5 text-sm">
                {blocked ? (
                  <div data-testid="release-readiness-blocked" role="alert" className="rounded-xl border border-red-200 bg-red-50/80 p-4 text-red-950">
                    <div className="flex items-start gap-3">
                      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-red-100 text-red-700">
                        <LockKeyhole size={17} />
                      </span>
                      <div className="min-w-0">
                        <h3 className="font-semibold">当前版本暂时不能发布</h3>
                        <p className="mt-1 leading-6 text-red-800">
                          {mappingIssues.length > 0
                            ? `发现 ${groups.filter(group => group.issues.some(issue => MAPPING_CODES.has(issue.code))).length} 个本体元素存在 ${mappingIssues.length} 项映射问题${propertyIssues.length > 0 ? `，其中 ${propertyIssues.length} 个存储属性尚未映射` : ''}。`
                            : `仍有 ${readiness?.blockingCount || issues.length || 1} 项发布条件未满足。`}
                        </p>
                        <p className="mt-1 text-xs leading-5 text-red-700">
                          {repairable
                            ? `${promotion.node.version_number} 已完成试跑并被冻结，系统会从它创建一个完整草稿分支；补齐后需重新试跑。`
                            : '当前发布基线已经变化，请先基于最新发布版合并本分支改动，再重新试跑。'}
                        </p>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div data-testid="release-readiness-ready" role="status" className="flex items-start gap-3 rounded-xl border border-emerald-200 bg-emerald-50/80 p-4 text-emerald-900">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-emerald-700"><Check size={17} /></span>
                    <div><h3 className="font-semibold">发布条件已满足</h3><p className="mt-1 text-xs leading-5 text-emerald-700">精确试跑、数据版本和映射完整性均已通过检查，可以确认发布。</p></div>
                  </div>
                )}

                {blocked && groups.length > 0 && (
                  <section aria-labelledby="release-blockers-heading">
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <h3 id="release-blockers-heading" className="font-semibold text-slate-800">需要处理的问题</h3>
                      <span className="text-xs text-slate-500">{issues.length} 项 · 按本体元素归组</span>
                    </div>
                    <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
                      {groups.map(group => (
                        <details key={group.key} data-testid="release-readiness-group" className="group rounded-lg border border-slate-200 bg-white open:border-red-200 open:bg-red-50/30">
                          <summary className="flex min-h-12 cursor-pointer list-none items-center gap-3 px-3 py-2.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 [&::-webkit-details-marker]:hidden">
                            <CircleAlert size={16} className="shrink-0 text-red-500" />
                            <span className="min-w-0 flex-1"><b className="block truncate text-slate-800">{group.title}</b><span className="text-xs text-slate-500">{group.subtitle}</span></span>
                            <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">{group.issues.length} 项</span>
                            <ChevronDown size={16} className="text-slate-400 transition-transform group-open:rotate-180" />
                          </summary>
                          <div className="border-t border-slate-100 px-3 py-3">
                            {group.fields.length > 0 && (
                              <div className="flex flex-wrap gap-1.5" aria-label={`${group.title} 未映射属性`}>
                                {group.fields.map(field => <code key={field} className="rounded-md border border-red-100 bg-white px-2 py-1 text-xs text-red-700">{field}</code>)}
                              </div>
                            )}
                            {group.issues.filter(issue => !PROPERTY_MAPPING_CODES.has(issue.code)).map((issue, index) => (
                              <p key={`${issue.code}-${index}`} className="text-xs leading-5 text-slate-700">{issue.message}</p>
                            ))}
                          </div>
                        </details>
                      ))}
                    </div>
                  </section>
                )}

                <section aria-labelledby="release-impact-heading">
                  <h3 id="release-impact-heading" className="mb-2 font-semibold text-slate-800">结构影响</h3>
                  <div className="grid grid-cols-3 gap-3">
                    <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3"><b className="text-lg text-emerald-700">+{promotion.impact.total.added}</b><p className="text-xs text-slate-500">新增</p></div>
                    <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3"><b className="text-lg text-amber-700">~{promotion.impact.total.modified}</b><p className="text-xs text-slate-500">修改</p></div>
                    <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3"><b className="text-lg text-red-700">-{promotion.impact.total.deleted}</b><p className="text-xs text-slate-500">删除</p></div>
                  </div>
                  <div className="mt-3">
                    {promotion.impact.breakingCount === 0
                      ? <p className="rounded-lg bg-emerald-50 px-3 py-2 text-emerald-800">未发现结构性破坏。</p>
                      : <div className="max-h-40 space-y-2 overflow-auto">{promotion.impact.breaking.map((item, index) => (
                        <div key={index} className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-900"><AlertTriangle size={16} className="mt-0.5 shrink-0" />{item.message}</div>
                      ))}</div>}
                  </div>
                </section>

                {!blocked && <p className="text-xs text-slate-500">确认后，仅本次试跑验证过的精确结构哈希和数据版本可晋级；任何变化都会自动拒绝。</p>}
                {createRepairDraft.isError && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-red-700">创建修复分支失败：{errorText(createRepairDraft.error)}</p>}
                {promote.isError && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-red-700">{concisePromotionError(promote.error)}</p>}
              </div>
            </Modal>
          )
        })()
      )}

      <ConfirmModal
        open={Boolean(deleteTarget)}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteVersion.mutate(deleteTarget)}
        title={`删除叶子分支 ${deleteTarget?.version_number || ''}`}
        description="该草稿或试跑快照及其隔离试跑数据将被永久删除，版本编号不会复用。发布版本、已晋级版本及包含下级分支的节点始终不可删除。"
        confirmText="删除此分支"
        variant="danger"
        loading={deleteVersion.isPending}
      />
    </div>
  )
}
