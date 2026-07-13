import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  Activity, AlertTriangle, CheckCircle2, ChevronRight, Database,
  FileEdit, GitBranch, GitCommitHorizontal, Plus, Rocket, ShieldCheck,
} from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { LoadingState } from '@/components/ui/LoadingState'
import { Modal } from '@/components/ui/Modal'
import {
  ontologyVersionApi,
  type OntologyImpactReport,
  type OntologyTrialRun as Trial,
  type OntologyVersionNode as VersionNode,
} from '@/api/v2/ontology-versions'

function errorText(error: any) {
  const detail = error?.response?.data?.detail ?? error?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  if (Array.isArray(detail?.errors)) return detail.errors.map((item: any) => item.message).join('；')
  return error?.message || '操作失败'
}

function statusBadge(node: VersionNode, current: boolean) {
  if (current) return <Badge variant="success">当前发布</Badge>
  if (node.node_kind === 'release') return <Badge>历史发布</Badge>
  if (node.lifecycle_status === 'superseded') return <Badge>已晋级</Badge>
  if (node.latest_trial?.status === 'passed') return <Badge variant="success">试跑通过</Badge>
  if (node.latest_trial?.status === 'failed') return <Badge variant="danger">试跑失败</Badge>
  if (node.latest_trial?.status === 'stale') return <Badge variant="warning">试跑已失效</Badge>
  return <Badge variant="warning">草稿</Badge>
}

