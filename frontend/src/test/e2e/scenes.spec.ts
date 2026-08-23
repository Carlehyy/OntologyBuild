import { expect, test, type Page, type Route } from '@playwright/test'
import type { SceneSummary } from '@/types/scene'

// 三维场景（阶段一）：导航可见性、卡片列表 CRUD/克隆、详情三标签深链、角色授权。
// 全部 API 本地 mock，属 mocked 套件。

const now = '2026-08-24T08:00:00+00:00'

const json = (route: Route, data: unknown, status = 200) => route.fulfill({
  status,
  contentType: 'application/json',
  body: JSON.stringify({ data }),
})

const sceneA = {
  id: 'scn-1',
  name: '供应链园区',
  description: '园区白模演示场景',
  icon: 'boxes',
  status: 'published',
  current_version_no: 2,
  published_version_no: 1,
  created_by: null,
  created_at: now,
  updated_at: now,
}

const sceneB = {
  id: 'scn-2',
  name: '能源场站',
  description: '',
  icon: 'boxes',
  status: 'draft',
  current_version_no: 0,
  published_version_no: null,
  created_by: null,
  created_at: now,
  updated_at: now,
}

const definitionV1 = {
  meta: { id: 'demo-park', name: '演示园区', version: '0.1.0' },
  stage: { camera: { pos: [92, 78, 92], target: [0, 0, 0], fov: 30 } },
  objects: [
    { id: 'office', label: '办公楼', type: 'office', layout: { x: -20, z: 0, w: 12, d: 10, h: 16 } },
    { id: 'warehouse', label: '仓库', type: 'warehouse', layout: { x: 26, z: -10, w: 14, d: 20, h: 18 } },
  ],
  relations: [{ from: 'office', to: 'warehouse', kind: 'flow' }],
}

const versions = {
  items: [
    { id: 'sv-2', scene_id: 'scn-1', version_no: 2, source: 'manual', note: 'v2 草稿', created_by: null, created_at: now },
    { id: 'sv-1', scene_id: 'scn-1', version_no: 1, source: 'manual', note: '初始版本', created_by: null, created_at: now },
  ],
  total: 2,
}

const logs = {
  items: [
    { id: 'log-1', scene_id: 'scn-1', level: 'alarm', object_id: 'warehouse', event_key: 'binding.rule',
      message: '库位利用率 > 95%', payload: { value: 97.2 }, occurred_at: now, recorded_at: now },
    { id: 'log-2', scene_id: 'scn-1', level: 'normal', object_id: 'warehouse', event_key: 'binding.rule',
      message: '指标恢复 normal', payload: {}, occurred_at: now, recorded_at: now },
  ],
  total: 2,
}

async function seedAuth(page: Page, role = 'admin', menuPermissions?: string[]) {
  await page.addInitScript(([userRole, menus]) => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: {
          id: 'u-scene',
          username: 'scene-tester',
          role: userRole,
          ...(menus ? { menu_permissions: menus } : {}),
        },
      },
      version: 0,
    }))
  }, [role, menuPermissions])
}

async function mockPlatformShell(page: Page) {
  await page.route('**/api/v2/inbox/**', route => json(route, { openAlertCount: 0, actionableCount: 0, unreadCount: 0, resolvedCount: 0 }))
  await page.route('**/api/v2/inbox', route => json(route, { items: [], nextCursor: null, hasMore: false }))
}

