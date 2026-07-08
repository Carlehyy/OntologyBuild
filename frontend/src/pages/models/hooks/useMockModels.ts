import { useState, useCallback } from 'react'
import type { ModelConfig } from '@/types/ontology'

export interface CallRecord {
  id: string
  modelId: string
  modelName: string
  timestamp: string
  status: 'success' | 'error' | 'timeout'
  latency: number // ms
  promptTokens: number
  completionTokens: number
}

export interface DailyStats {
  date: string
  callCount: number
  successCount: number
  errorCount: number
  avgLatency: number
}

// ── 展示层新增类型 ───────────────────────────────
export type RunStatus = 'normal' | 'degraded' | 'error' | 'disabled'

export interface HeatCell {
  color: string
  title: string
  status: 'success' | 'timeout' | 'error' | 'none' | 'disabled'
}

export interface ModelSummary {
  todayCalls: number
  availability: string // '99.4' | '—'
  avgLatency: number   // ms，0 表示无数据
  lastCall: string     // 相对时间，'—' 表示无
  successRate: number  // 0~1
}

// 热力条配色
const HEAT_EMPTY = '#eceef1'
const HEAT_GREENS = ['#216e39', '#2d8a4e', '#40c463', '#9be9a8'] // 快 → 慢
const HEAT_TIMEOUT = '#f0a020'
const HEAT_ERROR = '#e5484d'

function heatColor(status: CallRecord['status'], latency: number): string {
  if (status === 'timeout') return HEAT_TIMEOUT
  if (status === 'error') return HEAT_ERROR
  if (latency < 1000) return HEAT_GREENS[0]
  if (latency < 2000) return HEAT_GREENS[1]
  if (latency < 3000) return HEAT_GREENS[2]
  return HEAT_GREENS[3]
}

function relTime(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime()
  if (diff < 60_000) return '刚刚'
  const m = Math.floor(diff / 60_000)
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  const d = Math.floor(h / 24)
  return `${d} 天前`
}

// Mock 模型数据
const MOCK_MODELS: ModelConfig[] = [
  {
    id: '1',
    name: 'GPT-4o 生产环境',
    config_type: 'llm',
    provider: 'openai',
    api_base: 'https://api.openai.com/v1',
    has_api_key: true,
    models: ['gpt-4o', 'gpt-4o-mini'],
    options: { timeout: 30 },
    created_by: 'admin',
    created_at: '2024-01-15T08:30:00Z',
    updated_at: '2024-06-20T14:22:00Z',
  },
  {
    id: '2',
    name: 'Claude 3.5 Sonnet',
    config_type: 'llm',
    provider: 'anthropic',
    api_base: 'https://api.anthropic.com',
    has_api_key: true,
    models: ['claude-3-5-sonnet-20241022'],
    options: { timeout: 45 },
    created_by: 'admin',
    created_at: '2024-02-10T10:15:00Z',
    updated_at: '2024-06-18T09:45:00Z',
  },
  {
    id: '3',
    name: 'DeepSeek V3',
    config_type: 'llm',
    provider: 'compatible',
    api_base: 'https://api.deepseek.com/v1',
    has_api_key: true,
    models: ['deepseek-chat', 'deepseek-coder'],
    options: { timeout: 60 },
    created_by: 'admin',
    created_at: '2024-03-05T16:40:00Z',
    updated_at: '2024-06-22T11:30:00Z',
  },
  {
    id: '4',
    name: 'OCR 文字识别服务',
    config_type: 'ocr',
    provider: 'paddleocr',
    api_base: 'http://localhost:8080',
    has_api_key: false,
    models: ['ch_PP-OCRv4'],
    options: { enabled: true, lang: 'ch', device: 'gpu' },
    created_by: 'admin',
    created_at: '2024-01-20T09:00:00Z',
    updated_at: '2024-05-15T13:20:00Z',
  },
  {
    id: '5',
    name: '通义千问 Max',
    config_type: 'llm',
    provider: 'compatible',
    api_base: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    has_api_key: true,
    models: ['qwen-max', 'qwen-plus'],
    options: { timeout: 30 },
    created_by: 'admin',
    created_at: '2024-04-12T11:25:00Z',
    updated_at: '2024-06-21T15:10:00Z',
  },
  {
    id: '6',
    name: '本地 Llama3',
    config_type: 'llm',
    provider: 'compatible',
    api_base: 'http://localhost:11434/v1',
    has_api_key: false,
    models: ['llama3:70b', 'llama3:8b'],
    options: { timeout: 120 },
    created_by: 'admin',
    created_at: '2024-05-01T08:00:00Z',
    updated_at: '2024-06-19T10:45:00Z',
  },
]

// 每个模型的健康画像（决定 mock 调用记录的失败率），使不同模型呈现不同运行状态
const HEALTH_PROFILE: Record<string, number> = {
  '1': 0.03, // 正常
  '2': 0.04, // 正常
  '3': 0.16, // 降级
  '4': 0.04, // 正常
  '5': 0.05, // 正常
  '6': 0.45, // 异常
}

