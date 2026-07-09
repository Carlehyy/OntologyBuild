import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  CheckCircle, AlertTriangle, Clock,
  X, Loader2, Trash2, Table2, RefreshCw,
  Eye, XCircle,
} from 'lucide-react'
import pipelinesApi, { type Pipeline } from '@/api/v2/pipelines'
import curatedApi from '@/api/v2/curated'
import type { CuratedDataset } from '@/api/v2/curated'
import { syncTasksApi, type SyncTask } from '@/api/v2/sync-tasks'
import CuratedDetailPanel from './CuratedDetailPanel'
import RawDatasetsView from './RawDatasetsView'
import ConfirmDialog from '@/components/ConfirmDialog'

interface Row {
  pipelineId: string
  pipelineName: string
  domain: string
  curatedId: string
  curatedName: string
  curatedStatus: string
  rowCount: number | null
  quality: number | null
}

const STATUS_ICON = (status: string) => {
  if (status === 'approved') return <CheckCircle size={13} className="text-green-500" />
  if (status === 'rejected') return <AlertTriangle size={13} className="text-red-400" />
  return <Clock size={13} className="text-yellow-400" />
}

const STATUS_LABEL: Record<string, string> = {
  pending_review: '待审核',
  approved:       '已审核',
  rejected:       '已拒绝',
}

const STATUS_STYLE: Record<string, string> = {
  pending_review: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  approved:       'bg-green-50 text-green-700 border-green-200',
  rejected:       'bg-red-50 text-red-600 border-red-200',
}

type LakeTab = 'curated' | 'raw'

