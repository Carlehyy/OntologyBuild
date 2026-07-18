import { apiClient } from './client'
import type { OntologyListItem, OntologyDetail, Entity, LogicRule, Action, UploadedFile, Prompt, ModelConfig, ModelCallLogPage } from '@/types/ontology'

export interface OntologyImportResult {
  ontology: OntologyDetail
  version: {
    id: string
    version_number: 'v0'
    version_label: string
  }
  counts: {
    objectTypes: number
    linkTypes: number
    actions: number
    functions: number
  }
}

function safeDownloadName(value: string) {
  const printable = [...value.trim()].filter(character => character.charCodeAt(0) >= 32).join('')
  const cleaned = printable.replace(/[\\/:*?"<>|]/g, '_')
  return cleaned || 'ontology'
}

export const ontologyApi = {
  list: (params?: { name?: string; domain?: string; page?: number; page_size?: number }) =>
    apiClient.get<{ items: OntologyListItem[]; total: number; page: number; page_size: number }>('/ontologies', { params }),
  create: (body: { name: string; domain: string; description?: string; icon?: string; build_mode?: string }) =>
    apiClient.post<OntologyDetail>('/ontologies', body),
  get: (id: string) => apiClient.get<OntologyDetail>(`/ontologies/${id}`),
  update: (id: string, body: Partial<OntologyDetail>) => apiClient.put<OntologyDetail>(`/ontologies/${id}`, body),
  delete: (id: string) => apiClient.delete(`/ontologies/${id}`),
  importStructure: (body: unknown) => apiClient.post<OntologyImportResult>('/ontologies/import', body),

  // Files
  listFiles: (oid: string) => apiClient.get<UploadedFile[]>(`/ontologies/${oid}/files`),
  deleteFile: (oid: string, fid: string) => apiClient.delete(`/ontologies/${oid}/files/${fid}`),

  // Graph
  getGraph: (oid: string) => apiClient.get<{ nodes: object[]; edges: object[]; meta: object }>(`/ontologies/${oid}/graph`),
  createRelation: (oid: string, body: object) => apiClient.post(`/ontologies/${oid}/graph/relations`, body),
  deleteRelation: (oid: string, rid: string) => apiClient.delete(`/ontologies/${oid}/graph/relations/${rid}`),

  // Entities
  listEntities: (oid: string) => apiClient.get<Entity[]>(`/ontologies/${oid}/entities`),
  createEntity: (oid: string, body: Partial<Entity>) => apiClient.post<Entity>(`/ontologies/${oid}/entities`, body),
  updateEntity: (oid: string, eid: string, body: Partial<Entity>) => apiClient.put<Entity>(`/ontologies/${oid}/entities/${eid}`, body),
  deleteEntity: (oid: string, eid: string) => apiClient.delete(`/ontologies/${oid}/entities/${eid}`),
  getEntityRelated: (oid: string, eid: string) =>
    apiClient.get<{ logic: any[]; actions: any[] }>(`/ontologies/${oid}/entities/${eid}/related`),

  // Logic
  listLogic: (oid: string) => apiClient.get<LogicRule[]>(`/ontologies/${oid}/logic`),
  createLogic: (oid: string, body: Partial<LogicRule>) => apiClient.post<LogicRule>(`/ontologies/${oid}/logic`, body),
  updateLogic: (oid: string, lid: string, body: Partial<LogicRule>) => apiClient.put<LogicRule>(`/ontologies/${oid}/logic/${lid}`, body),
  deleteLogic: (oid: string, lid: string) => apiClient.delete(`/ontologies/${oid}/logic/${lid}`),

  // Actions
  listActions: (oid: string) => apiClient.get<Action[]>(`/ontologies/${oid}/actions`),
  createAction: (oid: string, body: Partial<Action>) => apiClient.post<Action>(`/ontologies/${oid}/actions`, body),
  updateAction: (oid: string, aid: string, body: Partial<Action>) => apiClient.put<Action>(`/ontologies/${oid}/actions/${aid}`, body),
  deleteAction: (oid: string, aid: string) => apiClient.delete(`/ontologies/${oid}/actions/${aid}`),

  // Extraction
  startExtraction: (oid: string, body: { prompt_id: string; model_id: string; model_name: string; constraints?: string[] }) =>
    apiClient.post<{ task_id: string }>(`/ontologies/${oid}/execute`, body),
  getExtractionStatus: (oid: string, task_id: string) =>
    apiClient.get(`/ontologies/${oid}/execute/status?task_id=${task_id}`),

  // Export (must use authenticated request — plain links omit Bearer token)
  exportOntology: async (oid: string, name: string, version: string) => {
    const blob = (await apiClient.get(`/ontologies/${oid}/export`, {
      responseType: 'blob',
    })) as unknown as Blob
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${safeDownloadName(name)}_${safeDownloadName(version || 'draft')}.json`
    a.style.display = 'none'
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
  },

  // Audit
  startAudit: (oid: string, body: { model_id: string; model_name: string }) =>
    apiClient.post<{ task_id: string }>(`/ontologies/${oid}/audit`, body),
  getAuditStatus: (oid: string, task_id: string) =>
    apiClient.get(`/ontologies/${oid}/audit/status?task_id=${task_id}`),
}

export const promptApi = {
  list: (domain?: string) => apiClient.get<Prompt[]>('/prompts', { params: domain ? { domain } : {} }),
  getTemplates: () => apiClient.get<{ name: string; domain: string; content: string }[]>('/prompts/templates'),
  create: (body: Partial<Prompt>) => apiClient.post<Prompt>('/prompts', body),
  get: (id: string) => apiClient.get<Prompt>(`/prompts/${id}`),
  update: (id: string, body: Partial<Prompt>) => apiClient.put<Prompt>(`/prompts/${id}`, body),
  delete: (id: string) => apiClient.delete(`/prompts/${id}`),
  generateTemplate: (domain: string) =>
    apiClient.post<{ domain: string; content: string }>(`/prompts/generate-template?domain=${encodeURIComponent(domain)}&style=ontology_extraction`, {}),
}

export const modelApi = {
  list: () => apiClient.get<ModelConfig[]>('/models'),
  create: (body: Partial<ModelConfig> & { api_key?: string }) => apiClient.post<ModelConfig>('/models', body),
  get: (id: string) => apiClient.get<ModelConfig>(`/models/${id}`),
  update: (id: string, body: Partial<ModelConfig> & { api_key?: string }) => apiClient.put<ModelConfig>(`/models/${id}`, body),
  delete: (id: string) => apiClient.delete(`/models/${id}`),
  setDefault: (id: string) => apiClient.post<ModelConfig>(`/models/${id}/default`),
  setEnabled: (id: string, enabled: boolean) => apiClient.post<ModelConfig>(`/models/${id}/enabled`, { enabled }),
  test: (id: string) => apiClient.post<{ ok: boolean; response: string; code?: string; tested_at?: string }>(`/models/${id}/test`),
  import: (configs: Array<Partial<ModelConfig>>) => apiClient.post<{
    imported: number
    configs: ModelConfig[]
    warning: string
  }>('/models/import', { configs }),
  stats: (id: string) => apiClient.get<{
    todayCalls: number; availability: string | null; avgLatency: number | null;
    lastCall: string | null; successRate: number | null;
    heatCells: Array<{ color: string; title: string; status: string }>;
  }>(`/models/${id}/stats`),
  calls: (id: string, params: {
    page?: number; page_size?: number; status?: string; start?: string; end?: string;
  }) => apiClient.get<ModelCallLogPage>(`/models/${id}/calls`, { params }),
}

export const settingsApi = {
  getRules: () => apiClient.get<{ rule_key: string; rule_value: string; rule_label_cn: string; rule_label_en: string; editable: boolean }[]>('/settings/rules'),
  updateRules: (rules: { rule_key: string; rule_value: string }[]) => apiClient.put('/settings/rules', rules),

  // Agent config
  getAgentConfig: () => apiClient.get<{
    base_url: string; auth_enabled: boolean; username: string;
    has_password: boolean; target_agent_id: string; target_agent_name: string;
  }>('/settings/agent-config'),
  updateAgentConfig: (body: {
    base_url: string; auth_enabled: boolean; username: string; password: string;
    target_agent_id: string; target_agent_name: string;
  }) => apiClient.put('/settings/agent-config', body),
  testAgentConnection: (body: {
    base_url: string; auth_enabled: boolean; username: string; password: string;
  }) => apiClient.post<{ ok: boolean; message: string; has_auth: boolean; token_valid: boolean }>(
    '/settings/agent-config/test', body,
  ),
  fetchAgents: (body: {
    base_url: string; auth_enabled: boolean; username: string; password: string;
  }) => apiClient.post<{ agents: { id: string; name: string; description: string }[] }>(
    '/settings/agent-config/agents', body,
  ),

  // Workflow/n8n config
  getWorkflowConfig: () => apiClient.get<{
    enabled: boolean; api_url: string; has_api_key: boolean; timeout_seconds: number;
  }>('/settings/workflow-config'),
  updateWorkflowConfig: (body: {
    enabled: boolean; api_url: string; api_key: string; timeout_seconds: number;
  }) => apiClient.put('/settings/workflow-config', body),
  testWorkflowConnection: (body: {
    enabled: boolean; api_url: string; api_key: string; timeout_seconds: number;
  }) => apiClient.post<{ ok: boolean; message: string; api_base: string }>(
    '/settings/workflow-config/test', body,
  ),
}

export const domainApi = {
  list: (search?: string) => apiClient.get<{ id: string; name: string; description: string; created_by: string; created_at: string; updated_at: string }[]>(
    '/domains', { params: search ? { search } : {} },
  ),
  create: (body: { name: string; description: string }) => apiClient.post('/domains', body),
  update: (id: string, body: { name?: string; description?: string }) => apiClient.put(`/domains/${id}`, body),
  delete: (id: string) => apiClient.delete(`/domains/${id}`),
}

export const usersApi = {
  list: () => apiClient.get<{ id: string; username: string; email: string; role: string; created_at: string }[]>('/users'),
  create: (body: { username: string; email: string; password: string; role: string }) =>
    apiClient.post('/users', body),
  update: (id: string, body: { username?: string; email?: string; password?: string; role?: string }) =>
    apiClient.put(`/users/${id}`, body),
  delete: (id: string) => apiClient.delete(`/users/${id}`),
}
