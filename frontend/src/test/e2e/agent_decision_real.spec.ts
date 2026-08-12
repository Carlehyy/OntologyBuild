import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import {
  STACK_ADMIN_PASSWORD,
  STACK_ADMIN_USERNAME,
} from './support/stack-credentials'

const API = process.env.PLAYWRIGHT_API_URL || 'http://localhost:8000'

async function login(page: Page): Promise<string> {
  await page.goto('/#/login')
  await page.getByLabel('用户名', { exact: true }).fill(STACK_ADMIN_USERNAME)
  await page.getByLabel('密码', { exact: true }).fill(STACK_ADMIN_PASSWORD)
  await page.locator('button[type="submit"]').click()
  await page.waitForURL('**/#/agent')
  const token = await page.evaluate(() => localStorage.getItem('token'))
  expect(token).toBeTruthy()
  return token!
}

async function api<T>(
  request: APIRequestContext,
  token: string,
  method: 'get' | 'post' | 'put',
  path: string,
  data?: unknown,
): Promise<T> {
  const response = await request[method](API + path, {
    headers: { Authorization: `Bearer ${token}` },
    data,
    timeout: 300_000,
  })
  expect(response.ok(), `${method.toUpperCase()} ${path}: ${await response.text()}`).toBeTruthy()
  const body = await response.json()
  return body.data ?? body
}

test('真实模型从助手消息完成隔离决策推演并保持实例不变', async ({ page, request }) => {
  test.skip(process.env.PLAYWRIGHT_EXPECT_LLM !== '1', '只在显式真实模型验收时运行')
  test.setTimeout(360_000)
  const token = await login(page)
  const suffix = Date.now().toString(36)
  const ontology = await api<any>(request, token, 'post', '/api/v1/ontologies', {
    name: `决策推演真机-${suffix}`,
    domain: '供应链',
    description: '验证通用决策推演引擎的真实模型端到端链路',
  })

  try {
    const demandValues = [68, 72, 79, 86, 96, 110, 128]
    await api(request, token, 'put', `/api/v2/formal/ontologies/${ontology.id}/full`, {
      objectTypes: [{
        id: `ot-product-${suffix}`, name: 'Product', displayName: '商品', primaryKey: 'sku',
        positionX: 0, positionY: 0,
        properties: [
          { id: 'p-sku', name: 'sku', displayName: '商品编码', type: 'string', required: true },
          { id: 'p-name', name: 'name', displayName: '商品名称', type: 'string', required: true },
          { id: 'p-stock', name: 'stock', displayName: '当前库存', type: 'number', required: true },
          { id: 'p-safety', name: 'safety_stock', displayName: '安全库存', type: 'number', required: true },
          { id: 'p-lead', name: 'lead_time_days', displayName: '补货提前期', type: 'number', required: true },
          { id: 'p-cost', name: 'unit_cost', displayName: '单位成本', type: 'number', required: true },
        ],
      }, {
        id: `ot-demand-${suffix}`, name: 'DemandSignal', displayName: '需求信号', primaryKey: 'date',
        positionX: 320, positionY: 0,
        properties: [
          { id: 'd-date', name: 'date', displayName: '日期', type: 'date', required: true },
          { id: 'd-orders', name: 'orders', displayName: '订单量', type: 'number', required: true },
          { id: 'd-promo', name: 'promotion_index', displayName: '促销强度', type: 'number', required: true },
        ],
      }],
      linkTypes: [{
        id: `lt-demand-${suffix}`, name: 'signals_demand_for', displayName: '反映需求',
        sourceObjectTypeId: `ot-demand-${suffix}`, targetObjectTypeId: `ot-product-${suffix}`,
        cardinality: 'many-to-one', properties: [],
      }],
      actions: [], functions: [], instances: [], linkInstances: [],
    })
    // 先发布模式，再通过运行时实例 API 写入数据，确保每行都绑定当前不可变发布版。
    await api(request, token, 'post', `/api/v2/ontologies/${ontology.id}/versions`, {
      version_label: 'decision-e2e', description: '真实决策推演验收发布版',
    })
    await api(request, token, 'post', `/api/v2/formal/ontologies/${ontology.id}/instances`, {
      id: `product-${suffix}`, objectTypeId: `ot-product-${suffix}`,
      properties: { sku: 'P-100', name: '轻量保温杯', stock: 180, safety_stock: 120, lead_time_days: 7, unit_cost: 42 },
      computed: {}, source: 'manual',
    })
    for (const [index, orders] of demandValues.entries()) {
      await api(request, token, 'post', `/api/v2/formal/ontologies/${ontology.id}/instances`, {
        id: `demand-${index}-${suffix}`, objectTypeId: `ot-demand-${suffix}`,
        properties: { date: `2026-07-${String(16 + index).padStart(2, '0')}`, orders, promotion_index: index < 4 ? 1 : 2 },
        computed: {}, source: 'manual',
      })
      await api(request, token, 'post', `/api/v2/formal/ontologies/${ontology.id}/link-instances`, {
        linkTypeId: `lt-demand-${suffix}`,
        sourceObjectId: `demand-${index}-${suffix}`,
        targetObjectId: `product-${suffix}`,
        properties: {},
      })
    }

    await page.goto('/#/agent')
    await page.getByLabel('选择本体').selectOption(ontology.id)
    const model = page.getByLabel('选择对话模型')
    const flashId = await model.locator('option').evaluateAll(options =>
      options.find(option => option.textContent?.toLowerCase().includes('flash'))?.getAttribute('value'),
    )
    expect(flashId, '真实 DeepSeek Flash 配置必须可用').toBeTruthy()
    await model.selectOption(flashId!)

    const question = '请运行决策推演：针对商品 P-100，未来两周临近促销，比较“维持库存”和“提前补货 300 件”两个方案。目标是兼顾缺货风险与资金占用；请给出多视角、可能情景、早期信号和停止条件。'
    await page.getByPlaceholder('问业务问题，或让它帮你预演一个操作…').fill(question)
    await page.getByPlaceholder('问业务问题，或让它帮你预演一个操作…').press('Enter')

    await expect(page.getByRole('heading', { name: '决策推演', exact: true })).toBeVisible()
    await expect(page.getByTestId('decision-simulation-result')).toBeVisible({ timeout: 300_000 })
    const result = page.getByTestId('decision-simulation-result')
    await expect(result).toContainText('方案比较')
    await expect(result).toContainText('多视角审议')
    await expect(result).toContainText('无悔行动')
    await expect(result).toContainText('停止条件')
    await expect(result).toContainText('不是未来概率')
    await expect(result.getByText(/证据审计视角|业务执行视角/).first()).toBeVisible()

    const runs = await api<any[]>(
      request, token, 'get',
      `/api/v2/formal/ontologies/${ontology.id}/agent/decision-simulations`,
    )
    expect(runs).toHaveLength(1)
    expect(runs[0].status).toBe('succeeded')
    expect(runs[0].perspectiveCount).toBeGreaterThanOrEqual(2)

    const instance = await api<any>(
      request, token, 'get',
      `/api/v2/formal/ontologies/${ontology.id}/agent/graph/instances/product-${suffix}`,
    )
    expect(instance.properties.stock).toBe(180)
    await page.screenshot({
      path: '/tmp/ontologybuild-decision-e2e.GzGjtc/decision-simulation.png',
      fullPage: true,
    })
  } finally {
    await request.delete(`${API}/api/v1/ontologies/${ontology.id}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
  }
})
