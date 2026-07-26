import { expect, test, type Page, type Route } from '@playwright/test'

const ontologyId = 'ontology-sentinel-loading'

const sentinel = {
  id: 'sentinel-high-risk',
  ontologyId,
  name: 'high_risk_order',
  displayName: '高风险订单监控',
  description: '风险评分达到阈值时触发',
  bindings: [{ alias: 'o', objectTypeId: 'object-order', filter: null }],
  links: [],
  condition: 'o["risk_score"] >= 80',
  conditionRows: [],
  conditionLogic: 'and',
  primaryAlias: 'o',
  actionIds: ['action-notify'],
  actionParameters: {},
  onChange: true,
  onSchedule: false,
  scanIntervalSeconds: 300,
  triggerMode: 'on_enter',
  muted: false,
  enabled: true,
  releaseId: 'release-v1',
  enableGeneration: 0,
  origin: 'release_builtin',
  status: 'published',
}

async function mockPublishedGraph(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: {
          id: 'admin',
          username: 'admin',
          email: 'admin@example.com',
          role: 'admin',
        },
      },
      version: 0,
    }))
  })

  const ok = (route: Route, data: unknown) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ data }),
  })
  const fail = (route: Route, detail: string) => route.fulfill({
    status: 503,
    contentType: 'application/json',
    body: JSON.stringify({ detail }),
  })

  await page.route(/^https?:\/\/[^/]+\/api\/v2\//, route => {
    const path = new URL(route.request().url()).pathname
    if (path === `/api/v2/formal/ontologies/${ontologyId}/full`) {
      return ok(route, {
        id: ontologyId,
        name: '供应链风险本体',
        version: 'v1',
        workspaceMode: 'runtime',
        objectTypes: [{
          id: 'object-order',
          name: 'Order',
          displayName: '订单',
          primaryKey: 'order_id',
          properties: [{
            id: 'property-risk-score',
            name: 'risk_score',
            displayName: '风险评分',
            type: 'number',
            required: true,
          }],
          positionX: 120,
          positionY: 120,
        }],
        linkTypes: [],
        actions: [{
          id: 'action-notify',
          name: 'notify_risk',
          displayName: '发送风险通知',
          objectTypeId: 'object-order',
          parameters: [],
          rules: [],
          requiresApproval: true,
        }],
        functions: [],
        instances: [],
        linkInstances: [],
        executionLogs: [],
      })
    }
    return ok(route, [])
  })

  await page.route(/^https?:\/\/[^/]+\/api\/v1\//, async route => {
    const path = new URL(route.request().url()).pathname
    const sentinelBase = `/api/v1/ontologies/${ontologyId}/sentinels`
    if (path === `${sentinelBase}/`) {
      await new Promise(resolve => setTimeout(resolve, 1_200))
      return ok(route, [sentinel])
    }
    if (path === `${sentinelBase}/firings`) {
      await new Promise(resolve => setTimeout(resolve, 3_000))
      return fail(route, '触发日志服务暂不可用')
    }
    if (path === `${sentinelBase}/cdc-status`) {
      await new Promise(resolve => setTimeout(resolve, 3_200))
      return fail(route, '变化执行链状态暂不可用')
    }
    return ok(route, [])
  })
}

test('发布哨兵定义先展示，辅助运行请求失败不会伪装成空列表', async ({ page }) => {
  await mockPublishedGraph(page)
  await page.goto(`/#/ontologies/${ontologyId}/graph`, { waitUntil: 'domcontentloaded' })

  await expect(page.getByText(/当前发布 v1/)).toBeVisible()
  await page.getByTitle('打开菜单').click()
  await page.getByTestId('graph-runtime-tool-sentinel').click()

  await expect(page.getByRole('heading', { name: '哨兵引擎' })).toBeVisible()
  await expect(page.getByText('正在加载当前发布的哨兵定义…', { exact: true }))
    .toBeVisible()
  await expect(page.getByText('还没有哨兵')).toHaveCount(0)

  // 定义请求先完成；两个辅助请求仍在等待。定义必须立即独立展示。
  await expect(page.getByText('高风险订单监控')).toBeVisible({ timeout: 2_000 })
  await expect(page.getByRole('button', { name: '哨兵 (1)' })).toBeVisible()
  await expect(page.getByText('还没有哨兵')).toHaveCount(0)

  await expect(page.getByText(/加载触发日志失败：触发日志服务暂不可用/))
    .toBeVisible({ timeout: 4_000 })
  await expect(page.getByText(/加载变化执行链状态失败：变化执行链状态暂不可用/))
    .toBeVisible()
  await expect(page.getByText('高风险订单监控')).toBeVisible()
})
