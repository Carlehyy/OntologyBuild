import axios from 'axios'

type RuntimeWindow = Window & { __API_BASE_URL__?: string }
const runtimeBase = (typeof window !== 'undefined' && (window as RuntimeWindow).__API_BASE_URL__) || ''
const http = axios.create({ baseURL: `${runtimeBase}/api/api-hub` })

http.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})
http.interceptors.response.use(
  response => response,
  error => Promise.reject(error.response?.data ?? error),
)

export interface KV { key: string; value: string }
export interface FileField { key: string; accept: string; multiple: boolean }
export interface InterfaceParameter {
  name: string
  location: 'path' | 'query' | 'header' | 'body'
  value_type: 'string' | 'integer' | 'number' | 'boolean' | 'object' | 'array'
  required: boolean
  default: unknown
  description: string
  sensitive: boolean
  dynamic: boolean
}

export interface HubInterface {
  id: number | null
  name: string
  description: string
  group_name: string
  method: string
  url: string
  query_params: KV[]
  headers: KV[]
  body_type: 'none' | 'json' | 'form' | 'multipart' | 'raw'
  body_content: string
  file_fields: FileField[]
  use_w3: boolean
  mcp_enabled: boolean
  open_enabled: boolean
  http_enabled: boolean
  proxy_slug: string
  proxy_query_keys: string[]
  proxy_header_keys: string[]
  proxy_body_enabled: boolean
  proxy_body_keys: string[]
  parameter_schema: InterfaceParameter[]
  config_revision?: number
  created_by?: string
  updated_by?: string
  created_at?: string
  updated_at?: string
}

export interface RunResult {
  run_id?: number
  ok: boolean
  status_code: number | null
  elapsed_ms: number | null
  response_headers: Record<string, string>
  response_body: string
  content_type: string
  error: string | null
  relogin: boolean
  download?: { blob: Blob; filename: string }
}

export interface RunSummary {
  id: number
  interface_id: number
  name: string
  method: string
  ok: number | boolean
  status_code: number | null
  elapsed_ms: number | null
  error: string | null
  relogin: number | boolean
  source: string
  proxy_key_name: string | null
  source_ip: string | null
  created_at: string
}

export interface RunDetail extends RunSummary {
  request_snapshot: Record<string, unknown> | null
  response_headers: Record<string, string> | null
  response_body: string
}

export interface CredentialStatus {
  configured: boolean
  has_session: boolean
  expired: boolean
  expires_at: string | null
  acquired_at: string | null
  last_result: 'success' | 'failed' | null
  message: string
  refreshed_at: string | null
  cron: string
  next_run: string | null
  username: string
  credential_source: 'online' | 'environment'
}

export interface CredentialConfig {
  username: string
  password_configured: boolean
  login_url: string
  source: 'online' | 'environment'
}

export interface CredentialCookieHeader {
  cookie: string
  count: number
}

export interface CredentialUsageRecord {
  id: number
  interface_id: number | null
  interface_name: string
  ok: boolean
  relogin: boolean
  status_code: number | null
  error: string | null
  created_at: string
}

export interface CredentialUsage {
  total: number
  success: number
  failed: number
  relogin: number
  success_rate: number
  recent: CredentialUsageRecord[]
}

export interface RunOverview {
  total_interfaces: number
  executed_interfaces: number
  unexecuted_interfaces: number
  today_traffic: number
  seven_day_traffic: number
  seven_day_success: number
  seven_day_failed: number
  success_rate: number
  p95_elapsed_ms: number | null
  slow_threshold_ms: number
  retention_limit_per_interface: number
  daily: { date: string; count: number; failed: number }[]
}

export interface McpInfo {
  endpoint: string
  host: string
  port: number
  lan_ip: string
  server_name: string
  transport: string
  lan_exposed: boolean
  token_required: boolean
  published?: { id: number; name: string; tool_name: string }[]
  tools?: { name: string; desc: string }[]
}

export interface McpContractParameter {
  name: string
  location: 'path' | 'query' | 'header' | 'body'
  value_type: InterfaceParameter['value_type']
  required: boolean
  description: string
}