export default function StructuredDataPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [activeTab, setActiveTab] = useState<LakeTab>(
    searchParams.get('tab') === 'raw' ? 'raw' : 'curated')
  const focusDatasetId = searchParams.get('dataset')

  // 深链/浏览器前进后退时同步 Tab（同路由 query 变化不会重挂载组件）
  useEffect(() => {
    const t = searchParams.get('tab')
    if ((t === 'raw' || t === 'curated') && t !== activeTab) setActiveTab(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  const switchTab = (tab: LakeTab) => {
    setActiveTab(tab)
    setSearchParams(prev => {
      const n = new URLSearchParams(prev)
      n.set('tab', tab)
      n.delete('dataset')
      return n
    }, { replace: true })
  }

  // 视图切换 Tab 滑动指示器
  const viewTabsRef = useRef<HTMLDivElement>(null)
  const [tabIndicator, setTabIndicator] = useState({ left: 0, width: 0 })
  useEffect(() => {
    const container = viewTabsRef.current
    if (!container) return
    const activeBtn = container.querySelector(`[data-tab-value="${activeTab}"]`) as HTMLElement | null
    if (!activeBtn) return
    const cr = container.getBoundingClientRect()
    const br = activeBtn.getBoundingClientRect()
    setTabIndicator({ left: br.left - cr.left, width: br.width })
  }, [activeTab])

  const TABS: [LakeTab, string][] = [['curated', '成品数据集'], ['raw', '人工数据集']]

  return (
    <div className="flex flex-col h-full space-y-3">
      {/* 顶部数据流卡片：SVG 分支数据流图 + 视图切换 Tab */}
      <div className="shrink-0 flex items-start justify-between gap-4 bg-white rounded-xl border border-slate-200 px-5 py-4 shadow-sm/50">
        {/* 左侧 SVG 数据流图：双分支结构 */}
        <svg
          viewBox="0 0 860 175"
          className="w-full max-w-[820px] h-auto shrink"
          style={{ minWidth: 600 }}
        >
          <defs>
            <style>
              {`
                @keyframes dashFlow { to { stroke-dashoffset: -24; } }
                @keyframes dashFlowRev { to { stroke-dashoffset: 24; } }
                .flow-line {
                  fill: none;
                  stroke-width: 1.8;
                  stroke-dasharray: 8 4;
                  animation: dashFlow 1.2s linear infinite;
                }
                .flow-line-rev {
                  fill: none;
                  stroke-width: 1.8;
                  stroke-dasharray: 8 4;
                  animation: dashFlowRev 1.2s linear infinite;
                }
                .flow-arrow { fill: #94a3b8; }
                .node-text { font-family: system-ui, -apple-system, sans-serif; }
              `}
            </style>
            {/* 箭头标记 */}
            <marker id="arr-teal" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><polygon points="0,0 6,3 0,6" fill="#0d9488" /></marker>
            <marker id="arr-blue" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><polygon points="0,0 6,3 0,6" fill="#3b82f6" /></marker>
            <marker id="arr-green" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><polygon points="0,0 6,3 0,6" fill="#10b981" /></marker>
            <marker id="arr-purple" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><polygon points="0,0 6,3 0,6" fill="#8b5cf6" /></marker>
          </defs>

          {/* ═══════════════════════════════════════════════════
              连接线（虚线 + 流动动画）
             ═══════════════════════════════════════════════════ */}
          {/* 系统流水线 → 任务池（上支汇入） */}
          <path d="M130,48 Q160,48 170,68 L175,85" className="flow-line" stroke="#0d9488" marker-end="url(#arr-teal)" />
          {/* n8n流水线 → 任务池（下支汇入） */}
          <path d="M130,136 Q160,136 170,116 L175,99" className="flow-line-rev" stroke="#14b8a6" marker-end="url(#arr-teal)" />
          {/* 任务池 → 资产湖 */}
          <line x1="330" y1="92" x2="395" y2="92" className="flow-line" stroke="#3b82f6" marker-end="url(#arr-blue)" />
          {/* 资产湖 → 成品数据集（上支出） */}
          <path d="M535,92 Q575,92 585,72 L595,50" className="flow-line" stroke="#10b981" marker-end="url(#arr-green)" />
          {/* 资产湖 → 人工数据集（下支出） */}
          <path d="M535,92 Q575,92 585,112 L595,134" className="flow-line-rev" stroke="#10b981" marker-end="url(#arr-green)" />
          {/* 成品数据集 → 本体模型（上支汇入） */}
          <path d="M730,48 Q755,48 760,68 L763,85" className="flow-line" stroke="#059669" marker-end="url(#arr-purple)" />
          {/* 人工数据集 → 本体模型（下支汇入） */}
          <path d="M730,136 Q755,136 760,116 L763,99" className="flow-line-rev" stroke="#f59e0b" marker-end="url(#arr-purple)" />

          {/* ═══════════════════════════════════════════════════
              节点
             ═══════════════════════════════════════════════════ */}

          {/* ─── 系统流水线（左上）─── */}
          <g onClick={() => navigate('/data/pipelines')} style={{ cursor: 'pointer' }}>
            <rect x="8" y="24" width="120" height="48" rx="9" ry="9"
              fill="#f0fdfa" stroke="#0d9488" strokeWidth="1.5" />
            <text x="22" y="42" fontSize="11" fill="#0d9488" className="node-text">⚙</text>
            <text x="42" y="40" fontSize="11" fontWeight="600" fill="#134e4a" className="node-text">系统流水线</text>
            <text x="42" y="56" fontSize="8" fill="#5eead4" className="node-text">System Pipeline</text>
          </g>

          {/* ─── n8n流水线（左下）─── */}
          <g onClick={() => navigate('/data/pipelines')} style={{ cursor: 'pointer' }}>
            <rect x="8" y="112" width="120" height="48" rx="9" ry="9"
              fill="#f0fdfa" stroke="#14b8a6" strokeWidth="1.5" />
            <text x="22" y="130" fontSize="11" fill="#14b8a6" className="node-text">🔗</text>
            <text x="42" y="128" fontSize="11" fontWeight="600" fill="#0f766e" className="node-text">n8n 流水线</text>
            <text x="42" y="144" fontSize="8" fill="#99f6e4" className="node-text">n8n Pipeline</text>
          </g>

          {/* ─── 数据任务池（中左）─── */}
          <g onClick={() => navigate('/data/pipelines/sync-tasks')} style={{ cursor: 'pointer' }}>
            <rect x="180" y="68" width="148" height="48" rx="9" ry="9"
              fill="#eff6ff" stroke="#3b82f6" strokeWidth="1.5" />
            <text x="198" y="86" fontSize="11" fill="#3b82f6" className="node-text">📋</text>
            <text x="218" y="84" fontSize="11" fontWeight="600" fill="#1e40af" className="node-text">数据任务池</text>
            <text x="218" y="100" fontSize="8" fill="#93c5fd" className="node-text">Sync Tasks</text>
          </g>

          {/* ─── 数据资产湖（正中 · 当前页高亮）─── */}
          <g style={{ cursor: 'default' }}>
            <rect x="400" y="64" width="133" height="56" rx="12" ry="12"
              fill="#ecfdf5" stroke="#10b981" strokeWidth="2.5" />
            <text x="418" y="86" fontSize="12" fill="#10b981" className="node-text">📊</text>
            <text x="438" y="82" fontSize="11" fontWeight="700" fill="#065f46" className="node-text">数据资产湖</text>
            <text x="438" y="98" fontSize="8" fill="#6ee7b7" className="node-text">Data Lake</text>
            {/* 当前页角标 */}
            <rect x="485" y="64" width="48" height="17"
              fill="#10b981" style={{ clipPath: 'polygon(0 0, 100% 0, 100% 100%, 14px 100%, 0 50%, 14px 0)' }} />
            <text x="510" y="77" textAnchor="middle" fontSize="9" fontWeight="600" fill="white" className="node-text">当前</text>
          </g>

          {/* ─── 成品数据集（右上）─── */}
          <g onClick={() => switchTab('curated')} style={{ cursor: 'pointer' }}>
            <rect x="600" y="24" width="128" height="48" rx="9" ry="9"
              fill="#f0fdf4" stroke="#059669" strokeWidth="1.5" />
            <text x="618" y="42" fontSize="11" fill="#059669" className="node-text">✅</text>
            <text x="638" y="40" fontSize="11" fontWeight="600" fill="#065f46" className="node-text">成品数据集</text>
            <text x="638" y="56" fontSize="8" fill="#6ee7b7" className="node-text">Curated</text>
          </g>

          {/* ─── 人工数据集（右下）─── */}
          <g onClick={() => switchTab('raw')} style={{ cursor: 'pointer' }}>
            <rect x="600" y="112" width="128" height="48" rx="9" ry="9"
              fill="#fffbeb" stroke="#f59e0b" strokeWidth="1.5" />
            <text x="618" y="130" fontSize="11" fill="#f59e0b" className="node-text">✏️</text>
            <text x="638" y="128" fontSize="11" fontWeight="600" fill="#92400e" className="node-text">人工数据集</text>
            <text x="638" y="144" fontSize="8" fill="#fcd34d" className="node-text">Manual / Raw</text>
          </g>

          {/* ─── 本体模型（最右）─── */}
          <g onClick={() => navigate('/ontologies')} style={{ cursor: 'pointer' }}>
            <rect x="768" y="68" width="88" height="48" rx="9" ry="9"
              fill="#faf5ff" stroke="#8b5cf6" strokeWidth="1.5" />
            <text x="784" y="86" fontSize="11" fill="#8b5cf6" className="node-text">🧠</text>
            <text x="804" y="84" fontSize="11" fontWeight="600" fill="#5b21b6" className="node-text">本体模型</text>
            <text x="804" y="100" fontSize="8" fill="#c4b5fd" className="node-text">Ontology</text>
          </g>
        </svg>

        {/* 右侧：视图切换 Tab */}
        <div className="shrink-0 pt-2">
          <div
            ref={viewTabsRef}
            className="relative flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-50/70 p-0.5"
          >
            <div
              className="absolute top-0.5 h-[calc(100%-4px)] rounded-md bg-[var(--color-nav-bg)] shadow-sm transition-all duration-300 ease-out"
              style={{ left: `${tabIndicator.left}px`, width: `${tabIndicator.width}px` }}
            />
            {TABS.map(([key, label]) => (
              <button
                key={key}
                data-tab-value={key}
                onClick={() => switchTab(key)}
                className={`relative z-10 px-5 py-2 text-sm font-medium rounded-md transition-colors duration-200 ${
                  activeTab === key ? 'text-white' : 'text-slate-500 hover:text-slate-700'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 下方内容区域 —— 单页展示，内容区可滚动 */}
      <div className="flex-1 min-h-0">
        {activeTab === 'raw'
          ? <RawDatasetsView focusDatasetId={focusDatasetId} />
          : <CuratedView />}
      </div>
    </div>
  )
}

/** 成品数据集（Curated）视图：流水线 × 产物 关联表 */
function CuratedView() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [syncTasks, setSyncTasks] = useState<SyncTask[]>([])
  const [curated, setCurated] = useState<CuratedDataset[]>([])
  const [loading, setLoading] = useState(true)
  const [pipelineFilter, setPipelineFilter] = useState(searchParams.get('pipeline') || '')
  const [taskFilter, setTaskFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  const [panelRow, setPanelRow] = useState<Row | null>(null)
  const [approvingId, setApprovingId] = useState<string | null>(null)
  const [rejectingId, setRejectingId] = useState<string | null>(null)
  const [deleteRow, setDeleteRow] = useState<Row | null>(null)
  const [deleting, setDeleting] = useState(false)

  const load = () => {
    setLoading(true)
    Promise.all([
      pipelinesApi.list(),
      syncTasksApi.list(),
      curatedApi.list() as Promise<CuratedDataset[]>,
    ]).then(([pls, tasksResp, cur]) => {
      setPipelines(Array.isArray(pls) ? pls : [])
      setSyncTasks(tasksResp?.items ?? [])
      setCurated(Array.isArray(cur) ? cur : [])
    }).catch(() => {}).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  // 以成品数据集为主视角，关联其来源流水线
  const allRows = useMemo<Row[]>(() => {
    const rows: Row[] = []
    const claimed = new Set<string>()

    const pushRow = (c: CuratedDataset, pl?: Pipeline) => rows.push({
      pipelineId: pl?.id ?? '', pipelineName: pl?.name ?? '—', domain: pl?.domain || '通用',
      curatedId: c.id, curatedName: c.name, curatedStatus: c.status || 'pending_review',
      rowCount: c.row_count ?? null, quality: c.quality_score ?? null,
    })

    const curatedById = new Map(curated.map(c => [c.id, c]))
    pipelines.forEach(pl => {
      const ids: string[] = pl.target_curated_ids ?? []
      ids.forEach(cid => {
        const c = curatedById.get(cid)
        if (c && !claimed.has(c.id)) { claimed.add(c.id); pushRow(c, pl) }
      })
    })
    // 名称前缀兜底匹配（旧数据无 target_curated_ids）
    pipelines.forEach(pl => {
      curated.filter(c => !claimed.has(c.id) && c.name.startsWith(pl.name)).forEach(c => {
        claimed.add(c.id); pushRow(c, pl)
      })
    })
    // 无来源流水线的孤儿产物也要可见可管理
    curated.filter(c => !claimed.has(c.id)).forEach(c => pushRow(c))
    return rows
  }, [pipelines, curated])

  // 已发布状态的流水线（用于筛选下拉）
  const publishedPipelines = useMemo(() =>
    pipelines.filter(p => p.status === 'published'),
  [pipelines])

  // 选中任务关联的流水线 ID
  const taskPipelineId = useMemo(() => {
    if (!taskFilter) return null
    return syncTasks.find(t => t.id === taskFilter)?.trigger_pipeline_id || null
  }, [taskFilter, syncTasks])

  const filtered = useMemo(() => {
    return allRows.filter(r => {
      if (pipelineFilter && r.pipelineId !== pipelineFilter) return false
      if (taskPipelineId && r.pipelineId !== taskPipelineId) return false
      if (statusFilter && r.curatedStatus !== statusFilter) return false
      return true
    })
  }, [allRows, pipelineFilter, taskPipelineId, statusFilter])

  const clearFilters = () => {
    setPipelineFilter('')
    setTaskFilter('')
    setStatusFilter('')
  }

  const handleStatusChange = (id: string, newStatus: string) => {
    setCurated(prev => prev.map(c => c.id === id ? { ...c, status: newStatus } : c))
    if (panelRow?.curatedId === id) setPanelRow(r => r ? { ...r, curatedStatus: newStatus } : r)
  }

  const handleDeleted = (id: string) => {
    setCurated(prev => prev.filter(c => c.id !== id))
    setPanelRow(null)
    setDeleteRow(null)
  }

  const handleQuickApprove = async (e: React.MouseEvent, row: Row) => {
    e.stopPropagation()
    if (!row.curatedId) return
    setApprovingId(row.curatedId)
    try {
      await curatedApi.approve(row.curatedId)
      handleStatusChange(row.curatedId, 'approved')
    } finally { setApprovingId(null) }
  }

  const handleQuickReject = async (e: React.MouseEvent, row: Row) => {
    e.stopPropagation()
    if (!row.curatedId) return
    setRejectingId(row.curatedId)
    try {
      await curatedApi.reject(row.curatedId)
      handleStatusChange(row.curatedId, 'rejected')
    } finally { setRejectingId(null) }
  }

  const handleQuickDelete = async () => {
    if (!deleteRow?.curatedId) return
    setDeleting(true)
    try {
      await curatedApi.delete(deleteRow.curatedId)
      handleDeleted(deleteRow.curatedId)
    } catch {
      setDeleteRow(null)
    } finally {
      setDeleting(false)
    }
  }

  if (loading) return <p className="text-gray-400 text-sm p-6">加载中...</p>

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm/50 h-full flex flex-col">
      {/* 筛选 */}
      <div className="shrink-0 flex gap-3 flex-wrap items-center px-5 pt-4 pb-3 border-b border-gray-100">
        {/* 按流水线筛选（仅已发布） */}
        <select
          value={pipelineFilter}
          onChange={e => setPipelineFilter(e.target.value)}
          className="px-3 py-1.5 border rounded-lg text-sm text-gray-600 bg-white"
        >
          <option value="">全部已发布流水线</option>
          {publishedPipelines.map(pl => (
            <option key={pl.id} value={pl.id}>{pl.name}</option>
          ))}
        </select>

        {/* 按数据任务筛选 */}
        <select
          value={taskFilter}
          onChange={e => setTaskFilter(e.target.value)}
          className="px-3 py-1.5 border rounded-lg text-sm text-gray-600 bg-white"
        >
          <option value="">全部数据任务</option>
          {syncTasks.map(t => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </select>

        {/* 审核状态 */}
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="px-3 py-1.5 border rounded-lg text-sm text-gray-600 bg-white"
        >
          <option value="">全部审核状态</option>
          <option value="pending_review">待审核</option>
          <option value="approved">已审核</option>
          <option value="rejected">已拒绝</option>
        </select>

        {/* 清除筛选 */}
        {(pipelineFilter || taskFilter || statusFilter) && (
          <button
            onClick={clearFilters}
            className="flex items-center gap-1 text-xs text-red-500 hover:text-red-600 px-2 py-1 rounded hover:bg-red-50 transition-colors"
          >
            <X size={11} /> 清除筛选
          </button>
        )}

        <button onClick={load} className="ml-auto flex items-center gap-1 text-xs text-gray-500 hover:text-gray-800 px-2 py-1.5">
          <RefreshCw size={12} /> 刷新
        </button>
      </div>

      {/* 表格 — 可滚动 */}
      <div className="flex-1 overflow-y-auto px-5 py-3">
      {allRows.length === 0 ? (
        <div className="border-2 border-dashed rounded-xl p-12 text-center text-gray-400 space-y-2">
          <Table2 size={32} className="mx-auto opacity-30" />
          <p className="text-sm font-medium">暂无成品数据集</p>
          <p className="text-xs">运行数据流水线后，加工产物会自动出现在这里</p>
          <button
            onClick={() => navigate('/data/pipelines')}
            className="text-xs px-3 py-1.5 mt-1 bg-[var(--color-nav-bg)] text-white rounded-lg hover:opacity-90"
          >
            去流水线运行
          </button>
        </div>
      ) : filtered.length === 0 ? (
        <div className="border rounded-xl p-8 text-center text-gray-400 text-sm">没有匹配的数据集</div>
      ) : (
        <div className="border rounded-xl overflow-hidden bg-white">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">数据集</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">来源流水线</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">领域</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">行数</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">质量分</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">审核状态</th>
                <th className="px-4 py-2.5 text-gray-600 text-xs text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {filtered.map((row, idx) => (
                <tr
                  key={`${row.pipelineId}-${row.curatedId}-${idx}`}
                  onClick={() => row.curatedId && setPanelRow(row)}
                  className={`transition-colors ${row.curatedId ? 'cursor-pointer hover:bg-gray-50' : 'opacity-60'}`}
                >
                  <td className="px-4 py-3 font-medium text-gray-800 max-w-[240px]">
                    <span className="block truncate" title={row.curatedName}>{row.curatedName}</span>
                    <span className="text-xs text-gray-400 font-mono font-normal">{row.curatedId.slice(0, 8)}</span>
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-600 max-w-[160px] truncate" title={row.pipelineName}>
                    {row.pipelineName}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">{row.domain}</td>
                  <td className="px-4 py-3 text-xs text-gray-600">
                    {row.rowCount != null ? `${row.rowCount} 行` : '—'}
                  </td>
                  <td className="px-4 py-3 text-xs">
                    {row.quality != null ? (
                      <span className={row.quality >= 0.9 ? 'text-green-600' : row.quality >= 0.7 ? 'text-yellow-600' : 'text-red-500'}>
                        {(row.quality * 100).toFixed(0)}%
                      </span>
                    ) : <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-4 py-3">
                    {row.curatedStatus ? (
                      <span className={`inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded border ${STATUS_STYLE[row.curatedStatus] || 'bg-gray-100 text-gray-600 border-gray-200'}`}>
                        {STATUS_ICON(row.curatedStatus)}
                        {STATUS_LABEL[row.curatedStatus] || row.curatedStatus}
                      </span>
                    ) : (
                      <span className="text-xs text-gray-300">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right" onClick={e => e.stopPropagation()}>
                    <div className="flex items-center gap-1 justify-end">
                      {/* 批准 — 仅待审核 */}
                      {row.curatedId && row.curatedStatus === 'pending_review' && (
                        <button
                          onClick={e => handleQuickApprove(e, row)}
                          disabled={approvingId === row.curatedId}
                          className="p-1.5 rounded hover:bg-green-50 text-gray-400 hover:text-green-600 disabled:opacity-50"
                          title="批准"
                        >
                          {approvingId === row.curatedId
                            ? <Loader2 size={13} className="animate-spin" />
                            : <CheckCircle size={13} />}
                        </button>
                      )}

                      {/* 驳回 — 仅待审核 */}
                      {row.curatedId && row.curatedStatus === 'pending_review' && (
                        <button
                          onClick={e => handleQuickReject(e, row)}
                          disabled={rejectingId === row.curatedId}
                          className="p-1.5 rounded hover:bg-orange-50 text-gray-400 hover:text-orange-600 disabled:opacity-50"
                          title="驳回"
                        >
                          {rejectingId === row.curatedId
                            ? <Loader2 size={13} className="animate-spin" />
                            : <XCircle size={13} />}
                        </button>
                      )}

                      {/* 删除 — 待审核 / 已拒绝 */}
                      {row.curatedId && (row.curatedStatus === 'pending_review' || row.curatedStatus === 'rejected') && (
                        <button
                          onClick={() => setDeleteRow(row)}
                          className="p-1.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-500"
                          title="删除"
                        >
                          <Trash2 size={13} />
                        </button>
                      )}

                      {/* 查看 — 所有状态 */}
                      {row.curatedId && (
                        <button
                          onClick={() => setPanelRow(row)}
                          className="p-1.5 rounded hover:bg-blue-50 text-gray-400 hover:text-blue-600"
                          title="查看详情"
                        >
                          <Eye size={13} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      </div>

      {/* 详情面板 */}
      {panelRow && (
        <CuratedDetailPanel
          datasetId={panelRow.curatedId}
          datasetName={panelRow.curatedName}
          datasetStatus={panelRow.curatedStatus}
          pipelineName={panelRow.pipelineName}
          onClose={() => setPanelRow(null)}
          onStatusChange={handleStatusChange}
          onDeleted={handleDeleted}
        />
      )}

      {/* 删除确认 */}
      <ConfirmDialog
        open={!!deleteRow}
        title="删除数据集"
        message={`确认删除「${deleteRow?.curatedName}」？此操作不可撤销。`}
        confirmLabel={deleting ? '删除中...' : '确认删除'}
        onConfirm={handleQuickDelete}
        onCancel={() => setDeleteRow(null)}
      />
    </div>
  )
}
