import { Fragment, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Upload, RefreshCw, Trash2, ChevronDown, ChevronUp, Loader2,
  CheckCircle2, XCircle, X, GitBranch, Play, FileUp, Repeat,
  Database, Pencil, KeyRound,
} from 'lucide-react'
import datasetsApi, { type DatasetOverviewItem, type DatasetConsumer, type DatasetVersionItem } from '@/api/v2/datasets'
import pipelinesApi from '@/api/v2/pipelines'
import ConfirmDialog from '@/components/ConfirmDialog'
import DatasetEditorModal from './DatasetEditorModal'

const KIND_META: Record<string, { label: string; color: string }> = {
  structured:   { label: '结构化',   color: 'bg-blue-50 text-blue-600 border-blue-200' },
  semi:         { label: '半结构化', color: 'bg-amber-50 text-amber-600 border-amber-200' },
  unstructured: { label: '非结构化', color: 'bg-purple-50 text-purple-600 border-purple-200' },
}

const PIPELINE_STATUS_LABEL: Record<string, string> = {
  draft: '草稿', editing: '编辑中', running: '运行中', failed: '失败', published: '已发布',
}

function formatTime(iso?: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    })
  } catch { return iso }
}

interface Banner {
  type: 'success' | 'error'
  text: string
  /** 上传新版本后，可一键运行的已发布消费流水线 */
  runnableConsumers?: DatasetConsumer[]
}

