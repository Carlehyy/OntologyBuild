import { expect, test, type Page, type Route } from '@playwright/test'

const ontologyId = 'ontology-structure-initial-view'

async function mockOntologyStructure(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'structure-view-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'structure-view-token',
        user: { id: 'structure-view-user', username: 'structure-view-user', role: 'admin' },
      },
      version: 0,
    }))
  })

  const ok = (route: Route, data: unknown) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ data, message: 'ok' }),
  })

  await page.route(/^https?:\/\/[^/]+\/api\/v[12]\//, async (route) => {
    const path = new URL(route.request().url()).pathname

    if (path === `/api/v1/ontologies/${ontologyId}`) {
      return ok(route, {
        id: ontologyId,
        name: '初始视口测试本体',
        domain: '供应链',
        description: '验证本体结构首次展示时直接居中',
        version: 'v1',
        current_release_id: 'release-1',
        current_release_version: 'v1',
        status: 'published',
        entity_count: 1,
        relation_count: 0,
        action_count: 0,
        sentinel_count: 0,
        created_by: 'structure-view-user',
        created_at: '2026-08-07T00:00:00Z',
        updated_at: '2026-08-07T00:00:00Z',
      })
    }
    if (path === `/api/v2/ontologies/${ontologyId}/current-release/workspace`) {
      return ok(route, {
        version: 'v1',
        versionId: 'release-1',
        workspaceMode: 'release',
        editable: false,
        isCurrentRelease: true,
        objectTypes: [{
          id: 'object-order',
          name: 'Order',
          displayName: '订单',
          primaryKey: 'order_no',
          properties: [{
            id: 'order_no',
            name: 'order_no',
            displayName: '订单号',
            type: 'string',
            required: true,
          }],
        }],
        linkTypes: [],
        actions: [],
        functions: [],
        sentinels: [],
        canvasLayout: {},
      })
    }
    if (path === `/api/v2/formal/ontologies/${ontologyId}/agent/dynamic-sentinels`) {
      return ok(route, [])
    }
    if (path === '/api/v2/inbox/summary') {
      return ok(route, { openAlertCount: 0, actionableCount: 0, unreadCount: 0, resolvedCount: 0 })
    }
    if (path === `/api/v2/formal/ontologies/${ontologyId}/overview`) {
      return ok(route, {
        release: { id: 'release-1', version: 'v1', publishedAt: '2026-08-07T00:00:00Z' },
        model: {
          objectTypes: 1,
          linkTypes: 0,
          actions: 0,
          actionsRequiringApproval: 0,
          functions: 0,
          sentinels: { total: 0, enabled: 0, muted: 0 },
        },
        data: {
          instances: 0,
          instancesBySource: {},
          linkInstances: 0,
          mappings: { total: 0, bound: 0, nameMatch: 0, autoCreate: 0, autoApply: 0 },
          topTypes: [],
        },
        runtime: {
          pendingApprovals: 0,
          decisions: { total: 0, approved: 0, rejected: 0, recentApprovalRate: null },
          firings7d: { total: 0, fired: 0, error: 0 },
          actionRuns7d: { total: 0, success: 0, failed: 0 },
          daily7d: [],
        },
        facts: { total: 0, byKind: {} },
      })
    }
    if (path === `/api/v2/formal/ontologies/${ontologyId}/facts/recent`) {
      return ok(route, [])
    }
    return ok(route, [])
  })
}

