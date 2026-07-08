import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Search, CheckCircle, AlertTriangle, Clock,
  ExternalLink, X, Loader2, Trash2, Table2, Database, RefreshCw, Waves,
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

  return (
    <div className="space-y-4">
      {/* 页头 */}
      <div>
        <h1 className="text-xl font-bold text-gray-900 flex items-center gap-2">
          <Waves size={20} className="text-[var(--color-nav-bg)]" />
          数据资产湖
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          两类一等数据资产：<b>成品数据集</b>（流水线加工后的产物：手动执行与任务池调度都沉淀于此）与<b>人工数据集</b>（用户上传并持续维护的表格，声明主键后可直接作为本体对象的数据来源）
        </p>
      </div>

      {/* Tab 切换 */}
      <div className="flex items-center gap-1 border-b border-gray-200">
        {([
          ['curated', '成品数据集', <Table2 size={13} key="i" />],
          ['raw', '人工数据集', <Database size={13} key="i" />],
        ] as [LakeTab, string, React.ReactNode][]).map(([key, label, icon]) => (
          <button
            key={key}
            onClick={() => switchTab(key)}
            className={`flex items-center gap-1.5 px-4 py-2.5 text-sm border-b-2 -mb-px transition-colors ${
              activeTab === key
                ? 'border-[var(--color-nav-bg)] text-[var(--color-nav-bg)] font-medium'
                : 'border-transparent text-gray-500 hover:text-gray-800'
            }`}
          >
            {icon} {label}
          </button>
        ))}
        <span className="ml-auto text-xs text-gray-400 pb-2">
          {activeTab === 'curated'
            ? '流水线每次运行会把产物追加为新版本；在本体管理中可绑定已审核的成品数据集灌入实例数据'
            : '上传或在线编辑即可更新数据（每次保存生成新版本）；声明主键后可直接被本体映射灌入'}
        </span>
      </div>

      {activeTab === 'raw'
        ? <RawDatasetsView focusDatasetId={focusDatasetId} />
        : <CuratedView />}
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

  const [panelRow, setPanelRow] = useState<Row | null>(null)
  const [approvingId, setApprovingId] = useState<string | null>(null)
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
      return true
    })
  }, [allRows, pipelineFilter, curatedFilter])

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
    <div className="space-y-4">
      {/* 筛选 */}
      <div className="flex gap-3 flex-wrap items-center">
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
        <span className="text-xs text-gray-400">共 {filtered.length} 条</span>
        <button onClick={load} className="ml-auto flex items-center gap-1 text-xs text-gray-500 hover:text-gray-800 px-2 py-1.5">
          <RefreshCw size={12} /> 刷新
        </button>
      </div>

      {/* 表格 */}
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
                      {row.curatedId && row.curatedStatus !== 'approved' && (
                        <button
                          onClick={e => handleQuickApprove(e, row)}
                          disabled={approvingId === row.curatedId}
                          className="p-1.5 rounded hover:bg-green-50 text-gray-400 hover:text-green-600 disabled:opacity-50"
                          title="批准（审核通过后可用于本体数据绑定）"
                        >
                          {approvingId === row.curatedId
                            ? <Loader2 size={13} className="animate-spin" />
                            : <CheckCircle size={13} />}
                        </button>
                      )}

                      {row.pipelineId && (
                        <button
                          onClick={() => navigate(`/data/pipelines/${row.pipelineId}`)}
                          className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-black"
                          title="打开来源流水线"
                        >
                          <ExternalLink size={13} />
                        </button>
                      )}

                      {row.curatedId && (
                        <button
                          onClick={() => setDeleteRow(row)}
                          className="p-1.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-500"
                          title="删除"
                        >
                          <Trash2 size={13} />
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
