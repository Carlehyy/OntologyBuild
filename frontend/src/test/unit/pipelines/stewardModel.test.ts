import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  buildStewardTimeline,
  errorText,
  filterStewardTargets,
  formatBytes,
  type StewardArtifact,
  type StewardChatMessage,
  type StewardPipeline,
} from '../../../pages/pipelines/steward/stewardModel.ts'


function artifact(
  id: string,
  source: string,
  createdAt: string,
): StewardArtifact {
  return {
    id,
    filename: `${id}.csv`,
    source,
    mimeType: 'text/csv',
    size: 12,
    sha256: id,
    extractedChars: 0,
    urls: [],
    createdAt,
  }
}

function pipeline(
  id: string,
  name: string,
  description: string,
): StewardPipeline {
  return {
    id,
    name,
    description,
    n8nWorkflowId: `n8n-${id}`,
    status: 'draft',
    pipelineId: null,
    pipelineStatus: 'draft',
    conversationId: null,
    summary: {
      node_count: 0,
      nodes: [],
      connections: {},
      has_trigger: false,
      webhook_path: null,
    },
    createdAt: null,
    updatedAt: null,
  }
}

describe('Data Steward presentation model', () => {
  it('merges messages, uploaded files, and pending uploads chronologically', () => {
    const messages: StewardChatMessage[] = [
      {
        id: 'message-late',
        role: 'assistant',
        content: 'done',
        steps: [],
        createdAt: '2026-07-30T10:00:03.000Z',
      },
      {
        id: 'message-fallback',
        role: 'user',
        content: 'run',
        steps: [],
      },
    ]
    const timeline = buildStewardTimeline(
      messages,
      [
        artifact('upload-file', 'upload', '2026-07-30T10:00:01.000Z'),
        artifact('download-file', 'download', '2026-07-30T10:00:00.000Z'),
      ],
      [{
        uid: 'upload-pending',
        name: 'pending.csv',
        ts: Date.parse('2026-07-30T10:00:02.000Z'),
      }],
    )

    assert.deepEqual(
      timeline.map(item => [item.kind, item.key]),
      [
        ['file', 'file-upload-file'],
        ['upload', 'upload-pending'],
        ['message', 'message-late'],
        ['message', 'message-fallback'],
      ],
    )
  })

  it('preserves message order when timestamps are unavailable', () => {
    const messages: StewardChatMessage[] = [
      { id: 'first', role: 'user', content: 'a', steps: [] },
      { id: 'second', role: 'assistant', content: 'b', steps: [] },
    ]

    assert.deepEqual(
      buildStewardTimeline(messages, [], []).map(item => item.key),
      ['first', 'second'],
    )
  })

  it('filters targets by case-insensitive name, description, or id', () => {
    const records = [
      pipeline('orders-daily', 'Daily Orders', '同步 ERP 订单'),
      pipeline('crm-weekly', '客户周报', 'CRM aggregation'),
    ]

    assert.deepEqual(
      filterStewardTargets(records, 'ORDER').map(item => item.id),
      ['orders-daily'],
    )
    assert.deepEqual(
      filterStewardTargets(records, 'aggregation').map(item => item.id),
      ['crm-weekly'],
    )
    assert.deepEqual(
      filterStewardTargets(records, 'crm-week').map(item => item.id),
      ['crm-weekly'],
    )
    assert.equal(filterStewardTargets(records, '   '), records)
  })

  it('keeps byte labels and API error precedence stable', () => {
    assert.equal(formatBytes(1023), '1023 B')
    assert.equal(formatBytes(1024), '1.0 KB')
    assert.equal(formatBytes(1024 * 1024), '1.0 MB')
    assert.equal(errorText({ detail: '详情', message: '消息' }, 'fallback'), '详情')
    assert.equal(errorText(new Error('消息'), 'fallback'), '消息')
    assert.equal(errorText(null, 'fallback'), 'fallback')
  })
})
