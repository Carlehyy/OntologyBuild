import { expect, test, type Page, type Route } from '@playwright/test'

const PIPELINE_ID = 'py-pipe-1'

const pythonPipeline = {
  id: PIPELINE_ID,
  name: '订单取数脚本',
  description: '每日从业务系统抓取订单',
  domain: '通用',
  status: 'draft',
  enabled: false,
  column_definitions: [],
  target_curated_ids: [],
  definition: {
    engine: 'python',
    nodes: [],
    edges: [],
    python: {
      script: 'result = [{"id": 1, "name": "已保存"}]',
      saved_at: '2026-08-08T10:00:00Z',
      output_columns: ['id', 'name'],
    },
  },
  created_at: '2026-08-08T09:00:00Z',
  updated_at: '2026-08-08T10:00:00Z',
}

const executeOk = {
  ok: true,
  format_valid: true,
  format_error: null,
  row_count: 2,
  columns: ['id', 'name'],
  sample: [
    { id: 1, name: '示例数据 A' },
    { id: 2, name: '示例数据 B' },
  ],
  stdout: '抓取完成\n',
  error: null,
  traceback: '',
  duration_ms: 860,
}

async function mockScriptPage(page: Page, executeBody: unknown = executeOk) {
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
    if (url.pathname === `/api/v2/pipelines/${PIPELINE_ID}/script/execute`) {
      return ok(executeBody)
    }
    if (url.pathname === `/api/v2/pipelines/${PIPELINE_ID}/script`) {
      return ok({ pipeline: pythonPipeline, execution: executeOk })
    }
    if (url.pathname === `/api/v2/pipelines/${PIPELINE_ID}`) {
      return ok(pythonPipeline)
    }
    return ok({})
  })
}

test.describe('Python 脚本编辑页', () => {
  test('浅色编辑器渲染、执行成功展示指标与样本表格、保存门槛解锁', async ({ page }) => {
    await mockScriptPage(page)
    await page.goto(`/#/data/pipelines/script/${PIPELINE_ID}`)

    // 页头徽章与已保存状态
    await expect(page.getByText('Python 脚本').first()).toBeVisible()
    await expect(page.getByText('未发布').first()).toBeVisible()
    await expect(page.getByText('已保存', { exact: false }).first()).toBeVisible()

    // CodeMirror 浅色编辑器就位，加载已保存脚本
    const editor = page.locator('.cm-editor')
    await expect(editor).toBeVisible()
    await expect(page.locator('.cm-content')).toContainText('result =')
    await expect(page.locator('.cm-lineNumbers')).toBeVisible()

    // 保存按钮在执行通过前置灰
    const saveButton = page.getByRole('button', { name: /保存/ })
    await expect(saveButton).toBeDisabled()

    // 执行 → 成功结果区：指标、列、样本表
    await page.getByRole('button', { name: /^执行$/ }).click()
    await expect(page.getByText('执行成功 · 输出格式校验通过')).toBeVisible()
    await expect(page.getByText('输出行数')).toBeVisible()
    await expect(page.getByText('示例数据 A')).toBeVisible()
    await expect(saveButton).toBeEnabled()
  })

  test('执行失败展示错误与浅色 traceback 面板', async ({ page }) => {
    await mockScriptPage(page, {
      ...executeOk,
      ok: false,
      format_valid: false,
      row_count: 0,
      columns: [],
      sample: [],
      error: '脚本执行失败（ValueError）：boom',
      traceback: 'Traceback (most recent call last):\nValueError: boom',
    })
    await page.goto(`/#/data/pipelines/script/${PIPELINE_ID}`)
    await page.getByRole('button', { name: /^执行$/ }).click()
    await expect(page.getByText('执行失败', { exact: true })).toBeVisible()
    await expect(page.getByText('脚本执行失败（ValueError）：boom')).toBeVisible()
    await expect(page.getByText(/Traceback/)).toBeVisible()
    await expect(page.getByRole('button', { name: /保存/ })).toBeDisabled()
  })

  test('未保存修改自动缓存草稿，刷新后恢复并提示', async ({ page }) => {
    await mockScriptPage(page)
    await page.goto(`/#/data/pipelines/script/${PIPELINE_ID}`)
    await expect(page.locator('.cm-content')).toContainText('result =')

    // 编辑触发草稿缓存（防抖 500ms）
    await page.locator('.cm-content').click()
    await page.keyboard.press('End')
    await page.keyboard.type('\n# 草稿改动')
    await expect(page.getByText('有未保存修改 · 草稿已自动缓存')).toBeVisible()
    await page.waitForTimeout(700)

    await page.reload()
    await expect(page.getByText('已恢复你上次未保存的编辑草稿')).toBeVisible()
    await expect(page.locator('.cm-content')).toContainText('草稿改动')

    // 放弃草稿 → 回到已保存版本
    await page.getByRole('button', { name: '放弃草稿' }).click()
    await expect(page.locator('.cm-content')).not.toContainText('草稿改动')
  })
})
