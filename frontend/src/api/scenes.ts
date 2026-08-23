/**
 * 三维场景 API — /api/v2/scenes
 *
 * 字段命名对齐 world_model（snake_case），apiClientV2 已解包 {data} 信封。
 * URL 统一用单引号拼接（本文件约定不用模板串，保持与生成工具链友好）。
 */
import { apiClientV2 } from './client'
import type {
  RuntimeLogItem, SceneDetail, SceneDefinition, SceneListResp,
  SceneStatus, SceneVersionMeta, SceneVersionSource,
} from '@/types/scene'

export interface SceneRuntimeLogResp {
  items: RuntimeLogItem[]
  total: number
}

export interface SceneVersionsResp {
  items: SceneVersionMeta[]
  total: number
}

export interface SaveDefinitionResult {
  scene: SceneDetail
  version: SceneVersionMeta & { definition: SceneDefinition }
}

function normalizeParams(params: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') out[key] = value
  }
  return out
}

export const scenesApi = {
  list(params: { q?: string; status?: SceneStatus | 'all'; page?: number; page_size?: number } = {}) {
    return apiClientV2.get<SceneListResp>('/scenes', { params: normalizeParams(params) })
  },

  create(body: { name: string; description?: string; icon?: string; definition?: SceneDefinition }) {
    return apiClientV2.post<SceneDetail>('/scenes', body)
  },

  get(sceneId: string) {
    return apiClientV2.get<SceneDetail>('/scenes/' + sceneId)
  },

  updateBasicInfo(sceneId: string, body: { name?: string; description?: string; icon?: string }) {
    return apiClientV2.patch<SceneDetail>('/scenes/' + sceneId, body)
  },

  remove(sceneId: string) {
    return apiClientV2.delete<void>('/scenes/' + sceneId)
  },

  clone(sceneId: string) {
    return apiClientV2.post<SceneDetail>('/scenes/' + sceneId + '/clone')
  },

  saveDefinition(
    sceneId: string,
    definition: SceneDefinition,
    options: { note?: string; source?: Extract<SceneVersionSource, 'manual' | 'assistant'> } = {},
  ) {
    return apiClientV2.put<SaveDefinitionResult>(
      '/scenes/' + sceneId + '/definition',
      { definition, note: options.note ?? '' },
      { params: normalizeParams({ source: options.source }) },
    )
  },

  publish(sceneId: string) {
    return apiClientV2.post<SceneDetail>('/scenes/' + sceneId + '/publish')
  },

  versions(sceneId: string, includeDefinition = false) {
    return apiClientV2.get<SceneVersionsResp>('/scenes/' + sceneId + '/versions', {
      params: normalizeParams({ include_definition: includeDefinition || undefined }),
    })
  },

  version(sceneId: string, versionNo: number) {
    return apiClientV2.get<SceneVersionMeta & { definition: SceneDefinition }>(
      '/scenes/' + sceneId + '/versions/' + versionNo)
  },

  runtimeLogs(
    sceneId: string,
    params: { level?: string; object_id?: string; page?: number; page_size?: number } = {},
  ) {
    return apiClientV2.get<SceneRuntimeLogResp>('/scenes/' + sceneId + '/runtime-logs', {
      params: normalizeParams(params),
    })
  },

  appendRuntimeLogs(
    sceneId: string,
    entries: {
      level: string
      object_id?: string | null
      event_key?: string
      message: string
      payload?: Record<string, unknown>
      occurred_at?: string
    }[],
  ) {
    return apiClientV2.post<{ appended: number }>(
      '/scenes/' + sceneId + '/runtime-logs', { entries })
  },
}
