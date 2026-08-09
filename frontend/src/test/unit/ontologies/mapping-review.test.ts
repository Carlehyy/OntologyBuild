import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  appliedDatasetVersionId,
  appliedLinkVersionId,
  buildFlowModel,
  datasetReviewState,
  formatFullDateTime,
  formatShortDateTime,
  isReviewIssue,
  matchAppliedVersion,
  resolveFlowNodeClick,
} from '../../../pages/ontologies/detail/mapping/mapping-review.ts'

describe('datasetReviewState', () => {
  it('maps curated review statuses through', () => {
    assert.equal(datasetReviewState({ source: 'curated', reviewStatus: 'approved' }), 'approved')
    assert.equal(datasetReviewState({ source: 'curated', reviewStatus: 'rejected' }), 'rejected')
    assert.equal(datasetReviewState({ source: 'curated', reviewStatus: 'pending_review' }), 'pending_review')
  })

  it('treats unknown non-approved curated statuses as pending_review', () => {
    assert.equal(datasetReviewState({ source: 'curated', reviewStatus: 'draft' }), 'pending_review')
  })

  it('returns na for manual datasets and missing status', () => {
    assert.equal(datasetReviewState({ source: 'manual', reviewStatus: null }), 'na')
    assert.equal(datasetReviewState({ source: 'manual', reviewStatus: 'approved' }), 'na')
    assert.equal(datasetReviewState({ source: 'curated', reviewStatus: null }), 'na')
  })
})

describe('isReviewIssue', () => {
  it('flags only rejected and pending_review', () => {
    assert.equal(isReviewIssue('rejected'), true)
    assert.equal(isReviewIssue('pending_review'), true)
    assert.equal(isReviewIssue('approved'), false)
    assert.equal(isReviewIssue('na'), false)
  })
})

describe('appliedDatasetVersionId', () => {
  it('extracts the applied dataset version id from an object mapping snapshot', () => {
    assert.equal(
      appliedDatasetVersionId({ __applied_dataset_version_id__: 'ver-1', name: 'x' }),
      'ver-1',
    )
  })

  it('returns null when missing or not a string', () => {
    assert.equal(appliedDatasetVersionId({}), null)
    assert.equal(appliedDatasetVersionId(undefined), null)
    assert.equal(appliedDatasetVersionId({ __applied_dataset_version_id__: true }), null)
    assert.equal(appliedDatasetVersionId({ __applied_dataset_version_id__: '' }), null)
  })
})

describe('appliedLinkVersionId', () => {
  const fieldMapping = {
    __applied_source_version_id__: 'ver-src',
    __applied_target_version_id__: 'ver-tgt',
    __applied_edge_version_id__: 'ver-edge',
  }
  const roles = { srcDatasetId: 'ds-src', tgtDatasetId: 'ds-tgt', edgeDatasetId: 'ds-edge' }

  it('prefers the edge version for the edge dataset of a fat relation', () => {
    assert.equal(appliedLinkVersionId(fieldMapping, roles, 'ds-edge'), 'ver-edge')
  })

  it('resolves src and tgt datasets by role', () => {
    assert.equal(appliedLinkVersionId(fieldMapping, roles, 'ds-src'), 'ver-src')
    assert.equal(appliedLinkVersionId(fieldMapping, roles, 'ds-tgt'), 'ver-tgt')
  })

  it('returns null for unrelated datasets or missing markers', () => {
    assert.equal(appliedLinkVersionId(fieldMapping, roles, 'ds-other'), null)
    assert.equal(appliedLinkVersionId({}, roles, 'ds-src'), null)
    assert.equal(appliedLinkVersionId(undefined, roles, 'ds-src'), null)
  })

  it('handles thin relations without an edge dataset', () => {
    const thin = { srcDatasetId: 'ds-src', tgtDatasetId: 'ds-tgt', edgeDatasetId: null }
    assert.equal(appliedLinkVersionId(fieldMapping, thin, 'ds-src'), 'ver-src')
    assert.equal(appliedLinkVersionId(fieldMapping, thin, 'ds-edge'), null)
  })
})