export default function RawDatasetsView({ focusDatasetId }: { focusDatasetId?: string | null }) {
  const navigate = useNavigate()
  const [items, setItems] = useState<DatasetOverviewItem[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<string | null>(focusDatasetId ?? null)
  const [banner, setBanner] = useState<Banner | null>(null)

  // 预览缓存
  const [previews, setPreviews] = useState<Record<string, { columns: string[]; rows: Record<string, unknown>[]; total: number; versionNo?: number }>>({})
  const [versions, setVersions] = useState<Record<string, DatasetVersionItem[]>>({})
  const [previewLoading, setPreviewLoading] = useState<string | null>(null)

  // 上传（新建 / 新版本）
  const newFileRef = useRef<HTMLInputElement>(null)
  const versionFileRef = useRef<HTMLInputElement>(null)
  const versionTargetRef = useRef<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadingVersionId, setUploadingVersionId] = useState<string | null>(null)

  // 运行流水线
  const [runningPipelineId, setRunningPipelineId] = useState<string | null>(null)

  // 删除
  const [deleteTarget, setDeleteTarget] = useState<DatasetOverviewItem | null>(null)
  const [deleteBlocked, setDeleteBlocked] = useState<{ item: DatasetOverviewItem; consumers: DatasetConsumer[] } | null>(null)
  const [deleting, setDeleting] = useState(false)

  // 在线维护
  const [editorTarget, setEditorTarget] = useState<DatasetOverviewItem | null>(null)

  const load = () => {
    setLoading(true)
    datasetsApi.overview()
      .then(res => setItems(res.items ?? []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  // 深链定位：?dataset=xxx 自动展开并加载预览
  useEffect(() => {
    if (focusDatasetId) {
      setExpanded(focusDatasetId)
      loadDetail(focusDatasetId)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusDatasetId])

  const loadDetail = async (id: string, refresh = false) => {
    if (previews[id] && !refresh) return
    setPreviewLoading(id)
    try {
      const [pv, vers] = await Promise.all([
        datasetsApi.previewLatest(id, 20),
        datasetsApi.versions(id),
      ])
      setPreviews(p => ({ ...p, [id]: { columns: pv.columns ?? [], rows: pv.rows ?? [], total: pv.total_rows ?? 0, versionNo: pv.version_no } }))
      setVersions(p => ({ ...p, [id]: Array.isArray(vers) ? vers : [] }))
    } catch {
      setPreviews(p => ({ ...p, [id]: { columns: [], rows: [], total: 0 } }))
    } finally {
      setPreviewLoading(null)
    }
  }

  const handleExpand = (id: string) => {
    if (expanded === id) { setExpanded(null); return }
    setExpanded(id)
    loadDetail(id)
  }

  /** 上传文件新建数据集 */
  const handleNewUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setUploading(true)
    setBanner(null)
    try {
      const res = await datasetsApi.upload(file)
      setBanner({ type: 'success', text: `「${res.name}」上传成功，已创建人工数据集。在「维护数据」中声明主键后可直接灌入本体，也可作为流水线的数据源。` })
      load()
    } catch (err: unknown) {
      const er = err as { detail?: string; message?: string }
      setBanner({ type: 'error', text: `上传失败：${er?.detail || er?.message || '未知错误'}` })
    } finally {
      setUploading(false)
    }
  }

  /** 给已有数据集上传新版本 */
  const pickVersionFile = (id: string) => {
    versionTargetRef.current = id
    versionFileRef.current?.click()
  }

  const handleVersionUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    const id = versionTargetRef.current
    e.target.value = ''
    versionTargetRef.current = null
    if (!file || !id) return
    setUploadingVersionId(id)
    setBanner(null)
    try {
      const res = await datasetsApi.uploadVersion(id, file)
      const colChange = [
        ...(res.columns_added.length ? [`新增列：${res.columns_added.join('、')}`] : []),
        ...(res.columns_removed.length ? [`缺失列：${res.columns_removed.join('、')}`] : []),
      ].join('；')
      const runnable = (res.consumers || []).filter(c => c.status === 'published')
      setBanner({
        type: 'success',
        text: `「${res.dataset_name}」已更新至 v${res.version_no}${res.rowcount != null ? `（${res.rowcount} 行）` : ''}${colChange ? `。注意，${colChange}` : ''}`,
        runnableConsumers: runnable,
      })
      load()
      loadDetail(id, true)
    } catch (err: unknown) {
      const er = err as { detail?: string; message?: string }
      setBanner({ type: 'error', text: `更新失败：${er?.detail || er?.message || '未知错误'}` })
    } finally {
      setUploadingVersionId(null)
    }
  }

  /** 一键运行消费流水线 */
  const handleRunPipeline = async (pl: DatasetConsumer) => {
    if (runningPipelineId) return
    setRunningPipelineId(pl.id)
    try {
      const res = await pipelinesApi.runSync(pl.id)
      const stats = (res.stats || {}) as Record<string, unknown>
      if (res.status === 'success') {
        setBanner({
          type: 'success',
          text: `流水线「${pl.name}」运行成功：输入 ${stats.rows_in ?? 0} 行 → 输出 ${stats.rows_out ?? 0} 行，产物已更新到成品数据集`,
        })
      } else {
        setBanner({ type: 'error', text: `流水线「${pl.name}」运行失败：${(res as { error?: string }).error || '未知错误'}` })
      }
    } catch (err: unknown) {
      const er = err as { detail?: string; message?: string }
      setBanner({ type: 'error', text: `流水线「${pl.name}」运行失败：${er?.detail || er?.message || '未知错误'}` })
    } finally {
      setRunningPipelineId(null)
    }
  }

  /** 删除 */
  const handleDelete = async (force = false) => {
    const target = deleteTarget || deleteBlocked?.item
    if (!target) return
    setDeleting(true)
    try {
      await datasetsApi.delete(target.id, force)
      setDeleteTarget(null)
      setDeleteBlocked(null)
      setBanner({ type: 'success', text: `数据集「${target.name}」已删除` })
      load()
    } catch (err: unknown) {
      const er = err as { detail?: { message?: string; consumers?: DatasetConsumer[] } | string }
      if (typeof er?.detail === 'object' && er.detail?.consumers) {
        setDeleteTarget(null)
        setDeleteBlocked({ item: target, consumers: er.detail.consumers })
      } else {
        setBanner({ type: 'error', text: `删除失败：${typeof er?.detail === 'string' ? er.detail : '未知错误'}` })
        setDeleteTarget(null)
      }
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="space-y-4">
      <input ref={newFileRef} type="file" className="hidden" accept=".csv,.xlsx,.xls,.json,.xml,.pdf,.docx,.txt,.md" onChange={handleNewUpload} />
      <input ref={versionFileRef} type="file" className="hidden" accept=".csv,.xlsx,.xls,.json,.xml,.pdf,.docx,.txt,.md" onChange={handleVersionUpload} />

      {/* 工具行 */}
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-gray-400">
          人工数据集由你上传并持续维护（改单元格 / 增删行都会生成新版本）；声明主键后可直接被本体映射灌入
        </p>
        <div className="flex items-center gap-2 shrink-0">
          <button onClick={load} className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-800 px-2 py-1.5">
            <RefreshCw size={12} /> 刷新
          </button>
          <button
            onClick={() => newFileRef.current?.click()}
            disabled={uploading}
            className="flex items-center gap-1.5 px-3.5 py-2 bg-[var(--color-nav-bg)] text-white text-sm font-medium rounded-lg hover:opacity-90 disabled:opacity-50"
          >
            {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
            上传数据文件
          </button>
        </div>
      </div>

      {/* 操作结果提示条 */}
      {banner && (
        <div className={`px-4 py-2.5 rounded-lg border text-sm ${
          banner.type === 'success' ? 'bg-green-50 border-green-200 text-green-700' : 'bg-red-50 border-red-200 text-red-600'
        }`}>
          <div className="flex items-center gap-2">
            {banner.type === 'success' ? <CheckCircle2 size={15} className="shrink-0" /> : <XCircle size={15} className="shrink-0" />}
            <span className="flex-1">{banner.text}</span>
            <button onClick={() => setBanner(null)} className="text-gray-400 hover:text-gray-600 shrink-0"><X size={14} /></button>
          </div>
          {banner.runnableConsumers && banner.runnableConsumers.length > 0 && (
            <div className="flex items-center gap-2 mt-2 ml-6 flex-wrap">
              <span className="text-xs">立即用新数据运行流水线：</span>
              {banner.runnableConsumers.map(pl => (
                <button
                  key={pl.id}
                  onClick={() => handleRunPipeline(pl)}
                  disabled={!!runningPipelineId}
                  className="flex items-center gap-1 text-xs px-2.5 py-1 bg-white border border-green-300 rounded-lg hover:bg-green-100 disabled:opacity-50"
                >
                  {runningPipelineId === pl.id ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
                  {pl.name}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 列表 */}
      {loading ? (
        <div className="text-gray-400 text-sm p-8 text-center">加载中...</div>
      ) : items.length === 0 ? (
        <div className="border-2 border-dashed rounded-xl p-12 text-center text-gray-400 space-y-2">
          <Database size={32} className="mx-auto opacity-30" />
          <p className="text-sm font-medium">暂无人工数据集</p>
          <p className="text-xs">点击「上传数据文件」导入 Excel/CSV，之后可在线维护、声明主键并直接灌入本体</p>
          <div className="flex justify-center gap-2 pt-1">
            <button
              onClick={() => newFileRef.current?.click()}
              className="text-xs px-3 py-1.5 bg-[var(--color-nav-bg)] text-white rounded-lg hover:opacity-90"
            >
              上传文件
            </button>
            <button
              onClick={() => navigate('/data/pipelines/sync-tasks')}
              className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50 text-gray-600"
            >
              去任务池创建同步任务
            </button>
          </div>
        </div>
      ) : (
        <div className="border rounded-xl overflow-hidden bg-white">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">名称</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">来源</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">类型</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">最新数据</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">被流水线使用</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">更新时间</th>
                <th className="text-right px-4 py-2.5 font-medium text-gray-600 text-xs">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {items.map(ds => {
                const meta = KIND_META[ds.kind] ?? { label: ds.kind, color: 'bg-gray-50 text-gray-600 border-gray-200' }
                const isOpen = expanded === ds.id
                const preview = previews[ds.id]
                const vers = versions[ds.id] ?? []
                return (
                  <Fragment key={ds.id}>
                    <tr
                      className={`hover:bg-gray-50 transition-colors cursor-pointer ${isOpen ? 'bg-gray-50' : ''}`}
                      onClick={() => handleExpand(ds.id)}
                    >
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          <span className="font-medium text-gray-900">{ds.name}</span>
                          {ds.primary_key && (
                            <span className="inline-flex items-center gap-0.5 text-[10px] px-1 py-0.5 rounded border bg-amber-50 text-amber-700 border-amber-200 shrink-0"
                              title={`主键契约：${ds.primary_key}（可直接被本体映射灌入）`}>
                              <KeyRound size={9} /> {ds.primary_key}
                            </span>
                          )}
                          {isOpen ? <ChevronUp size={13} className="text-gray-400" /> : <ChevronDown size={13} className="text-gray-400" />}
                        </div>
                        <p className="text-xs text-gray-400 font-mono">{ds.id.slice(0, 8)}</p>
                      </td>
                      <td className="px-4 py-3">
                        {ds.source === 'sync' ? (
                          <span className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded border bg-purple-50 text-purple-600 border-purple-200" title={ds.connection_name ? `连接：${ds.connection_name}` : '由同步任务落地'}>
                            <Repeat size={10} /> 同步任务
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded border bg-gray-50 text-gray-600 border-gray-200">
                            <FileUp size={10} /> 文件上传
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-xs px-1.5 py-0.5 rounded border ${meta.color}`}>{meta.label}</span>
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-600">
                        {ds.version_count > 0 ? (
                          <>
                            <span className="font-medium">v{ds.latest_version_no}</span>
                            <span className="text-gray-400"> · {ds.rowcount != null ? `${ds.rowcount} 行` : '行数未知'} · 共 {ds.version_count} 个版本</span>
                          </>
                        ) : (
                          <span className="text-gray-300">暂无数据</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {ds.consumers.length > 0 ? (
                          <span
                            className="inline-flex items-center gap-1 text-xs text-[var(--color-nav-bg)]"
                            title={ds.consumers.map(c => `${c.name}（${PIPELINE_STATUS_LABEL[c.status] || c.status}）`).join('\n')}
                          >
                            <GitBranch size={11} /> {ds.consumers.length} 条流水线
                          </span>
                        ) : (
                          <span className="text-xs text-gray-300">未使用</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-500">{formatTime(ds.updated_at)}</td>
                      <td className="px-4 py-3 text-right" onClick={e => e.stopPropagation()}>
                        <div className="flex gap-1 justify-end">
                          {ds.source === 'upload' && (
                            <button
                              onClick={() => setEditorTarget(ds)}
                              className="flex items-center gap-1 text-xs px-2 py-1.5 border rounded-lg hover:bg-amber-50 text-amber-700 border-amber-200"
                              title="在线维护：声明主键契约、改单元格、增删行（保存生成新版本）"
                            >
                              <Pencil size={12} />
                              维护数据
                            </button>
                          )}
                          {ds.source === 'upload' && (
                            <button
                              onClick={() => pickVersionFile(ds.id)}
                              disabled={uploadingVersionId === ds.id}
                              className="flex items-center gap-1 text-xs px-2 py-1.5 border rounded-lg hover:bg-blue-50 text-blue-600 border-blue-200 disabled:opacity-50"
                              title="上传新的数据文件，追加为新版本（数据集 ID 不变，流水线绑定不受影响）"
                            >
                              {uploadingVersionId === ds.id ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
                              上传新版本
                            </button>
                          )}
                          {/*
                            旧版同步任务（DataSyncTask）已废弃，sync 数据集为遗留数据。
                            用户如需同类数据，请通过新版任务池（PipelineTask）重建。
                          */}
                          <button
                            onClick={() => setDeleteTarget(ds)}
                            className="p-1.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-500"
                            title="删除数据集"
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      </td>
                    </tr>

                    {/* 展开详情 */}
                    {isOpen && (
                      <tr>
                        <td colSpan={7} className="bg-gray-50/70 border-t px-6 py-4">
                          {previewLoading === ds.id && !preview ? (
                            <p className="text-xs text-gray-400 flex items-center gap-1"><Loader2 size={12} className="animate-spin" /> 加载数据...</p>
                          ) : (
                            <div className="space-y-4">
                              {/* 消费流水线 */}
                              <div>
                                <p className="text-xs font-medium text-gray-500 mb-1.5">被以下流水线使用</p>
                                {ds.consumers.length === 0 ? (
                                  <p className="text-xs text-gray-400">
                                    暂未被任何流水线使用。
                                    <button onClick={() => navigate('/data/pipelines')} className="text-[var(--color-nav-bg)] hover:underline ml-1">
                                      去创建流水线加工这份数据 →
                                    </button>
                                  </p>
                                ) : (
                                  <div className="flex gap-2 flex-wrap">
                                    {ds.consumers.map(pl => (
                                      <div key={pl.id} className="flex items-center gap-2 bg-white border rounded-lg px-3 py-1.5 text-xs">
                                        <GitBranch size={11} className="text-gray-400" />
                                        <button onClick={() => navigate(`/data/pipelines/${pl.id}`)} className="font-medium text-gray-700 hover:text-[var(--color-nav-bg)] hover:underline">
                                          {pl.name}
                                        </button>
                                        <span className={`px-1 py-0.5 rounded text-[10px] ${pl.status === 'published' ? 'bg-green-50 text-green-600' : 'bg-gray-100 text-gray-500'}`}>
                                          {PIPELINE_STATUS_LABEL[pl.status] || pl.status}
                                        </span>
                                        {pl.status === 'published' && (
                                          <button
                                            onClick={() => handleRunPipeline(pl)}
                                            disabled={!!runningPipelineId}
                                            className="flex items-center gap-0.5 text-[10px] text-green-700 hover:bg-green-50 px-1.5 py-0.5 rounded disabled:opacity-50"
                                            title="立即运行该流水线加工最新数据"
                                          >
                                            {runningPipelineId === pl.id ? <Loader2 size={10} className="animate-spin" /> : <Play size={10} />}
                                            运行
                                          </button>
                                        )}
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>

                              {/* 数据预览 */}
                              <div>
                                <p className="text-xs font-medium text-gray-500 mb-1.5">
                                  数据预览{preview?.versionNo ? `（v${preview.versionNo}，共 ${preview.total} 行，显示前 ${preview.rows.length} 行）` : ''}
                                </p>
                                {!preview || preview.rows.length === 0 ? (
                                  <p className="text-xs text-gray-400">暂无可预览的数据</p>
                                ) : (
                                  <div className="overflow-x-auto bg-white border rounded-lg">
                                    <table className="text-xs w-full min-w-max">
                                      <thead className="bg-gray-50 border-b">
                                        <tr>
                                          {preview.columns.map(c => (
                                            <th key={c} className="text-left px-3 py-1.5 font-medium text-gray-500 whitespace-nowrap">{c}</th>
                                          ))}
                                        </tr>
                                      </thead>
                                      <tbody className="divide-y">
                                        {preview.rows.slice(0, 10).map((row, i) => (
                                          <tr key={i} className="hover:bg-gray-50">
                                            {preview.columns.map(c => (
                                              <td key={c} className="px-3 py-1.5 text-gray-600 max-w-[10rem] truncate" title={String(row[c] ?? '')}>
                                                {String(row[c] ?? '')}
                                              </td>
                                            ))}
                                          </tr>
                                        ))}
                                      </tbody>
                                    </table>
                                  </div>
                                )}
                              </div>

                              {/* 版本历史 */}
                              {vers.length > 0 && (
                                <div>
                                  <p className="text-xs font-medium text-gray-500 mb-1.5">版本历史</p>
                                  <div className="flex gap-2 flex-wrap">
                                    {[...vers].reverse().map(v => (
                                      <span key={v.id} className={`text-xs px-2 py-1 rounded-lg border bg-white ${v.version_no === ds.latest_version_no ? 'border-green-300 text-green-700' : 'text-gray-500'}`}>
                                        v{v.version_no}{v.rowcount != null ? ` · ${v.rowcount} 行` : ''}
                                        {v.version_no === ds.latest_version_no && ' · 当前'}
                                      </span>
                                    ))}
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 在线维护编辑器 */}
      {editorTarget && (
        <DatasetEditorModal
          dataset={editorTarget}
          onClose={() => setEditorTarget(null)}
          onSaved={() => { load(); loadDetail(editorTarget.id, true) }}
        />
      )}

      {/* 删除确认 */}
      <ConfirmDialog
        open={!!deleteTarget}
        title="删除人工数据集"
        message={`确认删除「${deleteTarget?.name}」及其全部 ${deleteTarget?.version_count ?? 0} 个版本？此操作不可撤销。`}
        confirmLabel={deleting ? '删除中...' : '确认删除'}
        onConfirm={() => handleDelete(false)}
        onCancel={() => setDeleteTarget(null)}
      />

      {/* 被引用时的强制删除确认 */}
      {deleteBlocked && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-lg p-6 w-[440px]">
            <h3 className="font-semibold text-lg mb-2">数据集正在被使用</h3>
            <p className="text-gray-600 text-sm mb-3">
              「{deleteBlocked.item.name}」被以下流水线引用，删除后这些流水线将无法运行：
            </p>
            <div className="space-y-1 mb-5">
              {deleteBlocked.consumers.map(c => (
                <div key={c.id} className="flex items-center gap-2 text-xs bg-gray-50 rounded-lg px-3 py-2">
                  <GitBranch size={11} className="text-gray-400" />
                  <span className="font-medium">{c.name}</span>
                  <span className="text-gray-400">（{PIPELINE_STATUS_LABEL[c.status] || c.status}）</span>
                </div>
              ))}
            </div>
            <div className="flex justify-end gap-3">
              <button onClick={() => setDeleteBlocked(null)} className="px-4 py-2 border rounded-lg text-sm hover:bg-gray-50">取消</button>
              <button
                onClick={() => handleDelete(true)}
                disabled={deleting}
                className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700 disabled:opacity-50"
              >
                {deleting ? '删除中...' : '仍要删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
