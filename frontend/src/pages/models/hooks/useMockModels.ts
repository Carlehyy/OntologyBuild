import { useState, useCallback, useEffect } from 'react'
import type { ModelConfig } from '@/types/ontology'
import { modelApi } from '@/api/ontologies'

export interface CallRecord {
  id: string
  modelId: string
  modelName: string
  timestamp: string
  status: 'success' | 'error' | 'timeout'
  latency: number
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

export type RunStatus = 'normal' | 'degraded' | 'error' | 'disabled'

export interface HeatCell {
  color: string
  title: string
  status: 'success' | 'timeout' | 'error' | 'none' | 'disabled'
}

export interface ModelSummary {
  todayCalls: number
  availability: string
  avgLatency: number
  lastCall: string
  successRate: number
}

const HEAT_EMPTY = '#eceef1'

function emptyHeatCells(model: ModelConfig | undefined, n: number): HeatCell[] {
  const disabled = model ? model.enabled === false : false
  return Array.from({ length: n }, () => ({
    color: HEAT_EMPTY,
    title: disabled ? '已停用（无调用）' : '暂无调用记录',
    status: disabled ? 'disabled' : 'none',
  }))
}

function emptySummary(model: ModelConfig | undefined): ModelSummary {
  if (model?.enabled === false) {
    return { todayCalls: 0, availability: '—', avgLatency: 0, lastCall: '已停用', successRate: 0 }
  }
  return { todayCalls: 0, availability: '—', avgLatency: 0, lastCall: '—', successRate: 1 }
}

export function useMockModels() {
  const [models, setModels] = useState<ModelConfig[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [callRecords] = useState<CallRecord[]>([])
  const [dailyStats] = useState<DailyStats[]>([])

  const loadModels = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await modelApi.list()
      setModels(Array.isArray(data) ? data : [])
    } catch (err) {
      setError(err instanceof Error ? err.message : '模型配置加载失败')
      setModels([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadModels() }, [loadModels])

  const defaultModelId = models.find(m => m.is_default)?.id || models[0]?.id || ''

  const createModel = useCallback(async (data: Partial<ModelConfig> & { api_key?: string }) => {
    const created = await modelApi.create(data)
    setModels(prev => [created, ...prev.map(m => created.is_default ? { ...m, is_default: false } : m)])
    return created
  }, [])

  const updateModel = useCallback(async (id: string, data: Partial<ModelConfig> & { api_key?: string }) => {
    const updated = await modelApi.update(id, data)
    setModels(prev => prev.map(m => {
      if (m.id === id) return updated
      return updated.is_default ? { ...m, is_default: false } : m
    }))
    return updated
  }, [])

  const deleteModel = useCallback(async (id: string) => {
    await modelApi.delete(id)
    await loadModels()
  }, [loadModels])

  const setDefault = useCallback(async (id: string) => {
    const updated = await modelApi.setDefault(id)
    setModels(prev => prev.map(m => m.id === id ? updated : { ...m, is_default: false }))
    return updated
  }, [])

  const isEnabled = useCallback((id: string) => {
    return models.find(m => m.id === id)?.enabled !== false
  }, [models])

  const toggleEnabled = useCallback(async (id: string) => {
    const target = models.find(m => m.id === id)
    if (!target) return
    const updated = await modelApi.setEnabled(id, target.enabled === false)
    setModels(prev => prev.map(m => {
      if (m.id === id) return updated
      return updated.is_default ? { ...m, is_default: false } : m
    }))
  }, [models])

  const testConnection = useCallback(async (id: string): Promise<{ ok: boolean; message: string }> => {
    try {
      const result = await modelApi.test(id)
      return { ok: Boolean(result.ok), message: result.response || (result.ok ? '连接成功，响应正常' : '连接失败') }
    } catch (err: any) {
      const detail = err?.detail || err?.message || '连接失败'
      return { ok: false, message: String(detail) }
    }
  }, [])

  const getModelCallRecords = useCallback((modelId: string) => {
    return callRecords.filter(r => r.modelId === modelId)
  }, [callRecords])

  const getModelDailyStats = useCallback((modelId: string) => {
    return dailyStats.filter(s => s.date && modelId)
  }, [dailyStats])

  const getModelRecentCalls = useCallback((modelId: string, n = 60) => {
    return callRecords.filter(r => r.modelId === modelId).slice(-n)
  }, [callRecords])

  const getModelHeatCells = useCallback((modelId: string, n = 60): HeatCell[] => {
    return emptyHeatCells(models.find(m => m.id === modelId), n)
  }, [models])

  const getModelRunStatus = useCallback((modelId: string): RunStatus => {
    return isEnabled(modelId) ? 'normal' : 'disabled'
  }, [isEnabled])

  const getModelSummary = useCallback((modelId: string): ModelSummary => {
    return emptySummary(models.find(m => m.id === modelId))
  }, [models])

  return {
    models,
    loading,
    error,
    defaultModelId,
    createModel,
    updateModel,
    deleteModel,
    setDefault,
    testConnection,
    callRecords,
    dailyStats,
    getModelCallRecords,
    getModelDailyStats,
    getModelRecentCalls,
    isEnabled,
    toggleEnabled,
    getModelHeatCells,
    getModelRunStatus,
    getModelSummary,
    reload: loadModels,
  }
}
