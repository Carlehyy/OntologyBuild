import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const WEB = 'http://localhost:5173'
const API = 'http://localhost:8000'

async function login(page: Page) {
  await page.goto(`${WEB}/#/login`)
  await page.getByLabel('用户名', { exact: true }).fill('admin')
  await page.getByLabel('密码', { exact: true }).fill('admin123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(`${WEB}/#/overview`)
  const token = await page.evaluate(() => localStorage.getItem('token') || '')
  expect(token).toBeTruthy()
  return token
}

async function apiJson(
  request: APIRequestContext,
  method: 'GET' | 'POST' | 'PUT',
  path: string,
  token: string,
  data?: unknown,
) {
  const response = await request.fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}` },
    data,
  })
  expect(response.ok(), `${method} ${path}: ${await response.text()}`).toBeTruthy()
  const body = await response.json()
  return body.data ?? body
}

test.describe('本体总览待审批流水线', () => {
  test('使用真实动作数据完成批准、拒绝与决策事实留痕', async ({ page, request }) => {
    const token = await login(page)
    const stamp = Date.now()
    const objectTypeId = `ot-monthly-report-${stamp}`
    const updateLampActionId = `act-update-lamp-${stamp}`
    const refreshGovernanceActionId = `act-refresh-governance-${stamp}`
    const lampRuleId = `rule-lamp-${stamp}`
    const rateRuleId = `rule-rate-${stamp}`
    const instanceId = `inst-ops-2026-07-${stamp}`
    const domainName = `审批回归-${stamp}`
    const domain = await apiJson(request, 'POST', '/api/v1/domains', token, {
      name: domainName,
      description: '待审批流水线端到端回归测试',
    })
    const ontology = await apiJson(request, 'POST', '/api/v1/ontologies', token, {
      name: `运营治理本体-${stamp}`,
      domain: domainName,
      description: '待审批流水线端到端回归测试',
      build_mode: 'simple_llm',
    })
    const ontologyId = ontology.id as string
    const formal = `/api/v2/formal/ontologies/${ontologyId}`

    try {
      await apiJson(request, 'PUT', `${formal}/full`, token, {
        objectTypes: [{
          id: objectTypeId,
          name: 'MonthlyOpsReport',
          displayName: '月度运营报告',
          primaryKey: 'report_month',
          positionX: 0,
          positionY: 0,
          properties: [
            { id: 'report_month', name: 'report_month', displayName: '报告月份', type: 'string', required: true },
            { id: 'lamp_level', name: 'lamp_level', displayName: '运营灯号', type: 'string' },
            { id: 'consistency_rate', name: 'consistency_rate', displayName: '治理一致率', type: 'number' },
          ],
        }],
        linkTypes: [],
        actions: [
          {
            id: updateLampActionId,
            name: 'update_lamp',
            displayName: '更新运营灯号',
            objectTypeId,
            requiresApproval: true,
            parameters: [{ name: 'lamp_level', displayName: '运营灯号', type: 'string', required: true }],
            rules: [{
              id: lampRuleId, type: 'update_property', name: '更新运营灯号', enabled: true, order: 0,
              config: { targetProperty: 'lamp_level', valueSource: 'parameter', value: 'lamp_level' },
            }],
          },
          {
            id: refreshGovernanceActionId,
            name: 'refresh_governance',
            displayName: '刷新治理指标',
            objectTypeId,
            requiresApproval: true,
            parameters: [{ name: 'consistency_rate', displayName: '治理一致率', type: 'number', required: true }],
            rules: [{
              id: rateRuleId, type: 'update_property', name: '刷新治理指标', enabled: true, order: 0,
              config: { targetProperty: 'consistency_rate', valueSource: 'parameter', value: 'consistency_rate' },
            }],
          },
        ],
        functions: [],
        instances: [{
          id: instanceId,
          objectTypeId,
          properties: { report_month: '2026-07', lamp_level: '黄灯', consistency_rate: 92 },
          computed: {},
        }],
        linkInstances: [],
      })

      for (const [actionId, parameters] of [
        [updateLampActionId, { lamp_level: '绿灯' }],
        [refreshGovernanceActionId, { consistency_rate: 100 }],
      ] as const) {
        const result = await apiJson(request, 'POST', `${formal}/run-action`, token, {
          actionId,
          parameters,
          targetInstanceId: instanceId,
          dryRun: false,
          idempotencyKey: `${actionId}-${stamp}`,
        })
        expect(result.status).toBe('pending')
      }

      await page.goto(`${WEB}/#/ontologies/${ontologyId}`)
      const pipeline = page.getByTestId('approval-pipeline')
      await expect(pipeline).toHaveAttribute('data-pending-count', '2')
      await expect(pipeline).toContainText('lamp_level = 绿灯')
      await expect(pipeline).toContainText('consistency_rate = 100')
      await expect(pipeline).toContainText('月度运营报告 · 2026-07')

      await page.locator('.approval-inspector .approve-button').click()
      await expect(pipeline).toHaveAttribute('data-pending-count', '1')
      await expect(page.locator('.approval-message')).toContainText('已批准并执行')

      await page.locator('.approval-inspector .reject-button').click()
      await expect(pipeline).toHaveAttribute('data-pending-count', '0')
      await expect(page.locator('.approval-message')).toContainText('已拒绝')
      await expect(pipeline).toContainText('流水线已清空')

      const facts = await apiJson(request, 'GET', `${formal}/facts/recent?limit=20&kind=decision`, token)
      expect(new Set(facts.map((fact: { value: string }) => fact.value))).toEqual(new Set(['APPROVED', 'REJECTED']))
      const full = await apiJson(request, 'GET', `${formal}/full`, token)
      const instance = full.instances.find((item: { id: string }) => item.id === instanceId)
      expect(instance.properties.consistency_rate).toBe(100)
      expect(instance.properties.lamp_level).toBe('黄灯')
    } finally {
      await request.delete(`${API}/api/v1/ontologies/${ontologyId}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      await request.delete(`${API}/api/v1/domains/${domain.id}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
    }
  })
})
