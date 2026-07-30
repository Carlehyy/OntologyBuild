import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  coerceSentinelConstant,
  compileSentinelCondition,
  compileSentinelConditionRow,
  sentinelOperatorsForType,
  sentinelParameterMode,
} from '../../../palantir-graph/components/panels/sentinelDefinitionCompiler.ts'
import { nextSentinelAlias } from '../../../palantir-graph/components/panels/sentinelDefinitionModel.ts'


describe('Sentinel definition compiler', () => {
  it('uses bracket notation for every property, including Unicode punctuation', () => {
    assert.equal(
      compileSentinelConditionRow({
        leftAlias: '订单',
        leftProp: '状态。码',
        op: '==',
        rightKind: 'value',
        rightValue: '待审',
      }),
      '订单["状态。码"] == "待审"',
    )
  })

  it('compiles numeric, text, contains, and property comparisons deterministically', () => {
    assert.equal(
      compileSentinelCondition([
        {
          leftAlias: 'a',
          leftProp: 'amount',
          op: '>=',
          rightKind: 'value',
          rightValue: '12.5',
        },
        {
          leftAlias: 'a',
          leftProp: 'tags',
          op: 'contains',
          rightKind: 'value',
          rightValue: 'urgent',
        },
        {
          leftAlias: 'a',
          leftProp: 'owner_id',
          op: '==',
          rightKind: 'property',
          rightAlias: 'b',
          rightProp: 'id',
        },
      ], 'and'),
      (
        'a["amount"] >= 12.5 and "urgent" in a["tags"] '
        + 'and a["owner_id"] == b["id"]'
      ),
    )
  })

  it('omits incomplete rows instead of emitting an invalid expression', () => {
    assert.equal(
      compileSentinelConditionRow({
        leftAlias: 'a',
        leftProp: '',
        op: '==',
        rightKind: 'value',
        rightValue: '1',
      }),
      null,
    )
  })

  it('selects operators from the declared property type', () => {
    assert.deepEqual(
      sentinelOperatorsForType('integer'),
      ['>', '>=', '<', '<=', '==', '!='],
    )
    assert.deepEqual(sentinelOperatorsForType('boolean'), ['==', '!='])
    assert.deepEqual(
      sentinelOperatorsForType('string'),
      ['==', '!=', 'contains'],
    )
  })

  it('classifies parameter sources without inventing an editable mode', () => {
    assert.equal(sentinelParameterMode(undefined), 'default')
    assert.equal(sentinelParameterMode('hello'), 'constant')
    assert.equal(sentinelParameterMode('{{ object.name }}'), 'template')
    assert.equal(sentinelParameterMode({ sourceType: 'match-property' }), 'property')
    assert.equal(sentinelParameterMode({ source: 'target_id' }), 'primary_id')
    assert.equal(sentinelParameterMode({ source: 'event_property' }), 'event')
    assert.equal(sentinelParameterMode({ source: 'expression' }), 'advanced')
  })

  it('coerces valid constants while preserving invalid input for backend validation', () => {
    assert.equal(coerceSentinelConstant('12.5', 'number'), 12.5)
    assert.equal(coerceSentinelConstant('not-a-number', 'number'), 'not-a-number')
    assert.equal(coerceSentinelConstant('true', 'boolean'), true)
    assert.deepEqual(coerceSentinelConstant('{"ok":true}', 'json'), { ok: true })
    assert.equal(coerceSentinelConstant('{broken', 'json'), '{broken')
  })

  it('allocates the first unused stable binding alias', () => {
    assert.equal(nextSentinelAlias([{ alias: 'a' }, { alias: 'c' }]), 'b')
  })
})
