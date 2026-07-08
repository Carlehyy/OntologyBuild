import React, { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ontologyApi } from '@/api/ontologies'
import { Badge } from '@/components/ui/Badge'
import { LoadingState } from '@/components/ui/LoadingState'
import FilesTab from './tabs/FilesTab'
import OverviewDashboard from './tabs/OverviewDashboard'
import GovernanceTab from './tabs/GovernanceTab'
import ModelStructureView from './tabs/ModelStructureView'
import FormalInstancesView from './tabs/FormalInstancesView'
import CuratedDatasetsTab from './tabs/CuratedDatasetsTab'
import VersionsTab from './tabs/VersionsTab'
import './ontology-glass.css'
import {
  Network, Box,
  Database, Upload, Shield, ArrowLeft,
  LayoutGrid, History, Download,
  Boxes, ChevronRight, Sparkles, X, Loader2,
} from 'lucide-react'

/* ═════════════════════════════════════════════════════════════
   信息架构（按用户操作旅程重组）：
   ① 本体概览    —— 进来先看懂"这本体是什么、健康吗"
   ② 本体建模    —— 设计/调整对象、属性、链接、规则；主入口=图谱编辑器
   ③ 实例数据    —— 真实数据进来了吗、长啥样
   ④ 本体治理    —— 质量、推理、审计、版本
   关键合并：原 知识图谱 / 建模画布 / GraphV2 四个重复视图统一为
   一个"图谱编辑器"（Palantir 风格，探索+建模一体）。
   ═════════════════════════════════════════════════════════════ */

interface CardDef {
  key: string
  label: string
  description: string
  icon: React.ElementType
  primary?: boolean   // 旅程主入口，卡片更大更突出
  route?: boolean     // 跳转到独立路由（图谱编辑器）
}

interface GroupDef {
  key: string
  label: string
  hint: string        // 给用户的"这一步要做什么"引导语
  icon: React.ElementType
  cards: CardDef[]
}

/* 信息架构 v2 —— 对齐平台内核（事实流+投影 / 哨兵×动作 / HITL 审批）：
   ① 总览        驾驶舱：模型/数据/运行/事实流 一屏看懂 + 健康检查
   ② 本体建模    主入口=图谱编辑器（正规本体唯一编辑面）+ 只读结构速览
   ③ 实例数据    灌数据（绑定映射）→ 看数据（formal 实例）→ 原始文件
   ④ 治理与推演  待审批 / 自治等级 / 哨兵 / 事实流 / 版本 */
const GROUPS: GroupDef[] = [
  {
    key: 'overview',
    label: '总览',
    hint: '一屏看懂：模型建得怎么样、数据进来了吗、平台在替你做什么',
    icon: LayoutGrid,
    cards: [],
  },
  {
    key: 'design',
    label: '本体建模',
    hint: '只读速览：查看对象实体、关系、动作与函数的完整结构',
    icon: Boxes,
    cards: [],
  },
  {
    key: 'data',
    label: '实例数据',
    hint: '先建模、再灌数据：把 curated 数据绑定灌入已有对象实体',
    icon: Database,
    cards: [
      { key: 'curated', label: '关联数据集（灌入本体）', description: '绑定对象实体 → 字段映射 → 一键灌入；支持审核后自动灌入', icon: Database, primary: true },
      { key: 'instances', label: '实例浏览', description: '正规本体实例的当前态投影，带来源徽章（手工/管道/采集）', icon: Boxes },
      { key: 'files', label: '文件管理', description: '上传原始文档与数据源，批量导入', icon: Upload },
    ],
  },
  {
    key: 'governance',
    label: '治理与推演',
    hint: '人是最终裁决者：审批挂起的动作、按批准率放权、盯住哨兵与事实流',
    icon: Shield,
    cards: [
      { key: 'gov-console', label: '治理驾驶舱', description: '待审批（批准/拒绝）· 自治等级（影子→人审→自动）· 哨兵触发 · 事实流', icon: Shield, primary: true },
      { key: 'versions', label: '版本记录', description: '模型版本快照与变更日志，支持回滚对比', icon: History },
    ],
  },
]

type ViewKey = string

