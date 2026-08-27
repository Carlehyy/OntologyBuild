/**
 * 三维场景领域类型 — 对齐 backend/app/scenes（snake_case，与 world_model 同风格）。
 *
 * 场景定义是声明式 JSON 三件套超集（对象/关系/数据绑定），
 * 产物与渲染引擎分离：本文件只描述「存储形态」，引擎包形态见 lib/scene3d。
 */

export type SceneStatus = 'draft' | 'published'
export type SceneLogLevel = 'info' | 'normal' | 'warning' | 'alarm'
export type SceneVersionSource = 'manual' | 'assistant' | 'clone'

export interface SceneSummary {
  id: string
  name: string
  description: string
  icon: string
  status: SceneStatus
  /** 最新草稿版本号；0 = 尚无任何定义 */
  current_version_no: number
  /** 列表卡片指标：版本总数与运行日志条数（仅列表接口聚合返回） */
  version_count?: number
  runtime_log_count?: number
  /** 最近一次发布冻结的版本号；null = 从未发布 */
  published_version_no: number | null
  created_by: string | null
  created_at: string | null
  updated_at: string | null
}

export interface SceneDetail extends SceneSummary {
  version_count: number
}

export interface SceneListResp {
  items: SceneSummary[]
  total: number
}

export interface SceneVersionMeta {
  id: string
  scene_id: string
  version_no: number
  source: SceneVersionSource
  note: string
  created_by: string | null
  created_at: string | null
  definition?: unknown
}

export interface RuntimeLogItem {
  id: string
  scene_id: string
  level: SceneLogLevel
  object_id: string | null
  event_key: string
  message: string
  payload: Record<string, unknown>
  occurred_at: string | null
  recorded_at: string | null
}

// ---------- 场景定义 DSL ----------

export interface SceneObjectLayout {
  x: number
  z: number
  w: number
  d: number
  h: number
}

export type SceneObjectType = 'office' | 'tower' | 'warehouse' | 'podium' | 'plant'

export interface SceneObjectDef {
  id: string
  label: string
  type: SceneObjectType
  layout: SceneObjectLayout
  extras?: string[]
  /** 可选：该对象挂载的本体概念 id */
  ontology_concept_id?: string
  info?: {
    desc?: string
    metrics?: [string, string][]
  }
  beacon?: boolean
}

export interface SceneRelationDef {
  from: string
  to: string
  kind?: 'flow' | 'dependency' | 'hierarchy'
}

export interface BindingRuleDef {
  when: string
  status: 'normal' | 'warning' | 'alarm'
  message?: string
}

export interface DataBindingDef {
  target: string
  source: string
  path?: string
  metrics?: [string, string][]
  rules: BindingRuleDef[]
}

/** 场景事件：key 为 kebab-case 且全场景唯一，可关联一个对象。 */
export interface SceneEventDef {
  key: string
  label: string
  objectId?: string
  description?: string
}

export interface SceneDefinition {
  meta: { id: string; name: string; version: string }
  stage?: {
    background?: string
    camera?: { pos: [number, number, number]; target: [number, number, number]; fov?: number }
    floor?: { size?: number; gridCell?: number }
    ambience?: Record<string, unknown>
  }
  objects: SceneObjectDef[]
  relations?: SceneRelationDef[]
  dataBindings?: DataBindingDef[]
  /** 可选事件清单（≤50 条） */
  events?: SceneEventDef[]
  sources?: Record<string, unknown>
}

/** 前端引擎上报的绑定规则命中/恢复事件（运行日志的来源）。 */
export interface RuleHit {
  objectId: string | null
  level: 'normal' | 'warning' | 'alarm'
  message: string
  path?: string
  value?: number | null
  occurredAt: string
}
