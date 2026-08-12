import { apiClientV2 } from '@/api/client'

/** 映射建议来源：knowledge=历史映射复用（数据飞轮），rule=名称规则，llm=LLM 概念化裁决 */
export type MappingSuggestionSource = 'knowledge' | 'rule' | 'llm'

export interface MappingFieldSuggestion {
  column: string
  property: string
  verdict: 'match' | 'unsure'
  confidence: number
  reason: string
  source: MappingSuggestionSource
}

export interface MappingSkippedColumn {
  column: string
  reason: string
}

export interface DatasetMappingSuggestion {
  datasetId: string
  datasetName: string
  /** 建议配对的对象实体 id（null=未找到可信配对，需在面板人工选择） */
  objectTypeId: string | null
  pairingVerdict: 'match' | 'unsure'
  pairingReason: string
  primaryKeyColumn: string | null
  /** 该数据集在草稿中已有映射时的既有对象 id */
  existingObjectTypeId: string | null
  fieldMappings: MappingFieldSuggestion[]
  skippedColumns: MappingSkippedColumn[]
  error: string | null
}

export interface MappingSuggestionResponse {
  llmAvailable: boolean
  /** 命中历史映射知识库的字段建议条数（飞轮可见性指标） */
  knowledgeHits: number
  suggestions: DatasetMappingSuggestion[]
}

export function fetchMappingSuggestions(
  ontologyId: string,
  versionId: string,
  datasetIds: string[],
): Promise<MappingSuggestionResponse> {
  return apiClientV2.post(
    `/ontologies/${ontologyId}/versions/${versionId}/mapping-suggestions`,
    { datasetIds },
  )
}
