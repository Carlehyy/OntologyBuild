import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { buildAnalysisPrompt } from '../../pages/settings/tabs/traceAnalysisPrompt.ts'

const request = {
  id: 1,
  created_at: '2026-08-16T08:21:58.426147Z',
  method: 'POST',
  route: '/api/v2/world-model/services/{id}/invoke',
  status_code: 200,
  duration_ms: 4740,
  request_id: '3ee634de2d064f1fb3f7e118cc279948',
  username: 'admin',
  source_ip: '10.0.0.8',
  user_agent: 'pytest',
  breakdown: {
    db: { count: 5, total_ms: 24 },
    http: { count: 2, total_ms: 4033 },
  },
  spans: [
    {
      seq: 1,
      layer: 'db',
      name: 'SELECT',
      target: 'users',
      start_ms: 8,
      duration_ms: 4,
      status: '',
      detail: 'SELECT users.id AS users_id, users.username AS users_username FROM users',
    },
    {
      seq: 2,
      layer: 'http',
      name: 'WS execute',
      target: 'ws://python_kernel_gateway:8088/api/kernels/x/channels',
      start_ms: 581,
      duration_ms: 3482,
      status: 'success',
      detail: '',
    },
  ],
  spans_truncated: false,
}

describe('buildAnalysisPrompt', () => {
  it('生成包含概要/调用链/分析要求的完整提示词', () => {
    const text = buildAnalysisPrompt(request as never)
    assert.ok(text.includes('# 平台慢接口调用链分析请求'))
    assert.ok(text.includes('## 背景'))
    assert.ok(text.includes('## 技术栈上下文'))
    assert.ok(text.includes('## 慢请求概要'))
    assert.ok(text.includes('POST /api/v2/world-model/services/{id}/invoke'))
    assert.ok(text.includes('4.74s（4740ms）'))
    assert.ok(text.includes('3ee634de2d064f1fb3f7e118cc279948'))
    assert.ok(text.includes('## 分层耗时汇总'))
    assert.ok(text.includes('数据库（DB）：5 次 · 0.02s'))
    assert.ok(text.includes('下游服务（HTTP/WS）：2 次 · 4.03s'))
    assert.ok(text.includes('## 调用链（按时间顺序）'))
    assert.ok(text.includes('| # | 层级 | 操作 | 目标 | 开始偏移 | 耗时 | 占比 | 状态 |'))
    assert.ok(text.includes('| 1 | 数据库 SQL | SELECT | users | +8ms | 4ms | 0.1% | - |'))
    assert.ok(text.includes('| 2 | 下游 HTTP/WS 服务调用 | WS execute |'))
    assert.ok(text.includes('### 关键步骤明细（SQL 语句等）'))
    assert.ok(text.includes('SELECT users.id AS users_id'))
    assert.ok(text.includes('## 分析要求'))
    assert.ok(text.includes('瓶颈定位'))
    assert.ok(text.includes('未归因耗时'))
    assert.ok(text.includes('## 输出格式要求'))
  })

  it('调用链过长时标注截断提示', () => {
    const text = buildAnalysisPrompt({ ...request, spans_truncated: true } as never)
    assert.ok(text.includes('存在截断'))
  })

  it('旧记录（无 spans）明确说明未采集调用链', () => {
    const text = buildAnalysisPrompt({ ...request, spans: [] } as never)
    assert.ok(text.includes('未采集到逐步调用链'))
    assert.ok(!text.includes('| # | 层级 | 操作 | 目标 |'))
  })

  it('用户与来源 IP 缺失时不输出对应行', () => {
    const text = buildAnalysisPrompt({ ...request, username: '', source_ip: '' } as never)
    assert.ok(!text.includes('- 用户：'))
    assert.ok(!text.includes('- 来源 IP：'))
  })
})
