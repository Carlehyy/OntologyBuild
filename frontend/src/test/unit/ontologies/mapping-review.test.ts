import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  appliedDatasetVersionId,
  appliedLinkVersionId,
  datasetReviewState,
  formatFullDateTime,
  formatShortDateTime,
  isReviewIssue,
  matchAppliedVersion,
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