export interface McpContract {
  interface_id: number
  interface_name: string
  open_enabled: boolean
  parameters: McpContractParameter[]
  call_example: Record<string, unknown>
}

export interface ProxyInfo {
  path: string
  key_header: string
  port: number
  key_count: number
  published: { id: number; name: string; method: string; proxy_slug: string }[]
}

export type ProxyKeyStatus = 'active' | 'disabled' | 'scheduled' | 'expired'

export interface ProxyKey {
  id: number
  name: string
  key_prefix: string
  masked_key: string
  enabled: boolean
  valid_from: string | null
  expires_at: string | null
  scope_all: boolean
  interface_ids: number[]
  status: ProxyKeyStatus
  last_used_at: string | null
  created_at: string
  updated_at: string
  secret?: string
}

export interface ProxyKeyPayload {
  name: string
  enabled: boolean
  valid_from: string | null
  expires_at: string | null
  scope_all: boolean
  interface_ids: number[]
}

export interface ForwardingPackage {
  key_id: number
  key_name: string
  secret: string
  path: string
  key_header: string
  method: string
  query_params: KV[]
  header_params: KV[]
  body_type: HubInterface['body_type']
  body_enabled: boolean
  body_template: string
  editable_body_keys: string[]
  multipart_fields: KV[]
  file_fields: FileField[]
  generated_at: string
}

const data = <T>(promise: Promise<{ data: T }>) => promise.then(response => response.data)

export const apiHub = {
  listInterfaces: () => data<HubInterface[]>(http.get('/interfaces')),
  getInterface: (id: number) => data<HubInterface>(http.get(`/interfaces/${id}`)),
  createInterface: (body: HubInterface) => data<HubInterface>(http.post('/interfaces', body)),
  updateInterface: (id: number, body: HubInterface) => data<HubInterface>(http.put(`/interfaces/${id}`, body)),
  moveInterface: (id: number, body: { group_name: string; target_index: number }) =>
    data<{ ok: boolean }>(http.put(`/interfaces/${id}/move`, body)),
  deleteInterface: (id: number) => data<{ ok: boolean }>(http.delete(`/interfaces/${id}`)),
  deleteGroup: (group_name: string) => data<{ ok: boolean; count: number }>(http.post('/interfaces/groups/delete', { group_name })),
  setOpen: (id: number, open: boolean) => data<HubInterface>(http.post(`/interfaces/${id}/open`, { open })),
  mcpContract: (id: number) => data<McpContract>(http.get(`/interfaces/${id}/mcp-contract`)),
  setHttpPublication: (
    id: number,
    body: { enabled: boolean; slug: string; query_keys: string[]; header_keys: string[]; body_enabled: boolean; body_keys?: string[] },
  ) => data<HubInterface>(http.put(`/interfaces/${id}/http-publication`, body)),
  autoHttpPublication: (id: number) => data<HubInterface>(http.post(`/interfaces/${id}/http-publication/auto`)),
  run: (id: number) => data<RunResult>(http.post(`/interfaces/${id}/run`)),
  runDraft: (body: HubInterface) => data<RunResult>(http.post('/interfaces/preview-run', body)),
  runDraftRaw: async (body: HubInterface, selectedFiles: File[][]) => {
    const requestBody: HubInterface | FormData = body.body_type === 'multipart'
      ? multipartDraft(body, selectedFiles)
      : body
    const response = await http.post<ArrayBuffer>('/interfaces/preview-run/raw', requestBody, {
      responseType: 'arraybuffer',
      validateStatus: () => true,
    })
    return rawRunResult(response.status, response.headers as Record<string, unknown>, response.data)
  },
  listRuns: (params: Record<string, string | number>) => data<{ items: RunSummary[]; total: number; page: number; size: number }>(http.get('/runs', { params })),
  runOverview: (timezoneOffsetMinutes = new Date().getTimezoneOffset()) =>
    data<RunOverview>(http.get('/runs/overview', { params: { timezone_offset_minutes: timezoneOffsetMinutes } })),
  getRun: (interfaceId: number, runId: number) => data<RunDetail>(http.get(`/interfaces/${interfaceId}/runs/${runId}`)),
  credentialStatus: () => data<CredentialStatus>(http.get('/credential/status')),
  credentialConfig: () => data<CredentialConfig>(http.get('/credential/config')),
  credentialCookieHeader: () => data<CredentialCookieHeader>(http.get('/credential/cookie-header')),
  updateCredentialConfig: (body: { username: string; password?: string; login_url: string; clear_password?: boolean }) => data<CredentialConfig>(http.put('/credential/config', body)),
  credentialUsage: (limit = 60) => data<CredentialUsage>(http.get('/credential/usage', { params: { limit } })),
  refreshCredential: () => data<CredentialStatus>(http.post('/credential/refresh')),
  setSchedule: (cron: string) => data<{ cron: string; next_run: string | null }>(http.put('/credential/schedule', { cron })),
  mcpInfo: () => data<McpInfo>(http.get('/mcp/info')),
  systemMcpInfo: () => data<McpInfo>(http.get('/mcp/system/info')),
  proxyInfo: () => data<ProxyInfo>(http.get('/proxy/info')),
  listProxyKeys: () => data<ProxyKey[]>(http.get('/proxy/keys')),
  createProxyKey: (body: ProxyKeyPayload) => data<ProxyKey>(http.post('/proxy/keys', body)),
  createForwardingPackage: (id: number) => data<ForwardingPackage>(http.post(`/proxy/packages/${id}`)),
  updateProxyKey: (id: number, body: ProxyKeyPayload) => data<ProxyKey>(http.put(`/proxy/keys/${id}`, body)),
  deleteProxyKey: (id: number) => data<{ ok: boolean }>(http.delete(`/proxy/keys/${id}`)),
  importBackup: (payload: unknown) => data<{ imported: number; skipped: number; total: number; name: string }>(http.post('/backup/import', payload)),
  exportBackup: (payload: { name: string; mode: 'full' | 'partial'; ids: number[]; include_sensitive: boolean }) =>
    http.post('/backup/export', payload, { responseType: 'blob' }),
}

