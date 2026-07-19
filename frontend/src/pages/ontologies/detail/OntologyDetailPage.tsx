import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ontologyApi } from '@/api/ontologies'
import { LoadingState } from '@/components/ui/LoadingState'
import type { OntologyDetail } from '@/types/ontology'
import OverviewDashboard from './tabs/OverviewDashboard'
import GovernanceTab from './tabs/GovernanceTab'
import ModelStructureView from './tabs/ModelStructureView'
import FormalInstancesView from './tabs/FormalInstancesView'
import DataMappingOverview from './mapping/DataMappingOverview'
import VersionsTab from './tabs/VersionsTab'
import { Modal } from '@/components/ui/Modal'
import './ontology-glass.css'
import {
  History, Download, Loader2, Network,
} from 'lucide-react'

/* ═════════════════════════════════════════════════════════════
   信息架构（按用户操作旅程重组，五段式）：
   ① 本体总览  —— 进来先看懂"这本体是什么、健康吗"
   ② 本体结构  —— 只读展示当前发布快照的对象实体/关系/动作/函数结构
   ③ 数据映射  —— 把 curated 数据集绑定灌入已有对象实体（先建模、再灌数据）
   ④ 实例数据  —— 真实数据进来了吗、长啥样（formal 实例的当前态投影）
   ⑤ 治理推演  —— 待审批 / 自治等级 / 哨兵 / 事实流 / 版本
   五段各自直达内容，不再有"分组 → 卡片 → 子视图"的二级跳转。
   ═════════════════════════════════════════════════════════════ */

interface GroupDef {
  key: string
  label: string
}

const GROUPS: GroupDef[] = [
  { key: 'overview', label: '本体总览' },
  { key: 'design', label: '本体结构' },
  { key: 'data-mapping', label: '数据映射' },
  { key: 'data', label: '实例数据' },
  { key: 'governance', label: '治理推演' },
]

