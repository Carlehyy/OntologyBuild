import { expect, test, type Page, type Route } from '@playwright/test'

interface MockOptions {
  failFirstApply?: boolean
  datasetStatus?: 'approved' | 'rejected'
  withInstances?: boolean
  withUnmappedObject?: boolean
  noMappings?: boolean
  darkTheme?: boolean
}

async function mockMappingPreview(page: Page, options: MockOptions = {}) {
  const columns = Array.from({ length: 8 }, (_, index) => `field_${index + 1}`)
  const datasetStatus = options.datasetStatus ?? 'approved'
  let applyAttempts = 0
  const requests = { objectsTypeId: '' }
  await page.addInitScript(opts => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: { token: 'e2e-token', user: { id: 'u1', username: 'tester', role: 'admin' } },
      version: 0,
    }))
    if (opts.darkTheme) {
      localStorage.setItem('theme', JSON.stringify({ state: { theme: 'dark' }, version: 0 }))
    }
  }, { darkTheme: Boolean(options.darkTheme) })

  await page.route(/^https?:\/\/[^/]+\/api\/v[12]\//, async (route: Route) => {
    const url = new URL(route.request().url())
    const ok = (data: unknown) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data, message: 'ok' }),
    })

    if (url.pathname === '/api/v1/ontologies/ontology-preview') return ok({
      id: 'ontology-preview', name: '预览测试本体', domain: '测试', version: 'v1',
      current_release_id: 'release-1', current_release_version: 'v1', status: 'published',
      entity_count: 1, relation_count: 0, action_count: 0, sentinel_count: 0,
      created_by: 'tester', created_at: '2026-07-22T00:00:00Z', updated_at: '2026-07-22T00:00:00Z',
    })
    if (url.pathname === '/api/v2/ontologies/ontology-preview/current-release/workspace') return ok({
      versionId: 'release-1', versionNumber: 'v1', isCurrentRelease: true,
      workspaceMode: 'release', editable: false, revision: 'r1',
      objectTypes: [
        {
          id: 'object-order', name: 'Order', displayName: '订单', primaryKey: 'id',
          properties: [{ id: 'id', name: 'id', displayName: '订单编号', type: 'string' }],
        },
        ...(options.withUnmappedObject ? [{
          id: 'object-log', name: 'Log', displayName: '日志', primaryKey: null,
          properties: [{ id: 'msg', name: 'msg', displayName: '消息', type: 'string' }],
        }] : []),
      ],
      linkTypes: [],
      mappings: options.noMappings ? [] : [{
        id: 'mapping-1', curatedDatasetId: 'dataset-wide', targetObjectTypeId: 'object-order',
        entityClass: 'Order',
        fieldMapping: { field_1: 'id', __applied_dataset_version_id__: 'dataset-version-23' },
        status: 'published',
      }],
      linkMappings: [],
    })
    if (url.pathname === '/api/v2/curated') return ok([{
      id: 'dataset-wide', name: '订单宽表', status: datasetStatus, row_count: 45,
      quality_score: .96, primary_key: 'field_1', producer_pipeline_id: null,
      output_key: null, has_review_evidence: true,
    }])
    if (url.pathname === '/api/v2/datasets/overview') return ok({ items: [], total: 0, page: 1, page_size: 20 })
    if (url.pathname === '/api/v2/datasets/dataset-wide/schema') return ok({
      dataset_id: 'dataset-wide',
      columns: columns.map((name, index) => ({
        name, display_name: `字段 ${index + 1}`, type: 'string', nullable: index > 0,
        is_primary_key: index === 0, sample_values: [`值 ${index + 1}`],
      })),
    })
    if (url.pathname === '/api/v2/datasets/dataset-wide/versions') return ok([
      { id: 'dataset-version-23', version_no: 23, rowcount: 45, processed_at: '2026-07-26T08:27:21+00:00' },
    ])
    if (url.pathname === '/api/v2/datasets/dataset-wide/preview') return ok({
      columns,
      total_rows: 2,
      rows: Array.from({ length: 2 }, (_, rowIndex) => Object.fromEntries(
        columns.map((column, columnIndex) => [column, `R${rowIndex + 1}-C${columnIndex + 1}`]),
      )),
    })
    if (
      url.pathname === '/api/v2/ontologies/ontology-preview/mappings/mapping-1/apply-from-dataset'
      && route.request().method() === 'POST'
    ) {
      applyAttempts += 1
      await new Promise(resolve => setTimeout(resolve, 180))
      if (options.failFirstApply && applyAttempts === 1) {
        return route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: {
              code: 'dataset_version_not_approved',
              message: '当前成品版本尚未批准，不能进入正式本体',
            },
          }),
        })
      }
      return ok({
        total_entities: 2,
        total_relations: 1,
        sentinel_dispatch: { evaluated: 2, fired: 1 },
      })
    }
    if (url.pathname === '/api/v2/curated/dataset-wide/preview') {
      const offset = Number(url.searchParams.get('offset') || 0)
      const limit = Number(url.searchParams.get('limit') || 20)
      const end = Math.min(offset + limit, 45)
      return ok({
        dataset_id: 'dataset-wide', name: '订单宽表', columns, total_rows: 45,
        offset, limit, has_more: end < 45,
        rows: Array.from({ length: end - offset }, (_, rowIndex) => Object.fromEntries(
          columns.map((column, columnIndex) => [column, `R${offset + rowIndex + 1}-C${columnIndex + 1}`]),
        )),
        count: end - offset,
      })
    }
    if (
      url.pathname === '/api/v2/formal/ontologies/ontology-preview/instances'
      && options.withInstances
    ) return ok([{ id: 'inst-1', objectTypeId: 'object-order' }])
    if (url.pathname === '/api/v2/formal/ontologies/ontology-preview/instance-browser/catalog') {
      return ok({
        release: { id: 'release-1', version: 'v1' },
        objectTypes: [{
          id: 'object-order', name: 'Order', displayName: '订单', primaryKey: 'id',
          properties: [{ id: 'id', name: 'id', displayName: '订单编号', type: 'string', required: true }],
          instanceCount: 1,
          associatedDatasets: [{
            id: 'dataset-wide', name: '订单宽表', kind: 'curated', roles: ['实体数据'], available: true,
          }],
        }],
        linkTypes: [],
        legacyProjection: {
          objectInstances: 0, linkInstances: 0, total: 0,
          canAdopt: false, recommendedAction: 'none', blockingReasons: [],
        },
      })
    }
    if (url.pathname === '/api/v2/formal/ontologies/ontology-preview/instance-browser/objects') {
      requests.objectsTypeId = url.searchParams.get('object_type_id') || ''
      return ok({
        release: { id: 'release-1', version: 'v1' },
        items: [{
          id: 'inst-1', objectTypeId: 'object-order',
          properties: { id: 'ORD-1001' }, computed: {},
          createdAt: '2026-07-26T00:00:00Z', updatedAt: '2026-07-26T00:00:00Z',
        }],
        total: 1, page: 1, pageSize: 20,
      })
    }
    if (url.pathname.startsWith('/api/v2/formal/ontologies/ontology-preview/')) return ok([])
    return ok([])
  })
  return { applyAttempts: () => applyAttempts, requests }
}