async function mockScenesApi(page: Page, options: { listItems?: typeof sceneA[] } = {}) {
  const listItems = options.listItems ?? [sceneA, sceneB]
  const createdScenes: SceneSummary[] = []
  await page.route(/\/api\/v2\/scenes(\?.*)?$/, route => {
    const request = route.request()
    if (request.method() === 'GET') {
      const items = [...listItems, ...createdScenes]
      return json(route, { items, total: items.length })
    }
    if (request.method() === 'POST') {
      const body = request.postDataJSON()
      const created: SceneSummary = { ...sceneB, id: 'scn-' + (createdScenes.length + 1), name: body.name, description: body.description || '', status: 'draft', current_version_no: 0, published_version_no: null }
      createdScenes.push(created)
      return json(route, { ...created }, 201)
    }
    return json(route, {})
  })
  await page.route(/\/api\/v2\/scenes\/scn-clone(\?.*)?$/, route =>
    json(route, { ...sceneA, id: 'scn-clone', name: '供应链园区-副本', status: 'draft', published_version_no: null } as SceneSummary))
  await page.route(/\/api\/v2\/scenes\/scn-1\/clone(\?.*)?$/, route =>
    json(route, { ...sceneA, id: 'scn-clone', name: '供应链园区-副本', status: 'draft', published_version_no: null } as SceneSummary, 201))
  await page.route(/\/api\/v2\/scenes\/scn-1\/publish(\?.*)?$/, route =>
    json(route, { ...sceneA, status: 'published', published_version_no: 2 }))
  await page.route(/\/api\/v2\/scenes\/scn-1\/versions(\?.*)?$/, route => json(route, versions))
  await page.route(/\/api\/v2\/scenes\/scn-1\/versions\/\d+(\?.*)?$/, route =>
    json(route, { ...versions.items[1], definition: definitionV1 }))
  await page.route(/\/api\/v2\/scenes\/scn-1\/runtime-logs(\?.*)?$/, route => json(route, logs))
  await page.route(/\/api\/v2\/scenes\/scn-1(\?.*)?$/, route => json(route, { ...sceneA, version_count: 2 }))
}

test('admin 左侧导航出现「三维场景」且位于「本体助手」之前', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  await mockScenesApi(page)
  await page.goto('/#/scenes')
  const nav = page.locator('nav')
  await expect(nav.getByText('三维场景', { exact: true })).toBeVisible()
  await expect(nav.getByText('本体助手', { exact: true })).toBeVisible()
  const sceneOrder = await nav.getByText('三维场景', { exact: true }).evaluate(el => {
    const navEl = el.closest('nav')
    return Array.from(navEl?.querySelectorAll('a, button') ?? []).findIndex(x => x.textContent?.includes('三维场景'))
  })
  const agentOrder = await nav.getByText('本体助手', { exact: true }).evaluate(el => {
    const navEl = el.closest('nav')
    return Array.from(navEl?.querySelectorAll('a, button') ?? []).findIndex(x => x.textContent?.includes('本体助手'))
  })
  expect(sceneOrder).toBeGreaterThanOrEqual(0)
  expect(agentOrder).toBeGreaterThan(sceneOrder)
})

test('列表页渲染卡片并支持新建场景', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  await mockScenesApi(page)
  await page.goto('/#/scenes')
  await expect(page.getByText('供应链园区')).toBeVisible()
  await expect(page.getByText('能源场站')).toBeVisible()
  // 状态徽章：发布态 / 草稿态
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()
  await expect(page.getByText('草稿', { exact: true }).first()).toBeVisible()
  // 新建
  await page.getByRole('button', { name: '新建场景' }).click()
  await page.getByLabel('场景名称', { exact: true }).fill('港口调度场景')
  await page.getByRole('button', { name: '创建' }).click()
  await expect(page.getByText('港口调度场景')).toBeVisible()
})

test('克隆发布态场景生成副本草稿', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  await mockScenesApi(page)
  await page.goto('/#/scenes')
  const card = page.locator('article', { hasText: '供应链园区' })
  await card.getByRole('button', { name: '克隆' }).click()
  await page.getByRole('button', { name: /确认克隆|克隆/ }).last().click()
  await expect(page.getByText('供应链园区-副本')).toBeVisible()
})

test('详情页三标签 ?tab= 深链与切换', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  await mockScenesApi(page)
  await page.goto('/#/scenes/scn-1?tab=logs')
  await expect(page.getByText('库位利用率 > 95%')).toBeVisible()
  // 深链直达运行日志标签
  await expect(page).toHaveURL(/tab=logs/)
  // 切到场景模型标签：URL 写回 + 对象清单渲染
  await page.getByRole('tab', { name: '场景模型' }).click()
  await expect(page).toHaveURL(/tab=models/)
  await expect(page.getByRole('cell', { name: 'warehouse' }).first()).toBeVisible()
  await expect(page.getByText('办公楼')).toBeVisible()
  // 场景展示标签：版本下拉默认选中已发布 v1
  await page.getByRole('tab', { name: '场景展示' }).click()
  await expect(page).toHaveURL(/\/scenes\/scn-1$/)
})

test('custom 角色未授权 scenes 时直达被拦截', async ({ page }) => {
  await seedAuth(page, 'custom', ['overview'])
  await mockPlatformShell(page)
  await mockScenesApi(page)
  await page.goto('/#/scenes')
  await expect(page.getByText('供应链园区')).toHaveCount(0)
})
