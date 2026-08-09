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
    if (url.pathname === `/api/v2/pipelines/${PIPELINE_ID}/script/versions`) {
      return ok({
        items: [
          {
            id: 'sv-2', version_no: 2,
            script: 'result = [{"id": 1, "name": "已保存"}]',
            output_columns: ['id', 'name'], row_count: 1, duration_ms: 700,
            created_at: '2026-08-09T02:00:00Z',
          },
          {
            id: 'sv-1', version_no: 1,
            script: 'result = [{"id": 0, "name": "第一版"}]',
            output_columns: ['id', 'name'], row_count: 1, duration_ms: 500,
            created_at: '2026-08-08T10:00:00Z',
          },
        ],
      })
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

  test('历史版本抽屉：查看、展开脚本、恢复到编辑器后需重新执行', async ({ page }) => {
    await mockScriptPage(page)
    await page.goto(`/#/data/pipelines/script/${PIPELINE_ID}`)
    await expect(page.locator('.cm-content')).toContainText('已保存')

    await page.getByRole('button', { name: /历史版本/ }).click()
    await expect(page.getByText('脚本历史版本')).toBeVisible()
    await expect(page.getByText('v2')).toBeVisible()
    await expect(page.getByText('v1')).toBeVisible()
    await expect(page.getByText('当前', { exact: true })).toBeVisible()

    // 展开 v1 脚本内容
    await page.getByRole('button', { name: '查看' }).last().click()
    await expect(page.getByText('第一版')).toBeVisible()

    // 恢复 v1 到编辑器：先弹二次确认，确认后内容替换为 v1、保存重新置灰
    await page.getByRole('button', { name: '恢复到编辑器' }).last().click()
    await expect(page.getByText('恢复 v1 到编辑器')).toBeVisible()
    await page.getByRole('button', { name: '恢复', exact: true }).click()
    await expect(page.locator('.cm-content')).toContainText('第一版')
    await expect(page.locator('.cm-content')).not.toContainText('已保存')
    await expect(page.getByText('有未保存修改 · 草稿已自动缓存')).toBeVisible()
    await expect(page.getByRole('button', { name: /保存/ })).toBeDisabled()
  })

  test('执行可取消：显示已执行耗时并终止等待', async ({ page }) => {
    // 让执行请求挂起，直到取消后才放行；不复用 mockScriptPage，避免 unroute 重注册
    const executeGate: { release?: () => void } = {}
    await page.addInitScript(() => {
      localStorage.setItem('token', 'e2e-token')
      localStorage.setItem('auth-store', JSON.stringify({
        state: { token: 'e2e-token', user: { id: 'u1', username: 'tester', role: 'admin' } },
        version: 0,
      }))
    })
    await page.route('**/api/**', async (route: Route) => {
      const url = new URL(route.request().url())
      // 只拦截后端 API；/src/api/*.ts 是 Vite 模块文件，必须放行
      if (!url.pathname.startsWith('/api/')) return route.continue()
      const ok = (data: unknown, status = 200) => route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(data),
      })
      if (url.pathname === `/api/v2/pipelines/${PIPELINE_ID}/script/execute`) {
        await new Promise<void>(resolve => { executeGate.release = resolve })
        return ok(executeOk)
      }
      if (url.pathname === `/api/v2/pipelines/${PIPELINE_ID}/script/cancel`) {
        return ok({ cancelled: true })
      }
      if (url.pathname === `/api/v2/pipelines/${PIPELINE_ID}`) {
        return ok(pythonPipeline)
      }
      return ok({})
    })
    await page.goto(`/#/data/pipelines/script/${PIPELINE_ID}`)

    await page.getByRole('button', { name: /^执行$/ }).click()
    await expect(page.getByText(/已执行 \d+s/)).toBeVisible()
    await page.getByRole('button', { name: '取消' }).first().click()
    await expect(page.getByText('已取消本次执行')).toBeVisible()
    executeGate.release?.()
  })

  test('保存门槛 checklist 随执行状态推进', async ({ page }) => {
    await mockScriptPage(page)
    await page.goto(`/#/data/pipelines/script/${PIPELINE_ID}`)

    // 初始：脚本非空 ✓，但未执行、未校验
    await expect(page.getByText('三项全部通过后保存可点')).toBeVisible()
    await page.getByRole('button', { name: /^执行$/ }).click()
    await expect(page.getByText('执行成功 · 输出格式校验通过')).toBeVisible()
    await expect(page.getByText('可以保存：保存时平台会重新执行并复验')).toBeVisible()
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
