/**
 * 场景建模助手类型 — 对齐 backend/app/scenes/assistant_service。
 */
import type { SceneStatus } from './scene'

export interface ConversationSummary {
  id: string
  scene_id: string | null
  title: string
  model_config_id: string | null
  created_at: string | null
  updated_at: string | null
}

export interface ConversationMessage {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  content: string
  status: 'complete' | 'error'
  version_no: number | null
  created_at: string | null
}

export interface ValidationIssue {
  path: string
  message: string
}

export type SceneSseEvent =
  | { event: 'meta'; data: { conversation_id: string; scene_id: string | null } }
  | { event: 'text'; data: { content: string } }
  | { event: 'scene_updated'; data: { scene_id: string; name: string; version_no: number; status: SceneStatus; note: string } }
  | { event: 'error'; data: { code: string; message: string; issues?: ValidationIssue[] } }
  | { event: 'done'; data: Record<string, never> }
