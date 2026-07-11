import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ontologyApi } from '@/api/ontologies'
import { LoadingState } from '@/components/ui/LoadingState'
import type { OntologyDetail } from '@/types/ontology'
import OverviewDashboard from './tabs/OverviewDashboard'
import GovernanceTab from './tabs/GovernanceTab'
import ModelStructureView from './tabs/ModelStructureView'
import FormalInstancesView from './tabs/FormalInstancesView'
import DataMappingStudio from './tabs/DataMappingStudio'
import VersionsTab from './tabs/VersionsTab'
import { Modal } from '@/components/ui/Modal'
import './ontology-glass.css'
import {
  History, Download, Loader2, X,
} from 'lucide-react'

/* ═════════════════════════════════════════════════════════════
   信息架构（按用户操作旅程重组，五段式）：
   ① 总览      —— 进来先看懂"这本体是什么、健康吗"
   ② 本体结构  —— 展示现有本体的对象实体/关系/动作/函数结构；主入口=图谱编辑器
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
  { key: 'overview', label: '总览' },
  { key: 'design', label: '本体结构' },
  { key: 'data-mapping', label: '数据映射' },
  { key: 'data', label: '实例数据' },
  { key: 'governance', label: '治理推演' },
]

export default function OntologyDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { t } = useTranslation()

  const [activeGroup, setActiveGroup] = useState<string>('overview')
  const groupTabsRef = useRef<HTMLDivElement>(null)
  const [indicatorPos, setIndicatorPos] = useState({ left: 0, width: 0 })
  const [exportOpen, setExportOpen] = useState(false)
  const [exportingFormat, setExportingFormat] = useState<string | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)
  const [showVersionModal, setShowVersionModal] = useState(false)

  const handleExport = async (format: string) => {
    setExportError(null)
    setExportingFormat(format)
    try {
      await ontologyApi.exportOntology(id!, format)
    } catch (error: unknown) {
      const candidate = typeof error === 'object' && error !== null
        ? error as { detail?: unknown; message?: unknown }
        : null
      setExportError(
        typeof candidate?.detail === 'string' ? candidate.detail
          : typeof candidate?.message === 'string' ? candidate.message : '导出失败',
      )
    } finally {
      setExportingFormat(null)
    }
  }

  const { data: ontology, isLoading } = useQuery<OntologyDetail>({
    queryKey: ['ontology', id],
    queryFn: () => ontologyApi.get(id!),
    enabled: !!id,
  })

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

  if (isLoading) return <LoadingState message={t('common.loading')} />
  if (!ontology) return <div className="p-6 text-red-500">Ontology not found</div>

  return (
    <div className="onto-glass-root space-y-4">
      {/* ═══ 功能导航与低频操作 ═══ */}
      <div className="onto-glass-header flex items-center justify-between gap-3 px-4 py-3">
        <div className="min-w-0 overflow-x-auto" style={{ scrollbarWidth: 'none' }}>
          <div ref={groupTabsRef} className="relative flex w-max items-center gap-1 rounded-lg border border-slate-200 bg-slate-50/70 p-0.5">
            <div
              aria-hidden="true"
              className="absolute top-0.5 h-[calc(100%-4px)] rounded-md bg-teal-600 shadow-sm transition-all duration-300 ease-out"
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
                  onClick={() => setActiveGroup(group.key)}
                  className={`relative z-10 whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-medium transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-1 ${
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
          <button
            type="button"
            onClick={() => setShowVersionModal(true)}
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--color-nav-bg)] text-white shadow-sm transition-all hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2"
            title="历史版本"
            aria-label="查看历史版本"
          >
            <History size={16} />
          </button>
          <button
            type="button"
            onClick={() => setExportOpen(true)}
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--color-nav-bg)] text-white shadow-sm transition-all hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2"
            title="导出本体结构"
            aria-label="导出本体结构"
          >
            <Download size={16} />
          </button>
        </div>
      </div>

      {/* ═══ 内容 ═══ */}
      {activeGroup === 'overview' ? (
        <div className="onto-glass-in">
          <OverviewDashboard ontologyId={id!} onGoGroup={setActiveGroup} />
        </div>
      ) : activeGroup === 'design' ? (
        <div className="onto-glass-card onto-glass-in p-4">
          <ModelStructureView ontologyId={id!} />
        </div>
      ) : activeGroup === 'data-mapping' ? (
        <div className="onto-glass-in">
          <DataMappingStudio ontologyId={id!} />
        </div>
      ) : activeGroup === 'data' ? (
        <div className="onto-glass-card onto-glass-in p-4">
          <FormalInstancesView ontologyId={id!} />
        </div>
      ) : (
        <div className="onto-glass-card onto-glass-in p-4">
          <GovernanceTab ontologyId={id!} />
        </div>
      )}

      {/* ═══ 导出本体结构弹窗 ═══ */}
      {exportOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-[var(--color-bg-overlay)]" onClick={() => { setExportOpen(false); setExportError(null) }} />
          <div className="onto-glass-card relative w-full max-w-lg mx-4 p-6 z-10">
            <div className="flex items-center justify-between mb-5">
              <div>
                <h3 className="text-base font-semibold" style={{ color: 'var(--color-text-primary)' }}>导出本体结构</h3>
                <p className="text-xs mt-0.5" style={{ color: 'var(--color-text-tertiary)' }}>选择格式下载 {ontology.name} 的结构数据</p>
              </div>
              <button
                onClick={() => { setExportOpen(false); setExportError(null) }}
                className="onto-glass-btn p-1.5"
              >
                <X size={16} />
              </button>
            </div>

            {exportError && (
              <p className="text-sm text-red-500 mb-4 px-3 py-2 rounded-lg bg-red-50 border border-red-200">{exportError}</p>
            )}

            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
              {[
                { fmt: 'json', label: 'JSON', desc: '结构化数据交换' },
                { fmt: 'yaml', label: 'YAML', desc: '可读配置文件' },
                { fmt: 'csv', label: 'CSV', desc: '表格数据导入' },
                { fmt: 'ttl', label: 'TTL', desc: 'RDF 语义网' },
                { fmt: 'html', label: 'HTML', desc: '网页可视化' },
                { fmt: 'cypher', label: 'CYPHER', desc: 'Neo4j 图查询' },
                { fmt: 'tugraph', label: 'TUGRAPH', desc: 'TuGraph 图数据库' },
              ].map(({ fmt, label, desc }) => (
                <button
                  key={fmt}
                  disabled={exportingFormat !== null}
                  onClick={() => handleExport(fmt)}
                  className="flex flex-col items-center gap-1.5 px-3 py-3 rounded-xl border border-[var(--color-border)] hover:border-[var(--color-primary)] hover:bg-[var(--color-primary-light)] transition-all duration-200 disabled:opacity-50"
                >
                  {exportingFormat === fmt ? (
                    <Loader2 size={18} className="animate-spin" style={{ color: 'var(--color-primary)' }} />
                  ) : (
                    <span className="text-xs font-mono font-semibold" style={{ color: 'var(--color-primary)' }}>{label}</span>
                  )}
                  <span className="text-[10px] leading-tight text-center" style={{ color: 'var(--color-text-tertiary)' }}>{desc}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ═══ 历史版本弹窗 ═══ */}
      <Modal open={showVersionModal} onClose={() => setShowVersionModal(false)} title="历史版本" size="3xl">
        <VersionsTab ontologyId={id!} />
      </Modal>
    </div>
  )
}
