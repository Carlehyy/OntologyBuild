import { expect, test, type Page, type Route } from '@playwright/test'

const customerObject = {
  id: 'ot-customer',
  name: 'Customer',
  displayName: '客户',
  primaryKey: 'customer_id',
  properties: [
    { id: 'p-id', name: 'customer_id', displayName: '客户编号', type: 'string' },
    { id: 'p-name', name: 'customer_name', displayName: '客户名称', type: 'string' },
  ],
}

const suggestionResponse = {
  llmAvailable: true,
  knowledgeHits: 1,
  suggestions: [{
    datasetId: 'ds-customers',
    datasetName: '客户表',
    objectTypeId: 'ot-customer',
    pairingVerdict: 'match',
    pairingReason: '列级历史映射多数指向该对象（数据飞轮复用）',
    primaryKeyColumn: 'cust_id',
    existingObjectTypeId: null,
    fieldMappings: [
      {
        column: 'cust_id', property: 'customer_id', verdict: 'match',
        confidence: 0.92, reason: '历史映射复用·已确认 3 次', source: 'knowledge',
      },
      {
        column: 'cust_name', property: 'customer_name', verdict: 'unsure',
        confidence: 0.5, reason: '概念化：客户名称', source: 'llm',
      },
    ],
    skippedColumns: [{ column: 'age', reason: '未找到可信的本体属性对应' }],
    error: null,
  }],
}

async function mockSuggestionWorkspace(page: Page) {
  let savedBody: Record<string, unknown> | null = null
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: { id: 'u1', username: 'tester', role: 'admin' },
      },
      version: 0,
    }))
    localStorage.setItem('mapping-tutorial:ont-customer', 'seen')
  })
  await page.route(/^https?:\/\/[^/]+\/api\/v[12]\//, async (route: Route) => {
    const url = new URL(route.request().url())
    const ok = (data: unknown) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data, message: 'ok' }),
    })

    if (
      url.pathname === '/api/v2/ontologies/ont-customer/versions/draft-1/mapping-suggestions'
      && route.request().method() === 'POST'
    ) {
      return ok(suggestionResponse)
    }
    if (
      url.pathname === '/api/v2/ontologies/ont-customer/versions/draft-1/workspace/mappings'
      && route.request().method() === 'PUT'
    ) {
      savedBody = route.request().postDataJSON()
      return ok({ revision: 'r2' })
    }
    if (url.pathname === '/api/v2/ontologies/ont-customer/versions/draft-1/workspace') {
      return ok({
        versionId: 'draft-1',
        versionNumber: 'v0.1',
        workspaceMode: 'draft',
        editable: true,
        revision: 'r1',
        objectTypes: [customerObject],
        linkTypes: [],
        mappings: [],
        linkMappings: [],
      })
    }
    if (url.pathname === '/api/v2/curated') {
      return ok([])
    }
    if (url.pathname === '/api/v2/datasets/overview') {
      return ok({
        items: [{
          id: 'ds-customers',
          name: '客户表',
          kind: 'structured',
          source: 'manual',
          primary_key: 'cust_id',
          rowcount: 5,
          version_count: 1,
          latest_version_no: 1,
        }],
        total: 1,
        page: 1,
        page_size: 20,
      })
    }
    if (url.pathname === '/api/v2/datasets/ds-customers/schema') {
      return ok({
        dataset_id: 'ds-customers',
        columns: [
          { name: 'cust_id', display_name: '客户编号', type: 'string', nullable: false, is_primary_key: true, sample_values: ['C-1'] },
          { name: 'cust_name', display_name: '客户名称', type: 'string', nullable: true, is_primary_key: false, sample_values: ['甲公司'] },
          { name: 'age', display_name: '年龄', type: 'integer', nullable: true, is_primary_key: false, sample_values: [36] },
        ],
      })
    }
    return ok([])
  })
  return () => savedBody
}

test('智能建议经人工确认后落画布并随保存写入草稿映射', async ({ page }) => {
  const savedBody = await mockSuggestionWorkspace(page)
  await page.goto(
    '/#/ontologies/ont-customer/mapping-config?versionId=draft-1',
    { waitUntil: 'domcontentloaded' },
  )

  // 画布为空时入口禁用；加入数据集后可用
  const openButton = page.getByTestId('mapping-suggest-open')
  await expect(openButton).toBeDisabled()
  await page.locator('.dmc-asset .dmc-add').first().click()
  await expect(openButton).toBeEnabled()
  await openButton.click()

  // 建议面板：知识库命中提示、配对与字段建议
  const panel = page.getByTestId('mapping-suggestion-panel')
  await expect(panel).toBeVisible()
  await expect(page.getByTestId('suggest-knowledge-hits')).toContainText('1 条建议来自历史映射复用')
  await expect(page.getByTestId('suggest-object-ds-customers')).toHaveValue('ot-customer')
  // match 默认勾选；unsure 默认不勾，人工确认后勾选
  const unsureField = page.getByTestId('suggest-field-ds-customers-cust_name')
  await expect(unsureField).not.toBeChecked()
  await unsureField.check()
  await expect(panel.locator('.dmc-suggest-source')).toContainText('历史复用')
  await page.getByTestId('suggest-apply').click()

  // 落画布后仍是未保存的前端草稿，保存才写库
  await expect(page.locator('.dmc-notice')).toContainText('已应用 2 条建议连线')
  const save = page.getByRole('button', { name: '保存配置' })
  await expect(save).toBeEnabled()
  await save.click()
  await expect(page.getByRole('button', { name: '已保存' })).toBeDisabled()

  const body = savedBody() as {
    mappings: Array<{
      curatedDatasetId: string
      targetObjectTypeId: string
      fieldMapping: Record<string, string>
    }>
  }
  expect(body.mappings).toHaveLength(1)
  const mapping = body.mappings[0]
  expect(mapping.curatedDatasetId).toBe('ds-customers')
  expect(mapping.targetObjectTypeId).toBe('ot-customer')
  expect(mapping.fieldMapping.cust_id).toBe('customer_id')
  expect(mapping.fieldMapping.cust_name).toBe('customer_name')
  expect(mapping.fieldMapping.__primary_key__).toBe('cust_id')
})
