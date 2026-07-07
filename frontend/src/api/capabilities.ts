/** 能力注册中心 API — /api/v2/capabilities/*（P1: skills；P2 扩展 MCP） */
import { apiClientV2 } from './client'

export type SkillScope = 'exploration' | 'agent'

export interface CapSkill {
  id: string
  name: string
  displayName: string
  description: string
  instructions: string
  scopes: SkillScope[]
  enabled: boolean
  builtin: boolean
  createdAt: string
  updatedAt: string
}

export interface SkillCreate {
  name: string
  displayName: string
  description?: string
  instructions?: string
  scopes?: SkillScope[]
  enabled?: boolean
}

export type SkillUpdate = Partial<Omit<SkillCreate, 'name'>>

export const capabilitiesApi = {
  skills: (scope?: SkillScope) =>
    apiClientV2.get<CapSkill[]>('/capabilities/skills', { params: scope ? { scope } : {} }),
  createSkill: (body: SkillCreate) => apiClientV2.post<CapSkill>('/capabilities/skills', body),
  updateSkill: (id: string, body: SkillUpdate) =>
    apiClientV2.put<CapSkill>(`/capabilities/skills/${id}`, body),
  deleteSkill: (id: string) => apiClientV2.delete(`/capabilities/skills/${id}`),
}