export function emptyHubInterface(): HubInterface {
  return {
    id: null,
    name: '未命名接口',
    description: '',
    group_name: '',
    method: 'GET',
    url: '',
    query_params: [{ key: '', value: '' }],
    headers: [{ key: '', value: '' }],
    body_type: 'none',
    body_content: '',
    file_fields: [],
    use_w3: false,
    mcp_enabled: false,
    open_enabled: false,
    http_enabled: false,
    proxy_slug: '',
    proxy_query_keys: [],
    proxy_header_keys: [],
    proxy_body_enabled: false,
    proxy_body_keys: [],
    parameter_schema: [],
  }
}

function multipartDraft(body: HubInterface, selectedFiles: File[][]): FormData {
  const form = new FormData()
  form.append('__interface', JSON.stringify(body))
  body.file_fields.forEach((field, index) => {
    if (!field.key.trim()) return
    ;(selectedFiles[index] || []).forEach(file => form.append(field.key.trim(), file, file.name))
  })
  return form
}

function rawRunResult(status: number, rawHeaders: Record<string, unknown>, data: ArrayBuffer): RunResult {
  const headers = Object.fromEntries(
    Object.entries(rawHeaders).map(([key, value]) => [key.toLowerCase(), String(value)]),
  )
  const bytes = data instanceof ArrayBuffer ? data : new Uint8Array(data).buffer
  if (headers['x-api-hub-upstream'] !== '1') {
    const text = new TextDecoder().decode(bytes)
    try {
      const payload = JSON.parse(text) as { detail?: string }
      throw { detail: payload.detail || `平台调用失败（HTTP ${status}）` }
    } catch (error) {
      if (error && typeof error === 'object' && 'detail' in error) throw error
      throw { detail: text || `平台调用失败（HTTP ${status}）` }
    }
  }

  const declaredContentType = headers['content-type'] || ''
  const contentType = declaredContentType || 'application/octet-stream'
  const disposition = headers['content-disposition'] || ''
  const textual = !declaredContentType || isTextContentType(contentType)
  const decoded = textual ? new TextDecoder().decode(bytes) : ''
  const responseBody = textual
    ? (decoded.length > 1_000_000 ? `${decoded.slice(0, 1_000_000)}\n\n…（响应过大，页面预览已截断）` : decoded)
    : `（二进制响应：${bytes.byteLength} bytes，Content-Type: ${contentType.split(';', 1)[0]}）`
  const downloadable = bytes.byteLength > 0 && (Boolean(disposition) || !textual)
  return {
    run_id: Number(headers['x-api-hub-run-id']) || undefined,
    ok: status >= 200 && status < 300,
    status_code: status,
    elapsed_ms: Number(headers['x-api-hub-elapsed-ms']) || 0,
    response_headers: headers,
    response_body: responseBody,
    content_type: contentType,
    error: status >= 200 && status < 300 ? null : `上游返回 HTTP ${status}`,
    relogin: headers['x-api-hub-relogin'] === '1',
    download: downloadable ? {
      blob: new Blob([bytes], { type: contentType }),
      filename: contentDispositionFilename(disposition) || 'download',
    } : undefined,
  }
}

