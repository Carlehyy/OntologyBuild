import { expect, test, type Page, type Route } from '@playwright/test'

const now = '2026-08-12T08:00:00+00:00'

const json = (route: Route, data: unknown) => route.fulfill({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify({ data }),
})

async function seedAuth(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: { id: 'u-evolution', username: 'evolution-tester', role: 'admin' },
      },
      version: 0,
    }))
  })
}

test('超级助手：待审批与记忆面板全链路', async ({ page }) => {
  await seedAuth(page)

  let autoAccept = true
  let pendingCount = 1
  let candidatePending = true
  const decisions: Array<{ decision: string }> = []
  const memories = [
    {
      id: 'mem-1', content: '用户偏好简洁的中文回答', zone: 'core', pinned: true,
      confidence: 'high', source: 'user', tags: ['偏好'], supersedes: [], superseded: false,
      match_count: 3, reference_count: 2, last_accessed_at: null,
      created_at: now, updated_at: now,
    },
    {
      id: 'mem-2', content: '正在推进本体发布流程', zone: 'work', pinned: false,
      confidence: 'medium', source: 'reflection', tags: [], supersedes: [], superseded: false,
      match_count: 1, reference_count: 0, last_accessed_at: null,
      created_at: now, updated_at: now,
    },
  ]

  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/models') return json(route, [{
      id: 'model-1', name: 'Fake model', config_type: 'llm', provider: 'openai',
      api_base: 'https://example.com', has_api_key: true, enabled: true, is_default: true,
      last_test_status: 'success', last_tested_at: now, last_test_message: 'ok',
      models: ['fake-model'], options: {}, created_by: 'admin',
      created_at: now, updated_at: now,
    }])
    return route.continue()
  })

  await page.route('**/api/v2/super-assistant/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (path === '/api/v2/super-assistant/conversations') {
      return json(route, [{
        id: 'conv-1', title: '测试会话', model_config_id: 'model-1', status: 'active',
        created_at: now, updated_at: now,
      }])
    }
    if (path === '/api/v2/super-assistant/mcp-servers') return json(route, [])
    if (path === '/api/v2/super-assistant/skills') return json(route, [])
    if (path === '/api/v2/super-assistant/memories' && method === 'GET') {
      return json(route, memories)
    }
    if (path === '/api/v2/super-assistant/memories' && method === 'POST') {
      const body = request.postDataJSON() as { content: string }
      if (body.content.includes('简洁')) {
        return route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: '与现有记忆过于相似',
            existing: { id: 'mem-1', content: '用户偏好简洁的中文回答', similarity: 0.87 },
          }),
        })
      }
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ data: { ...memories[0], id: 'mem-new', content: body.content } }),
      })
    }
    if (path === '/api/v2/super-assistant/reflection/settings') {
      if (method === 'PUT') {
        const body = request.postDataJSON() as { auto_accept_enabled: boolean }
        autoAccept = body.auto_accept_enabled
      }
      return json(route, {
        auto_accept_enabled: autoAccept,
        palace_index: null,
        profile: null,
        memory_count: memories.length,
        pending_count: pendingCount,
      })
    }
    if (path === '/api/v2/super-assistant/reflection/candidates') {
      return json(route, candidatePending ? [{
        id: 'cand-1', run_id: 'run-1', conversation_id: 'conv-1',
        kind: 'memory', status: 'pending', confidence: 'medium',
        payload: { content: '用户正在评估知识库收敛方案', zone: 'work', tags: [], pinned: false, supersedes: [] },
        decision: null, created_at: now, decided_at: null,
      }] : [])
    }
    if (path.startsWith('/api/v2/super-assistant/reflection/candidates/') && method === 'POST') {
      const body = request.postDataJSON() as { decision: string }
      decisions.push(body)
      candidatePending = false
      pendingCount = 0
      return json(route, {
        id: 'cand-1', run_id: 'run-1', conversation_id: 'conv-1',
        kind: 'memory', status: 'accepted', confidence: 'medium',
        payload: {}, decision: body.decision, created_at: now, decided_at: now,
      })
    }
    return route.fulfill({ status: 404, body: '{}' })
  })

  await page.goto('/#/super-assistant')
  await page.getByRole('button', { name: '打开助手配置' }).click()

  // 待审批：接受一条 memory 候选
  await page.getByRole('button', { name: '待审批' }).click()
  await expect(page.getByTestId('candidate-memory')).toBeVisible()
  await expect(page.getByText('用户正在评估知识库收敛方案')).toBeVisible()
  await page.getByRole('button', { name: '接受', exact: true }).click()
  await expect(page.getByText('没有待审批的候选')).toBeVisible()
  expect(decisions).toEqual([{ decision: 'accept' }])

  // 记忆：列表、搜索过滤、auto-accept 开关、409 冲突提示
  await page.getByRole('button', { name: '记忆', exact: true }).click()
  await expect(page.getByTestId('memory-item')).toHaveCount(2)
  await expect(page.getByText('用户偏好简洁的中文回答')).toBeVisible()

  const autoAcceptSwitch = page.getByTestId('auto-accept-switch')
  await expect(autoAcceptSwitch).toHaveAttribute('aria-checked', 'true')
  await autoAcceptSwitch.click()
  await expect(autoAcceptSwitch).toHaveAttribute('aria-checked', 'false')

  await page.getByPlaceholder('搜索记忆内容或标签…').fill('本体')
  await expect(page.getByTestId('memory-item')).toHaveCount(1)
  await page.getByPlaceholder('搜索记忆内容或标签…').fill('')

  await page.getByRole('button', { name: '新增记忆' }).click()
  await page.getByPlaceholder(/要记住的事实/).fill('用户偏好简洁的回答风格')
  await page.getByRole('button', { name: '保存记忆' }).click()
  await expect(page.getByText('与现有记忆过于相似')).toBeVisible()
  await expect(page.getByText(/相似度 87%/)).toBeVisible()
})
