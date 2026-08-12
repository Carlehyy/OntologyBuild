import type { Edge } from '@xyflow/react'
import type {
  MappingDataset,
  MappingObjectType,
} from '../detail/mapping/mapping-data'
import { typesCompatible } from './mapping-types.ts'

/** 面板确认后的采纳项：一个数据集 → 一个对象实体 + 勾选的字段对 */
export interface SuggestionAcceptance {
  datasetId: string
  objectId: string
  fields: Array<{ column: string; property: string }>
}

export interface SuggestionSkip {
  datasetId: string
  column: string
  reason: string
}

export interface SuggestionAdditions {
  /** 不在画布上、需要补放的数据集节点 */
  datasetIdsToAdd: string[]
  /** 不在画布上、需要补放的对象节点 */
  objectIdsToAdd: string[]
  edgesToAdd: Edge[]
  skipped: SuggestionSkip[]
}

/**
 * 把采纳的建议转换为画布节点/连线增量。去重与校验规则和手动连线
 * （MappingConfigurationPage.onConnect）完全一致：目标属性已被占用、同一字段
 * 重复连到同一对象、类型不兼容一律跳过并给出原因。纯函数，便于单测。
 */
export function buildSuggestionAdditions(args: {
  accepted: SuggestionAcceptance[]
  nodeIds: Set<string>
  existingEdges: Array<Pick<Edge, 'source' | 'target' | 'sourceHandle' | 'targetHandle'>>
  datasetById: Map<string, MappingDataset>
  objectById: Map<string, MappingObjectType>
}): SuggestionAdditions {
  const { accepted, nodeIds, existingEdges, datasetById, objectById } = args
  const datasetIdsToAdd: string[] = []
  const objectIdsToAdd: string[] = []
  const edgesToAdd: Edge[] = []
  const skipped: SuggestionSkip[] = []
  const occupiedTargets = new Set(
    existingEdges.map(edge => `${edge.target}:${edge.targetHandle}`),
  )
  const wiredSources = new Set(
    existingEdges.map(edge => `${edge.source}:${edge.target}:${edge.sourceHandle}`),
  )

  for (const item of accepted) {
    const dataset = datasetById.get(item.datasetId)
    const object = objectById.get(item.objectId)
    const datasetNodeId = `dataset:${item.datasetId}`
    const objectNodeId = `object:${item.objectId}`
    if (!dataset || !object) {
      for (const field of item.fields) {
        skipped.push({
          datasetId: item.datasetId,
          column: field.column,
          reason: '数据资产或本体对象已不存在',
        })
      }
      continue
    }
    if (!nodeIds.has(datasetNodeId) && !datasetIdsToAdd.includes(dataset.id)) {
      datasetIdsToAdd.push(dataset.id)
    }
    if (!nodeIds.has(objectNodeId) && !objectIdsToAdd.includes(object.id)) {
      objectIdsToAdd.push(object.id)
    }
    for (const field of item.fields) {
      const column = dataset.columns.find(entry => entry.name === field.column)
      const property = object.properties.find(
        entry => entry.name === field.property && entry.source !== 'computed' && !entry.computed,
      )
      if (!column || !property) {
        skipped.push({
          datasetId: item.datasetId, column: field.column, reason: '字段或属性已不存在',
        })
        continue
      }
      if (!typesCompatible(column.type, property.type)) {
        skipped.push({
          datasetId: item.datasetId, column: field.column, reason: '源字段与目标属性类型不兼容',
        })
        continue
      }
      const targetKey = `${objectNodeId}:${field.property}`
      const sourceKey = `${datasetNodeId}:${objectNodeId}:${field.column}`
      if (occupiedTargets.has(targetKey) || wiredSources.has(sourceKey)) {
        skipped.push({
          datasetId: item.datasetId, column: field.column, reason: '目标属性已有连线',
        })
        continue
      }
      occupiedTargets.add(targetKey)
      wiredSources.add(sourceKey)
      edgesToAdd.push({
        id: `suggest:${dataset.id}:${object.id}:${field.column}:${field.property}`,
        source: datasetNodeId,
        target: objectNodeId,
        sourceHandle: field.column,
        targetHandle: field.property,
        type: 'default',
      })
    }
  }
  return { datasetIdsToAdd, objectIdsToAdd, edgesToAdd, skipped }
}
