import { useState, useCallback, useRef } from 'react'
import { useDropzone } from 'react-dropzone'
import { Upload, X, FileUp, Loader2, CheckCircle, XCircle, ChevronDown, ChevronRight, Table, RefreshCw } from 'lucide-react'
import { apiClientV2 } from '@/api/client'
import datasetsApi from '@/api/v2/datasets'

const SOURCE_LABEL: Record<string, string> = { file: '文件上传', postgresql: 'PostgreSQL', mysql: 'MySQL', mongodb: 'MongoDB', rest_api: 'REST API' }
const DB_CONFIG_FIELDS: Record<string, { key: string; label: string; placeholder: string; type?: string }[]> = {
  postgresql: [{ key: 'host', label: '主机', placeholder: 'localhost' }, { key: 'port', label: '端口', placeholder: '5432' }, { key: 'database', label: '数据库名', placeholder: 'mydb' }, { key: 'user', label: '用户名', placeholder: 'postgres' }, { key: 'password', label: '密码', placeholder: '••••••', type: 'password' }],
  mysql: [{ key: 'host', label: '主机', placeholder: 'localhost' }, { key: 'port', label: '端口', placeholder: '3306' }, { key: 'database', label: '数据库名', placeholder: 'mydb' }, { key: 'user', label: '用户名', placeholder: 'root' }, { key: 'password', label: '密码', placeholder: '••••••', type: 'password' }],
  mongodb: [{ key: 'uri', label: '连接字符串', placeholder: 'mongodb://localhost:27017/mydb' }],
  rest_api: [{ key: 'url', label: 'API URL', placeholder: 'https://api.example.com/data' }, { key: 'headers', label: '请求头 (JSON)', placeholder: '{"Authorization":"Bearer token"}' }, { key: 'method', label: '请求方法', placeholder: 'GET' }],
}

interface UploadedFileMeta {
  name: string
  size?: number
  dataset_id?: string
  kind?: string
}

