/**
 * 助手评估 API — /api/v1/assistant-evaluation
 *
 * 基于 OpenJudge 的助手会话质量评估（仅 admin）。
 * apiClient 已解包 {data} 信封。
 */
import { apiClient } from './client'

export interface AssistantMeta {
  key: string
  label: string
  description: string
  conversation_count: number
  supported_dimension_keys: string[]
}

export interface DimensionMeta {
  key: string
  label: string
  kind: 'llm' | 'code'
  description: string
}

export interface EvalMeta {
  engine: string
  assistants: AssistantMeta[]
  dimension_catalog: DimensionMeta[]
  base_dimension_keys: string[]
}

export interface ConversationRef {
  id: string
  title: string
  created_at: string | null
  updated_at: string | null
  message_count: number
}

export interface ConversationPage {
  total: number
  items: ConversationRef[]
}

export type EvalTaskStatus = 'queued' | 'running' | 'success' | 'error'

export interface EvalDimensionStat {
  label: string
  avg: number
  min: number
  max: number
  count: number
}

export interface EvalSummary {
  overall?: number | null
  dimensions: Record<string, EvalDimensionStat>
  badcase_conversation_ids: string[]
  evaluated: number
  failed: number
  /** 无适用维度或内容不完整的会话数（非失败） */
  skipped?: number
  llm_calls: number
  engine: string
}

export interface EvalRubricSnapshot {
  name: string
  rubrics: string
  min_score: number
  max_score: number
}

export interface EvalRubric {
  id: string
  name: string
  task_description: string
  rubrics: string
  min_score: number
  max_score: number
  judge_model_name: string
  created_at: string | null
}

export interface CreateRubricBody {
  name: string
  task_description: string
  sample_queries?: string[]
  min_score: number
  max_score: number
  model_config_id?: string | null
}

export interface TrendPoint {
  id: string
  title: string
  created_at: string | null
  overall: number | null
  dimensions: Record<string, EvalDimensionStat>
  judge_model_name: string
}

export interface EvalItemTrace {
  conversation_id: string
  conversation_title: string
  query: string
  response: string
  openai_messages: Array<Record<string, unknown>>
  actions: Array<Record<string, unknown>>
  tool_error_count: number
}

export interface EvalTask {
  id: string
  assistant_key: string
  assistant_label: string
  title: string
  status: EvalTaskStatus
  params: {
    mode?: string
    dimension_keys?: string[]
    conversation_ids?: string[]
    rubric?: EvalRubricSnapshot
  }
  judge_model_name: string
  conversation_count: number
  completed_conversations: number
  summary: EvalSummary
  error: string | null
  created_at: string | null
  finished_at: string | null
  duration_ms: number | null
}

export interface EvalItemReason {
  score?: number | null
  reason?: string
}

export interface EvalTaskItem {
  id: string
  conversation_id: string
  conversation_title: string
  overall_score: number | null
  scores: Record<string, number>
  reasons: Record<string, EvalItemReason>
  flags: { loop_detected?: boolean; tool_error_count?: number; low_dims?: string[]; engine_error?: string }
  root_cause: string
  created_at: string | null
}

export interface EvalTaskDetail extends EvalTask {
  items: EvalTaskItem[]
}

export interface CreateEvalTaskBody {
  assistant_key: string
  conversation_ids: string[]
  sample_size?: number
  sample_days?: number
  dimension_keys: string[]
  model_config_id?: string | null
  rubric_id?: string | null
}

