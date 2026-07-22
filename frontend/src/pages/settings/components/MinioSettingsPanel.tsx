import { useEffect, useMemo, useState } from 'react'
import {
  CheckCircle2,
  Clipboard,
  Database,
  KeyRound,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Wifi,
} from 'lucide-react'

import { settingsApi, type MinioConfig } from '@/api/ontologies'
import { writeTextToClipboard } from '@/utils/clipboard'


const errorText = (error: any) => error?.detail || error?.message || '操作失败'

export default function MinioSettingsPanel() {
  const [loading, setLoading] = useState(true)
  const [testing, setTesting] = useState(false)
  const [rotating, setRotating] = useState(false)
  const [config, setConfig] = useState<MinioConfig | null>(null)
  const [enabled, setEnabled] = useState(true)
  const [endpoint, setEndpoint] = useState('')
  const [secure, setSecure] = useState(false)
  const [region, setRegion] = useState('us-east-1')
  const [defaultBucket, setDefaultBucket] = useState('openontology')
  const [accessKey, setAccessKey] = useState('')
  const [secretKey, setSecretKey] = useState('')
  const [readEnabled, setReadEnabled] = useState(true)
  const [writeEnabled, setWriteEnabled] = useState(true)
  const [deleteEnabled, setDeleteEnabled] = useState(false)
  const [mcpEnabled, setMcpEnabled] = useState(true)
  const [message, setMessage] = useState('')
  const [messageOk, setMessageOk] = useState(true)
  const [mcpToken, setMcpToken] = useState('')

  const apply = (value: MinioConfig) => {
    setConfig(value)
    setEnabled(value.enabled)
    setEndpoint(value.endpoint)
    setSecure(value.secure)
    setRegion(value.region || 'us-east-1')
    setDefaultBucket(value.default_bucket || 'openontology')
    setReadEnabled(value.read_enabled)
    setWriteEnabled(value.write_enabled)
    setDeleteEnabled(value.delete_enabled)
    setMcpEnabled(value.mcp_enabled)
  }

  const reload = async () => {
    setLoading(true)
    try { apply(await settingsApi.getMinioConfig()) }
    catch (error) { setMessage(errorText(error)); setMessageOk(false) }
    finally { setLoading(false) }
  }

  useEffect(() => { void reload() }, [])

  const mcpUrl = useMemo(() => {
    const runtimeBase = (window as any).__API_BASE_URL__ || window.location.origin
    return `${String(runtimeBase).replace(/\/$/, '')}/mcp/minio`
  }, [])
  const mcpClientConfig = useMemo(() => mcpToken ? JSON.stringify({
    mcpServers: {
      openontology_minio: {
        transport: 'streamable_http',
        url: mcpUrl,
        headers: { Authorization: `Bearer ${mcpToken}` },
      },
    },
  }, null, 2) : '', [mcpToken, mcpUrl])

  const testAndSave = async () => {
    if (!endpoint.trim()) { setMessage('请填写 MinIO S3 API 端点'); setMessageOk(false); return }
    setTesting(true); setMessage(''); setMcpToken('')
    try {
      const result = await settingsApi.testMinioConnection({
        enabled,
        endpoint: endpoint.trim(),
        secure,
        region: region.trim() || 'us-east-1',
        default_bucket: defaultBucket.trim(),
        access_key: accessKey,
        secret_key: secretKey,
        read_enabled: readEnabled,
        write_enabled: writeEnabled,
        delete_enabled: deleteEnabled,
        mcp_enabled: mcpEnabled,
        create_default_bucket: true,
        timeout_seconds: 10,
      })
      setMessage(result.message)
      setMessageOk(result.ok)
      if (result.ok) {
        setEndpoint(result.endpoint)
        setAccessKey(''); setSecretKey('')
        if (result.mcp_token) setMcpToken(result.mcp_token)
        await reload()
      }
    } catch (error) { setMessage(errorText(error)); setMessageOk(false) }
    finally { setTesting(false) }
  }

  const rotateToken = async () => {
    if (!window.confirm('重置后，旧的外部 MCP Token 会立即失效。确认继续？')) return
    setRotating(true)
    try {
      const result = await settingsApi.rotateMinioMcpToken()
      setMcpToken(result.token)
      setMessage('MCP Token 已重置；请立即复制下方配置，关闭页面后将不再显示明文。')
      setMessageOk(true)
      await reload()
    } catch (error) { setMessage(errorText(error)); setMessageOk(false) }
    finally { setRotating(false) }
  }

  const copyConfig = async () => {
    try {
      await writeTextToClipboard(mcpClientConfig)
      setMessage('MCP 客户端配置已复制')
      setMessageOk(true)
    } catch (error) {
      setMessage(errorText(error))
      setMessageOk(false)
    }
  }

  if (loading) return <div className="flex min-h-48 items-center justify-center"><Loader2 className="animate-spin text-blue-600" /></div>

  return (
    <div className="max-w-3xl space-y-5">
      <div className="rounded-xl border bg-white p-6">
        <div className="mb-1 flex items-center gap-2">
          <Database size={17} className="text-blue-600" />
          <h3 className="text-sm font-semibold">MinIO 对象存储</h3>
          {config?.connected && <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-1 text-[11px] text-emerald-700"><CheckCircle2 size={12} /> 已连接</span>}
        </div>
        <p className="mb-5 text-xs leading-5 text-gray-500">
          配置用于流水线文件、非结构化附件等文件型对象，以及管理员 HTTP 接口和 MinIO MCP。结构化、半结构化和成品数据始终保存在平台数据库，不受此配置影响。凭据加密保存且不会回显。
        </p>

        <div className="space-y-4">
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} className="h-4 w-4 accent-blue-600" />
            启用管理员配置的 MinIO 作为平台文件对象存储
          </label>

          <div className="grid gap-4 sm:grid-cols-[1fr_130px]">
            <label className="text-xs text-gray-500">S3 API 端点
              <input value={endpoint} onChange={event => setEndpoint(event.target.value)} placeholder="minio.example.com:9000"
                className="mt-1.5 w-full rounded-lg border px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-blue-500" />
              <span className="mt-1 block text-[11px] text-gray-400">可填写 http(s)://；不要填写 9001/browser 控制台地址。</span>
            </label>
            <label className="text-xs text-gray-500">传输安全
              <span className="mt-1.5 flex min-h-[42px] items-center gap-2 rounded-lg border px-3 text-sm text-gray-700">
                <input type="checkbox" checked={secure} onChange={event => setSecure(event.target.checked)} className="accent-blue-600" /> HTTPS/TLS
              </span>
            </label>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="text-xs text-gray-500">Access Key
              <input value={accessKey} onChange={event => setAccessKey(event.target.value)} autoComplete="off"
                placeholder={config?.has_access_key ? '已安全保存；留空保持不变' : '请输入 Access Key'}
                className="mt-1.5 w-full rounded-lg border px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-blue-500" />
            </label>
            <label className="text-xs text-gray-500">Secret Key
              <input type="password" value={secretKey} onChange={event => setSecretKey(event.target.value)} autoComplete="new-password"
                placeholder={config?.has_secret_key ? '已安全保存；留空保持不变' : '请输入 Secret Key'}
                className="mt-1.5 w-full rounded-lg border px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-blue-500" />
            </label>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="text-xs text-gray-500">默认 Bucket
              <input value={defaultBucket} onChange={event => setDefaultBucket(event.target.value)}
                className="mt-1.5 w-full rounded-lg border px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-blue-500" />
            </label>
            <label className="text-xs text-gray-500">Region
              <input value={region} onChange={event => setRegion(event.target.value)}
                className="mt-1.5 w-full rounded-lg border px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-blue-500" />
            </label>
          </div>

          <div className="rounded-lg border bg-gray-50 p-4">
            <div className="mb-3 flex items-center gap-2 text-xs font-medium text-gray-700"><ShieldCheck size={14} className="text-blue-600" /> 能力边界</div>
            <div className="grid gap-3 sm:grid-cols-2">
              {[
                ['读取与检索', readEnabled, setReadEnabled],
                ['上传、覆盖、复制和建桶', writeEnabled, setWriteEnabled],
                ['删除、移动和删桶（高风险）', deleteEnabled, setDeleteEnabled],
                ['开放标准 MinIO MCP', mcpEnabled, setMcpEnabled],
              ].map(([label, checked, setter]) => (
                <label key={String(label)} className="flex items-center gap-2 text-xs text-gray-700">
                  <input type="checkbox" checked={checked as boolean} onChange={event => (setter as (value: boolean) => void)(event.target.checked)} className="accent-blue-600" />
                  {String(label)}
                </label>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => void testAndSave()} disabled={testing}
              className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-blue-600 px-4 text-sm text-white hover:bg-blue-700 disabled:opacity-50">
              {testing ? <Loader2 size={14} className="animate-spin" /> : <Wifi size={14} />}
              {testing ? '正在连接并验证…' : '测试连接并保存'}
            </button>
            {config?.connected && config.mcp_enabled && (
              <button type="button" onClick={() => void rotateToken()} disabled={rotating}
                className="inline-flex min-h-10 items-center gap-2 rounded-lg border px-4 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50">
                {rotating ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} 重置外部 MCP Token
              </button>
            )}
          </div>
          {message && <p role="status" className={`text-xs ${messageOk ? 'text-emerald-700' : 'text-red-600'}`}>{message}</p>}
        </div>
      </div>

      {config?.connected && config.mcp_enabled && (
        <div className="rounded-xl border bg-white p-6">
          <div className="mb-1 flex items-center gap-2"><KeyRound size={16} className="text-teal-700" /><h3 className="text-sm font-semibold">MinIO MCP</h3></div>
          <p className="text-xs leading-5 text-gray-500">超级助手中可点击“添加平台 MinIO”完成无密钥内置接入。外部 MCP 客户端使用 <code className="rounded bg-gray-100 px-1">{config.mcp_path}</code> 和独立 Bearer Token。</p>
          {config.has_mcp_token && !mcpToken && <p className="mt-3 text-xs text-gray-500">当前 Token 尾号：••••••{config.mcp_token_hint}。出于安全原因不回显；需要外部配置时请重置。</p>}
          {mcpClientConfig && (
            <div className="mt-4">
              <div className="mb-2 flex items-center justify-between"><span className="text-xs font-medium text-amber-700">此 Token 仅显示一次，请立即保存</span><button type="button" onClick={() => void copyConfig()} className="inline-flex min-h-9 items-center gap-1 rounded-md border px-3 text-xs hover:bg-gray-50"><Clipboard size={13} /> 复制配置</button></div>
              <pre className="max-h-72 overflow-auto rounded-lg bg-slate-950 p-4 text-[11px] leading-5 text-slate-100">{mcpClientConfig}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