export default function ConnectorInspector({ config, onChange, readOnly = false }: { config: Record<string, unknown>; onChange: (key: string, value: unknown) => void; readOnly?: boolean }) {
  const sourceType = String(config.source_type || 'file')
  const cv = (config.config_values || {}) as Record<string, string>
  const storedFiles = (config.files || []) as UploadedFileMeta[]
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'success' | 'failed'>('idle')
  const [testMessage, setTestMessage] = useState('')
  const [previewOpen, setPreviewOpen] = useState<Record<string, boolean>>({})
  const [previewData, setPreviewData] = useState<Record<string, any>>({})
  const [previewLoading, setPreviewLoading] = useState<Record<string, boolean>>({})
  const [updatingId, setUpdatingId] = useState<string | null>(null)
  const [updateMsg, setUpdateMsg] = useState('')
  const updateInputRef = useRef<HTMLInputElement>(null)
  const updateTargetRef = useRef<string | null>(null)

  const pickUpdateFile = (datasetId: string) => {
    updateTargetRef.current = datasetId
    updateInputRef.current?.click()
  }

  const handleUpdateFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    const datasetId = updateTargetRef.current
    e.target.value = ''
    if (!file || !datasetId) return
    setUpdatingId(datasetId)
    setUpdateMsg('')
    try {
      const res = await datasetsApi.uploadVersion(datasetId, file)
      // 同步文件名/大小到节点配置，刷新预览缓存
      const nextFiles = storedFiles.map((f: any) =>
        f.dataset_id === datasetId ? { ...f, name: file.name, size: file.size } : f)
      onChange('files', nextFiles)
      setPreviewData(p => { const n = { ...p }; delete n[datasetId]; return n })
      setPreviewOpen(p => ({ ...p, [datasetId]: false }))
      const colChange = [
        ...(res.columns_added.length ? [`新增列 ${res.columns_added.join('/')}`] : []),
        ...(res.columns_removed.length ? [`缺失列 ${res.columns_removed.join('/')}`] : []),
      ].join('，')
      setUpdateMsg(`「${res.dataset_name}」已更新至 v${res.version_no}${res.rowcount != null ? `（${res.rowcount} 行）` : ''}${colChange ? `，注意：${colChange}` : ''}，下次运行将使用新数据`)
    } catch (err: any) {
      setUpdateMsg(`更新失败：${err?.detail || err?.message || '未知错误'}`)
    } finally {
      setUpdatingId(null)
      updateTargetRef.current = null
    }
  }

  const togglePreview = async (datasetId: string) => {
    if (previewOpen[datasetId]) { setPreviewOpen(p => ({...p, [datasetId]: false})); return }
    setPreviewOpen(p => ({...p, [datasetId]: true}))
    if (!previewData[datasetId]) {
      setPreviewLoading(p => ({...p, [datasetId]: true}))
      try {
        const res: any = await apiClientV2.get(`/datasets/${datasetId}/preview?limit=20`)
        setPreviewData(p => ({...p, [datasetId]: res}))
      } catch { /* ignore */ }
      setPreviewLoading(p => ({...p, [datasetId]: false}))
    }
  }
  const onDrop = useCallback(async (accepted: File[]) => {
    if (accepted.length === 0) return
    setUploading(true)
    setUploadError('')
    try {
      const uploaded: UploadedFileMeta[] = []
      for (const file of accepted) {
        const fd = new FormData()
        fd.append('file', file)
        const res: any = await apiClientV2.post('/datasets/upload', fd, {
          headers: { 'Content-Type': 'multipart/form-data' },
        })
        uploaded.push({ name: file.name, size: file.size, dataset_id: res.id, kind: res.kind })
      }
      onChange('files', [...storedFiles, ...uploaded])
      setTestStatus('idle')
    } catch (e: any) {
      setUploadError(e?.detail || e?.message || '上传失败')
    } finally {
      setUploading(false)
    }
  }, [storedFiles, onChange])
  const { getRootProps, getInputProps, isDragActive } = useDropzone({ onDrop, multiple: true })

  const hasStoredFiles = storedFiles.length > 0
  const hasDbConfig = sourceType !== 'file' && Object.keys(cv).length > 0

  const formatSize = (s: number | undefined) => s ? `(${(s / 1024).toFixed(1)} KB)` : ''

  const updateFileInput = (
    <input
      ref={updateInputRef}
      type="file"
      accept=".csv,.xlsx,.xls,.json,.xml,.pdf,.docx,.txt,.md"
      className="hidden"
      onChange={handleUpdateFile}
    />
  )

  const updateMsgBanner = updateMsg && (
    <p className={`text-xs rounded-lg px-2.5 py-1.5 ${updateMsg.startsWith('更新失败') ? 'bg-red-50 text-red-600' : 'bg-green-50 text-green-700'}`}>
      {updateMsg}
    </p>
  )

  if (readOnly) {
    return (
      <div className="space-y-3">
        {updateFileInput}
        <div className="bg-blue-50 border border-blue-100 rounded-lg p-3 text-xs">
          <p className="text-blue-700 font-medium mb-1">📋 已保存配置</p>
          <p className="text-blue-600">类型: {SOURCE_LABEL[sourceType] || sourceType}</p>
          {sourceType === 'file' && hasStoredFiles && storedFiles.map((f: any, i: number) => (
            <p key={i} className="text-blue-500">📄 {f.name} {formatSize(f.size)}</p>
          ))}
          {sourceType !== 'file' && hasDbConfig && Object.entries(cv).filter(([k]) => k !== 'password').map(([k, v]) => (
            <p key={k} className="text-blue-500">{k}: {String(v).slice(0, 30)}</p>
          ))}
          {!hasStoredFiles && !hasDbConfig && <p className="text-blue-400">暂无配置数据</p>}
        </div>
        {updateMsgBanner}
        {hasStoredFiles && storedFiles.some((f: any) => f.dataset_id) && (
          <DataPreviewSection files={storedFiles} previewOpen={previewOpen} previewData={previewData} previewLoading={previewLoading} togglePreview={togglePreview} onUpdateFile={pickUpdateFile} updatingId={updatingId} />
        )}
      </div>
    )
  }

  return (
    <>
      <div><label className="text-xs text-gray-500 mb-1 block">数据源类型</label>
        <select value={sourceType} onChange={e => { onChange('source_type', e.target.value); onChange('config_values', {}); onChange('files', []); setTestStatus('idle') }} className="w-full border rounded-lg px-3 py-1.5 text-sm"><option value="file">文件上传</option><option value="postgresql">PostgreSQL</option><option value="mysql">MySQL</option><option value="mongodb">MongoDB</option><option value="rest_api">REST API</option></select></div>
      {sourceType === 'file' && (
        <div>
          <label className="text-xs text-gray-500 mb-1 block">上传文件（支持多选）</label>
          <div {...getRootProps()} className={`border-2 border-dashed rounded-lg p-4 text-center cursor-pointer transition-colors ${isDragActive ? 'border-blue-500 bg-blue-50' : 'border-gray-200 hover:border-gray-400'}`}>
            <input {...getInputProps()} /><Upload size={20} className="mx-auto mb-1 text-gray-400" />
            {uploading ? <p className="text-xs text-blue-500 font-medium">上传中...</p> : isDragActive ? <p className="text-xs text-blue-500 font-medium">松开以添加文件</p> : <p className="text-xs text-gray-500">拖拽文件到此处，或<span className="underline ml-0.5">点击选择</span></p>}
            <p className="text-[10px] text-gray-400 mt-1">支持 CSV/XLSX/JSON/PDF/DOCX 等，可批量多选</p>
          </div>
          {uploadError && <p className="text-xs text-red-500 mt-1">{uploadError}</p>}
          {hasStoredFiles && (<div className="mt-2 space-y-1">{storedFiles.map((f: any, i: number) => (
            <div key={i} className="flex items-center gap-2 text-xs bg-gray-50 rounded px-2 py-1.5">
              <FileUp size={11} className="text-gray-400" /><span className="flex-1 truncate">{f.name}</span>
              <span className="text-gray-400">{formatSize(f.size)}</span>
              <button onClick={() => { onChange('files', storedFiles.filter((_: any, j: number) => j !== i)) }} className="text-gray-400 hover:text-red-500"><X size={11} /></button>
            </div>
          ))}</div>)}
        </div>
      )}
      {updateFileInput}
      {updateMsgBanner}
      {hasStoredFiles && storedFiles.some((f: any) => f.dataset_id) && (
        <DataPreviewSection files={storedFiles} previewOpen={previewOpen} previewData={previewData} previewLoading={previewLoading} togglePreview={togglePreview} onUpdateFile={pickUpdateFile} updatingId={updatingId} />
      )}
      {sourceType !== 'file' && (<div className="space-y-3">{DB_CONFIG_FIELDS[sourceType]?.map(f => (<div key={f.key}><label className="text-xs text-gray-500 mb-1 block">{f.label}</label><input type={f.type || 'text'} value={String((config as any).config_values?.[f.key] || '')} onChange={e => { const cv2 = { ...((config as any).config_values || {}), [f.key]: e.target.value }; onChange('config_values', cv2); setTestStatus('idle') }} placeholder={f.placeholder} className="w-full border rounded-lg px-3 py-1.5 text-sm" /></div>))}</div>)}
      <div>
        <button onClick={async () => { setTestStatus('testing'); try { if (sourceType === 'file') { setTestStatus(hasStoredFiles ? 'success' : 'failed'); setTestMessage(hasStoredFiles ? '就绪' : '请上传'); return } await apiClientV2.post('/connections/test-config', { type: sourceType, config: cv }); setTestStatus('success'); setTestMessage('连接成功') } catch (e: any) { setTestStatus('failed'); setTestMessage(e?.detail || '失败') } }} disabled={testStatus === 'testing' || uploading}
          className={`w-full flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border ${testStatus === 'success' ? 'bg-green-50 text-green-700 border-green-200' : testStatus === 'failed' ? 'bg-red-50 text-red-700 border-red-200' : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'}`}>
          {testStatus === 'testing' && <Loader2 size={11} className="animate-spin" />}{testStatus === 'success' ? <CheckCircle size={11} /> : testStatus === 'failed' ? <XCircle size={11} /> : null}{testStatus === 'testing' ? '测试中...' : testStatus === 'success' ? '连接成功' : testStatus === 'failed' ? testMessage : '测试连接'}</button>
      </div>
      <div><label className="text-xs text-gray-500 mb-1 block">同步模式</label><select value={String(config.sync_mode || 'snapshot')} onChange={e => onChange('sync_mode', e.target.value)} className="w-full border rounded-lg px-3 py-1.5 text-sm"><option value="snapshot">SNAPSHOT</option><option value="append">APPEND</option></select></div>
    </>
  )
}