// 生成30天的调用记录
function generateCallRecords(): CallRecord[] {
  const records: CallRecord[] = []
  const now = new Date()
  for (let d = 30; d >= 0; d--) {
    const date = new Date(now)
    date.setDate(date.getDate() - d)
    const dateStr = date.toISOString().split('T')[0]

    MOCK_MODELS.forEach((model) => {
      const failRate = HEALTH_PROFILE[model.id] ?? 0.12
      const dailyCalls = Math.floor(Math.random() * 50) + 10
      for (let i = 0; i < dailyCalls; i++) {
        const isSuccess = Math.random() > failRate
        const isTimeout = !isSuccess && Math.random() > 0.5
        records.push({
          id: `${model.id}-${dateStr}-${i}`,
          modelId: model.id,
          modelName: model.name,
          timestamp: `${dateStr}T${String(Math.floor(Math.random() * 24)).padStart(2, '0')}:${String(Math.floor(Math.random() * 60)).padStart(2, '0')}:00Z`,
          status: isSuccess ? 'success' : isTimeout ? 'timeout' : 'error',
          latency: isSuccess ? Math.floor(Math.random() * 3000) + 200 : Math.floor(Math.random() * 5000) + 3000,
          promptTokens: Math.floor(Math.random() * 2000) + 100,
          completionTokens: Math.floor(Math.random() * 1000) + 50,
        })
      }
    })
  }
  return records
}

// 生成每日统计
function generateDailyStats(records: CallRecord[]): DailyStats[] {
  const grouped = new Map<string, { calls: CallRecord[] }>()
  records.forEach((r) => {
    const date = r.timestamp.split('T')[0]
    if (!grouped.has(date)) grouped.set(date, { calls: [] })
    grouped.get(date)!.calls.push(r)
  })

  return Array.from(grouped.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, { calls }]) => ({
      date,
      callCount: calls.length,
      successCount: calls.filter((c) => c.status === 'success').length,
      errorCount: calls.filter((c) => c.status !== 'success').length,
      avgLatency: Math.floor(calls.reduce((sum, c) => sum + c.latency, 0) / calls.length),
    }))
}

const ALL_CALL_RECORDS = generateCallRecords()
const ALL_DAILY_STATS = generateDailyStats(ALL_CALL_RECORDS)

// localStorage keys
const DEFAULT_MODEL_KEY = 'default_model_id'
const ENABLED_MAP_KEY = 'model_enabled_map'

function loadEnabledMap(): Record<string, boolean> {
  // 默认：本地 Llama3 停用，其余启用
  const defaults: Record<string, boolean> = { '6': false }
  try {
    return { ...defaults, ...JSON.parse(localStorage.getItem(ENABLED_MAP_KEY) || '{}') }
  } catch {
    return defaults
  }
}

