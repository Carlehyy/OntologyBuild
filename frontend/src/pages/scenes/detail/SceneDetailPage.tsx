/**
 * 三维场景详情页 — 参照本体详情页的三标签结构：
 * 场景展示（Three.js 白模）/ 场景模型（对象/关系/数据绑定）/ 运行日志。
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Boxes, Copy, Pencil, Rocket, Trash2 } from 'lucide-react'
import { scenesApi } from '@/api/scenes'
import type { SceneDefinition } from '@/types/scene'
import { Button } from '@/components/ui/Button'
import { LoadingState } from '@/components/ui/LoadingState'
import { ConfirmModal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'
import { SceneFormModal } from '../components/SceneFormModal'
import { DisplayTab } from './DisplayTab'
import { LogsTab } from './LogsTab'
import { ModelsTab } from './ModelsTab'

const GROUPS = [
  { key: 'display', label: '场景展示' },
  { key: 'models', label: '场景模型' },
  { key: 'logs', label: '运行日志' },
] as const

type GroupKey = (typeof GROUPS)[number]['key']

export default function SceneDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [searchParams, setSearchParams] = useSearchParams()

  const requestedTab = searchParams.get('tab')
  const activeTab: GroupKey = GROUPS.some(group => group.key === requestedTab)
    ? (requestedTab as GroupKey)
    : 'display'

  const sceneQuery = useQuery({
    queryKey: ['scenes', id],
    queryFn: () => scenesApi.get(id ?? ''),
    enabled: !!id,
  })
  const activeVersionNo = sceneQuery.data?.published_version_no
    ?? sceneQuery.data?.current_version_no ?? 0
  const versionQuery = useQuery({
    queryKey: ['scenes', id, 'version', activeVersionNo],
    queryFn: () => scenesApi.version(id ?? '', activeVersionNo),
    enabled: !!id && activeVersionNo >= 1 && activeTab !== 'display',
  })
  // 场景展示标签自带版本选择；模型标签跟随当前生效版本
  const modelsDefinition = (versionQuery.data?.definition ?? null) as SceneDefinition | null

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['scenes'] })
  const publishMutation = useMutation({
    mutationFn: () => scenesApi.publish(id ?? ''),
    onSuccess: published => {
      invalidate()
      toast({ tone: 'success', title: '已发布：v' + published.published_version_no + ' 为对外生效版本' })
    },
    onError: (error) => {
      const detail = (error as { detail?: { message?: string; code?: string } }).detail
      toast({ tone: 'error', title: detail?.message ?? '发布失败' })
    },
  })
  const cloneMutation = useMutation({
    mutationFn: () => scenesApi.clone(id ?? ''),
    onSuccess: created => {
      invalidate()
      toast({ tone: 'success', title: '已克隆为新的草稿态场景' })
      navigate('/scenes/' + created.id)
    },
    onError: () => toast({ tone: 'error', title: '克隆失败' }),
  })
  const removeMutation = useMutation({
    mutationFn: () => scenesApi.remove(id ?? ''),
    onSuccess: () => {
      invalidate()
      toast({ tone: 'success', title: '场景已删除' })
      navigate('/scenes')
    },
    onError: () => toast({ tone: 'error', title: '删除失败' }),
  })

  const [editOpen, setEditOpen] = useState(false)
  const [publishConfirmOpen, setPublishConfirmOpen] = useState(false)
  const [cloneConfirmOpen, setCloneConfirmOpen] = useState(false)
  const [removeConfirmOpen, setRemoveConfirmOpen] = useState(false)

  // 未登录跳转由 axios 拦截器负责；这里处理 404（后端 detail.code=scene_not_found）
  useEffect(() => {
    if (sceneQuery.isError && !sceneQuery.isLoading) {
      toast({ tone: 'error', title: '场景不存在或已被删除' })
      navigate('/scenes', { replace: true })
    }
  }, [sceneQuery.isError, sceneQuery.isLoading, navigate, toast])

  if (sceneQuery.isLoading || !sceneQuery.data || !id) {
    return <div className="p-6"><LoadingState /></div>
  }
  const scene = sceneQuery.data
  const canPublish =
    scene.current_version_no >= 1 &&
    !(scene.status === 'published' && scene.published_version_no === scene.current_version_no)

  const selectTab = (tab: GroupKey) => setSearchParams(tab === 'display' ? {} : { tab })

  return (
    <div className="mx-auto w-full max-w-6xl px-6 py-6">
      <Link
        to="/scenes"
        className="mb-3 inline-flex items-center gap-1 text-xs text-[var(--color-text-secondary)] hover:text-teal-600"
      >
        <ArrowLeft size={13} /> 返回三维场景列表
      </Link>

      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-teal-50 text-teal-700 dark:bg-teal-950 dark:text-teal-300">
            <Boxes size={22} />
          </span>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">{scene.name}</h1>
              <span
                className={
                  'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ' +
                  (scene.status === 'published'
                    ? 'bg-[var(--color-success-bg)] text-[var(--color-success)]'
                    : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300')
                }
              >
                {scene.status === 'published' ? '已发布' : '草稿'}
              </span>
            </div>
            <p className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">
              当前 v{scene.current_version_no}
              {scene.published_version_no != null
                ? ' · 已发布 v' + scene.published_version_no
                : ' · 从未发布'}
            </p>
            {scene.description && (
              <p className="mt-1 max-w-2xl text-xs leading-5 text-[var(--color-text-secondary)]">{scene.description}</p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          {canPublish && (
            <Button onClick={() => setPublishConfirmOpen(true)}>
              <Rocket size={15} /> 发布
            </Button>
          )}
          <Button variant="outline" onClick={() => setEditOpen(true)}>
            <Pencil size={14} /> 编辑信息
          </Button>
          <Button variant="outline" onClick={() => setCloneConfirmOpen(true)}>
            <Copy size={14} /> 克隆
          </Button>
          <Button variant="ghost" onClick={() => setRemoveConfirmOpen(true)}>
            <Trash2 size={14} /> 删除
          </Button>
        </div>
      </header>

      <nav className="mb-4 flex items-center gap-1 border-b border-[var(--color-border)]" aria-label="场景详情标签栏">
        {GROUPS.map(group => {
          const isActive = activeTab === group.key
          return (
            <button
              key={group.key}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => selectTab(group.key)}
              className={
                '-mb-px border-b-2 px-3 py-2 text-sm transition-colors ' +
                (isActive
                  ? 'border-teal-600 font-medium text-teal-700 dark:text-teal-300'
                  : 'border-transparent text-[var(--color-text-secondary)] hover:text-teal-600')
              }
            >
              {group.label}
            </button>
          )
        })}
      </nav>

      {activeTab === 'display' && <DisplayTab scene={scene} />}
      {activeTab === 'models' && <ModelsTab definition={modelsDefinition} />}
      {activeTab === 'logs' && <LogsTab sceneId={scene.id} everPublished={scene.published_version_no != null} />}

      <SceneFormModal
        open={editOpen}
        title="编辑场景信息"
        initial={{ name: scene.name, description: scene.description }}
        onClose={() => setEditOpen(false)}
        onSubmit={async value => {
          await scenesApi.updateBasicInfo(scene.id, value)
          await queryClient.invalidateQueries({ queryKey: ['scenes'] })
          setEditOpen(false)
          toast({ tone: 'success', title: '基本信息已更新' })
        }}
      />
      <ConfirmModal
        open={publishConfirmOpen}
        onClose={() => setPublishConfirmOpen(false)}
        onConfirm={() => publishMutation.mutate()}
        title="发布场景"
        description={'将冻结当前版本 v' + scene.current_version_no + ' 为对外生效版本。发布后再编辑会使场景回到草稿态，已发布版本保留可随时重新发布。'}
        confirmText="确认发布"
        loading={publishMutation.isPending}
      />
      <ConfirmModal
        open={cloneConfirmOpen}
        onClose={() => setCloneConfirmOpen(false)}
        onConfirm={() => cloneMutation.mutate()}
        title="克隆场景"
        description="将克隆当前生效定义生成一个全新的草稿态场景（名称自动加“-副本”），版本历史从 v1 开始。"
        confirmText="确认克隆"
        loading={cloneMutation.isPending}
      />
      <ConfirmModal
        open={removeConfirmOpen}
        onClose={() => setRemoveConfirmOpen(false)}
        onConfirm={() => removeMutation.mutate()}
        title="删除场景"
        variant="danger"
        description={'确定删除「' + scene.name + '」吗？其全部版本定义与运行日志将一并删除，操作不可恢复。'}
        confirmText="确认删除"
        loading={removeMutation.isPending}
      />
    </div>
  )
}
