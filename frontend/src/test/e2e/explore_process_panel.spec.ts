import { expect, test, type Page, type Route } from '@playwright/test'

const canvas = {
  objects: [{
    id: 'o1', name: 'PurchaseOrder', display_name: '采购订单', key_attribute: 'po_no',
    attributes: [
      { name: 'po_no', display_name: '订单号', type_hint: '文本' },
      { name: 'status', display_name: '状态', type_hint: '枚举', enum: ['draft', 'approved', 'paid'] },
    ],
    relations: [],
  }],
  actors: [{
    id: 'a1', name: 'buyer', display_name: '采购员', kind: 'role',
    responsibilities: ['提交采购申请'],
  }],
  behaviors: [{
    id: 'b1', name: 'approve_order', display_name: '审批订单', actor: 'buyer', object: 'PurchaseOrder',
    trigger: '订单金额超过五万', outcome: '订单进入已审批', needs_approval: false,
  }],
  events: [],
  rules: [],
  processes: [{
    id: 'p1', name: 'procure_to_pay', display_name: '采购到付款',
    description: '标准采购流程',
    goal: '把采购需求转化为准时付款的订单',
    trigger: '采购需求审批通过',
    steps: [
      {
        id: 'p1-s1', seq: 1, name: '提交采购申请', actor: 'buyer', behavior: null,
        inputs: ['采购需求'], outputs: ['采购申请单'], description: '汇总需求后提交',
      },
      {
        id: 'p1-s2', seq: 2, name: '审批订单', actor: 'buyer', behavior: 'approve_order',
        inputs: ['采购申请单'], outputs: ['审批结果'], description: null,
      },
      {
        id: 'p1-s3', seq: 3, name: '安排付款', actor: null, behavior: null,
        inputs: ['审批结果'], outputs: ['付款单'], description: '财务线下执行',
      },
    ],
    branches: [
      { id: 'p1-b1', from_step: 2, to_step: 3, condition: '审批通过', kind: 'normal' },
      { id: 'p1-b2', from_step: 2, to_step: null, condition: '审批驳回', kind: 'exception' },
    ],
    objects: ['PurchaseOrder'],
    metrics: [{
      id: 'p1-m1', name: 'cycle_days', display_name: '采购周期',
      formula: 'avg(付款时间 - 申请时间)', source_objects: ['PurchaseOrder'], target: '不超过 7 天',
    }],
    expected_outcome: '订单按时付款，供应商履约',
  }],
  scenarios: [{
    id: 's1', name: 'urgent_purchase', display_name: '紧急采购',
    goal: '紧急缺料时当天完成采购', actors: ['buyer'],
    steps: ['发起紧急申请', '特批', '当天付款'],
    objects: ['PurchaseOrder'], behaviors: ['approve_order'], branches: [],
    expected_outcome: '当天到货', process_ref: 'procure_to_pay', metrics: [],
  }],
  questions: [],
}

const readiness = {
  ready: true,
  stage: '已就绪 · 全部质量门通过，可生成需求文档与本体草稿',
  gatesPassed: 10,
  gatesTotal: 10,
  blockingCount: 0,
  advisoryCount: 0,
  openQuestions: { blocking: 0, advisory: 0 },
  gates: [
    { id: 'scope', label: '业务边界', passed: true, blockingItems: [], advisoryItems: [] },
    { id: 'objects', label: '对象齐备', passed: true, blockingItems: [], advisoryItems: [] },
    { id: 'relations', label: '关系闭合', passed: true, blockingItems: [], advisoryItems: [] },
    { id: 'behaviors', label: '行为落位', passed: true, blockingItems: [], advisoryItems: [] },
    { id: 'lifecycles', label: '状态闭环', passed: true, blockingItems: [], advisoryItems: [] },
    { id: 'rules', label: '规则定量', passed: true, blockingItems: [], advisoryItems: [] },
    { id: 'events', label: '事件可追溯', passed: true, blockingItems: [], advisoryItems: [] },
    { id: 'processes', label: '流程编排', passed: true, blockingItems: [], advisoryItems: [] },
    { id: 'questions', label: '疑问清零', passed: true, blockingItems: [], advisoryItems: [] },
    { id: 'coverage', label: '场景验收', passed: true, blockingItems: [], advisoryItems: [] },
  ],
}

const diagramSource: Record<string, string> = {
  er: 'erDiagram\n  A ||--|| B : has',
  flow: 'flowchart LR\n  s1 --> s2',
  sequence: 'sequenceDiagram\n  a->>b: hi',
  state: 'stateDiagram-v2\n  [*] --> open',
}

