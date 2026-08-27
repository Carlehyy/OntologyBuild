import { expect, test, type Page, type Route } from '@playwright/test'
import * as fs from 'node:fs'

const ontologyId = 'ontology-structure-doc-dialog'

const DOC_MD = [
  '# 订单需求说明',
  '',
  '围绕订单全流程沉淀的业务口径。',
  '',
  '## 业务对象',
  '',
  '- 订单：核心交易实体',
  '',
  '### 属性约束',
  '',
  '订单号必填且全局唯一。',
  '',
  '```python',
  '# 注释行不是标题',
  'value = 1',
  '```',
  '',
  '## 数据来源',
  '',
  '来自订单中台同步。',
  '',
  ...Array.from({ length: 40 }, (_, index) => `第 ${index + 1} 段补充口径说明，保证正文超出可视区高度，目录跳转后可以观察到滚动。`),
  '',
].join('\n')

/** 结构页最小后端桩：发布工作区 + 可配置的版本语义层（需求文档）。 */
async function mockStructurePage(page: Page, options: { semantic?: unknown } = {}) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'structure-doc-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'structure-doc-token',
        user: { id: 'structure-doc-user', username: 'structure-doc-user', role: 'admin' },
      },
      version: 0,
    }))
  })

  const ok = (route: Route, data: unknown) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ data, message: 'ok' }),
  })

  const semanticRequests: string[] = []

  await page.route(/^https?:\/\/[^/]+\/api\/v[12]\//, async (route) => {
    const path = new URL(route.request().url()).pathname

    if (path === `/api/v1/ontologies/${ontologyId}`) {
      return ok(route, {
        id: ontologyId,
        name: '结构说明弹窗测试本体',
        domain: '供应链',
        description: '验证本体结构页的结构说明弹窗',
        version: 'v1',
        current_release_id: 'release-1',
        current_release_version: 'v1',
        status: 'published',
        entity_count: 1,
        relation_count: 0,
        action_count: 0,
        sentinel_count: 0,
        created_by: 'structure-doc-user',
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
      })
    }
    if (path === `/api/v2/formal/ontologies/${ontologyId}/agent/dynamic-sentinels`) {
      return ok(route, [])
    }
    if (path === `/api/v2/formal/ontologies/${ontologyId}/overview`) {
      return ok(route, {
        release: { id: 'release-1', version: 'v1', publishedAt: '2026-08-07T00:00:00Z' },
        model: { objectTypes: 1, linkTypes: 0, actions: 0, actionsRequiringApproval: 0, functions: 0, sentinels: { total: 0, enabled: 0, muted: 0 } },
        data: { instances: 0, instancesBySource: {}, linkInstances: 0, mappings: { total: 0, bound: 0, nameMatch: 0, autoCreate: 0, autoApply: 0 }, topTypes: [] },
        runtime: { pendingApprovals: 0, decisions: { total: 0, approved: 0, rejected: 0, recentApprovalRate: null }, firings7d: { total: 0, fired: 0, error: 0 }, actionRuns7d: { success: 0, failed: 0, total: 0 }, daily7d: [] },
        facts: { total: 0, byKind: {} },
      })
    }
    if (path === `/api/v2/formal/ontologies/${ontologyId}/facts/recent`) {
      return ok(route, [])
    }
    if (path === '/api/v2/inbox/summary') {
      return ok(route, { openAlertCount: 0, actionableCount: 0, unreadCount: 0, resolvedCount: 0 })
    }
    if (path === `/api/v2/ontologies/${ontologyId}/versions/release-1/semantic`) {
      semanticRequests.push(path)
      return ok(route, options.semantic === undefined
        ? {
            semantic: {
              documentTitle: '订单业务需求文档',
              documentMd: DOC_MD,
            },
            overview: { hasSemanticLayer: true, documentTitle: '订单业务需求文档' },
          }
        : options.semantic)
    }
    return ok(route, [])
  })

  return { semanticRequests }
}