function isTextContentType(value: string): boolean {
  const mime = value.split(';', 1)[0].trim().toLowerCase()
  return mime.startsWith('text/') || mime.endsWith('+json') || mime.endsWith('+xml') || [
    'application/json', 'application/xml', 'application/javascript',
    'application/x-www-form-urlencoded', 'application/graphql',
  ].includes(mime)
}

function contentDispositionFilename(value: string): string {
  const utf8 = value.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (utf8) {
    try { return decodeURIComponent(utf8.replace(/^"|"$/g, '')) }
    catch { return utf8 }
  }
  return value.match(/filename="([^"]+)"/i)?.[1]
    || value.match(/filename=([^;]+)/i)?.[1]?.trim()
    || ''
}

/** Keep FastAPI validation payloads safe to render in every API-Hub surface. */
function errorText(value: unknown): string {
  if (typeof value === 'string') return value.trim()
  if (Array.isArray(value)) return value.map(errorText).filter(Boolean).join('；')
  if (!value || typeof value !== 'object') return ''

  const item = value as { detail?: unknown; message?: unknown; msg?: unknown; loc?: unknown }
  const message = errorText(item.msg ?? item.message ?? item.detail)
  if (!message) return ''
  const location = errorLocation(item.loc)
  return location ? `${location}：${message}` : message
}

function errorLocation(value: unknown): string {
  if (!Array.isArray(value)) return ''
  const parts = value
    .filter((item): item is string | number => typeof item === 'string' || typeof item === 'number')
    .map(String)
    .filter(item => item !== 'body' && item !== 'query' && item !== 'path')
  if (!parts.length) return ''
  const labels: Record<string, string> = {
    url: '请求 URL',
    name: '接口名称',
    method: '请求方法',
    description: '用途说明',
    group_name: '接口分组',
  }
  return labels[parts[0]] || parts.join('.')
}

export function validateHttpUrl(value: string): string {
  const url = value.trim()
  if (!url) return '请填写请求 URL'
  try {
    const parsed = new URL(url)
    // mcp-bridge:// 为平台保留方案：由接口代理执行器进程内分发为服务端
    // MCP 调用（插件社区 stdio/SSE 转接口生成），不是出站 HTTP 目标。
    if (parsed.protocol === 'mcp-bridge:') {
      if (!parsed.hostname || !parsed.pathname.replace(/^\//, '')) {
        return 'MCP 桥接地址格式无效，应为 mcp-bridge://<server_id>/<tool_name>'
      }
      return ''
    }
    if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname || parsed.username || parsed.password) {
      return '请求 URL 必须是无账号信息的 http:// 或 https:// 完整地址，例如 https://www.baidu.com'
    }
  } catch {
    return '请求 URL 必须是无账号信息的 http:// 或 https:// 完整地址，例如 https://www.baidu.com'
  }
  return ''
}

export function apiError(error: unknown): string {
  if (typeof error === 'string') return error
  if (error && typeof error === 'object') {
    const value = error as { detail?: unknown; message?: unknown; msg?: unknown }
    return errorText(value.detail) || errorText(value.message) || errorText(value.msg) || '请求失败'
  }
  return '请求失败'
}
