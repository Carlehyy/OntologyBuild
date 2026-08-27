/**
 * 三维场景详情页 — 2026-08 版式重排：
 * 顶部一张「场景基本信息卡」（名称/状态/版本/描述 + 右侧操作按钮组），
 * 下方左右双卡：左=三维可视化（SceneCanvas 常驻，含版本与模拟推送工具行），
 * 右=操作栏卡（参考超级助手助手配置卡片：圆角浮层式白卡 + 两标签滑动指示器，
 * 「场景模型 / 运行日志」）。?tab= 深链保留：models 直达模型、logs 直达日志，
 * 旧 display 值归一为默认（左画布常驻可见）。
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Boxes, Copy, Pencil, Rocket, Trash2 } from 'lucide-react'
import { scenesApi } from '@/api/scenes'
import type { SceneDefinition, SceneDetail } from '@/types/scene'
import { LoadingState } from '@/components/ui/LoadingState'
import { ConfirmModal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'
import { SceneFormModal } from '../components/SceneFormModal'
import { DisplayTab } from './DisplayTab'
import { LogsTab } from './LogsTab'
import { ModelsTab } from './ModelsTab'

const PANEL_GROUPS = [
  { key: 'models', label: '场景模型' },
  { key: 'logs', label: '运行日志' },
] as const

type PanelKey = (typeof PANEL_GROUPS)[number]['key']

const HEADER_ICON_BUTTON_CLASS = 'inline-flex h-10 w-10 items-center justify-center rounded-lg bg-[var(--color-nav-bg)] text-white shadow-sm transition-all hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2'

/** 旧深链值归一：display 已并入左侧常驻画布，映射到默认面板。 */
function normalizePanelKey(raw: string | null): PanelKey {
  if (raw === 'logs') return 'logs'
  return 'models'
}