async function openStructureTab(page: Page) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/ontologies/' + ontologyId, { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: '本体结构', exact: true }).click()
  await expect(page.getByTestId('structure-node-object')).toBeVisible()
}

test('结构说明按钮替换智能整理，弹窗展示需求文档并支持目录跳转与下载', async ({ page }) => {
  const { semanticRequests } = await mockStructurePage(page)
  await openStructureTab(page)

  // 按钮文案已替换：智能整理入口不再存在
  await expect(page.getByRole('button', { name: '结构说明' })).toBeVisible()
  await expect(page.getByRole('button', { name: '智能整理图谱' })).toHaveCount(0)

  await page.getByRole('button', { name: '结构说明' }).click()
  await expect(page.getByTestId('structure-doc-content')).toBeVisible()
  // 查询的是当前发布版本（release-1）的语义层
  await expect.poll(() => semanticRequests.length).toBe(1)
  expect(semanticRequests[0]).toBe(`/api/v2/ontologies/${ontologyId}/versions/release-1/semantic`)

  // 目录来自 Markdown 标题层级，代码围栏内的 # 注释不进目录
  const tocItems = page.getByTestId('structure-doc-toc-item')
  await expect(tocItems).toHaveCount(4)
  await expect(tocItems.filter({ hasText: '订单需求说明' })).toHaveCount(1)
  await expect(tocItems.filter({ hasText: '业务对象' })).toHaveCount(1)
  await expect(tocItems.filter({ hasText: '属性约束' })).toHaveCount(1)
  await expect(tocItems.filter({ hasText: '数据来源' })).toHaveCount(1)
  await expect(page.getByTestId('structure-doc-content')).toContainText('# 注释行不是标题')
  await expect(page.getByTestId('structure-doc-content')).toContainText('订单号必填且全局唯一。')
  await expect(page.getByText('发布版本 v1')).toBeVisible()

  // 点击目录项跳转正文位置，目录项进入激活态；平滑滚动用轮询等待到位
  await tocItems.filter({ hasText: '数据来源' }).click()
  await expect(tocItems.filter({ hasText: '数据来源' })).toHaveAttribute('aria-current', 'true')
  await expect.poll(
    () => page.getByTestId('structure-doc-content').evaluate(element => element.scrollTop),
    { timeout: 5000 },
  ).toBeGreaterThan(0)

  // 底部下载按钮：真实下载文件，文件名取文档标题，内容为 Markdown 原文
  const downloadPromise = page.waitForEvent('download')
  await page.getByTestId('structure-doc-download').click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('订单业务需求文档.md')
  const downloaded = fs.readFileSync(await download.path(), 'utf-8')
  expect(downloaded).toContain('## 数据来源')
  expect(downloaded).toContain('订单号必填且全局唯一。')

  await page.getByLabel('关闭结构说明').click()
  await expect(page.getByTestId('structure-doc-content')).toHaveCount(0)
})

test('版本没有语义层时展示允许的空态', async ({ page }) => {
  await mockStructurePage(page, { semantic: { semantic: null, overview: { hasSemanticLayer: false } } })
  await openStructureTab(page)

  await page.getByRole('button', { name: '结构说明' }).click()
  await expect(page.getByTestId('structure-doc-empty')).toContainText('当前版本没有关联的需求文档')
  await expect(page.getByTestId('structure-doc-download')).toHaveCount(0)
})

test('语义层存在但需求文档为空内容时展示空内容态', async ({ page }) => {
  await mockStructurePage(page, { semantic: { semantic: { documentTitle: '', documentMd: '' }, overview: { hasSemanticLayer: true } } })
  await openStructureTab(page)

  await page.getByRole('button', { name: '结构说明' }).click()
  await expect(page.getByTestId('structure-doc-empty')).toContainText('需求文档内容为空')
  await expect(page.getByTestId('structure-doc-download')).toHaveCount(0)
})
