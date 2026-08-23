/**
 * 三维场景详情页 — 参照本体详情页：白色玻璃卡头部 + 滑块式标签栏 +
 * 版本徽章与图标操作组；三标签（场景展示/场景模型/运行日志）由 ?tab= 深链驱动。
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Boxes, Copy, Pencil, Rocket, Trash2 } from 'lucide-react'
import { scenesApi } from '@/api/scenes'
import type { SceneDefinition } from '@/types/scene'
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

const HEADER_ICON_BUTTON_CLASS = 'inline-flex h-10 w-10 items-center justify-center rounded-lg bg-[var(--color-nav-bg)] text-white shadow-sm transition-all hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2'

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

  const groupTabsRef = useRef<HTMLDivElement>(null)
  const [indicatorPos, setIndicatorPos] = useState({ left: 0, width: 0 })

  useEffect(() => {
    const container = groupTabsRef.current
    if (!container) return
    const activeButton = container.querySelector('[data-tab-value="' + activeTab + '"]') as HTMLElement | null
    if (!activeButton) return
    activeButton.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    const containerRect = container.getBoundingClientRect()
    const buttonRect = activeButton.getBoundingClientRect()
    setIndicatorPos({ left: buttonRect.left - containerRect.left, width: buttonRect.width })
  }, [activeTab])

  const sceneQuery = useQuery({
    queryKey: ['scenes', id],
    queryFn: () => scenesApi.get(id ?? ''),
    enabled: !!id,
  })
  const activeVersionNo = sceneQuery.data?.published_version_no ?? sceneQuery.data?.current_version_no ?? 0
  const versionQuery = useQuery({
    queryKey: ['scenes', id, 'version', activeVersionNo],
    queryFn: () => scenesApi.version(id ?? '', activeVersionNo),
    enabled: !!id && activeVersionNo >= 1 && activeTab !== 'display',
  })
  const modelsDefinition = (versionQuery.data?.definition ?? null) as SceneDefinition | null

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['scenes'] })
  const publishMutation = useMutation({
    mutationFn: () => scenesApi.publish(id ?? ''),
    onSuccess: published => {
      invalidate()
      toast({ tone: 'success', title: '发布成功', description: 'v' + published.published_version_no + ' 已冻结为对外生效版本。' })
    },
    onError: error => {
      const detail = (error as { detail?: { message?: string } }).detail
      toast({ tone: 'error', title: '发布失败', description: detail?.message ?? '请稍后重试' })
    },
  })
  const cloneMutation = useMutation({
    mutationFn: () => scenesApi.clone(id ?? ''),
    onSuccess: created => {
      invalidate()
      toast({ tone: 'success', title: '已克隆为新草稿场景' })
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
  const [publishOpen, setPublishOpen] = useState(false)
  const [cloneOpen, setCloneOpen] = useState(false)
  const [removeOpen, setRemoveOpen] = useState(false)

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

  const selectTab = (key: GroupKey) => setSearchParams(key === 'display' ? {} : { tab: key }, { replace: true })

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-hidden">
      {/* ═══ 标题区 ═══ */}
      <header className="flex shrink-0 items-start justify-between gap-3 rounded-xl border border-slate-200 bg-white px-5 py-4 shadow-sm/50">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-teal-500 to-cyan-500 text-white shadow-sm">
            <Boxes size={22} />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-lg font-semibold text-slate-800">{scene.name}</h1>
              <span className={
                'inline-flex shrink-0 rounded-md border px-2 py-0.5 text-[11px] font-medium leading-4 ' +
                (scene.status === 'published'
                  ? 'border-teal-100 bg-teal-50 text-teal-700'
                  : 'border-slate-200 bg-slate-50 text-slate-500')
              }>
                {scene.status === 'published' ? '已发布' : '草稿'}
              </span>
            </div>
            <p className="mt-1 text-xs text-slate-400">
              当前 v{scene.current_version_no}
              {scene.published_version_no != null ? ' · 已发布 v' + scene.published_version_no : ' · 从未发布'}
              {scene.description ? ' · ' + scene.description : ''}
            </p>
          </div>
        </div>
        <Link to="/scenes" className="inline-flex shrink-0 items-center gap-1 text-xs text-slate-500 transition-colors hover:text-teal-700">
          <ArrowLeft size={13} /> 返回列表
        </Link>
      </header>

      {/* ═══ 标签栏 + 操作组 ═══ */}
      <div className="flex shrink-0 items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-2.5 shadow-sm/50">
        <div ref={groupTabsRef} className="relative flex w-max items-center gap-1 rounded-xl border border-slate-200 bg-slate-50/70 p-1">
          <div
            aria-hidden="true"
            className="absolute top-1 h-[calc(100%-8px)] rounded-lg bg-teal-600 shadow-sm transition-all duration-300 ease-out"
            style={{ left: indicatorPos.left + 'px', width: indicatorPos.width + 'px' }}
          />
          {GROUPS.map(group => {
            const isActive = activeTab === group.key
            return (
              <button
                key={group.key}
                type="button"
                role="tab"
                data-tab-value={group.key}
                aria-selected={isActive}
                onClick={() => selectTab(group.key)}
                className={
                  'relative z-10 whitespace-nowrap rounded-lg px-4 py-2 text-sm font-medium transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-1 ' +
                  (isActive ? 'text-white' : 'text-slate-500 hover:text-slate-700')
                }
              >
                {group.label}
              </button>
            )
          })}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <span className="inline-flex h-10 items-center rounded-lg border border-teal-100 bg-teal-50 px-3 font-mono text-sm font-semibold tabular-nums text-teal-700" title="当前生效版本">
            {scene.published_version_no != null ? 'v' + scene.published_version_no : 'v' + scene.current_version_no}
          </span>
          {canPublish && (
            <button
              type="button"
              onClick={() => setPublishOpen(true)}
              className="inline-flex h-10 items-center gap-1.5 rounded-lg bg-[var(--color-nav-bg)] px-4 text-sm font-medium text-white shadow-sm transition-all hover:-translate-y-0.5 hover:opacity-90"
              title="冻结当前版本为对外生效版本"
            >
              <Rocket size={15} /> 发布
            </button>
          )}
          <button type="button" onClick={() => setEditOpen(true)} className={HEADER_ICON_BUTTON_CLASS} title="编辑场景信息" aria-label="编辑场景信息">
            <Pencil size={18} />
          </button>
          <button type="button" onClick={() => setCloneOpen(true)} className={HEADER_ICON_BUTTON_CLASS} title="克隆场景" aria-label="克隆场景">
            <Copy size={18} />
          </button>
          <button type="button" onClick={() => setRemoveOpen(true)} className={HEADER_ICON_BUTTON_CLASS + ' hover:!bg-red-500'} title="删除场景" aria-label="删除场景">
            <Trash2 size={18} />
          </button>
        </div>
      </div>

      {/* ═══ 内容 ═══ */}
      <div className="min-h-0 flex-1 overflow-hidden">
        {activeTab === 'display' && <DisplayTab scene={scene} />}
        {activeTab === 'models' && (
          <div className="h-full overflow-y-auto"><ModelsTab definition={modelsDefinition} /></div>
        )}
        {activeTab === 'logs' && (
          <div className="h-full overflow-y-auto"><LogsTab sceneId={scene.id} everPublished={scene.published_version_no != null} /></div>
        )}
      </div>

      <SceneFormModal
        open={editOpen}
        title="编辑场景信息"
        initial={{ name: scene.name, description: scene.description }}
        onClose={() => setEditOpen(false)}
        onSubmit={async value => {
          await scenesApi.updateBasicInfo(scene.id, value)
          await queryClient.invalidateQueries({ queryKey: ['scenes'] })
          setEditOpen(false)
          toast({ tone: 'success', title: '场景信息已更新' })
        }}
      />
      <ConfirmModal
        open={publishOpen}
        onClose={() => setPublishOpen(false)}
        onConfirm={() => publishMutation.mutate()}
        title="发布场景"
        description={'将冻结当前版本 v' + scene.current_version_no + ' 为对外生效版本。发布后再编辑会使场景回到草稿态，已发布版本保留可随时重新发布。'}
        confirmText="确认发布"
        loading={publishMutation.isPending}
      />
      <ConfirmModal
        open={cloneOpen}
        onClose={() => setCloneOpen(false)}
        onConfirm={() => cloneMutation.mutate()}
        title="克隆场景"
        description="将克隆当前生效定义生成一个全新的草稿态场景（名称自动加“-副本”），版本历史从 v1 开始。"
        confirmText="确认克隆"
        loading={cloneMutation.isPending}
      />
      <ConfirmModal
        open={removeOpen}
        onClose={() => setRemoveOpen(false)}
        onConfirm={() => removeMutation.mutate()}
        title="删除场景"
        variant="danger"
        description={'确定删除「' + scene.name + '」吗？其全部版本定义与运行日志将一并删除，操作不可恢复。'}
        confirmText="删除场景"
        loading={removeMutation.isPending}
      />
    </div>
  )
}
