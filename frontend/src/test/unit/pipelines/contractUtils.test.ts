import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  buildInitialColumnDefinitions,
  normalizeContractType,
} from '../../../pages/pipelines/contractUtils.ts'

describe('normalizeContractType', () => {
  it('兼容旧词表并拒绝未知类型', () => {
    assert.equal(normalizeContractType('int'), 'integer')
    assert.equal(normalizeContractType('datetime'), 'timestamp')
    assert.equal(normalizeContractType('json'), 'json')
    assert.equal(normalizeContractType('uuid'), 'string')
  })
})

describe('buildInitialColumnDefinitions', () => {
  it('根据试运行样本推荐字段类型', () => {
    const definitions = buildInitialColumnDefinitions(
      ['id', 'amount', 'active', 'created_at', 'payload'],
      [{
        id: 42,
        amount: 100.5,
        active: true,
        created_at: '2026-08-16T10:30:00Z',
        payload: { source: 'erp' },
      }],
    )

    assert.deepEqual(
      definitions.map(definition => definition.field_type),
      ['integer', 'float', 'boolean', 'timestamp', 'json'],
    )
  })

  it('保留人工契约，并将湖中主键自动设为非空', () => {
    const definitions = buildInitialColumnDefinitions(
      ['id', 'amount'],
      [{ id: 1, amount: 10.5 }],
      [{
        source_key: 'amount',
        field_key: 'total_amount',
        field_name: '订单金额',
        field_type: 'str',
        is_primary_key: false,
        nullable: true,
      }],
      new Set(['id']),
    )

    assert.equal(definitions[0].field_type, 'integer')
    assert.equal(definitions[0].is_primary_key, true)
    assert.equal(definitions[0].nullable, false)
    assert.deepEqual(definitions[1], {
      source_key: 'amount',
      field_key: 'total_amount',
      field_name: '订单金额',
      field_type: 'string',
      is_primary_key: false,
      nullable: true,
    })
  })
})