test('点击本体结构后画布内容首次可见时已经居中且不再横向滑动', async ({ page }) => {
  await mockOntologyStructure(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`/#/ontologies/${ontologyId}`, { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('button', { name: '本体结构', exact: true })).toBeVisible()

  await page.evaluate(() => {
    const state = window as typeof window & {
      __structureViewportFrames?: Array<{ centerX: number; centerY: number; transform: string }>
    }
    state.__structureViewportFrames = []
    let firstVisibleAt: number | null = null
    const capture = () => {
      const node = document.querySelector<HTMLElement>('[data-testid="structure-node-object"]')
      const viewport = document.querySelector<HTMLElement>('.react-flow__viewport')
      if (node && viewport && getComputedStyle(node).visibility !== 'hidden') {
        firstVisibleAt ??= performance.now()
        const rect = node.getBoundingClientRect()
        state.__structureViewportFrames?.push({
          centerX: rect.x + rect.width / 2,
          centerY: rect.y + rect.height / 2,
          transform: viewport.style.transform,
        })
      }
      if (firstVisibleAt === null || performance.now() - firstVisibleAt < 700) requestAnimationFrame(capture)
    }
    requestAnimationFrame(capture)
  })

  await page.getByRole('button', { name: '本体结构', exact: true }).click()
  const graph = page.getByTestId('ontology-structure-graph')
  const node = page.getByTestId('structure-node-object')
  await expect(graph).toBeVisible()
  await expect(node).toBeVisible()
  await page.waitForTimeout(750)

  const frames = await page.evaluate(() => {
    const state = window as typeof window & {
      __structureViewportFrames?: Array<{ centerX: number; centerY: number; transform: string }>
    }
    return state.__structureViewportFrames || []
  })
  expect(frames.length).toBeGreaterThan(5)
  const firstFrame = frames[0]
  const lastFrame = frames.at(-1)!
  expect(Math.abs(lastFrame.centerX - firstFrame.centerX)).toBeLessThanOrEqual(1)
  expect(Math.abs(lastFrame.centerY - firstFrame.centerY)).toBeLessThanOrEqual(1)
  expect(new Set(frames.map(frame => frame.transform)).size).toBe(1)

  const canvasBox = await page.locator('.react-flow').boundingBox()
  const nodeBox = await node.boundingBox()
  expect(canvasBox).not.toBeNull()
  expect(nodeBox).not.toBeNull()
  expect(Math.abs(nodeBox!.x + nodeBox!.width / 2 - (canvasBox!.x + canvasBox!.width / 2))).toBeLessThanOrEqual(1)
  expect(Math.abs(nodeBox!.y + nodeBox!.height / 2 - (canvasBox!.y + canvasBox!.height / 2))).toBeLessThanOrEqual(1)
})

test('切换 L1/L2 视角时视口直接到位而不从角落滑入', async ({ page }) => {
  await mockOntologyStructure(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`/#/ontologies/${ontologyId}`, { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: '本体结构', exact: true }).click()
  await expect(page.getByTestId('structure-node-object')).toBeVisible()
  // Wait for the initial fit to fully settle so every later transform change
  // is caused by the level switch itself.
  await page.waitForTimeout(750)

  await page.evaluate(() => {
    const state = window as typeof window & { __levelSwitchTransforms?: string[] }
    state.__levelSwitchTransforms = []
    const startedAt = performance.now()
    const capture = () => {
      const viewport = document.querySelector<HTMLElement>('.react-flow__viewport')
      if (viewport) state.__levelSwitchTransforms?.push(viewport.style.transform)
      if (performance.now() - startedAt < 900) requestAnimationFrame(capture)
    }
    requestAnimationFrame(capture)
  })

  await page.getByRole('button', { name: 'L2', exact: true }).click()
  await expect(page.getByTestId('structure-node-property')).toBeVisible()
  await page.waitForTimeout(950)

  const transforms = await page.evaluate(() => {
    const state = window as typeof window & { __levelSwitchTransforms?: string[] }
    return state.__levelSwitchTransforms || []
  })
  expect(transforms.length).toBeGreaterThan(5)
  // An animated fit interpolates the viewport across ~16 frames; a direct fit
  // changes the transform at most once (before vs after the switch).
  expect(new Set(transforms).size).toBeLessThanOrEqual(2)

  const canvasBox = await page.locator('.react-flow').boundingBox()
  const graphBox = await page.evaluate(() => {
    const rects = Array.from(document.querySelectorAll<HTMLElement>('.react-flow__node'))
      .map(element => element.getBoundingClientRect())
    if (!rects.length) return null
    const left = Math.min(...rects.map(rect => rect.x))
    const top = Math.min(...rects.map(rect => rect.y))
    const right = Math.max(...rects.map(rect => rect.x + rect.width))
    const bottom = Math.max(...rects.map(rect => rect.y + rect.height))
    return { centerX: (left + right) / 2, centerY: (top + bottom) / 2 }
  })
  expect(canvasBox).not.toBeNull()
  expect(graphBox).not.toBeNull()
  expect(Math.abs(graphBox!.centerX - (canvasBox!.x + canvasBox!.width / 2))).toBeLessThanOrEqual(2)
  expect(Math.abs(graphBox!.centerY - (canvasBox!.y + canvasBox!.height / 2))).toBeLessThanOrEqual(2)
})
