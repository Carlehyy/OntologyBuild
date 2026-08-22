import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { describeJsonParseError, validateJsonObject } from '../../../utils/jsonInput.ts'

describe('describeJsonParseError', () => {
  const text = '{"a":\n1 2}'

  it('从旧版 V8 的 position 消息计算行列', () => {
    // '}' 在第 2 行第 4 列（下标 9）
    const issue = describeJsonParseError(text, new Error('Unexpected token } in JSON at position 9'))
    assert.equal(issue.line, 2)
    assert.equal(issue.column, 4)
    assert.match(issue.message, /position 9/)
  })

  it('新版 V8 的 line/column 消息直接透传', () => {
    const issue = describeJsonParseError(text, new Error("Expected ',' at line 2 column 4 of the JSON data"))
    assert.equal(issue.line, 2)
    assert.equal(issue.column, 4)
  })

  it('position 超出文本长度时按末尾收敛', () => {
    const issue = describeJsonParseError('123', new Error('Unexpected end of JSON input at position 999'))
    assert.equal(issue.line, 1)
    assert.equal(issue.column, 3)
  })

  it('消息不含位置信息时行列返回 null', () => {
    const issue = describeJsonParseError(text, new Error('boom'))
    assert.equal(issue.line, null)
    assert.equal(issue.column, null)
    assert.equal(issue.message, 'boom')
  })
})

describe('validateJsonObject', () => {
  it('合法对象返回解析值', () => {
    const result = validateJsonObject('{"context":{"series":[1,2]},"horizon":3}')
    assert.equal(result.issue, null)
    assert.deepEqual(result.value, { context: { series: [1, 2] }, horizon: 3 })
  })

  it('空文本按空对象处理（与历史默认行为一致）', () => {
    const result = validateJsonObject('')
    assert.equal(result.issue, null)
    assert.deepEqual(result.value, {})
  })

  it('数组与标量根节点判为非法', () => {
    assert.ok(validateJsonObject('[1,2]').issue)
    assert.ok(validateJsonObject('"str"').issue)
    assert.ok(validateJsonObject('42').issue)
    const arrayIssue = validateJsonObject('[1,2]').issue
    assert.match(arrayIssue!.message, /JSON 对象/)
  })

  it('语法错误带定位信息', () => {
    const result = validateJsonObject('{\n  "a": 1,\n}')
    assert.ok(result.issue)
    assert.equal(result.value, null)
    assert.equal(result.issue!.line, 3)
  })
})
