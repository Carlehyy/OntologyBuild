import { expect, test, type Page, type Route } from '@playwright/test'

const N8N_PIPELINE = {
  id: 'n8n-pipe-1',
  name: '汇率采集',
  description: '采集每日汇率',
  domain: '财务',
  status: 'published',
  enabled: true,
  column_definitions: [],
  target_curated_ids: [],
  task_count: 0,
  definition: { engine: 'n8n', nodes: [], edges: [], n8n: { steward_id: 'rec-1', workflow_id: 'wf-1' } },
  created_at: '2026-08-10T09:00:00Z',
  updated_at: '2026-08-10T09:00:00Z',
}

const PYTHON_PIPELINE = {
  id: 'py-pipe-1',
  name: '订单取数脚本',
  description: '每日抓取订单',
  domain: '通用',
  status: 'draft',
  enabled: false,
  column_definitions: [],
  target_curated_ids: [],
  task_count: 0,
  definition: { engine: 'python', nodes: [], edges: [], python: { script: 'result = []' } },
  created_at: '2026-08-11T09:00:00Z',
  updated_at: '2026-08-11T09:00:00Z',
}

async function mockListPage(page: Page, capture: { putBody?: unknown }) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: { token: 'e2e-token', user: { id: 'u1', username: 'tester', role: 'admin' } },
      version: 0,
    }))
  })
  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const ok = (data: unknown, status = 200) => route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(data),
    })
    if (url.pathname === '/api/v2/pipelines' && url.searchParams.get('paginated') === 'true') {
      return ok({
        items: [PYTHON_PIPELINE, N8N_PIPELINE], total: 2, page: 1, page_size: 10,
        overview: { total: 2, published: 1, enabled: 1, latest_failed: 0 },
      })
    }
    if (url.pathname === `/api/v2/pipelines/${PYTHON_PIPELINE.id}/clone`) {
      return ok({ ...PYTHON_PIPELINE, id: 'py-pipe-clone', name: `${PYTHON_PIPELINE.name}_复制` }, 201)
    }
    if (url.pathname === `/api/v2/pipelines/${PYTHON_PIPELINE.id}` && route.request().method() === 'PUT') {
      capture.putBody = route.request().postDataJSON()
      const body = capture.putBody as { name?: string; description?: string }
      return ok({ ...PYTHON_PIPELINE, name: body.name ?? PYTHON_PIPELINE.name })
    }
    if (url.pathname === '/api/v2/steward/status') {
      return ok({
        n8n: { configured: false, enabled: false, api_url: '', reachable: false },
        python: { configured: false },
      })
    }
    return ok({})
  })
}

test.describe('数据流水线列表页', () => {
  test('流水线类型列渲染两种等宽类型标识', async ({ page }) => {
    await mockListPage(page, {})
    await page.goto('/#/data/pipelines')

    await expect(page.locator('th', { hasText: '流水线类型' })).toBeVisible()
    await expect(page.locator('th', { hasText: '流水线来源' })).toHaveCount(0)

    const n8nBadge = page.locator('tbody span', { hasText: 'n8n 流水线' })
    const pythonBadge = page.locator('tbody span', { hasText: 'Python 脚本' })
    await expect(n8nBadge).toBeVisible()
    await expect(pythonBadge).toBeVisible()
    const n8nBox = await n8nBadge.boundingBox()
    const pythonBox = await pythonBadge.boundingBox()
    expect(n8nBox?.width).toBe(pythonBox?.width)
    await expect(page.getByRole('heading', { name: '数据流水线' })).toBeVisible()
    await expect(page.getByText('Python 执行网关尚未配置')).toBeVisible()
  })

  test('克隆需二次确认，确认后副本以「_复制」尾缀加入列表', async ({ page }) => {
    await mockListPage(page, {})
    await page.goto('/#/data/pipelines')

    const pythonRow = page.locator('tr', { hasText: '订单取数脚本' })
    await pythonRow.getByRole('button', { name: '更多操作：订单取数脚本' }).click()
    await page.getByRole('menuitem', { name: '克隆为草稿' }).click()
    await expect(page.getByText('确认克隆流水线「订单取数脚本」', { exact: false })).toBeVisible()

    const cloneCall = page.waitForRequest(`/api/v2/pipelines/${PYTHON_PIPELINE.id}/clone`)
    await page.getByRole('button', { name: '确认克隆' }).click()
    await cloneCall

    await expect(page.locator('tr', { hasText: '订单取数脚本_复制' })).toBeVisible()
    await expect(page.getByText('流水线已克隆')).toBeVisible()
  })

  test('草稿 Python 流水线编辑向导第 1 步可单独保存基础信息', async ({ page }) => {
    const capture: { putBody?: unknown } = {}
    await mockListPage(page, capture)
    await page.goto('/#/data/pipelines')

    const pythonRow = page.locator('tr', { hasText: '订单取数脚本' })
    await pythonRow.getByTitle('配置流水线：信息 / 执行预览 / 主键组 / 发布').click()
    await expect(page.getByText('配置流水线「订单取数脚本」')).toBeVisible()

    const saveButton = page.getByRole('button', { name: '保存基础信息' })
    await expect(saveButton).toBeVisible()
    await expect(saveButton).toBeDisabled()

    await page.getByPlaceholder('输入流水线名称').fill('订单取数脚本改')
    await expect(saveButton).toBeEnabled()
    await saveButton.click()

    await expect.poll(() => capture.putBody).toEqual({
      name: '订单取数脚本改',
      description: '每日抓取订单',
    })
    await expect(page.getByText('配置流水线「订单取数脚本」')).toHaveCount(0)
  })

  test('新建流水线名称错误在字段旁展示并关联无障碍状态', async ({ page }) => {
    await mockListPage(page, {})
    await page.goto('/#/data/pipelines')

    await page.getByRole('button', { name: '新建流水线' }).click()
    await page.getByRole('button', { name: '创建', exact: true }).click()

    const nameInput = page.getByLabel(/流水线名称/)
    await expect(nameInput).toHaveAttribute('aria-invalid', 'true')
    await expect(page.getByText('请输入流水线名称')).toBeVisible()
  })

  test('窄屏使用卡片布局，主操作无需横向滚动', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await mockListPage(page, {})
    await page.goto('/#/data/pipelines')

    const pythonCard = page.locator('article', { hasText: '订单取数脚本' })
    await expect(pythonCard).toBeVisible()
    await expect(pythonCard.getByRole('button', { name: '配置' })).toBeVisible()
    await expect(pythonCard.getByRole('button', { name: '试执行' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  })
})