export default function OntologyDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { t } = useTranslation()

  const requestedTab = searchParams.get('tab')
  const activeGroup = GROUPS.some(group => group.key === requestedTab) ? requestedTab! : 'overview'
  const groupTabsRef = useRef<HTMLDivElement>(null)
  const [indicatorPos, setIndicatorPos] = useState({ left: 0, width: 0 })
  const [isExporting, setIsExporting] = useState(false)
  const [exportFeedback, setExportFeedback] = useState<{ tone: 'success' | 'error'; message: string } | null>(null)
  const [showVersionModal, setShowVersionModal] = useState(false)

  useEffect(() => {
    if (requestedTab === 'versions') setShowVersionModal(true)
  }, [requestedTab])

  const closeVersionModal = () => {
    setShowVersionModal(false)
    if (requestedTab === 'versions') setSearchParams({}, { replace: true })
  }
  const { data: ontology, isLoading } = useQuery<OntologyDetail>({
    queryKey: ['ontology', id],
    queryFn: () => ontologyApi.get(id!),
    enabled: !!id,
  })

  const handleExport = async () => {
    if (!id || !ontology || isExporting) return
    setExportFeedback(null)
    setIsExporting(true)
    try {
      await ontologyApi.exportOntology(id, ontology.name, ontology.version)
      setExportFeedback({ tone: 'success', message: '本体结构 JSON 已下载' })
    } catch (error: unknown) {
      const candidate = typeof error === 'object' && error !== null
        ? error as { detail?: unknown; message?: unknown }
        : null
      const detail = candidate?.detail
      setExportFeedback({
        tone: 'error',
        message: typeof detail === 'string' ? detail
          : detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string'
            ? detail.message
            : typeof candidate?.message === 'string' ? candidate.message : '导出失败，请稍后重试',
      })
    } finally {
      setIsExporting(false)
    }
  }

  useEffect(() => {
    if (exportFeedback?.tone !== 'success') return
    const timer = window.setTimeout(() => setExportFeedback(null), 3000)
    return () => window.clearTimeout(timer)
  }, [exportFeedback])

  useEffect(() => {
    const container = groupTabsRef.current
    if (!container) return
    const activeButton = container.querySelector(`[data-tab-value="${activeGroup}"]`) as HTMLElement | null
    if (!activeButton) return
    const containerRect = container.getBoundingClientRect()
    const buttonRect = activeButton.getBoundingClientRect()
    setIndicatorPos({
      left: buttonRect.left - containerRect.left,
      width: buttonRect.width,
    })
  }, [activeGroup, ontology?.id])

  const selectGroup = (key: string) => {
    if (key === 'overview') setSearchParams({}, { replace: true })
    else setSearchParams({ tab: key }, { replace: true })
  }

  if (isLoading) return <LoadingState message={t('common.loading')} />
  if (!ontology) return <div className="p-6 text-red-500">Ontology not found</div>

  return (
    <div className={`onto-glass-root flex h-full min-h-0 flex-col gap-4 overflow-hidden ${
      activeGroup === 'data' || activeGroup === 'design' ? 'onto-glass-root--flat' : ''
    }`}>
      {/* ═══ 功能导航与低频操作 ═══ */}
      <div data-testid="ontology-detail-header" className="onto-glass-header flex shrink-0 items-center justify-between gap-3 px-5 py-4">
        <div className="min-w-0 overflow-x-auto" style={{ scrollbarWidth: 'none' }}>
          <div ref={groupTabsRef} className="relative flex w-max items-center gap-1 rounded-xl border border-slate-200 bg-slate-50/70 p-1">
            <div
              aria-hidden="true"
              className="absolute top-1 h-[calc(100%-8px)] rounded-lg bg-teal-600 shadow-sm transition-all duration-300 ease-out"
              style={{ left: `${indicatorPos.left}px`, width: `${indicatorPos.width}px` }}
            />
            {GROUPS.map(group => {
              const isActive = activeGroup === group.key
              return (
                <button
                  key={group.key}
                  type="button"
                  data-tab-value={group.key}
                  aria-pressed={isActive}
                  onClick={() => selectGroup(group.key)}
                  className={`relative z-10 whitespace-nowrap rounded-lg px-4 py-2 text-sm font-medium transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-1 ${
                    isActive ? 'text-white' : 'text-slate-500 hover:text-slate-700'
                  }`}
                >
                  {group.label}
                </button>
              )
            })}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <span
            className="inline-flex h-9 items-center rounded-lg border border-teal-100 bg-teal-50 px-3 font-mono text-sm font-semibold tabular-nums text-teal-700"
            title="当前最新发布版本"
            data-testid="current-release-version"
          >
            {ontology.current_release_version || ontology.version || 'v0'}
          </span>
          {activeGroup !== 'design' ? (
            <button
              type="button"
              onClick={() => navigate(`/ontologies/${id}/graph`)}
              className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-[var(--color-nav-bg)] text-white shadow-sm transition-all hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2"
              title="查看当前发布图谱"
              aria-label="查看当前发布图谱"
            >
              <Network size={18} />
            </button>
          ) : (
            <span aria-hidden="true" className="h-10 w-10 shrink-0" />
          )}
          <button
            type="button"
            onClick={() => setShowVersionModal(true)}
            className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-[var(--color-nav-bg)] text-white shadow-sm transition-all hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2"
            title="历史版本"
            aria-label="查看历史版本"
          >
            <History size={18} />
          </button>
          <button
            type="button"
            onClick={() => void handleExport()}
            disabled={isExporting}
            className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-[var(--color-nav-bg)] text-white shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:opacity-90 active:translate-y-0 disabled:cursor-wait disabled:opacity-70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2"
            title={isExporting ? '正在导出 JSON' : '导出本体结构 JSON'}
            aria-label={isExporting ? '正在导出本体结构 JSON' : '导出本体结构 JSON'}
            aria-busy={isExporting}
          >
            {isExporting ? <Loader2 size={18} className="animate-spin" /> : <Download size={18} />}
          </button>
        </div>
      </div>

      {exportFeedback && (
        <div
          role={exportFeedback.tone === 'error' ? 'alert' : 'status'}
          aria-live="polite"
          className={`rounded-xl border px-4 py-2.5 text-sm ${
            exportFeedback.tone === 'error'
              ? 'border-red-200 bg-red-50 text-red-700'
              : 'border-emerald-200 bg-emerald-50 text-emerald-700'
          }`}
        >
          {exportFeedback.message}
        </div>
      )}

      {/* ═══ 内容 ═══ */}
      {activeGroup === 'overview' ? (
        <div className="onto-glass-in min-h-0 flex-1 overflow-auto">
          <OverviewDashboard ontologyId={id!} ontology={ontology} onGoGroup={selectGroup} />
        </div>
      ) : activeGroup === 'design' ? (
        <div className="onto-glass-card onto-glass-in min-h-0 flex-1 overflow-hidden">
          <ModelStructureView ontologyId={id!} />
        </div>
      ) : activeGroup === 'data-mapping' ? (
        <div className="onto-glass-in min-h-0 flex-1 overflow-auto">
          <DataMappingOverview ontologyId={id!} />
        </div>
      ) : activeGroup === 'data' ? (
        <div className="onto-glass-card onto-glass-in min-h-0 flex-1 overflow-hidden">
          <FormalInstancesView ontologyId={id!} />
        </div>
      ) : (
        <div className="onto-glass-card onto-glass-in min-h-0 flex-1 overflow-auto p-4">
          <GovernanceTab
            ontologyId={id!}
            currentReleaseId={ontology.current_release_id}
            currentReleaseVersion={ontology.current_release_version || ontology.version}
          />
        </div>
      )}

      {/* ═══ 历史版本弹窗 ═══ */}
      <Modal
        open={showVersionModal}
        onClose={closeVersionModal}
        title="本体版本演进"
        size="3xl"
        panelClassName="h-[min(86dvh,820px)]"
        contentClassName="flex-1 overflow-hidden"
      >
        <VersionsTab ontologyId={id!} onClose={closeVersionModal} />
      </Modal>
    </div>
  )
}
