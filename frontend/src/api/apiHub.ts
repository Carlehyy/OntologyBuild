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

export interface HubInterface {
  id: number | null
  name: string
  description: string
  group_name: string
  method: string
  url: string
  query_params: KV[]
  headers: KV[]
  body_type: 'none' | 'json' | 'form' | 'raw'
  body_content: string
  use_w3: boolean
  mcp_enabled: boolean
  open_enabled: boolean
  http_enabled: boolean
  proxy_slug: string
  proxy_query_keys: string[]
  proxy_header_keys: string[]
  proxy_body_enabled: boolean
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
  token: string
  published?: { id: number; name: string; tool_name: string }[]
  tools?: { name: string; desc: string }[]
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

const data = <T>(promise: Promise<{ data: T }>) => promise.then(response => response.data)

export const apiHub = {
  listInterfaces: () => data<HubInterface[]>(http.get('/interfaces')),
  getInterface: (id: number) => data<HubInterface>(http.get(`/interfaces/${id}`)),
  createInterface: (body: HubInterface) => data<HubInterface>(http.post('/interfaces', body)),
  updateInterface: (id: number, body: HubInterface) => data<HubInterface>(http.put(`/interfaces/${id}`, body)),
  deleteInterface: (id: number) => data<{ ok: boolean }>(http.delete(`/interfaces/${id}`)),
  deleteGroup: (group_name: string) => data<{ ok: boolean; count: number }>(http.post('/interfaces/groups/delete', { group_name })),
  setOpen: (id: number, open: boolean) => data<HubInterface>(http.post(`/interfaces/${id}/open`, { open })),
  setHttpPublication: (
    id: number,
    body: { enabled: boolean; slug: string; query_keys: string[]; header_keys: string[]; body_enabled: boolean },
  ) => data<HubInterface>(http.put(`/interfaces/${id}/http-publication`, body)),
  run: (id: number) => data<RunResult>(http.post(`/interfaces/${id}/run`)),
  listRuns: (params: Record<string, string | number>) => data<{ items: RunSummary[]; total: number; page: number; size: number }>(http.get('/runs', { params })),
  runOverview: () => data<RunOverview>(http.get('/runs/overview')),
  getRun: (interfaceId: number, runId: number) => data<RunDetail>(http.get(`/interfaces/${interfaceId}/runs/${runId}`)),
  credentialStatus: () => data<CredentialStatus>(http.get('/credential/status')),
  credentialConfig: () => data<CredentialConfig>(http.get('/credential/config')),
  updateCredentialConfig: (body: { username: string; password?: string; login_url: string; clear_password?: boolean }) => data<CredentialConfig>(http.put('/credential/config', body)),
  credentialUsage: (limit = 60) => data<CredentialUsage>(http.get('/credential/usage', { params: { limit } })),
  refreshCredential: () => data<CredentialStatus>(http.post('/credential/refresh')),
  setSchedule: (cron: string) => data<{ cron: string; next_run: string | null }>(http.put('/credential/schedule', { cron })),
  cookieHeader: () => data<{ cookie: string; count: number }>(http.get('/credential/cookie-header')),
  mcpInfo: () => data<McpInfo>(http.get('/mcp/info')),
  systemMcpInfo: () => data<McpInfo>(http.get('/mcp/system/info')),
  proxyInfo: () => data<ProxyInfo>(http.get('/proxy/info')),
  listProxyKeys: () => data<ProxyKey[]>(http.get('/proxy/keys')),
  createProxyKey: (body: ProxyKeyPayload) => data<ProxyKey>(http.post('/proxy/keys', body)),
  updateProxyKey: (id: number, body: ProxyKeyPayload) => data<ProxyKey>(http.put(`/proxy/keys/${id}`, body)),
  deleteProxyKey: (id: number) => data<{ ok: boolean }>(http.delete(`/proxy/keys/${id}`)),
  importBackup: (payload: unknown) => data<{ imported: number; skipped: number; total: number; name: string }>(http.post('/backup/import', payload)),
  exportBackup: (payload: { name: string; mode: 'full' | 'partial'; ids: number[] }) =>
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
    use_w3: true,
    mcp_enabled: false,
    open_enabled: false,
    http_enabled: false,
    proxy_slug: '',
    proxy_query_keys: [],
    proxy_header_keys: [],
    proxy_body_enabled: false,
  }
}

export function apiError(error: unknown): string {
  if (typeof error === 'string') return error
  if (error && typeof error === 'object') {
    const value = error as { detail?: string; message?: string }
    return value.detail || value.message || '请求失败'
  }
  return '请求失败'
}