export default function VersionsTab({ ontologyId }: { ontologyId: string }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [source, setSource] = useState<VersionNode | null>(null)
  const [label, setLabel] = useState('')
  const [description, setDescription] = useState('')
  const [trialDetail, setTrialDetail] = useState<Trial | null>(null)
  const [promotion, setPromotion] = useState<{ node: VersionNode; impact: OntologyImpactReport } | null>(null)
  const [notice, setNotice] = useState<{ tone: 'good' | 'bad'; text: string } | null>(null)

  const treeQuery = useQuery({
    queryKey: ['version-tree', ontologyId],
    queryFn: () => ontologyVersionApi.tree(ontologyId),
  })

  const nodes = (treeQuery.data?.versions || []) as VersionNode[]
  const currentReleaseId = treeQuery.data?.current_release_id as string | undefined
  const ordered = useMemo(() => {
    const children = new Map<string | null, VersionNode[]>()
    for (const node of nodes) {
      const parent = node.parent_version_id || null
      children.set(parent, [...(children.get(parent) || []), node])
    }
    const output: Array<{ node: VersionNode; depth: number }> = []
    const visit = (parent: string | null, depth: number) => {
      for (const node of children.get(parent) || []) {
        output.push({ node, depth })
        visit(node.id, depth + 1)
      }
    }
    visit(null, 0)
    // 历史迁移数据可能没有 parent；确保仍可见。
    for (const node of nodes) if (!output.some(item => item.node.id === node.id)) output.push({ node, depth: 0 })
    return output
  }, [nodes])

  const refresh = () => qc.invalidateQueries({ queryKey: ['version-tree', ontologyId] })
  const refreshReleasedProjection = async () => {
    const keys = [
      ['ontologies'],
      ['ontology', ontologyId],
      ['formal-overview', ontologyId], ['recent-facts', ontologyId],
      ['ms-ot', ontologyId], ['ms-lt', ontologyId], ['ms-act', ontologyId],
      ['ms-fn', ontologyId], ['ms-inst', ontologyId],
      ['fi-ot', ontologyId], ['fi-inst', ontologyId],
      ['formal-object-types', ontologyId], ['formal-link-types', ontologyId],
      ['mappings', ontologyId], ['link-mappings', ontologyId],
      ['mapping-object-instances', ontologyId], ['mapping-link-instances', ontologyId],
      ['gov-sentinels', ontologyId], ['gov-autonomy', ontologyId],
      ['gov-facts', ontologyId],
    ]
    await Promise.all(keys.map(queryKey => qc.invalidateQueries({ queryKey })))
  }
  const createDraft = useMutation({
    mutationFn: () => ontologyVersionApi.createDraft(
      ontologyId, source!.id, { versionLabel: label, description }),
    onSuccess: async node => {
      await refresh(); setSource(null); setLabel(''); setDescription('')
      setNotice({ tone: 'good', text: `${node.version_number} 已从完整快照创建。` })
    },
  })

  const runTrial = useMutation({
    mutationFn: (node: VersionNode) => ontologyVersionApi.runTrial(ontologyId, node.id),
    onSuccess: async run => {
      await refresh(); setTrialDetail(run)
      setNotice(run.status === 'passed'
        ? { tone: 'good', text: '试跑通过：真实数据已在隔离空间完成验证，未执行任何外部动作。' }
        : { tone: 'bad', text: '试跑未通过，请根据错误修正结构或映射。' })
    },
    onError: error => setNotice({ tone: 'bad', text: errorText(error) }),
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
      ontologyId, promotion!.node.id,
      {
        trialRunId: promotion!.node.latest_trial!.id,
        impactHash: promotion!.impact.impactHash,
        versionLabel: promotion!.node.version_label || '',
      }),
    onSuccess: async release => {
      setPromotion(null); await refresh()
      await refreshReleasedProjection()
      setNotice({ tone: 'good', text: `${release.version_number} 已发布并成为唯一运行版本。` })
    },
    onError: error => setNotice({ tone: 'bad', text: errorText(error) }),
  })

  if (treeQuery.isLoading) return <LoadingState />
  if (treeQuery.isError) return (
    <Card className="space-y-3 border-red-200 bg-red-50 p-4 text-sm text-red-800">
      <p>版本树加载失败：{errorText(treeQuery.error)}</p>
      <Button variant="outline" size="sm" onClick={() => treeQuery.refetch()}>重新加载</Button>
    </Card>
  )

  return (
    <div className="space-y-4 pb-6">
      <Card className="p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="flex items-center gap-2 font-medium text-[var(--color-text-primary)]">
              <GitCommitHorizontal size={17} /> 在秩序中演化
            </h3>
            <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
              每个节点都是完整结构。草稿只有通过真实数据隔离试跑并确认影响后，才能晋级为新的发布版。
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
            <ShieldCheck size={15} className="text-emerald-600" /> 线上结构始终指向一个发布版本
          </div>
        </div>
      </Card>

      {notice && (
        <div className={`rounded-lg border px-3 py-2 text-sm ${notice.tone === 'good'
          ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
          : 'border-red-200 bg-red-50 text-red-800'}`}>
          {notice.text}
        </div>
      )}

      <div className="overflow-x-auto rounded-xl border border-[var(--color-border)] bg-white">
        <div className="grid min-w-[820px] grid-cols-[minmax(220px,1fr)_130px_150px_minmax(280px,auto)] gap-3 border-b bg-gray-50 px-4 py-2 text-xs font-medium text-gray-500">
          <span>版本树</span><span>状态</span><span>最近试跑</span><span className="text-right">操作</span>
        </div>
        {ordered.map(({ node, depth }) => {
          const current = node.id === currentReleaseId
          const editable = node.node_kind === 'draft' && node.lifecycle_status !== 'superseded'
          const passed = editable && node.latest_trial?.status === 'passed'
          return (
            <div key={node.id} data-testid={`version-row-${node.version_number}`}
              className="grid min-w-[820px] grid-cols-[minmax(220px,1fr)_130px_150px_minmax(280px,auto)] items-center gap-3 border-b px-4 py-3 last:border-b-0">
              <div className="min-w-0" style={{ paddingLeft: depth * 24 }}>
                <div className="flex items-center gap-2">
                  {depth > 0 && <ChevronRight size={13} className="text-gray-300" />}
                  {node.node_kind === 'release'
                    ? <GitCommitHorizontal size={15} className="text-emerald-600" />
                    : <GitBranch size={15} className="text-amber-600" />}
                  <span className="font-mono font-semibold">{node.version_number}</span>
                  {node.version_label && <span className="truncate text-xs text-gray-500">{node.version_label}</span>}
                </div>
                {node.description && <p className="mt-1 truncate text-xs text-gray-400">{node.description}</p>}
              </div>
              <div>{statusBadge(node, current)}</div>
              <div className="text-xs text-gray-500">
                {node.latest_trial ? (
                  <button className="inline-flex items-center gap-1 hover:text-gray-900" onClick={() => setTrialDetail(node.latest_trial!)}>
                    {node.latest_trial.status === 'passed' ? <CheckCircle2 size={13} className="text-emerald-600" /> : <Activity size={13} />}
                    {node.latest_trial.result?.counts?.objects ?? 0} 对象
                  </button>
                ) : '—'}
              </div>
              <div className="flex flex-wrap justify-end gap-1.5">
                <Button variant="ghost" size="sm" onClick={() => setSource(node)}>
                  <Plus size={13} /> 创建分支
                </Button>
                {editable && <>
                  <Button variant="ghost" size="sm" onClick={() => navigate(`/ontologies/${ontologyId}/graph?versionId=${node.id}`)}>
                    <FileEdit size={13} /> 结构
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => navigate(`/ontologies/${ontologyId}/mapping-config?versionId=${node.id}`)}>
                    <Database size={13} /> 映射
                  </Button>
                  <Button variant="ghost" size="sm" loading={runTrial.isPending} onClick={() => runTrial.mutate(node)}>
                    <Activity size={13} /> 试跑
                  </Button>
                  {passed && <Button size="sm" onClick={() => inspectImpact.mutate(node)}>
                    <Rocket size={13} /> 发布
                  </Button>}
                </>}
              </div>
            </div>
          )
        })}
      </div>

      {source && (
        <Modal open onClose={() => setSource(null)} title={`从 ${source.version_number} 创建完整分支`} size="sm"
          footer={<>
            <Button variant="ghost" onClick={() => setSource(null)}>取消</Button>
            <Button loading={createDraft.isPending} onClick={() => createDraft.mutate()}>创建分支</Button>
          </>}>
          <div className="space-y-3">
            <p className="text-sm text-gray-600">系统会复制该节点的全部对象、关系、动作、函数、哨兵和映射，不依赖祖先拼装。</p>
            <input value={label} onChange={event => setLabel(event.target.value)} placeholder="分支标签（可选）" className="w-full rounded-lg border px-3 py-2 text-sm" />
            <textarea value={description} onChange={event => setDescription(event.target.value)} placeholder="本次变化目标（可选）" className="h-20 w-full resize-none rounded-lg border px-3 py-2 text-sm" />
            {createDraft.isError && <p className="text-sm text-red-600">{errorText(createDraft.error)}</p>}
          </div>
        </Modal>
      )}

      {trialDetail && (
        <Modal open onClose={() => setTrialDetail(null)} title="隔离试跑结果" size="lg"
          footer={<Button onClick={() => setTrialDetail(null)}>关闭</Button>}>
          <div className="space-y-4 text-sm">
            <div className="grid grid-cols-4 gap-3">
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
        <Modal open onClose={() => setPromotion(null)} title={`审核 ${promotion.node.version_number} 的发布影响`} size="lg"
          footer={<>
            <Button variant="ghost" onClick={() => setPromotion(null)}>取消</Button>
            <Button disabled={promotion.impact.baseOutdated} loading={promote.isPending} onClick={() => promote.mutate()}><Rocket size={14} /> 确认发布</Button>
          </>}>
          <div className="space-y-4 text-sm">
            {promotion.impact.baseOutdated && <div className="flex gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-red-800"><AlertTriangle size={17} /> 草稿基线已过期，不能发布。</div>}
            <div className="grid grid-cols-3 gap-3">
              <div className="rounded-lg border p-3"><b className="text-emerald-700">+{promotion.impact.total.added}</b><p className="text-xs text-gray-500">新增</p></div>
              <div className="rounded-lg border p-3"><b className="text-amber-700">~{promotion.impact.total.modified}</b><p className="text-xs text-gray-500">修改</p></div>
              <div className="rounded-lg border p-3"><b className="text-red-700">-{promotion.impact.total.deleted}</b><p className="text-xs text-gray-500">删除</p></div>
            </div>
            <div>
              <h4 className="mb-2 font-medium">破坏性影响（{promotion.impact.breakingCount}）</h4>
              {promotion.impact.breakingCount === 0
                ? <p className="rounded-lg bg-emerald-50 p-3 text-emerald-800">未发现结构性破坏。</p>
                : <div className="max-h-56 space-y-2 overflow-auto">{promotion.impact.breaking.map((item: any, index: number) => (
                  <div key={index} className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-900">{item.message}</div>
                ))}</div>}
            </div>
            <p className="text-xs text-gray-500">确认后，仅本次试跑验证过的精确结构哈希和数据版本可晋级；任何变化都会自动拒绝。</p>
            {promote.isError && <p className="text-sm text-red-600">{errorText(promote.error)}</p>}
          </div>
        </Modal>
      )}
    </div>
  )
}