describe('matchAppliedVersion', () => {
  const versions = [
    { id: 'v1', version_no: 1, processed_at: '2026-07-26T08:27:21+00:00' },
    { id: 'v2', version_no: 2, processed_at: null },
  ]

  it('finds the applied version and normalizes its shape', () => {
    assert.deepEqual(matchAppliedVersion(versions, 'v2'), { versionNo: 2, processedAt: null })
  })

  it('returns null when the id is missing or unknown', () => {
    assert.equal(matchAppliedVersion(versions, null), null)
    assert.equal(matchAppliedVersion(versions, 'nope'), null)
    assert.equal(matchAppliedVersion([], 'v1'), null)
  })
})

describe('datetime formatting', () => {
  it('formats short and full datetimes', () => {
    const iso = '2026-07-26T08:27:21+00:00'
    const date = new Date(iso)
    const pad = (value: number) => String(value).padStart(2, '0')
    const short = `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
    const full = `${date.getFullYear()}-${short}`
    assert.equal(formatShortDateTime(iso), short)
    assert.equal(formatFullDateTime(iso), full)
  })

  it('returns empty string for missing or invalid input', () => {
    assert.equal(formatShortDateTime(null), '')
    assert.equal(formatShortDateTime('not-a-date'), '')
    assert.equal(formatFullDateTime(undefined), '')
  })
})

describe('buildFlowModel', () => {
  const dataset = { id: 'ds-1', name: '订单宽表' }
  const mappedObject = {
    key: 'object:o1', kind: 'object' as const, name: '订单',
    mappingExists: true, instanceCount: 12, datasets: [dataset],
  }
  const mappedRelation = {
    key: 'relation:r1', kind: 'relation' as const, name: '履约',
    mappingExists: true, instanceCount: 0, datasets: [dataset],
  }
  const unmapped = {
    key: 'object:o2', kind: 'object' as const, name: '日志',
    mappingExists: false, instanceCount: 0, datasets: [],
  }

  it('builds dataset→element links with real instance volume', () => {
    const model = buildFlowModel([mappedObject, mappedRelation])
    assert.equal(model.mappedCount, 2)
    assert.equal(model.nodes.length, 3)
    const datasetNode = model.nodes.find(node => node.kind === 'dataset')
    assert.deepEqual(datasetNode, { id: 'dataset:ds-1', displayName: '订单宽表', kind: 'dataset', depth: 0 })
    assert.equal(model.links.length, 2)
    assert.deepEqual(model.links[0], {
      source: 'dataset:ds-1', target: 'object:o1', value: 12, realValue: 12,
    })
  })

  it('keeps zero-instance links visible as thin flows', () => {
    const model = buildFlowModel([mappedRelation])
    assert.equal(model.links[0].value, 0.4)
    assert.equal(model.links[0].realValue, 0)
  })

  it('excludes unmapped elements and dedupes shared datasets', () => {
    const model = buildFlowModel([mappedObject, mappedRelation, unmapped])
    assert.equal(model.mappedCount, 2)
    assert.equal(model.nodes.filter(node => node.kind === 'dataset').length, 1)
    assert.ok(!model.nodes.some(node => node.id === 'object:o2'))
  })

  it('returns an empty model when nothing is mapped', () => {
    const model = buildFlowModel([unmapped])
    assert.equal(model.mappedCount, 0)
    assert.equal(model.nodes.length, 0)
    assert.equal(model.links.length, 0)
  })
})

describe('resolveFlowNodeClick', () => {
  it('routes dataset nodes to preview and element nodes to selection', () => {
    assert.deepEqual(
      resolveFlowNodeClick({ id: 'dataset:ds-1', kind: 'dataset' }),
      { type: 'preview-dataset', datasetId: 'ds-1' },
    )
    assert.deepEqual(
      resolveFlowNodeClick({ id: 'object:o1', kind: 'object' }),
      { type: 'select-element', key: 'object:o1' },
    )
    assert.deepEqual(
      resolveFlowNodeClick({ id: 'relation:r1', kind: 'relation' }),
      { type: 'select-element', key: 'relation:r1' },
    )
  })

  it('ignores malformed clicks', () => {
    assert.equal(resolveFlowNodeClick(undefined), null)
    assert.equal(resolveFlowNodeClick({}), null)
    assert.equal(resolveFlowNodeClick({ id: 'x' }), null)
    assert.equal(resolveFlowNodeClick({ id: 'x', kind: 'edge' }), null)
  })
})
