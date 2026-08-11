import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  buildSuggestionAdditions,
  type SuggestionAcceptance,
} from '../../../pages/ontologies/mapping/suggestion-apply.ts'
import type {
  MappingDataset,
  MappingObjectType,
} from '../../../pages/ontologies/detail/mapping/mapping-data.ts'

const dataset: MappingDataset = {
  id: 'ds-1',
  name: '客户表',
  rows: 10,
  quality: null,
  primaryKeyColumns: ['cust_id'],
  source: 'manual',
  sourceLabel: '人工数据集',
  reviewStatus: null,
  columns: [
    { name: 'cust_id', display_name: '客户编号', type: 'string', nullable: false, is_primary_key: true, sample_values: [] },
    { name: 'cust_name', display_name: '客户名称', type: 'string', nullable: true, is_primary_key: false, sample_values: [] },
    { name: 'age', display_name: '年龄', type: 'integer', nullable: true, is_primary_key: false, sample_values: [] },
  ],
}

const object: MappingObjectType = {
  id: 'ot-customer',
  name: 'Customer',
  displayName: '客户',
  primaryKey: 'customer_id',
  properties: [
    { id: 'p-id', name: 'customer_id', displayName: '客户编号', type: 'string' },
    { id: 'p-name', name: 'customer_name', displayName: '客户名称', type: 'string' },
  ],
}

function build(overrides: Partial<Parameters<typeof buildSuggestionAdditions>[0]> = {}, accepted: SuggestionAcceptance[] = []) {
  return buildSuggestionAdditions({
    accepted,
    nodeIds: new Set<string>(),
    existingEdges: [],
    datasetById: new Map([[dataset.id, dataset]]),
    objectById: new Map([[object.id, object]]),
    ...overrides,
  })
}

describe('buildSuggestionAdditions', () => {
  it('为采纳的字段生成连线并补放缺失节点', () => {
    const result = build({}, [{
      datasetId: 'ds-1',
      objectId: 'ot-customer',
      fields: [
        { column: 'cust_id', property: 'customer_id' },
        { column: 'cust_name', property: 'customer_name' },
      ],
    }])
    assert.deepEqual(result.datasetIdsToAdd, ['ds-1'])
    assert.deepEqual(result.objectIdsToAdd, ['ot-customer'])
    assert.equal(result.edgesToAdd.length, 2)
    assert.equal(result.edgesToAdd[0].source, 'dataset:ds-1')
    assert.equal(result.edgesToAdd[0].target, 'object:ot-customer')
    assert.equal(result.edgesToAdd[0].sourceHandle, 'cust_id')
    assert.equal(result.edgesToAdd[0].targetHandle, 'customer_id')
    assert.equal(result.skipped.length, 0)
  })

  it('类型不兼容的建议被跳过并给出原因', () => {
    const result = build({}, [{
      datasetId: 'ds-1',
      objectId: 'ot-customer',
      fields: [{ column: 'age', property: 'customer_name' }],
    }])
    assert.equal(result.edgesToAdd.length, 0)
    assert.equal(result.skipped.length, 1)
    assert.match(result.skipped[0].reason, /类型不兼容/)
  })

  it('目标属性已有连线时不重复生成', () => {
    const result = build({
      nodeIds: new Set(['dataset:ds-1', 'object:ot-customer']),
      existingEdges: [{
        source: 'dataset:ds-1',
        target: 'object:ot-customer',
        sourceHandle: 'cust_id',
        targetHandle: 'customer_id',
      }],
    }, [{
      datasetId: 'ds-1',
      objectId: 'ot-customer',
      fields: [{ column: 'cust_id', property: 'customer_id' }],
    }])
    assert.equal(result.edgesToAdd.length, 0)
    assert.equal(result.datasetIdsToAdd.length, 0)
    assert.equal(result.objectIdsToAdd.length, 0)
    assert.match(result.skipped[0].reason, /已有连线/)
  })

  it('同一批次内重复建议同一目标属性只保留第一条', () => {
    const result = build({}, [{
      datasetId: 'ds-1',
      objectId: 'ot-customer',
      fields: [
        { column: 'cust_id', property: 'customer_id' },
        { column: 'cust_name', property: 'customer_id' },
      ],
    }])
    assert.equal(result.edgesToAdd.length, 1)
    assert.equal(result.edgesToAdd[0].sourceHandle, 'cust_id')
    assert.equal(result.skipped.length, 1)
  })

  it('计算属性不作为可映射目标', () => {
    const computedObject: MappingObjectType = {
      ...object,
      id: 'ot-computed',
      properties: [
        ...object.properties,
        { id: 'p-c', name: 'full_label', displayName: '完整标签', type: 'string', source: 'computed' },
      ],
    }
    const result = build({
      objectById: new Map([[computedObject.id, computedObject]]),
    }, [{
      datasetId: 'ds-1',
      objectId: 'ot-computed',
      fields: [{ column: 'cust_name', property: 'full_label' }],
    }])
    assert.equal(result.edgesToAdd.length, 0)
    assert.match(result.skipped[0].reason, /已不存在/)
  })
})
