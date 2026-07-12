import { expect, test, type Page, type Route } from '@playwright/test'

type Model = {
  id: string
  name: string
  config_type: 'llm' | 'ocr' | 'other'
  provider: string
  api_base: string
  has_api_key: boolean
  enabled: boolean
  is_default: boolean
  last_test_status: 'success' | 'error' | null
  last_tested_at: string | null
  last_test_message: string | null
  models: string[]
  options: Record<string, unknown>
  created_by: string
  created_at: string
  updated_at: string
}

const now = '2026-07-12T06:00:00+00:00'

function model(overrides: Partial<Model> = {}): Model {
  return {
    id: 'model-1',
    name: 'DeepSeek Pro',
    config_type: 'llm',
    provider: 'deepseek',
    api_base: 'https://api.deepseek.com',
    has_api_key: true,
    enabled: false,
    is_default: false,
    last_test_status: null,
    last_tested_at: null,
    last_test_message: null,
    models: ['deepseek-v4-pro'],
    options: {},
    created_by: 'admin',
    created_at: now,
    updated_at: now,
    ...overrides,
  }
}

const emptyStats = {
  todayCalls: 0,
  availability: null,
  avgLatency: null,
  lastCall: null,
  successRate: null,
  heatCells: Array.from({ length: 60 }, () => ({
    color: '#eceef1', title: '暂无调用记录', status: 'none',
  })),
}

async function mockModelsApi(page: Page, initial: Model[] = [model()]) {
  let models = [...initial]
  let testShouldPass = false
  let createCalls = 0

  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: { id: 'admin', username: 'admin', email: 'admin@example.com', role: 'admin' },
      },
      version: 0,
    }))
  })
  await page.route('**/api/v1/**', async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    const json = (data: unknown, status = 200) => route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify({ data }),
    })

    if (method === 'GET' && path === '/api/v1/models') return json(models)
    if (method === 'GET' && path.endsWith('/stats')) return json(emptyStats)
    if (method === 'GET' && /^\/api\/v1\/models\/[^/]+$/.test(path)) {
      return json(models.find(item => path.endsWith(item.id)))
    }
    if (method === 'POST' && path.endsWith('/test')) {
      const id = path.split('/').at(-2)!
      const current = models.find(item => item.id === id)!
      const updated = {
        ...current,
        last_test_status: testShouldPass ? 'success' as const : 'error' as const,
        last_tested_at: now,
        last_test_message: testShouldPass ? '连接成功，模型响应正常' : '认证失败，请检查 API Key 是否正确且仍然有效',
      }
      models = models.map(item => item.id === id ? updated : item)
      return json({
        ok: testShouldPass,
        response: updated.last_test_message,
        code: testShouldPass ? 'OK' : 'AUTH_FAILED',
        tested_at: now,
      })
    }
    if (method === 'POST' && path.endsWith('/enabled')) {
      const id = path.split('/').at(-2)!
      const body = request.postDataJSON() as { enabled: boolean }
      const current = models.find(item => item.id === id)!
      if (body.enabled && current.last_test_status !== 'success') {
        return route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({ detail: '请先完成连通性测试，再启用' }),
        })
      }
      models = models.map(item => item.id === id ? { ...item, enabled: body.enabled } : item)
      return json(models.find(item => item.id === id))
    }
    if (method === 'POST' && path === '/api/v1/models') {
      createCalls += 1
      const body = request.postDataJSON() as Partial<Model>
      const created = model({
        ...body,
        id: `created-${createCalls}`,
        enabled: false,
        is_default: false,
        has_api_key: true,
        last_test_status: null,
      })
      models = [created, ...models]
      return json(created, 201)
    }
    if (method === 'POST' && path === '/api/v1/models/import') {
      const body = request.postDataJSON() as { configs: Array<Partial<Model>> }
      const imported = body.configs.map((item, index) => model({
        ...item, id: `imported-${index}`, enabled: false, is_default: false, has_api_key: false,
      }))
      models = [...imported, ...models]
      return json({
        imported: imported.length,
        configs: imported,
        warning: 'API Key 不会随配置文件导入，所有导入项已保持停用，请补充密钥并测试后启用。',
      }, 201)
    }
    return route.fulfill({ status: 404, body: '{}' })
  })

  return {
    passNextTests: () => { testShouldPass = true },
    createCallCount: () => createCalls,
  }
}

test.describe('模型配置稳定性流程', () => {
  test('失败信息只显示在提示中，不写入模型卡片底部', async ({ page }) => {
    await mockModelsApi(page)
    await page.goto('/#/models')

    await page.getByRole('button', { name: '测试' }).click()

    const message = '认证失败，请检查 API Key 是否正确且仍然有效'
    await expect(page.getByText(message)).toBeVisible()
    await expect(page.locator('div.group').filter({ hasText: 'DeepSeek Pro' })).not.toContainText(message)
  })

  test('后端阻止未测试启用，测试成功后可以启用', async ({ page }) => {
    const api = await mockModelsApi(page)
    await page.goto('/#/models')

    const card = page.locator('div.group').filter({ hasText: 'DeepSeek Pro' })
    await card.locator('button[title="点击启用"]').click()
    await expect(page.getByText('"DeepSeek Pro" 状态更新失败')).toBeVisible()
    await expect(card.locator('button[title="点击启用"]')).toBeVisible()

    api.passNextTests()
    await page.getByRole('button', { name: '测试' }).click()
    await expect(page.getByText('连接成功，模型响应正常')).toBeVisible()
    await card.locator('button[title="点击启用"]').click()
    await expect(card.locator('button[title="点击停用"]')).toBeVisible()
  })

  test('创建配置后保持停用', async ({ page }) => {
    const api = await mockModelsApi(page)
    await page.goto('/#/models')
    await page.getByRole('button', { name: '添加提供商' }).click()

    await page.getByPlaceholder('如：GPT-4o 生产环境').fill('MiniMax M3')
    await page.getByPlaceholder('gpt-4o', { exact: true }).fill('MiniMax-M3')
    await page.getByRole('button', { name: '保存' }).click()

    await expect(page.getByText('模型 "MiniMax M3" 创建成功，请测试后启用')).toBeVisible()
    await expect(page.locator('div.group').filter({ hasText: 'MiniMax M3' })).toContainText('已停用')
    expect(api.createCallCount()).toBe(1)
  })

  test('导入配置会保留为停用并提示重新填写密钥', async ({ page }) => {
    await mockModelsApi(page)
    await page.goto('/#/models')

    await page.locator('input[type="file"]').setInputFiles({
      name: 'models.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify({ configs: [{
        name: 'GLM 5.1', config_type: 'llm', provider: 'zhipu',
        api_base: 'https://open.bigmodel.cn/api/paas/v4', models: ['glm-5.1'], options: {},
      }] })),
    })

    await expect(page.getByText('成功导入 1 个模型配置')).toBeVisible()
    await expect(page.getByText('API Key 不会随配置文件导入')).toBeVisible()
    await expect(page.locator('div.group').filter({ hasText: 'GLM 5.1' })).toContainText('已停用')
  })
})
