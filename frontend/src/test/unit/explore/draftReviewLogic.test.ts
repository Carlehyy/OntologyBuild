import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { appliedGraphPath } from '../../../pages/explore/draftReviewLogic.ts'


describe('appliedGraphPath · 草稿应用成功后的图谱跳转', () => {
  it('有 versionId 时落到该版本的草稿视图', () => {
    assert.equal(
      appliedGraphPath({ ontologyId: 'ont-1', versionId: 'ver-2' }),
      '/ontologies/ont-1/graph?versionId=ver-2',
    )
  })

  it('旧后端缺 versionId 时回退到运行版图谱', () => {
    assert.equal(appliedGraphPath({ ontologyId: 'ont-1' }), '/ontologies/ont-1/graph')
    assert.equal(appliedGraphPath({ ontologyId: 'ont-1', versionId: null }), '/ontologies/ont-1/graph')
    assert.equal(appliedGraphPath({ ontologyId: 'ont-1', versionId: '' }), '/ontologies/ont-1/graph')
  })

  it('versionId 含特殊字符时做 query 编码', () => {
    assert.equal(
      appliedGraphPath({ ontologyId: 'ont-1', versionId: 'a b&c' }),
      '/ontologies/ont-1/graph?versionId=a%20b%26c',
    )
  })
})