test('数据源眼睛按钮打开分页预览，宽表提供横向滚动', async ({ page }) => {
  await mockMappingPreview(page)
  await page.goto('/#/ontologies/ontology-preview?tab=data-mapping', { waitUntil: 'domcontentloaded' })

  await expect(page.getByText('映射结果清单')).toBeVisible()
  await expect(page.getByText('数据血缘详情')).toBeVisible()
  await expect(page.getByText('把本体结构，接到真实数据上')).toHaveCount(0)
  // 供给全景图是懒加载图表，落地前会把清单头部向下推；先等其 y 连续两次
  // 采样不变，再在同一个 JS 任务内原子量测两个元素——分两次 await 读取会
  // 在中间插入重排，造成对齐断言偶发失败。
  const registerHead = page.locator('.dmo-register-head')
  let anchor = await registerHead.boundingBox()
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await page.waitForTimeout(120)
    const next = await registerHead.boundingBox()
    const settled = anchor && next && Math.abs(next.y - anchor.y) < 1
    anchor = next
    if (settled) break
  }
  const { filtersBox, searchBox } = await page.evaluate(() => {
    const read = (selector: string) => {
      const element = document.querySelector(selector)
      if (!element) return null
      const { x, y, width, height } = element.getBoundingClientRect()
      return { x, y, width, height }
    }
    return { filtersBox: read('.dmo-filters'), searchBox: read('.dmo-search') }
  })
  expect(filtersBox).not.toBeNull()
  expect(searchBox).not.toBeNull()
  expect(filtersBox!.x + filtersBox!.width).toBeLessThanOrEqual(searchBox!.x)
  expect(Math.abs(filtersBox!.y + filtersBox!.height / 2 - (searchBox!.y + searchBox!.height / 2))).toBeLessThan(2)
  await expect(page.locator('.dmo-target-cell b').first()).toHaveCSS('font-size', '13px')
  await expect(page.locator('.dmo-target-cell small').first()).toHaveCSS('font-size', '11px')
  const cardBox = await page.locator('.dmo-card').boundingBox()
  expect(cardBox).not.toBeNull()
  expect(cardBox!.y + cardBox!.height).toBeLessThanOrEqual(page.viewportSize()!.height + 1)
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight + 1)).toBeTruthy()

  await page.getByRole('button', { name: '预览数据源 订单宽表' }).click()
  const dialog = page.getByRole('dialog', { name: '订单宽表' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('1–20 / 45 行')).toBeVisible()
  await expect(dialog.getByText('R1-C1')).toBeVisible()

  const scrollRegion = dialog.locator('.dmo-preview-table-scroll')
  await expect(dialog.locator('.dmo-preview-table')).toHaveClass(/is-scrollable/)
  expect(await scrollRegion.evaluate(element => element.scrollWidth > element.clientWidth)).toBeTruthy()
  expect((await dialog.boundingBox())!.height).toBeLessThanOrEqual(680)

  await dialog.getByRole('button', { name: '下一页' }).click()
  await expect(dialog.getByText('21–40 / 45 行')).toBeVisible()
  await expect(dialog.getByText('R21-C1')).toBeVisible()
  await dialog.getByRole('button', { name: '关闭数据预览' }).click()
  await expect(dialog).toHaveCount(0)
})