export default function SceneDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [searchParams, setSearchParams] = useSearchParams()

  const activePanel: PanelKey = normalizePanelKey(searchParams.get('tab'))

  const tabsRef = useRef<HTMLDivElement>(null)
  const [indicatorPos, setIndicatorPos] = useState({ left: 0, width: 0 })

  useEffect(() => {
    const container = tabsRef.current
    if (!container) return
    const activeButton = container.querySelector('[data-tab-value="' + activePanel + '"]') as HTMLElement | null
    if (!activeButton) return
    activeButton.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    const containerRect = container.getBoundingClientRect()
    const buttonRect = activeButton.getBoundingClientRect()
    setIndicatorPos({ left: buttonRect.left - containerRect.left, width: buttonRect.width })
  }, [activePanel])

  const sceneQuery = useQuery({
    queryKey: ['scenes', id],
    queryFn: () => scenesApi.get(id ?? ''),
    enabled: !!id,
  })

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
  const scene: SceneDetail = sceneQuery.data
  const canPublish =
    scene.current_version_no >= 1 &&
    !(scene.status === 'published' && scene.published_version_no === scene.current_version_no)

  // URL 同步：models 视为默认态清空参数，logs 写入 tab；旧 display 深链自然落回默认。
  const selectPanel = (key: PanelKey) =>
    setSearchParams(key === 'models' ? {} : { tab: key }, { replace: true })

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-hidden">
      {/* ═══ 场景基本信息卡（右上角操作组） ═══ */}
      <header className="flex shrink-0 items-center justify-between gap-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-5 py-4 shadow-[0_8px_28px_rgba(15,23,42,0.06)]">
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-teal-500 to-cyan-500 text-white shadow-sm">
            <Boxes size={22} />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-lg font-semibold text-slate-800 dark:text-[var(--color-text-primary)]">{scene.name}</h1>
              <span className={
                'inline-flex shrink-0 rounded-md border px-2 py-0.5 text-[11px] font-medium leading-4 ' +
                (scene.status === 'published'
                  ? 'border-teal-100 bg-teal-50 text-teal-700 dark:border-teal-800 dark:bg-teal-950 dark:text-teal-300'
                  : 'border-slate-200 bg-slate-50 text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300')
              }>
                {scene.status === 'published' ? '已发布' : '草稿'}
              </span>
            </div>
            <p className="mt-1 truncate text-xs text-slate-400 dark:text-[var(--color-text-tertiary)]" title={scene.description || undefined}>
              当前 v{scene.current_version_no}
              {scene.published_version_no != null ? ' · 生效 v' + scene.published_version_no : ' · 从未发布'}
              {scene.description ? ' · ' + scene.description : ''}
            </p>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
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
          <Link to="/scenes" className="inline-flex h-10 shrink-0 items-center gap-1 rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-text-secondary)] transition-colors hover:border-teal-300 hover:text-teal-700">
            <ArrowLeft size={13} /> 返回列表
          </Link>
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
      </header>

      {/* ═══ 左右双卡：三维可视化 / 操作栏 ═══ */}
      <div className="flex min-h-0 flex-1 gap-4 overflow-hidden">
        {/* 左：三维场景可视化（常驻画布 + 工具行） */}
        <section className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-[0_8px_28px_rgba(15,23,42,0.06)]" aria-label="三维场景可视化">
          <DisplayTab scene={scene} />
        </section>

        {/* 右：操作栏卡（场景模型 / 运行日志）——参考超级助手助手配置卡片 */}
        <aside className="flex w-[400px] shrink-0 flex-col overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-[0_8px_28px_rgba(15,23,42,0.06)]" aria-label="场景操作栏">
          <div className="shrink-0 border-b border-[var(--color-border)] p-3">
            <div ref={tabsRef} className="relative flex w-max items-center gap-1 rounded-xl border border-[var(--color-border)] bg-slate-50/70 p-1 dark:bg-slate-900/60">
              <div
                aria-hidden="true"
                className="absolute top-1 h-[calc(100%-8px)] rounded-lg bg-teal-600 shadow-sm transition-all duration-300 ease-out"
                style={{ left: indicatorPos.left + 'px', width: indicatorPos.width + 'px' }}
              />
              {PANEL_GROUPS.map(group => {
                const isActive = activePanel === group.key
                return (
                  <button
                    key={group.key}
                    type="button"
                    role="tab"
                    data-tab-value={group.key}
                    aria-selected={isActive}
                    onClick={() => selectPanel(group.key)}
                    className={
                      'relative z-10 whitespace-nowrap rounded-lg px-4 py-2 text-sm font-medium transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-1 ' +
                      (isActive ? 'text-white' : 'text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200')
                    }
                  >
                    {group.label}
                  </button>
                )
              })}
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {activePanel === 'models'
              ? <PanelModelsBody id={id} scene={scene} />
              : <LogsTab sceneId={scene.id} everPublished={scene.published_version_no != null} />}
          </div>
        </aside>
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

/**
 * 场景模型面板体 — 从当前生效版本拉定义只读展示；
 * 与旧三标签时代逻辑一致：已发布指针优先，其次最新草稿版本。
 */
function PanelModelsBody({ id, scene }: { id: string; scene: SceneDetail }) {
  const activeVersionNo = scene.published_version_no ?? scene.current_version_no ?? 0
  const versionQuery = useQuery({
    queryKey: ['scenes', id, 'version', activeVersionNo],
    queryFn: () => scenesApi.version(id, activeVersionNo),
    enabled: activeVersionNo >= 1,
  })
  if (activeVersionNo < 1) {
    return (
      <div className="rounded-xl border border-[var(--color-border)] bg-card p-8 text-center text-xs leading-5 text-slate-400 dark:text-slate-500">
        该场景还没有版本定义：先在建模页或列表页创建第一个版本
      </div>
    )
  }
  if (versionQuery.isLoading) return <LoadingState message="加载场景定义…" />
  return <ModelsTab definition={(versionQuery.data?.definition ?? null) as SceneDefinition | null} />
}