export const assistantEvaluationApi = {
  meta: () => apiClient.get<EvalMeta>('/assistant-evaluation/meta'),
  rubrics: () => apiClient.get<EvalRubric[]>('/assistant-evaluation/rubrics'),
  createRubric: (body: CreateRubricBody) =>
    apiClient.post<EvalRubric>('/assistant-evaluation/rubrics', body),
  deleteRubric: (rubricId: string) =>
    apiClient.delete(`/assistant-evaluation/rubrics/${rubricId}`),
  trend: (assistantKey: string, limit = 12) =>
    apiClient.get<TrendPoint[]>('/assistant-evaluation/trend', {
      params: { assistant_key: assistantKey, limit },
    }),
  itemTrace: (taskId: string, itemId: string) =>
    apiClient.get<EvalItemTrace>(`/assistant-evaluation/tasks/${taskId}/items/${itemId}/trace`),
  conversations: (assistantKey: string, limit = 50, offset = 0) =>
    apiClient.get<ConversationPage>(`/assistant-evaluation/${assistantKey}/conversations`, {
      params: { limit, offset },
    }),
  createTask: (body: CreateEvalTaskBody) =>
    apiClient.post<EvalTask>('/assistant-evaluation/tasks', body),
  tasks: (assistantKey?: string) =>
    apiClient.get<EvalTask[]>('/assistant-evaluation/tasks', {
      params: assistantKey ? { assistant_key: assistantKey } : {},
    }),
  taskDetail: (taskId: string) =>
    apiClient.get<EvalTaskDetail>(`/assistant-evaluation/tasks/${taskId}`),
  deleteTask: (taskId: string) => apiClient.delete(`/assistant-evaluation/tasks/${taskId}`),
  exportReport: async (taskId: string) => {
    // 必须走鉴权请求下载（纯链接不带 Bearer token）
    const blob = (await apiClient.get(`/assistant-evaluation/tasks/${taskId}/export`, {
      responseType: 'blob',
    })) as unknown as Blob
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `assistant-eval-${taskId.slice(0, 8)}.md`
    a.style.display = 'none'
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
  },

  // ---------------------------------------------------------------- 数据飞轮（M1-M3）

  benchmarks: (assistantKey?: string) =>
    apiClient.get<BenchmarkSet[]>('/assistant-evaluation/benchmarks', {
      params: assistantKey ? { assistant_key: assistantKey } : {},
    }),
  benchmarkDetail: (setId: string) =>
    apiClient.get<BenchmarkSetDetail>(`/assistant-evaluation/benchmarks/${setId}`),
  createBenchmark: (body: CreateBenchmarkBody) =>
    apiClient.post<BenchmarkSet>('/assistant-evaluation/benchmarks', body),
  createBenchmarkFromTask: (body: CreateBenchmarkFromTaskBody) =>
    apiClient.post<BenchmarkSet>('/assistant-evaluation/benchmarks/from-task', body),
  addBenchmarkItems: (setId: string, items: BenchmarkItemIn[]) =>
    apiClient.post<BenchmarkSet>(`/assistant-evaluation/benchmarks/${setId}/items`, { items }),
  removeBenchmarkItem: (setId: string, itemId: string) =>
    apiClient.delete(`/assistant-evaluation/benchmarks/${setId}/items/${itemId}`),
  deleteBenchmark: (setId: string) =>
    apiClient.delete(`/assistant-evaluation/benchmarks/${setId}`),

  calibrations: (assistantKey?: string) =>
    apiClient.get<Calibration[]>('/assistant-evaluation/calibrations', {
      params: assistantKey ? { assistant_key: assistantKey } : {},
    }),
  createCalibration: (body: CreateCalibrationBody) =>
    apiClient.post<Calibration>('/assistant-evaluation/calibrations', body),
  deleteCalibration: (id: string) =>
    apiClient.delete(`/assistant-evaluation/calibrations/${id}`),

  timeline: (params?: { assistant_key?: string; ref_type?: string; ref_id?: string; limit?: number }) =>
    apiClient.get<TimelineEvent[]>('/assistant-evaluation/timeline', { params }),

  proposals: (ontologyId?: string) =>
    apiClient.get<Proposal[]>('/assistant-evaluation/proposals', {
      params: ontologyId ? { ontology_id: ontologyId } : {},
    }),
  createProposal: (body: CreateProposalBody) =>
    apiClient.post<Proposal>('/assistant-evaluation/proposals', body),
  applyProposal: (proposalId: string) =>
    apiClient.post<ProfileVersion>(`/assistant-evaluation/proposals/${proposalId}/apply`),

  experiments: (ontologyId?: string) =>
    apiClient.get<Experiment[]>('/assistant-evaluation/experiments', {
      params: ontologyId ? { ontology_id: ontologyId } : {},
    }),
  experimentDetail: (experimentId: string, arm?: 'baseline' | 'trial') =>
    apiClient.get<ExperimentDetail>(
      `/assistant-evaluation/experiments/${experimentId}`,
      { params: arm ? { arm } : {} },
    ),
  createExperiment: (body: CreateExperimentBody) =>
    apiClient.post<Experiment>('/assistant-evaluation/experiments', body),

  profileVersions: (ontologyId: string) =>
    apiClient.get<ProfileVersion[]>('/assistant-evaluation/profile-versions', {
      params: { ontology_id: ontologyId },
    }),
  rollbackVersion: (versionId: string, reason: string) =>
    apiClient.post<ProfileVersion>(`/assistant-evaluation/profile-versions/${versionId}/rollback`, { reason }),

  autopilotConfig: (ontologyId: string) =>
    apiClient.get<AutopilotConfig | null>(
      `/assistant-evaluation/autopilot/config/${ontologyId}`),
  saveAutopilotConfig: (ontologyId: string, body: SaveAutopilotConfigBody) =>
    apiClient.put<AutopilotConfig>(
      `/assistant-evaluation/autopilot/config/${ontologyId}`, body),
  triggerAutopilot: (ontologyId: string) =>
    apiClient.post<{ dispatched: boolean }>(
      `/assistant-evaluation/autopilot/config/${ontologyId}/trigger`),
}

// ---------------------------------------------------------------- 数据飞轮类型（M1-M3）

export interface BenchmarkItem {
  id: string
  conversation_id: string
  conversation_title: string
  split: 'train' | 'heldout'
  origin: 'manual' | 'badcase' | 'task'
  created_at: string | null
}

export interface BenchmarkSet {
  id: string
  assistant_key: string
  ontology_id: string | null
  name: string
  description: string
  source_task_id: string | null
  item_count: number
  train_count: number
  heldout_count: number
  created_at: string | null
}

