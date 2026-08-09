import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  inferColumnTypes,
  inferValueType,
  parseTracebackLines,
  TYPE_LABELS,
} from '../../../pages/pipelines/script/scriptUtils.ts'

describe('inferValueType', () => {
  it('识别基本类型', () => {
    assert.equal(inferValueType('abc'), 'string')
    assert.equal(inferValueType('42'), 'integer')
    assert.equal(inferValueType('-7'), 'integer')
    assert.equal(inferValueType('3.14'), 'float')
    assert.equal(inferValueType('1e5'), 'float')
    assert.equal(inferValueType('1,234'), 'integer')
    assert.equal(inferValueType('true'), 'boolean')
    assert.equal(inferValueType('0'), 'boolean')
    assert.equal(inferValueType('2026-08-09'), 'timestamp')
    assert.equal(inferValueType('2026-08-09T10:30'), 'timestamp')
    assert.equal(inferValueType(1.5), 'float')
    assert.equal(inferValueType(2), 'integer')
    assert.equal(inferValueType(true), 'boolean')
  })

  it('空值归为 null 类型', () => {
    assert.equal(inferValueType(null), 'null')
    assert.equal(inferValueType(undefined), 'null')
    assert.equal(inferValueType(''), 'null')
    assert.equal(inferValueType('  '), 'null')
    assert.equal(inferValueType('NaN'), 'null')
  })
})

describe('inferColumnTypes', () => {
  it('多样本投票，平票取更具体类型', () => {
    const rows = [
      { id: 'A-1', amount: '10', flag: 'true', ts: '2026-08-09', mixed: '1' },
      { id: 'A-2', amount: '20.5', flag: 'false', ts: '2026-08-10', mixed: 'x' },
      { id: 'A-3', amount: null, flag: 'yes', ts: '', mixed: '2' },
    ]
    const types = inferColumnTypes(rows, ['id', 'amount', 'flag', 'ts', 'mixed'])
    assert.equal(types.id, 'string')
    assert.equal(types.amount, 'integer')
    assert.equal(types.flag, 'boolean')
    assert.equal(types.ts, 'timestamp')
    // string 与 integer 平票时 integer（更具体）优先——与后端优先级一致
    assert.equal(types.mixed, 'integer')
  })

  it('全空列回退 string', () => {
    assert.equal(inferColumnTypes([{ a: null }], ['a']).a, 'string')
    assert.deepEqual(inferColumnTypes([], ['a']), { a: 'string' })
  })

  it('类型词表有中文标签', () => {
    assert.equal(TYPE_LABELS.integer, '整数')
    assert.equal(TYPE_LABELS.timestamp, '时间')
  })
})

describe('parseTracebackLines', () => {
  it('提取 <string> 行号并去重排序', () => {
    const tb = [
      'Traceback (most recent call last):',
      '  File "<string>", line 12, in <module>',
      '  File "<string>", line 3, in fetch',
      '  File "/usr/lib/python3.12/json/__init__.py", line 346, in loads',
      '  File "<string>", line 12, in <module>',
      'ValueError: boom',
    ].join('\n')
    assert.deepEqual(parseTracebackLines(tb), [3, 12])
  })

  it('无匹配时返回空数组', () => {
    assert.deepEqual(parseTracebackLines('ValueError: boom'), [])
    assert.deepEqual(parseTracebackLines(''), [])
  })
})
