import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Search, CheckCircle, AlertTriangle, Clock,
  X, Loader2, Trash2, Table2, RefreshCw,
  Eye, XCircle,
} from 'lucide-react'
import pipelinesApi, { type Pipeline } from '@/api/v2/pipelines'
import curatedApi from '@/api/v2/curated'
import type { CuratedDataset } from '@/api/v2/curated'
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

  const toggleView = () => {
    switchTab(activeTab === 'curated' ? 'raw' : 'curated')
  }

  return (
    <div className="flex flex-col h-full space-y-3">
      {/* 顶部数据流卡片：SVG 数据流图 + 切换视图按钮 */}
      <div className="shrink-0 flex items-center justify-between gap-4 bg-white rounded-xl border border-slate-200 px-5 py-3 shadow-sm/50">
        {/* 左侧 SVG 数据流图 */}
        <svg
          viewBox="0 0 740 72"
          className="w-full max-w-[700px] h-auto shrink"
          style={{ minWidth: 500 }}
        >
          {/* ===== 节点 1：数据流水线 ===== */}
          <g onClick={() => navigate('/data/pipelines')} style={{ cursor: 'pointer' }}>
            <rect x="0" y="4" width="140" height="64" rx="10" ry="10"
              fill="#f0fdfa" stroke="#0d9488" strokeWidth="1.5" />
            <circle cx="24" cy="28" r="10" fill="#0d9488" opacity="0.15" />
            <text x="24" y="32" textAnchor="middle" fontSize="11" fill="#0d9488">⚙</text>
            <text x="44" y="24" fontSize="12" fontWeight="600" fill="#134e4a">数据流水线</text>
            <text x="44" y="40" fontSize="9" fill="#5eead4">系统流水线 / n8n流水线</text>
            <text x="44" y="54" fontSize="9" fill="#99f6e4">Pipeline</text>
          </g>

          {/* 箭头 1→2 */}
          <line x1="144" y1="36" x2="174" y2="36" stroke="#cbd5e1" strokeWidth="1.5" />
          <polygon points="172,31 182,36 172,41" fill="#cbd5e1" />

          {/* ===== 节点 2：数据任务池 ===== */}
          <g onClick={() => navigate('/data/pipelines/sync-tasks')} style={{ cursor: 'pointer' }}>
            <rect x="186" y="8" width="130" height="56" rx="10" ry="10"
              fill="#eff6ff" stroke="#3b82f6" strokeWidth="1.5" />
            <circle cx="208" cy="36" r="10" fill="#3b82f6" opacity="0.15" />
            <text x="208" y="40" textAnchor="middle" fontSize="11" fill="#3b82f6">📋</text>
            <text x="228" y="32" fontSize="12" fontWeight="600" fill="#1e40af">数据任务池</text>
            <text x="228" y="48" fontSize="10" fill="#93c5fd">Sync Tasks</text>
          </g>

          {/* 箭头 2→3 */}
          <line x1="320" y1="36" x2="350" y2="36" stroke="#cbd5e1" strokeWidth="1.5" />
          <polygon points="348,31 358,36 348,41" fill="#cbd5e1" />

          {/* ===== 节点 3：数据资产湖（当前页，高亮） ===== */}
          <g style={{ cursor: 'default' }}>
            <rect x="362" y="2" width="150" height="68" rx="12" ry="12"
              fill="#ecfdf5" stroke="#10b981" strokeWidth="2.5" />
            <circle cx="388" cy="28" r="11" fill="#10b981" opacity="0.2" />
            <text x="388" y="32" textAnchor="middle" fontSize="12" fill="#10b981">📊</text>
            <text x="410" y="22" fontSize="12" fontWeight="700" fill="#065f46">数据资产湖</text>
            <text x="410" y="37" fontSize="9" fill="#6ee7b7">成品数据集 / 人工数据集</text>
            <text x="410" y="52" fontSize="9" fill="#a7f3d0">Data Lake</text>
            {/* 当前页角标 */}
            <rect x="463" y="2" width="49" height="17" rx="0" ry="0"
              fill="#10b981" style={{ clipPath: 'polygon(0 0, 100% 0, 100% 100%, 14px 100%, 0 50%, 14px 0)' }} />
            <text x="489" y="14" textAnchor="middle" fontSize="9" fontWeight="600" fill="white">当前</text>
          </g>

          {/* 箭头 3→4 */}
          <line x1="516" y1="36" x2="546" y2="36" stroke="#cbd5e1" strokeWidth="1.5" />
          <polygon points="544,31 554,36 544,41" fill="#cbd5e1" />

          {/* ===== 节点 4：本体模型 ===== */}
          <g onClick={() => navigate('/ontologies')} style={{ cursor: 'pointer' }}>
            <rect x="558" y="8" width="130" height="56" rx="10" ry="10"
              fill="#faf5ff" stroke="#8b5cf6" strokeWidth="1.5" />
            <circle cx="580" cy="36" r="10" fill="#8b5cf6" opacity="0.15" />
            <text x="580" y="40" textAnchor="middle" fontSize="11" fill="#8b5cf6">🧠</text>
            <text x="600" y="32" fontSize="12" fontWeight="600" fill="#5b21b6">本体模型</text>
            <text x="600" y="48" fontSize="10" fill="#c4b5fd">Ontology</text>
          </g>
        </svg>

        {/* 右侧：切换视图按钮 */}
        <div className="shrink-0">
          <button
            onClick={toggleView}
            className="flex items-center gap-2 px-4 py-2.5 bg-teal-50 border-2 border-[var(--color-nav-bg)] text-[var(--color-nav-bg)] text-sm font-medium rounded-lg hover:bg-teal-100 active:scale-95 transition-all duration-150 shadow-sm"
          >
            <RefreshCw size={14} className={activeTab === 'raw' ? 'rotate-180 transition-transform' : 'transition-transform'} />
            切换数据集视图
          </button>
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
  const [curated, setCurated] = useState<CuratedDataset[]>([])
  const [loading, setLoading] = useState(true)
  const [pipelineFilter, setPipelineFilter] = useState(searchParams.get('pipeline') || '')
  const [curatedFilter, setCuratedFilter] = useState('')
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
      curatedApi.list() as Promise<CuratedDataset[]>,
    ]).then(([pls, cur]) => {
      setPipelines(Array.isArray(pls) ? pls : [])
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

  const filtered = useMemo(() => {
    const pq = pipelineFilter.toLowerCase()
    const cq = curatedFilter.toLowerCase()
    return allRows.filter(r => {
      if (pq && !r.pipelineName.toLowerCase().includes(pq) && !r.pipelineId.toLowerCase().includes(pq)) return false
      if (cq && !r.curatedName.toLowerCase().includes(cq) && !r.curatedId.toLowerCase().includes(cq)) return false
      if (statusFilter && r.curatedStatus !== statusFilter) return false
      return true
    })
  }, [allRows, pipelineFilter, curatedFilter, statusFilter])

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
        <div className="relative">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={pipelineFilter}
            onChange={e => setPipelineFilter(e.target.value)}
            placeholder="按流水线筛选..."
            className="pl-8 pr-7 py-1.5 border rounded-lg text-sm w-60"
          />
          {pipelineFilter && (
            <button onClick={() => setPipelineFilter('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-black">
              <X size={12} />
            </button>
          )}
        </div>
        <div className="relative">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={curatedFilter}
            onChange={e => setCuratedFilter(e.target.value)}
            placeholder="按数据集名称筛选..."
            className="pl-8 pr-7 py-1.5 border rounded-lg text-sm w-60"
          />
          {curatedFilter && (
            <button onClick={() => setCuratedFilter('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-black">
              <X size={12} />
            </button>
          )}
        </div>
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
        <span className="text-xs text-gray-400">共 {filtered.length} 条</span>
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
