import React, { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ontologyApi } from '@/api/ontologies'
import { Badge } from '@/components/ui/Badge'
import { LoadingState } from '@/components/ui/LoadingState'
import OverviewDashboard from './tabs/OverviewDashboard'
import GovernanceTab from './tabs/GovernanceTab'
import ModelStructureView from './tabs/ModelStructureView'
import FormalInstancesView from './tabs/FormalInstancesView'
import CuratedDatasetsTab from './tabs/CuratedDatasetsTab'
import VersionsTab from './tabs/VersionsTab'
import { Modal } from '@/components/ui/Modal'
import './ontology-glass.css'
import {
  Network, Database, Shield, ArrowLeft,
  LayoutGrid, History, Download,
  Boxes, Layers, Loader2, X,
} from 'lucide-react'

/* ═════════════════════════════════════════════════════════════
   信息架构（按用户操作旅程重组，五段式）：
   ① 总览      —— 进来先看懂"这本体是什么、健康吗"
   ② 本体建模  —— 展示现有本体的对象实体/关系/动作/函数结构；主入口=图谱编辑器
   ③ 数据映射  —— 把 curated 数据集绑定灌入已有对象实体（先建模、再灌数据）
   ④ 实例数据  —— 真实数据进来了吗、长啥样（formal 实例的当前态投影）
   ⑤ 治理与推演 —— 待审批 / 自治等级 / 哨兵 / 事实流 / 版本
   五段各自直达内容，不再有"分组 → 卡片 → 子视图"的二级跳转。
   ═════════════════════════════════════════════════════════════ */

interface GroupDef {
  key: string
  label: string
  icon: React.ElementType
}

const GROUPS: GroupDef[] = [
  { key: 'overview', label: '总览', icon: LayoutGrid },
  { key: 'design', label: '本体建模', icon: Boxes },
  { key: 'data-mapping', label: '数据映射', icon: Database },
  { key: 'data', label: '实例数据', icon: Layers },
  { key: 'governance', label: '治理与推演', icon: Shield },
]

export default function OntologyDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { t } = useTranslation()

  const [activeGroup, setActiveGroup] = useState<string>('overview')
  const [exportOpen, setExportOpen] = useState(false)
  const [exportingFormat, setExportingFormat] = useState<string | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)
  const [showVersionModal, setShowVersionModal] = useState(false)

  const handleExport = async (format: string) => {
    setExportError(null)
    setExportingFormat(format)
    try {
      await ontologyApi.exportOntology(id!, format)
    } catch (err: any) {
      setExportError(err?.detail ?? err?.message ?? '导出失败')
    } finally {
      setExportingFormat(null)
    }
  }

  const { data: ontology, isLoading } = useQuery({
    queryKey: ['ontology', id],
    queryFn: () => ontologyApi.get(id!) as any,
    enabled: !!id,
  })

  if (isLoading) return <LoadingState message={t('common.loading')} />
  if (!ontology) return <div className="p-6 text-red-500">Ontology not found</div>

  const statusMap: Record<string, { label: string; variant: any }> = {
    draft: { label: '草稿', variant: 'warning' },
    review: { label: '审核中', variant: 'info' },
    published: { label: '已发布', variant: 'success' },
  }
  const s = statusMap[ontology.status] || { label: ontology.status, variant: 'outline' }

  return (
    <div className="onto-glass-root space-y-4">
      {/* ═══ Header（玻璃条）═══ */}
      <div className="onto-glass-header flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={() => navigate('/ontologies')}
            className="onto-glass-btn p-2 shrink-0"
            title="返回列表"
          >
            <ArrowLeft size={18} />
          </button>

          <div className="flex items-center gap-3 min-w-0">
            <div className="onto-glass-iconwrap w-9 h-9 flex items-center justify-center shrink-0">
              <Network size={17} style={{ color: 'var(--color-primary)' }} />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="text-base font-semibold truncate" style={{ color: 'var(--color-text-primary)' }}>{ontology.name}</h1>
                <Badge variant={s.variant}>{s.label}</Badge>
              </div>
              <p className="text-xs truncate" style={{ color: 'var(--color-text-tertiary)' }}>
                {ontology.domain} · {ontology.version}
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          <button onClick={() => setShowVersionModal(true)} className="onto-glass-btn-primary flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium" title="历史版本">
            <History size={13} /><span className="hidden sm:inline">历史版本</span>
          </button>
          <button onClick={() => setExportOpen(true)} className="onto-glass-btn-primary flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium" title="导出本体结构">
            <Download size={13} /><span className="hidden sm:inline">导出本体结构</span>
          </button>
        </div>
      </div>

      {/* ═══ 主区分段导航（玻璃 segmented）═══ */}
      <div className="onto-glass-seg flex items-center overflow-x-auto" style={{ scrollbarWidth: 'none' }}>
        {GROUPS.map(group => {
          const Icon = group.icon
          const isActive = activeGroup === group.key
          return (
            <button
              key={group.key}
              onClick={() => setActiveGroup(group.key)}
              data-active={isActive}
              className="onto-glass-seg-item flex items-center gap-2 px-4 py-2 text-sm font-medium whitespace-nowrap"
            >
              <Icon size={15} />
              {group.label}
            </button>
          )
        })}
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
        <div className="onto-glass-card onto-glass-in p-4">
          <CuratedDatasetsTab ontologyId={id!} />
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
