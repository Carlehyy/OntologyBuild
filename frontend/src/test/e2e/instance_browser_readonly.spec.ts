import { expect, test, type Page, type Route } from '@playwright/test'

type WorkspaceMode = 'runtime' | 'draft' | 'trial' | 'release'

const ontologyId = 'ontology-instance-readonly'
const objectTypeId = 'ot-order'
const instanceId = 'order-1'

function workspace(mode: WorkspaceMode) {
  const carriesInstances = mode === 'runtime' || mode === 'trial'
  return {
    id: ontologyId,
    name: '实例只读契约本体',
    version: mode === 'runtime' ? 'v1' : 'v1.1',
    revision: '1:snapshot',
    workspaceMode: mode,
    editable: mode === 'draft',
    versionId: mode === 'runtime' ? null : `${mode}-1`,
    objectTypes: [{
      id: objectTypeId,
      name: 'Order',
      displayName: '订单',
      primaryKey: 'order_no',
      properties: [
        {
          id: 'order_no',
          name: 'order_no',
          displayName: '订单号',
          type: 'string',
          required: true,
        },
        {
          id: 'status',
          name: 'status',
          displayName: '状态',
          type: 'string',
          required: false,
        },
      ],
      positionX: 0,
      positionY: 0,
    }],
    linkTypes: [],
    actions: [],
    functions: [],
    instances: carriesInstances ? [{
      id: instanceId,
      objectTypeId,
      properties: { order_no: 'SO-100', status: 'risk_review_pending' },
      computed: {},
      source: mode === 'trial' ? 'trial' : 'pipeline',
      externalId: 'SO-100',
    }] : [],
    linkInstances: [],
    executionLogs: [],
    sentinels: [],
    trialRun: mode === 'trial'
      ? {
          id: 'trial-run-1',
          status: 'passed',
          result: { counts: { objects: 1, links: 0, facts: 2, datasets: 1 } },
        }
      : null,
  }
}

async function mockInstanceBrowser(page: Page) {
  const mutationRequests: string[] = []
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: { id: 'admin', username: 'admin', role: 'admin' },
      },
      version: 0,
    }))
  })

  const ok = (route: Route, data: unknown) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ data }),
  })

  await page.route(/^https?:\/\/[^/]+\/api\/v[12]\//, async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    if (method !== 'GET') {
      mutationRequests.push(`${method} ${path}`)
      return route.fulfill({
        status: 405,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'read-only test contract' }),
      })
    }

    if (path === `/api/v2/formal/ontologies/${ontologyId}/full`) {
      return ok(route, workspace('runtime'))
    }
    for (const mode of ['draft', 'trial', 'release'] as const) {
      if (path === `/api/v2/ontologies/${ontologyId}/versions/${mode}-1/workspace`) {
        return ok(route, workspace(mode))
      }
    }
    if (path === `/api/v2/formal/ontologies/${ontologyId}/instances/${instanceId}/facts`) {
      return ok(route, [{
        id: 'fact-status-1',
        instanceId,
        propertyName: 'status',
        value: 'risk_review_pending',
        kind: 'property',
        source: 'action://mark_risk_review',
        actorId: 'admin',
        confidence: 1,
        seq: 1,
        recordedAt: '2026-07-26T08:00:00Z',
      }, {
        id: 'fact-obsolete-note-removed',
        instanceId,
        propertyName: 'obsolete_note',
        value: null,
        present: false,
        kind: 'property',
        source: 'action://clear_obsolete_note',
        actorId: 'admin',
        confidence: 1,
        seq: 2,
        recordedAt: '2026-07-26T08:01:00Z',
      }])
    }
    if (path === '/api/v2/inbox/summary') {
      return ok(route, { unread_count: 0 })
    }
    return ok(route, [])
  })
  return mutationRequests
}

async function openInstanceBrowser(page: Page) {
  await page.getByTitle('打开菜单').click()
  await page.getByTestId('graph-runtime-tool-instances').click()
  await expect(page.getByRole('heading', { name: '对象实例浏览器' })).toBeVisible()
  await page.locator('select').filter({
    has: page.locator(`option[value="${objectTypeId}"]`),
  }).selectOption(objectTypeId)
}

async function expectNoInstanceMutationControls(page: Page) {
  const browser = page.getByTestId('instance-browser')
  await expect(browser.getByRole('button', { name: '新建实例' })).toHaveCount(0)
  await expect(browser.getByRole('button', { name: '创建第一个实例' })).toHaveCount(0)
  await expect(browser.getByRole('button', { name: '保存全部更改' })).toHaveCount(0)
  await expect(browser.getByTitle('编辑')).toHaveCount(0)
  await expect(browser.getByTitle('删除')).toHaveCount(0)
}

test('当前发布实例只读，保留事实溯源且不暴露直接写入入口', async ({ page }) => {
  const mutations = await mockInstanceBrowser(page)
  await page.goto(`/#/ontologies/${ontologyId}/graph`, {
    waitUntil: 'domcontentloaded',
  })
  await expect(page.getByTestId('graph-workspace-stage')).toContainText('当前发布')
  await openInstanceBrowser(page)

  await expect(page.getByRole('status')).toContainText('只读投影')
  await expect(page.getByRole('status')).toContainText('数据湖 Mapping 或 Action')
  await expect(page.getByText('SO-100')).toBeVisible()
  await expectNoInstanceMutationControls(page)

  await page.getByRole('button', {
    name: '查看 订单 SO-100 的属性溯源',
  }).click()
  await expect(page.getByRole('heading', { name: '属性溯源' })).toBeVisible()
  await expect(page.locator('#fact-fact-status-1').getByText('risk_review_pending')).toBeVisible()
  await expect(
    page.locator('#fact-fact-obsolete-note-removed').getByText('（已删除）'),
  ).toBeVisible()
  await expect(mutations).toEqual([])
})

test('草稿、试跑和历史发布均保持实例只读且不串读正式事实', async ({ page }) => {
  const mutations = await mockInstanceBrowser(page)

  for (const mode of ['draft', 'trial', 'release'] as const) {
    await page.goto(
      `/#/ontologies/${ontologyId}/graph?versionId=${mode}-1`,
      { waitUntil: 'domcontentloaded' },
    )
    await openInstanceBrowser(page)
    await expectNoInstanceMutationControls(page)
    await expect(page.getByRole('button', { name: /属性溯源/ })).toHaveCount(0)
    if (mode === 'trial') {
      await expect(page.getByRole('status')).toContainText('试跑隔离空间')
      await expect(page.getByText('SO-100')).toBeVisible()
    } else if (mode === 'draft') {
      await expect(page.getByRole('status')).toContainText('草稿态只维护模型定义')
    } else {
      await expect(page.getByRole('status')).toContainText('历史或归档版本')
    }
    await page.getByRole('button', { name: '关闭对象实例浏览器' }).click()
  }

  await expect(mutations).toEqual([])
})