export default function OntologyDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { t } = useTranslation()

  const [activeGroup, setActiveGroup] = useState<string>('overview')
  const [activeView, setActiveView] = useState<ViewKey | null>(null)
  const [exportOpen, setExportOpen] = useState(false)
  const [exportingFormat, setExportingFormat] = useState<string | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)

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

  // 「关联数据集」对所有本体可见：先建模、再灌数据是主流程——
  // 手工建模的本体同样需要把 curated 数据绑定灌入已有对象实体
  const availableGroups = GROUPS

  const currentGroup = availableGroups.find(g => g.key === activeGroup)

  const openView = (card: CardDef) => {
    if (card.route) { navigate(`/ontologies/${id}/graph`); return }
    setActiveView(card.key)
  }
  const backToDashboard = () => setActiveView(null)
  const switchGroup = (key: string) => { setActiveGroup(key); setActiveView(null) }

  if (isLoading) return <LoadingState message={t('common.loading')} />
  if (!ontology) return <div className="p-6 text-red-500">Ontology not found</div>

  const statusMap: Record<string, { label: string; variant: any }> = {
    draft: { label: '草稿', variant: 'warning' },
    review: { label: '审核中', variant: 'info' },
    published: { label: '已发布', variant: 'success' },
  }
  const s = statusMap[ontology.status] || { label: ontology.status, variant: 'outline' }

  const findCard = (key: string) => currentGroup?.cards.find(c => c.key === key)

  const renderView = () => {
    switch (activeView) {
      // —— 新内核视图 ——
      case 'model-structure': return <ModelStructureView ontologyId={id!} />
      case 'instances': return <FormalInstancesView ontologyId={id!} />
      case 'gov-console': return <GovernanceTab ontologyId={id!} />
      case 'curated': return <CuratedDatasetsTab ontologyId={id!} />
      case 'files': return <FilesTab ontologyId={id!} />
      case 'versions': return <VersionsTab ontologyId={id!} />
      default: return null
    }
  }

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

          {activeView ? (
            <button onClick={backToDashboard} className="flex items-center gap-1.5 text-sm hover:opacity-80 transition-opacity">
              <span style={{ color: 'var(--color-text-tertiary)' }}>{currentGroup?.label}</span>
              <ChevronRight size={14} style={{ color: 'var(--color-text-tertiary)' }} />
              <span className="font-medium" style={{ color: 'var(--color-text-primary)' }}>
                {findCard(activeView)?.label}
              </span>
            </button>
          ) : (
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
          )}
        </div>

        {!activeView && (
          <div className="flex items-center gap-1.5 shrink-0">
            <button onClick={() => setExportOpen(true)} className="onto-glass-btn-primary flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium" title="导出本体结构">
              <Download size={13} /><span className="hidden sm:inline">导出本体结构</span>
            </button>
          </div>
        )}
      </div>

      {/* ═══ 主区分段导航（玻璃 segmented）═══ */}
      {!activeView && (
        <div className="onto-glass-seg flex items-center overflow-x-auto" style={{ scrollbarWidth: 'none' }}>
          {availableGroups.map(group => {
            const Icon = group.icon
            const isActive = activeGroup === group.key
            return (
              <button
                key={group.key}
                onClick={() => switchGroup(group.key)}
                data-active={isActive}
                className="onto-glass-seg-item flex items-center gap-2 px-4 py-2 text-sm font-medium whitespace-nowrap"
              >
                <Icon size={15} />
                {group.label}
              </button>
            )
          })}
        </div>
      )}

      {/* ═══ 内容 ═══ */}
      {activeView ? (
        <div className="onto-glass-card onto-glass-in p-4">
          {renderView()}
        </div>
      ) : activeGroup === 'overview' ? (
        <div className="onto-glass-in">
          <OverviewDashboard
            ontologyId={id!}
            onGoGroup={(group, view) => { setActiveGroup(group); setActiveView(view ?? null) }}
          />
        </div>
      ) : activeGroup === 'design' ? (
        <div className="onto-glass-card onto-glass-in p-4">
          <ModelStructureView ontologyId={id!} />
        </div>
      ) : (
        <div className="onto-glass-in space-y-3">
          {/* 旅程引导语 */}
          <div className="flex items-center gap-2 px-1">
            <Sparkles size={14} style={{ color: 'var(--color-primary)' }} />
            <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>{currentGroup?.hint}</p>
          </div>

          {/* 主入口大卡（如本体建模的图谱编辑器） */}
          {currentGroup?.cards.filter(c => c.primary).map(card => {
            const Icon = card.icon
            return (
              <button
                key={card.key}
                onClick={() => openView(card)}
                className="onto-glass-card-btn group w-full flex items-center gap-5 p-5 text-left"
              >
                <div className="onto-glass-iconwrap w-14 h-14 flex items-center justify-center shrink-0">
                  <Icon size={24} style={{ color: 'var(--color-primary)' }} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="text-base font-semibold" style={{ color: 'var(--color-text-primary)' }}>{card.label}</p>
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full font-medium" style={{ background: 'var(--color-primary-light)', color: 'var(--color-primary-active)' }}>推荐入口</span>
                  </div>
                  <p className="text-sm mt-1" style={{ color: 'var(--color-text-secondary)' }}>{card.description}</p>
                </div>
                <ChevronRight size={20} className="shrink-0 transition-transform group-hover:translate-x-1" style={{ color: 'var(--color-primary)' }} />
              </button>
            )
          })}

          {/* 其余功能卡（网格） */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {currentGroup?.cards.filter(c => !c.primary).map(card => {
              const Icon = card.icon
              return (
                <button
                  key={card.key}
                  onClick={() => openView(card)}
                  className="onto-glass-card-btn group flex items-start gap-3.5 p-4 text-left"
                >
                  <div className="onto-glass-iconwrap w-10 h-10 flex items-center justify-center shrink-0">
                    <Icon size={18} style={{ color: 'var(--color-primary)' }} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold" style={{ color: 'var(--color-text-primary)' }}>{card.label}</p>
                    <p className="text-xs mt-0.5 leading-relaxed line-clamp-2" style={{ color: 'var(--color-text-tertiary)' }}>{card.description}</p>
                  </div>
                  <ChevronRight size={15} className="shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 transition-all group-hover:translate-x-0.5" style={{ color: 'var(--color-primary)' }} />
                </button>
              )
            })}
          </div>
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
    </div>
  )
}