export interface BenchmarkSetDetail extends BenchmarkSet {
  items: BenchmarkItem[]
}

export interface BenchmarkItemIn {
  conversation_id: string
  split?: 'train' | 'heldout'
  origin?: 'manual' | 'badcase' | 'task'
}

export interface CreateBenchmarkBody {
  assistant_key: string
  name: string
  description?: string
  ontology_id?: string | null
  items: BenchmarkItemIn[]
}

export interface CreateBenchmarkFromTaskBody {
  task_id: string
  name?: string | null
  include: 'badcase' | 'all'
  description?: string
}

export interface CalibrationPerDim {
  noise: number
  conversations: number
  samples: number
}

export interface CalibrationResult {
  repeats: number
  per_dim: Record<string, CalibrationPerDim>
  overall_noise: number
  scored_conversations: number
}

export interface Calibration {
  id: string
  assistant_key: string
  status: EvalTaskStatus
  params: { conversation_ids?: string[]; dimension_keys?: string[]; repeats?: number; benchmark_set_id?: string }
  judge_model_name: string
  result: CalibrationResult
  error: string | null
  created_at: string | null
  finished_at: string | null
  duration_ms: number | null
}

export interface CreateCalibrationBody {
  assistant_key: string
  conversation_ids?: string[]
  benchmark_set_id?: string | null
  repeats?: number
  dimension_keys?: string[]
  model_config_id?: string | null
}

export interface TimelineEvent {
  id: string
  assistant_key: string | null
  event_type: string
  actor: 'admin' | 'system' | 'autopilot'
  actor_user_id: string | null
  ref_type: string | null
  ref_id: string | null
  detail: Record<string, unknown>
  created_at: string | null
}

export type ProposalStatus = 'draft' | 'validated' | 'applied' | 'rolled_back' | 'superseded'

export interface Proposal {
  id: string
  ontology_id: string
  assistant_key: string
  type: 'prompt_patch' | 'model_swap'
  title: string
  rationale: string
  payload: {
    system_prompt_extra?: string
    base_system_prompt_extra?: string
    model_config_id?: string
    model_name?: string
    base_model_config_id?: string | null
  }
  evidence: Record<string, unknown>
  status: ProposalStatus
  created_by: string | null
  created_at: string | null
  updated_at: string | null
}

export interface CreateProposalBody {
  ontology_id: string
  type: 'prompt_patch' | 'model_swap'
  title?: string
  rationale?: string
  payload: { system_prompt_extra?: string; model_config_id?: string }
  evidence?: Record<string, unknown>
}

export interface ExperimentGate {
  passed: boolean
  heldout_delta: number | null
  threshold: number
  noise_floor: number
  effective_threshold: number
}

export interface ExperimentArmStats {
  overall: number | null
  per_dim: Record<string, { avg: number; min: number; count: number }>
  scored: number
  failed: number
}

export interface ExperimentResult {
  baseline: ExperimentArmStats
  trial: ExperimentArmStats
  by_split: Record<string, { baseline: number | null; trial: number | null; delta: number | null }>
  gate: ExperimentGate
}

export interface Experiment {
  id: string
  ontology_id: string
  proposal_id: string
  benchmark_set_id: string | null
  status: EvalTaskStatus
  params: Record<string, unknown>
  judge_model_name: string
  result: ExperimentResult
  error: string | null
  created_at: string | null
  finished_at: string | null
  duration_ms: number | null
}

export interface ExperimentItem {
  id: string
  arm: 'baseline' | 'trial'
  conversation_id: string
  conversation_title: string
  split: string
  overall_score: number | null
  scores: Record<string, number>
  flags: { engine_error?: string }
  transcript: { query?: string; response?: string; tool_error_count?: number }
  created_at: string | null
}

export interface ExperimentDetail extends Experiment {
  items: ExperimentItem[]
}

export interface CreateExperimentBody {
  proposal_id: string
  benchmark_set_id: string
  dimension_keys?: string[]
  threshold?: number
  model_config_id?: string | null
}

export interface ProfileVersion {
  id: string
  ontology_id: string
  version: number
  snapshot: Record<string, unknown>
  source: { proposal_id?: string; trigger?: string }
  status: 'active' | 'superseded' | 'rolled_back'
  pre_apply_stats: Record<string, unknown>
  verified: boolean
  created_by: string | null
  created_at: string | null
}

export interface AutopilotConfig {
  id: string
  ontology_id: string
  enabled: boolean
  run_at: string
  benchmark_set_id: string | null
  dimension_keys: string[]
  model_config_id: string | null
  threshold: number
  max_applies_per_week: number
  sample_days: number
  suspended: boolean
  suspend_reason: string
  last_dispatched_at: string | null
  last_cycle_at: string | null
  last_cycle_status: string
  consecutive_failures: number
  created_at: string | null
  updated_at: string | null
}

export interface SaveAutopilotConfigBody {
  enabled: boolean
  run_at: string
  benchmark_set_id?: string | null
  dimension_keys?: string[]
  model_config_id?: string | null
  threshold?: number
  max_applies_per_week?: number
  sample_days?: number
}