export function useMockModels() {
  const [models, setModels] = useState<ModelConfig[]>(MOCK_MODELS)
  const [callRecords] = useState<CallRecord[]>(ALL_CALL_RECORDS)
  const [dailyStats] = useState<DailyStats[]>(ALL_DAILY_STATS)
  const [defaultModelId, setDefaultModelId] = useState<string>(() => {
    return localStorage.getItem(DEFAULT_MODEL_KEY) || MOCK_MODELS[0]?.id || ''
  })
  const [enabledMap, setEnabledMap] = useState<Record<string, boolean>>(loadEnabledMap)

  const setDefault = useCallback((id: string) => {
    setDefaultModelId(id)
    localStorage.setItem(DEFAULT_MODEL_KEY, id)
  }, [])

  // ── 启用状态 ────────────────────────────────
  const isEnabled = useCallback((id: string) => enabledMap[id] ?? true, [enabledMap])

  const toggleEnabled = useCallback((id: string) => {
    setEnabledMap((prev) => {
      const next = { ...prev, [id]: !(prev[id] ?? true) }
      localStorage.setItem(ENABLED_MAP_KEY, JSON.stringify(next))
      return next
    })
  }, [])

  const createModel = useCallback((data: Partial<ModelConfig> & { api_key?: string }) => {
    const newModel: ModelConfig = {
      id: `mock-${Date.now()}`,
      name: data.name || '未命名模型',
      config_type: (data.config_type as any) || 'llm',
      provider: data.provider || 'openai',
      api_base: data.api_base || '',
      has_api_key: !!data.api_key,
      models: data.models || [],
      options: data.options || {},
      created_by: 'admin',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }
    setModels((prev) => [...prev, newModel])
    return newModel
  }, [])

  const updateModel = useCallback((id: string, data: Partial<ModelConfig> & { api_key?: string }) => {
    setModels((prev) =>
      prev.map((m) =>
        m.id === id
          ? {
              ...m,
              name: data.name || m.name,
              config_type: (data.config_type as any) || m.config_type,
              provider: data.provider || m.provider,
              api_base: data.api_base !== undefined ? data.api_base : m.api_base,
              has_api_key: data.api_key ? true : m.has_api_key,
              models: data.models || m.models,
              options: data.options !== undefined ? data.options : m.options,
              updated_at: new Date().toISOString(),
            }
          : m
      )
    )
  }, [])

  const deleteModel = useCallback((id: string) => {
    setModels((prev) => prev.filter((m) => m.id !== id))
    if (defaultModelId === id) {
      const remaining = models.filter((m) => m.id !== id)
      if (remaining.length > 0) {
        setDefault(remaining[0].id)
      }
    }
  }, [defaultModelId, models, setDefault])

  const testConnection = useCallback(async (id: string): Promise<{ ok: boolean; message: string }> => {
    // 模拟测试延迟
    await new Promise((resolve) => setTimeout(resolve, 1500 + Math.random() * 1000))
    const success = Math.random() > 0.2
    return {
      ok: success,
      message: success ? '连接成功，响应正常' : '连接失败：请求超时',
    }
  }, [])

  // 获取单个模型的调用记录
  const getModelCallRecords = useCallback(
    (modelId: string) => {
      return callRecords.filter((r) => r.modelId === modelId)
    },
    [callRecords]
  )

  // 获取单个模型的每日统计
  const getModelDailyStats = useCallback(
    (modelId: string) => {
      const records = callRecords.filter((r) => r.modelId === modelId)
      return generateDailyStats(records)
    },
    [callRecords]
  )

  // ── 最近 N 次调用（按时间升序） ──────────────
  const getModelRecentCalls = useCallback(
    (modelId: string, n = 60) => {
      return callRecords
        .filter((r) => r.modelId === modelId)
        .sort((a, b) => (a.timestamp < b.timestamp ? -1 : 1))
        .slice(-n)
    },
    [callRecords]
  )

  // ── 历史调用热力条 ─────────────────────────
  const getModelHeatCells = useCallback(
    (modelId: string, n = 60): HeatCell[] => {
      if (!isEnabled(modelId)) {
        return Array.from({ length: n }, () => ({ color: HEAT_EMPTY, title: '已停用（无调用）', status: 'disabled' as const }))
      }
      const recent = getModelRecentCalls(modelId, n)
      const cells: HeatCell[] = recent.map((r) => ({
        color: heatColor(r.status, r.latency),
        title:
          r.status === 'success'
            ? `成功 · ${r.latency}ms`
            : r.status === 'timeout'
            ? '超时'
            : '调用失败',
        status: r.status,
      }))
      while (cells.length < n) cells.unshift({ color: HEAT_EMPTY, title: '无调用', status: 'none' })
      return cells
    },
    [isEnabled, getModelRecentCalls]
  )

  // ── 运行 / 健康状态 ────────────────────────
  const getModelRunStatus = useCallback(
    (modelId: string): RunStatus => {
      if (!isEnabled(modelId)) return 'disabled'
      const recent = getModelRecentCalls(modelId, 60)
      if (recent.length === 0) return 'normal'
      const rate = recent.filter((r) => r.status === 'success').length / recent.length
      if (rate >= 0.95) return 'normal'
      if (rate >= 0.82) return 'degraded'
      return 'error'
    },
    [isEnabled, getModelRecentCalls]
  )

  // ── 卡片指标汇总 ───────────────────────────
  const getModelSummary = useCallback(
    (modelId: string): ModelSummary => {
      if (!isEnabled(modelId)) {
        return { todayCalls: 0, availability: '—', avgLatency: 0, lastCall: '—', successRate: 0 }
      }
      const recs = callRecords.filter((r) => r.modelId === modelId)
      if (recs.length === 0) {
        return { todayCalls: 0, availability: '—', avgLatency: 0, lastCall: '—', successRate: 0 }
      }
      const latestDate = recs.map((r) => r.timestamp.split('T')[0]).sort().slice(-1)[0]
      const todayCalls = recs.filter((r) => r.timestamp.startsWith(latestDate)).length
      const success = recs.filter((r) => r.status === 'success')
      const availability = ((success.length / recs.length) * 100).toFixed(1)
      const avgLatency = success.length
        ? Math.round(success.reduce((s, r) => s + r.latency, 0) / success.length)
        : 0
      const last = recs.slice().sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1))[0]
      return {
        todayCalls,
        availability,
        avgLatency,
        lastCall: last ? relTime(last.timestamp) : '—',
        successRate: success.length / recs.length,
      }
    },
    [isEnabled, callRecords]
  )

  return {
    models,
    defaultModelId,
    setDefault,
    createModel,
    updateModel,
    deleteModel,
    testConnection,
    callRecords,
    dailyStats,
    getModelCallRecords,
    getModelDailyStats,
    // 新增
    isEnabled,
    toggleEnabled,
    getModelRecentCalls,
    getModelHeatCells,
    getModelRunStatus,
    getModelSummary,
  }
}