test('查看字段级映射按钮进入映射工作台，左上角按钮返回上一页', async ({ page }) => {
  await mockMappingPreview(page)
  await page.goto('/#/ontologies/ontology-preview?tab=data-mapping', { waitUntil: 'domcontentloaded' })

  const mappingButton = page.locator('.dmo-primary-button')
  await expect(mappingButton).toHaveText('查看字段级映射')
  await mappingButton.click()

  await expect(page).toHaveURL(/\/ontologies\/ontology-preview\/graph\?view=mapping$/)
  await expect(page.getByTestId('mapping-workspace')).toBeVisible()
  const tutorialNextButton = page.getByRole('button', { name: '下一步' })
  await expect(tutorialNextButton).toHaveCSS('background-image', /linear-gradient/)
  await tutorialNextButton.hover()
  await expect(tutorialNextButton).toHaveCSS('background-image', /linear-gradient/)
  await expect(tutorialNextButton).toHaveCSS('color', 'rgb(255, 255, 255)')
  await page.locator('.dmc-tutorial header button').click()

  await page.locator('.dmc-eye').first().click()
  const previewPanel = page.locator('.dmc-preview-panel')
  await expect(previewPanel).toBeVisible()
  await expect(previewPanel).toHaveCSS('bottom', '12px')
  await expect(previewPanel).toHaveCSS('border-radius', '10px')
  await expect.poll(async () => {
    const canvasBox = await page.locator('.dmc-canvas-wrap').boundingBox()
    const previewBox = await previewPanel.boundingBox()
    if (!canvasBox || !previewBox) return null
    return Math.round(canvasBox.y + canvasBox.height - previewBox.y - previewBox.height)
  }).toBe(12)

  await page.getByRole('button', { name: '返回上一页' }).click()
  await expect(page).toHaveURL(/\/ontologies\/ontology-preview\?tab=data-mapping$/)
  await expect(
    page.getByTestId('ontology-detail-header').getByRole('button', { name: '数据映射', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true')

  await page.locator('.dmo-primary-button').click()
  await expect(page.getByTestId('mapping-workspace')).toBeVisible()
  await page.getByRole('button', { name: '模型结构', exact: true }).click()
  await expect(page).toHaveURL(/\/ontologies\/ontology-preview\/graph$/)
})

test('当前发布态可安全重放已批准数据：先确认，再明确反馈加载、失败与完整灌入结果', async ({ page }) => {
  const calls = await mockMappingPreview(page, { failFirstApply: true })
  await page.goto('/#/ontologies/ontology-preview?tab=data-mapping', { waitUntil: 'domcontentloaded' })

  const reconcile = page.getByRole('button', { name: '立即灌入' })
  await expect(reconcile).toBeVisible()
  await expect(page.getByText('只读取当前映射绑定的最新已批准版本')).toBeVisible()

  // 取消支线：弹窗出现但不发请求
  await reconcile.click()
  const confirmDialog = page.getByRole('dialog', { name: '确认重新灌入数据' })
  await expect(confirmDialog).toBeVisible()
  await expect(confirmDialog.getByText('重写「订单」的对象与关系实例')).toBeVisible()
  await confirmDialog.getByRole('button', { name: '取消' }).click()
  await expect(confirmDialog).toHaveCount(0)
  expect(calls.applyAttempts()).toBe(0)

  // 确认支线：第一次 409 失败反馈
  await reconcile.click()
  await confirmDialog.getByRole('button', { name: '确认灌入' }).click()
  await expect(page.getByRole('button', { name: '正在灌入…' })).toBeDisabled()
  await expect(page.getByRole('alert')).toContainText('当前成品版本尚未批准，不能进入正式本体')

  // 重试成功反馈
  await page.getByRole('button', { name: '立即灌入' }).click()
  await confirmDialog.getByRole('button', { name: '确认灌入' }).click()
  await expect(page.getByRole('status')).toContainText(
    '灌入完成：对象实例 2 条、关系实例 1 条已更新；哨兵评估 2 次、触发 1 次。',
  )
  expect(calls.applyAttempts()).toBe(2)
})

test('审核异常数据集前置可见：徽标、禁用预览与灌入、横幅提示、已灌入版本', async ({ page }) => {
  await mockMappingPreview(page, { datasetStatus: 'rejected', withInstances: true })
  await page.goto('/#/ontologies/ontology-preview?tab=data-mapping', { waitUntil: 'domcontentloaded' })

  // 行全通但来源数据集被拒绝 → 横幅审核异常态
  await expect(page.getByText('数据链路已连通，但 1 个来源数据集审核状态异常')).toBeVisible()
  // 行内与侧栏均有「已拒绝」徽标
  await expect(page.locator('.dmo-dataset-cell .dmo-review-badge')).toHaveText('已拒绝')
  await expect(page.locator('.dmo-source-item .dmo-review-badge')).toHaveText('已拒绝')
  // 已灌入版本新鲜度
  await expect(page.locator('.dmo-source-item small')).toContainText('已灌入 v23')
  // 预览前置禁用并给出原因
  const preview = page.getByRole('button', { name: '预览数据源 订单宽表' })
  await expect(preview).toBeDisabled()
  await expect(preview).toHaveAttribute('title', '数据集已拒绝，仅保留审计，不可预览')
  // 灌入前置禁用并给出原因，且不出现确认弹窗
  const reconcile = page.getByRole('button', { name: '立即灌入' })
  await expect(reconcile).toBeDisabled()
  await expect(page.getByText('来源数据集已拒绝，不能灌入；请先完成数据审核。')).toBeVisible()
})

test('未配置元素：实例列占位、下一步卡指向图谱页创建草稿', async ({ page }) => {
  await mockMappingPreview(page, { withUnmappedObject: true })
  await page.goto('/#/ontologies/ontology-preview?tab=data-mapping', { waitUntil: 'domcontentloaded' })

  // 未配置行实例列显示占位符而非误导性的 0 条
  await expect(page.locator('.dmo-instance-cell b.is-empty')).toHaveText('—')
  // KPI 口径：只统计本本体的来源数据资产
  await expect(page.locator('.dmo-kpis > div').nth(3)).toContainText('来源数据资产')
  await expect(page.locator('.dmo-kpis > div').nth(3)).toContainText('成品 1 · 人工 0')

  // 选中未配置行 → 下一步卡仅给草稿路径
  await page.locator('.dmo-map-row', { hasText: '日志' }).click()
  await expect(page.getByText('建立映射需在草稿版本中进行')).toBeVisible()
  await page.getByRole('button', { name: '前往图谱页创建草稿' }).click()
  await expect(page).toHaveURL(/\/ontologies\/ontology-preview\/graph$/)
})

test('侧栏齿轮携带元素上下文跳转字段级映射视图', async ({ page }) => {
  await mockMappingPreview(page)
  await page.goto('/#/ontologies/ontology-preview?tab=data-mapping', { waitUntil: 'domcontentloaded' })

  await page.getByRole('button', { name: '查看该元素字段映射' }).click()
  await expect(page).toHaveURL(/\/ontologies\/ontology-preview\/graph\?view=mapping&focus=object(%3A|:)object-order$/)
  await expect(page.getByTestId('mapping-workspace')).toBeVisible()
})

test('数据供给全景：桑基图渲染出数据流，行与图联动', async ({ page }) => {
  await mockMappingPreview(page)
  await page.goto('/#/ontologies/ontology-preview?tab=data-mapping', { waitUntil: 'domcontentloaded' })

  await expect(page.getByText('数据供给全景')).toBeVisible()
  const chart = page.getByTestId('mapping-flow-chart')
  await expect(chart).toBeVisible()
  // 1 个数据集 + 1 个对象 + 1 条流 → SVG 渲染出矢量节点
  await expect.poll(async () => chart.locator('svg path').count()).toBeGreaterThan(0)
  // 全部元素已映射时不显示"未接入数据流"caption
  await expect(page.locator('.dmo-flow-caption')).toHaveCount(0)
  // 行选中后图表保持渲染（selectedKey 联动不破坏画布）
  await page.locator('.dmo-map-row').first().click()
  await expect(chart).toBeVisible()
})

test('无映射本体显示诚实空态而非空画布', async ({ page }) => {
  await mockMappingPreview(page, { noMappings: true })
  await page.goto('/#/ontologies/ontology-preview?tab=data-mapping', { waitUntil: 'domcontentloaded' })

  await expect(page.getByText('暂无数据流')).toBeVisible()
  await expect(page.getByTestId('mapping-flow-chart')).toHaveCount(0)
  // 唯一的对象元素不在图中，caption 如实告知去向
  await expect(page.locator('.dmo-flow-caption')).toContainText('1 个本体元素未接入数据流')
})

test('实例数链接跳转实例数据 Tab 并选中对应类型', async ({ page }) => {
  const { requests } = await mockMappingPreview(page, { withInstances: true })
  await page.goto('/#/ontologies/ontology-preview?tab=data-mapping', { waitUntil: 'domcontentloaded' })

  const link = page.locator('.dmo-instance-link').first()
  await expect(link).toBeVisible()
  await link.click()
  await expect(page).toHaveURL(/tab=data&type=object(%3A|:)object-order/)
  // 实例数据页消费 type 参数：按选中的对象类型请求实例列表
  await expect.poll(() => requests.objectsTypeId).toBe('object-order')
})

test('深色模式：页面与桑基图随主题渲染', async ({ page }) => {
  await mockMappingPreview(page, { darkTheme: true })
  await page.goto('/#/ontologies/ontology-preview?tab=data-mapping', { waitUntil: 'domcontentloaded' })

  await expect(page.locator('html')).toHaveClass(/dark/)
  await expect(page.getByTestId('mapping-flow-chart')).toBeVisible()
  await expect.poll(async () => page.locator('.dmo-flow-canvas svg path').count()).toBeGreaterThan(0)
  await expect(page.locator('.dmo-card')).toHaveCSS('background-color', 'rgb(22, 28, 38)')
})