/** ── DataPreviewSection ── */
function DataPreviewSection({ files, previewOpen, previewData, previewLoading, togglePreview, onUpdateFile, updatingId }: {
  files: any[]
  previewOpen: Record<string, boolean>
  previewData: Record<string, any>
  previewLoading: Record<string, boolean>
  togglePreview: (id: string) => void
  onUpdateFile?: (datasetId: string) => void
  updatingId?: string | null
}) {
  return (
    <div className="space-y-2">
      {files.filter((f: any) => f.dataset_id).map((f: any) => (
        <div key={f.dataset_id} className="border border-gray-200 rounded-lg overflow-hidden">
          <div className="w-full flex items-center px-3 py-2 text-xs font-medium text-gray-600 hover:bg-gray-50">
            <button onClick={() => togglePreview(f.dataset_id)} className="flex-1 flex items-center gap-1 min-w-0">
              <Table size={12} className="shrink-0" />
              <span className="truncate">{f.name}</span>
              <span className="text-gray-400 shrink-0">
                {previewData[f.dataset_id] ? `v${previewData[f.dataset_id].version_no ?? '-'} · ${previewData[f.dataset_id].total_rows} 行` : ''}
              </span>
            </button>
            {onUpdateFile && (
              <button
                onClick={e => { e.stopPropagation(); onUpdateFile(f.dataset_id) }}
                disabled={updatingId === f.dataset_id}
                className="flex items-center gap-1 px-1.5 py-0.5 mr-1 rounded text-[10px] text-blue-600 hover:bg-blue-50 disabled:opacity-50 shrink-0"
                title="上传新的数据文件替换为新版本（数据集绑定不变）"
              >
                {updatingId === f.dataset_id ? <Loader2 size={10} className="animate-spin" /> : <RefreshCw size={10} />}
                更新数据
              </button>
            )}
            <button onClick={() => togglePreview(f.dataset_id)} className="shrink-0">
              {previewLoading[f.dataset_id] ? <Loader2 size={12} className="animate-spin" /> : previewOpen[f.dataset_id] ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            </button>
          </div>
          {previewOpen[f.dataset_id] && previewData[f.dataset_id] && (
            <div className="overflow-x-auto border-t">
              <table className="w-full text-[10px]">
                <thead className="bg-gray-50">
                  <tr>{previewData[f.dataset_id].columns?.map((c: string) => <th key={c} className="text-left px-2 py-1 font-medium text-gray-500 whitespace-nowrap border-r last:border-r-0">{c}</th>)}</tr>
                </thead>
                <tbody>
                  {previewData[f.dataset_id].rows?.map((row: any, i: number) => (
                    <tr key={i} className="border-t hover:bg-gray-50">{previewData[f.dataset_id].columns?.map((c: string) => <td key={c} className="px-2 py-0.5 whitespace-nowrap text-gray-600 border-r last:border-r-0 max-w-[120px] truncate">{String(row[c] ?? '')}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {previewOpen[f.dataset_id] && !previewData[f.dataset_id] && !previewLoading[f.dataset_id] && (
            <p className="text-xs text-gray-400 px-3 py-2 border-t">加载失败</p>
          )}
        </div>
      ))}
    </div>
  )
}
