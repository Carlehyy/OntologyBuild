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
    options: { usage_tags: ['VLM提取', '结构化提取'], timeout: 30 },
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
    options: { usage_tags: ['Ontology Mapping', 'NL-to-Cypher'], timeout: 45 },
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
    options: { usage_tags: ['宽表分析', '结构化提取'], timeout: 60 },
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
    options: { usage_tags: ['OCR文字提取'], enabled: true, lang: 'ch', device: 'gpu' },
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
    options: { usage_tags: ['VLM提取'], timeout: 30 },
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
    options: { usage_tags: ['NL-to-Cypher'], timeout: 120 },
    created_by: 'admin',
    created_at: '2024-05-01T08:00:00Z',
    updated_at: '2024-06-19T10:45:00Z',
  },
]

// 生成30天的调用记录
function generateCallRecords(): CallRecord[] {
  const records: CallRecord[] = []
  const now = new Date()
  for (let d = 30; d >= 0; d--) {
    const date = new Date(now)
    date.setDate(date.getDate() - d)
    const dateStr = date.toISOString().split('T')[0]

    MOCK_MODELS.forEach((model) => {
      const dailyCalls = Math.floor(Math.random() * 50) + 10
      for (let i = 0; i < dailyCalls; i++) {
        const isSuccess = Math.random() > 0.15
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

// localStorage key
const DEFAULT_MODEL_KEY = 'default_model_id'

export function useMockModels() {
  const [models, setModels] = useState<ModelConfig[]>(MOCK_MODELS)
  const [callRecords] = useState<CallRecord[]>(ALL_CALL_RECORDS)
  const [dailyStats] = useState<DailyStats[]>(ALL_DAILY_STATS)
  const [defaultModelId, setDefaultModelId] = useState<string>(() => {
    return localStorage.getItem(DEFAULT_MODEL_KEY) || MOCK_MODELS[0]?.id || ''
  })

  const setDefault = useCallback((id: string) => {
    setDefaultModelId(id)
    localStorage.setItem(DEFAULT_MODEL_KEY, id)
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
  }
}
