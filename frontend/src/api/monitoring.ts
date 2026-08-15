/**
 * 运行监控 API — /api/v1/settings/monitoring
 *
 * 平台运行健康度（仅 admin）：接口请求量/成功率/耗时分布、慢接口排行与
 * 单请求证据（含 db/llm/http 分层耗时分解）。apiClient 已解包 {data} 信封。
 */
import { apiClient } from './client'

export type MonitoringWindow = '24h' | '7d'

export interface MonitoringOverview {
  window: MonitoringWindow
  requests: number
  success_rate: number
  client_error_rate: number
  server_error_rate: number
  avg_ms: number | null
  p50_ms: number | null
  p95_ms: number | null
  p99_ms: number | null
  slow_requests: number
  slow_threshold_ms: number
}

export interface TrendPoint {
  t: string
  count: number
  avg_ms: number | null
  p95_ms: number | null
  error_rate: number
}

export interface MonitoringTrend {
  window: MonitoringWindow
  points: TrendPoint[]
}

export interface TopRouteItem {
  route: string
  method: string
  requests: number
  error_rate: number
  avg_ms: number | null
  p95_ms: number | null
  max_ms: number
  slow_count: number
}

export interface TopRoutesResponse {
  items: TopRouteItem[]
  total: number
}

export interface SlowRequestBreakdown {
  db?: { count: number; total_ms: number }
  llm?: { count: number; total_ms: number }
  http?: { count: number; total_ms: number }
}

export interface SlowRequestItem {
  id: number
  created_at: string | null
  method: string
  route: string
  status_code: number
  duration_ms: number
  request_id: string
  username: string
  source_ip: string
  user_agent: string
  breakdown: SlowRequestBreakdown
}

export interface SlowRequestsResponse {
  items: SlowRequestItem[]
  total: number
  page: number
  size: number
}

export const monitoringApi = {
  overview: (window: MonitoringWindow = '24h') =>
    apiClient.get<MonitoringOverview>('/settings/monitoring/overview', {
      params: { window },
    }),
  trend: (window: MonitoringWindow = '24h') =>
    apiClient.get<MonitoringTrend>('/settings/monitoring/trend', {
      params: { window },
    }),
  top: (
    window: MonitoringWindow = '24h',
    sortBy: string = 'slow_count',
    limit: number = 20,
  ) =>
    apiClient.get<TopRoutesResponse>('/settings/monitoring/top', {
      params: { window, sort_by: sortBy, limit },
    }),
  slowRequests: (params: {
    start?: string
    end?: string
    route?: string
    page?: number
    size?: number
  } = {}) =>
    apiClient.get<SlowRequestsResponse>('/settings/monitoring/slow-requests', {
      params,
    }),
}

