import { apiClientV2 } from '@/api/client'

export interface OntologyTrialIssue {
  code?: string
  kind?: string
  id?: string
  name?: string
  message: string
}

export interface OntologyTrialRun {
  id: string
  status: 'running' | 'passed' | 'failed' | 'stale'
  result?: {
    counts?: { objects?: number; links?: number; facts?: number; datasets?: number }
    errors?: OntologyTrialIssue[]
    warnings?: OntologyTrialIssue[]
    actionsExecuted?: number
  }
  impact_hash?: string
  created_at?: string
}

export interface OntologyVersionNode {
  id: string
  version_number: string
  version_label?: string
  description?: string
  parent_version_id?: string | null
  base_release_id?: string | null
  promoted_from_id?: string | null
  node_kind: 'release' | 'draft'
  lifecycle_status: 'editing' | 'trial_ready' | 'released' | 'superseded'
  revision: number
  latest_trial?: OntologyTrialRun | null
  created_at?: string
  published_at?: string
}

export interface OntologyVersionTree {
  current_release_id: string
  current_release_number: string
  current_release_version: string
  versions: OntologyVersionNode[]
}

export interface OntologyImpactReport {
  impactHash: string
  baseOutdated: boolean
  breakingCount: number
  breaking: Array<{ message: string }>
  total: { added: number; modified: number; deleted: number }
  releaseReadiness?: {
    ready: boolean
    blockingCount: number
    errors: OntologyReleaseGateIssue[]
    trialRunId?: string | null
    repairStrategy?: 'create_draft' | 'rebase' | null
    repairSourceVersionId?: string | null
  }
}

export interface OntologyReleaseGateIssue {
  code: string
  kind: string
  id?: string
  name?: string
  message: string
  field?: string
  targetId?: string
  targetName?: string
}

export const ontologyVersionApi = {
  tree: (ontologyId: string) =>
    apiClientV2.get<OntologyVersionTree>(`/ontologies/${ontologyId}/version-tree`),
  createDraft: (ontologyId: string, sourceVersionId: string, body: {
    versionLabel?: string
    description?: string
  }) => apiClientV2.post<OntologyVersionNode>(
    `/ontologies/${ontologyId}/versions/${sourceVersionId}/drafts`, body),
  deleteVersion: (ontologyId: string, versionId: string) =>
    apiClientV2.delete<{ id: string; version_number: string }>(
      `/ontologies/${ontologyId}/versions/${versionId}`),
  runTrial: (ontologyId: string, versionId: string) =>
    apiClientV2.post<OntologyTrialRun>(
      `/ontologies/${ontologyId}/versions/${versionId}/trial-runs`, {}),
  impact: (ontologyId: string, versionId: string) =>
    apiClientV2.get<OntologyImpactReport>(
      `/ontologies/${ontologyId}/versions/${versionId}/impact`),
  promote: (ontologyId: string, versionId: string, body: {
    trialRunId: string
    impactHash: string
    versionLabel?: string
  }) => apiClientV2.post<OntologyVersionNode>(
    `/ontologies/${ontologyId}/versions/${versionId}/promote`, body),
}