async function mockExplore(page: Page, flowTargets: (string | null)[]) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: { token: 'e2e-token', user: { id: 'u1', username: 'tester', role: 'admin' } },
      version: 0,
    }))
  })
  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (!path.startsWith('/api/')) return route.continue()
    const ok = (data: unknown) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data, message: 'ok' }),
    })
    if (path === '/api/v2/exploration/sessions') {
      return ok([{ id: 's1', title: '流程面板测试', canvasVersion: 1, status: 'active', createdAt: '', updatedAt: '' }])
    }
    if (path === '/api/v2/exploration/sessions/s1') {
      return ok({
        id: 's1', title: '流程面板测试', canvasVersion: 1, status: 'active', createdAt: '', updatedAt: '',
        canvas,
        completeness: {
          counts: { objects: 1, actors: 1, behaviors: 1, events: 0, rules: 0, processes: 1, scenarios: 1 },
          gaps: [],
        },
        readiness,
        messages: [],
      })
    }
    if (path === '/api/v2/exploration/sessions/s1/attachments') return ok([])
    if (path === '/api/v2/exploration/sessions/s1/documents') return ok([])
    const diagramMatch = path.match(/^\/api\/v2\/exploration\/sessions\/s1\/diagrams\/(\w+)$/)
    if (diagramMatch) {
      const kind = diagramMatch[1]
      if (kind === 'flow' || kind === 'sequence') flowTargets.push(url.searchParams.get('target'))
      return ok({ kind, title: `${kind} 图`, mermaid: diagramSource[kind] || diagramSource.er, warnings: [] })
    }
    return ok([])
  })
}

test.describe('业务探索流程模型面板', () => {
  test('流程分区渲染徽标，图示 target 下拉含流程名，质量门为 10 门口径', async ({ page }) => {
    const flowTargets: (string | null)[] = []
    await mockExplore(page, flowTargets)
    await page.goto('/#/explore', { waitUntil: 'domcontentloaded' })

    // 空对话欢迎文案已切换为七类模型口径
    await expect(page.getByText(/对象、主体、行为、事件、规则、流程、场景七类模型/)).toBeVisible()

    // 质量门：10 门口径，展开可见「流程编排」门
    const region = page.getByTestId('business-scenario-region')
    await expect(region.getByText('10/10', { exact: true })).toBeVisible()
    await region.getByRole('button', { name: /质量门/ }).click()
    await expect(region.getByText('流程编排', { exact: true })).toBeVisible()

    // 流程模型分区：卡片与徽标（步数/分支/指标/异常路径）
    await region.getByRole('button', { name: /流程模型/ }).click()
    const processCard = region.getByRole('button', { name: /采购到付款/ })
    await expect(processCard).toBeVisible()
    await expect(processCard.getByText('3 步', { exact: true })).toBeVisible()
    await expect(processCard.getByText('2 分支', { exact: true })).toBeVisible()
    await expect(processCard.getByText('1 指标', { exact: true })).toBeVisible()
    await expect(processCard.getByText('含异常路径', { exact: true })).toBeVisible()

    // 图示弹窗：flow/sequence 的 target 下拉合并场景名与流程名
    const diagramModal = page.locator('div.fixed.inset-0').filter({ has: page.getByTestId('canvas-diagram-title') })
    await page.getByTestId('business-flow-button').click()
    await page.getByRole('button', { name: '流程图', exact: true }).click()
    const flowSelect = diagramModal.locator('select')
    await expect(flowSelect.locator('option')).toContainText([
      '默认场景或流程（第一个）', '紧急采购', '采购到付款',
    ])
    await flowSelect.selectOption({ label: '采购到付款' })
    await expect.poll(() => flowTargets.at(-1)).toBe('采购到付款')

    await page.getByRole('button', { name: '时序图', exact: true }).click()
    const sequenceSelect = diagramModal.locator('select')
    await expect(sequenceSelect.locator('option')).toContainText([
      '默认场景或流程（第一个）', '紧急采购', '采购到付款',
    ])
    await sequenceSelect.selectOption({ label: '采购到付款' })
    await expect.poll(() => flowTargets.at(-1)).toBe('采购到付款')

    // 状态图仍为对象口径，不混入流程名
    await page.getByRole('button', { name: '状态图', exact: true }).click()
    const stateSelect = diagramModal.locator('select')
    await expect(stateSelect.locator('option')).toContainText(['自动选择对象', '采购订单'])
  })
})
