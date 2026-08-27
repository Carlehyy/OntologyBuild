/**
 * 三维场景详情页 — 版式 v3：
 * 顶部「场景基本信息卡」（名称/状态/版本/描述 + 右侧图标操作组：返回·发布·编辑·克隆·删除），
 * 下方左右双卡：左=三维可视化常驻画布；右=操作栏卡（参考超级助手助手配置卡片的
 * 平铺风格：白卡内不做嵌套圆角），五标签滑动指示器——
 * 对象 / 关系 / 事件 / 场景模型(数据绑定) / 运行日志，顺序按
 * 「空间构成 → 连接关系 → 动态事件 → 驱动绑定 → 运行观测」。
 * ?tab= 深链兼容：objects|relations|events|models|logs 直达；旧 display 归一默认。
 *
 * 指示器修复（v2 的首屏 bug）：加载态早退时标签条尚未挂载，仅依赖 activePanel
 * 的 effect 不再重测，导致滑块停在零宽。现改为 useCallback 测量函数 +
 * 成功数据到达后补测 + ResizeObserver 跟随字体/布局变化实时校准。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Boxes, Copy, Pencil, Rocket, Trash2 } from 'lucide-react'
import { scenesApi } from '@/api/scenes'
import type { SceneDefinition, SceneDetail } from '@/types/scene'
import { LoadingState } from '@/components/ui/LoadingState'
import { ConfirmModal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'
import { SceneFormModal } from '../components/SceneFormModal'
import { DisplayTab } from './DisplayTab'
import { LogsTab } from './LogsTab'
import { BindingsPanel, EventsPanel, ModelsEmpty, ObjectsPanel, RelationsPanel } from './ModelsTab'

const PANEL_GROUPS = [
  { key: 'objects', label: '对象' },
  { key: 'relations', label: '关系' },
  { key: 'events', label: '事件' },
  { key: 'models', label: '场景模型' },
  { key: 'logs', label: '运行日志' },
] as const

type PanelKey = (typeof PANEL_GROUPS)[number]['key']

const HEADER_ICON_BUTTON_CLASS = 'inline-flex h-10 w-10 items-center justify-center rounded-lg bg-[var(--color-nav-bg)] text-white shadow-sm transition-all hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2'

/** 旧深链值归一：display 已并入左侧常驻画布，映射到默认面板；未知值落回对象。 */
function normalizePanelKey(raw: string | null): PanelKey {
  if (raw === 'logs' || raw === 'relations' || raw === 'events' || raw === 'models') return raw
  return 'objects'
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

  // —— 指示器测量：任何依赖变化（含加载完成与容器尺寸）都能重新校准 ——
  const measureIndicator = useCallback(() => {
    const container = tabsRef.current
    const activeButton = container?.querySelector('[data-tab-value="' + activePanel + '"]') as HTMLElement | null
    if (!container || !activeButton) return
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

  useEffect(() => { measureIndicator() }, [measureIndicator, sceneQuery.isSuccess])

  useEffect(() => {
    const container = tabsRef.current
    if (!container || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => measureIndicator())
    observer.observe(container)
    return () => observer.disconnect()
  }, [measureIndicator])

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

  // URL 同步：objects 为默认态清参，其余写 tab；旧 display 深链自然落回默认。
  const selectPanel = (key: PanelKey) =>
    setSearchParams(key === 'objects' ? {} : { tab: key }, { replace: true })

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-hidden">
      {/* ═══ 场景基本信息卡（右侧图标操作组：返回 · 发布 · 编辑 · 克隆 · 删除） ═══ */}
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
          {/* 返回列表固定在最左 */}
          <button type="button" onClick={() => navigate('/scenes')} className={HEADER_ICON_BUTTON_CLASS} title="返回列表" aria-label="返回列表">
            <ArrowLeft size={18} />
          </button>
          {canPublish && (
            <button type="button" onClick={() => setPublishOpen(true)} className={HEADER_ICON_BUTTON_CLASS} title="发布场景" aria-label="发布场景">
              <Rocket size={18} />
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
      </header>

      {/* ═══ 左右双卡：三维可视化 / 操作栏（五标签平铺） ═══ */}
      <div className="flex min-h-0 flex-1 gap-4 overflow-hidden">
        {/* 左：三维场景可视化（常驻画布 + 工具行） */}
        <section className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-[0_8px_28px_rgba(15,23,42,0.06)]" aria-label="三维场景可视化">
          <DisplayTab scene={scene} />
        </section>

        {/* 右：操作栏卡（参考助手配置卡片：白卡平铺，不嵌套圆角容器） */}
        <aside className="flex w-[400px] shrink-0 flex-col overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-[0_8px_28px_rgba(15,23,42,0.06)]" aria-label="场景操作栏">
          <div className="shrink-0 border-b border-[var(--color-border)] px-4 pt-3 pb-2.5">
            <div ref={tabsRef} className="relative flex items-center gap-4 overflow-x-auto">
              <span
                aria-hidden="true"
                data-testid="panel-indicator"
                className="absolute bottom-[-10px] z-10 h-0.5 rounded-full bg-teal-600 shadow-sm transition-all duration-200 ease-out"
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
                      'relative z-10 whitespace-nowrap pb-2 text-sm transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-1 ' +
                      (isActive
                        ? 'font-semibold text-teal-700 dark:text-teal-300'
                        : 'text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300')
                    }
                  >
                    {group.label}
                  </button>
                )
              })}
            </div>
          </div>
          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
            <PanelBody id={id} scene={scene} panel={activePanel} />
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

/** 操作栏内容分发 — 全部从当前生效版本拉定义后平铺展示。 */
function PanelBody({ id, scene, panel }: { id: string; scene: SceneDetail; panel: PanelKey }) {
  const activeVersionNo = scene.published_version_no ?? scene.current_version_no ?? 0
  const versionQuery = useQuery({
    queryKey: ['scenes', id, 'version', activeVersionNo],
    queryFn: () => scenesApi.version(id, activeVersionNo),
    enabled: panel !== 'logs' && activeVersionNo >= 1,
  })

  if (panel === 'logs') {
    return <LogsTab sceneId={scene.id} everPublished={scene.published_version_no != null} />
  }

  if (activeVersionNo < 1 || versionQuery.isError) {
    return <ModelsEmpty />
  }
  if (versionQuery.isLoading || !versionQuery.data) {
    return <LoadingState message="加载场景定义…" />
  }
  const definition = (versionQuery.data.definition ?? {}) as Partial<SceneDefinition>
  switch (panel) {
    case 'relations':
      return <RelationsPanel relations={definition.relations ?? []} />
    case 'events':
      return <EventsPanel events={definition.events ?? []} />
    case 'models':
      return <BindingsPanel bindings={definition.dataBindings ?? []} />
    default:
      return <ObjectsPanel objects={definition.objects ?? []} />
  }
}
