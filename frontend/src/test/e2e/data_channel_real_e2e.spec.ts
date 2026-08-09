/// <reference types="node" />

import { createServer, type Server } from 'node:http'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import {
  STACK_ADMIN_PASSWORD,
  STACK_ADMIN_USERNAME,
} from './support/stack-credentials'

const API = (
  process.env.PLAYWRIGHT_API_URL
  || process.env.E2E_API_BASE
  || 'http://127.0.0.1:8000'
).replace(/\/+$/, '')
const FIXTURE_BIND_HOST = process.env.PLAYWRIGHT_FIXTURE_BIND_HOST || '127.0.0.1'
const FIXTURE_PUBLIC_HOST = process.env.PLAYWRIGHT_FIXTURE_HOST || FIXTURE_BIND_HOST

let fixtureServer: Server
let fixtureUrl = ''
let failFixtureRequests = false
let fixtureRows: Array<Record<string, unknown>> = [
  { id: 'A-1', name: 'Alpha' },
  { id: 'B-2', name: 'Beta' },
]

function unwrap<T>(body: T | { data: T }): T {
  return body && typeof body === 'object' && 'data' in body
    ? (body as { data: T }).data
    : body as T
}

async function login(page: Page): Promise<string> {
  await page.goto('/#/login')
  await page.getByLabel('用户名').fill(STACK_ADMIN_USERNAME)
  await page.locator('#login-password').fill(STACK_ADMIN_PASSWORD)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.waitForURL(/\/overview$/)
  const token = await page.evaluate(() => localStorage.getItem('token') || '')
  expect(token).toBeTruthy()
  return token
}

async function apiJson<T>(
  request: APIRequestContext,
  token: string,
  method: 'GET' | 'POST' | 'PUT',
  path: string,
  data?: unknown,
): Promise<T> {
  const response = await request.fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}` },
    data,
  })
  const text = await response.text()
  expect(response.ok(), `${method} ${path}: ${text}`).toBeTruthy()
  return unwrap(JSON.parse(text)) as T
}

test.beforeAll(async () => {
  fixtureServer = createServer((req, res) => {
    if (!req.url?.startsWith('/orders')) {
      res.writeHead(404).end()
      return
    }
    if (req.headers['x-e2e-token'] !== 'allowed') {
      res.writeHead(401, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ error: 'missing e2e header' }))
      return
    }
    if (failFixtureRequests) {
      res.writeHead(503, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ error: 'fixture unavailable' }))
      return
    }
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify(fixtureRows))
  })
  await new Promise<void>((resolve, reject) => {
    fixtureServer.once('error', reject)
    fixtureServer.listen(0, FIXTURE_BIND_HOST, () => resolve())
  })
  const address = fixtureServer.address()
  if (!address || typeof address === 'string') throw new Error('fixture server did not bind')
  fixtureUrl = `http://${FIXTURE_PUBLIC_HOST}:${address.port}/orders`
})

test.afterAll(async () => {
  if (!fixtureServer?.listening) return
  await new Promise<void>(resolve => fixtureServer.close(() => resolve()))
})

test('real data-channel flow: REST sync, lake visibility, publish gate and latest run', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(180_000)
  const token = await login(page)
  const suffix = Date.now()
  const connectionName = `E2E REST ${suffix}`
  const pipelineName = `E2E Python ${suffix}`
  const taskName = `E2E Task ${suffix}`

  // 真实页面创建 REST 连接；先验证非法 Header JSON 会被页面阻断。
  await page.goto('/#/data/pipelines/connections')
  await page.getByRole('button', { name: '新建连接' }).click()
  await page.getByRole('button', { name: 'REST API' }).click()
  await page.getByPlaceholder('例：ERP 订单数据库').fill(connectionName)
  await page.getByPlaceholder('https://api.example.com/data').fill(fixtureUrl)
  const headersInput = page.getByPlaceholder('{"Authorization": "Bearer token"}')
  await headersInput.fill('{invalid')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByText('请求头不是合法 JSON', { exact: true })).toBeVisible()

  await headersInput.fill('{"X-E2E-Token":"allowed"}')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByText(connectionName, { exact: true })).toBeVisible()

  const connectionCard = page.getByText(connectionName, { exact: true })
    .locator('xpath=ancestor::div[contains(@class,"p-4")][1]')
  await connectionCard.getByRole('button', { name: '测试', exact: true }).click()
  await expect(connectionCard).toContainText('连接可用')
  await connectionCard.getByRole('button', { name: '同步', exact: true }).click()
  await expect(connectionCard).toContainText('同步成功，共 2 行', {
    timeout: 20_000,
  })

  // 资产湖顶部只保留成品与人工数据集；同步结果继续由正式资产接口承载并在下方核验。
  await page.goto('/#/data/structured?tab=sync')
  await expect(page).toHaveURL(/tab=curated/)
  await expect(page.getByRole('button', { name: '成品数据集' })).toBeVisible()
  await expect(page.getByRole('button', { name: '人工数据集' })).toBeVisible()
  await expect(page.getByRole('button', { name: '连接同步数据集' })).toHaveCount(0)
  await page.screenshot({
    path: testInfo.outputPath('01-asset-lake-tabs.png'),
    fullPage: true,
  })

  const overview = await apiJson<{
    items: Array<{
      id: string
      latest_version_no: number
      rowcount: number
      connection_name: string
    }>
  }>(
    request,
    token,
    'GET',
    '/api/v2/datasets/overview?source=sync&paginated=true',
  )
  const dataset = overview.items.find(item => item.connection_name === connectionName)
  expect(dataset).toBeTruthy()
  expect(dataset?.rowcount).toBe(2)
  const preview = await apiJson<Array<Record<string, unknown>>>(
    request,
    token,
    'GET',
    `/api/v2/datasets/${dataset?.id}/versions/${dataset?.latest_version_no}/preview?limit=20`,
  )
  expect(preview).toEqual(fixtureRows)

  // 未配置 n8n 时，真实新建弹窗必须禁用 n8n 入口并给出配置说明；
  // 默认选中 Python 脚本，且不再提供旧版画布（系统自定义）入口。
  await page.goto('/#/data/pipelines')
  await page.getByRole('button', { name: '新建流水线' }).click()
  await expect(page.getByRole('button', { name: /n8n 流水线/ })).toBeDisabled()
  await expect(page.getByText(/启动配置缺少 n8n|n8n 集成当前处于停用状态/)).toBeVisible()
  const pythonCard = page.getByRole('button', { name: /Python 脚本/ })
  await expect(pythonCard).toBeEnabled()
  await expect(pythonCard).toHaveClass(/border-indigo-400/)
  await expect(page.getByRole('button', { name: /旧版系统流水线/ })).toHaveCount(0)
  await page.screenshot({
    path: testInfo.outputPath('02-n8n-preflight.png'),
    fullPage: true,
  })
  await page.getByRole('button', { name: '取消', exact: true }).click()

  // Python 引擎流水线：definition 只声明引擎，取数逻辑全部在脚本里。
  const pipeline = await apiJson<{ id: string }>(
    request,
    token,
    'POST',
    '/api/v2/pipelines',
    {
      name: pipelineName,
      description: '真实端到端发布门禁验证',
      definition: { engine: 'python', nodes: [], edges: [], python: {} },
    },
  )
  // 脚本经「保存」端点落库（服务端重跑复验输出格式）；之后的 dry-run、
  // 发布与任务运行都执行这份已保存脚本。脚本直连夹具服务取数，
  // 因此夹具行数变化会真实反映到下一次运行。
  const script = [
    'import requests',
    '',
    `result = requests.get(${JSON.stringify(fixtureUrl)}, headers={"X-E2E-Token": "allowed"}, timeout=30).json()`,
  ].join('\n')
  await apiJson(
    request,
    token,
    'PUT',
    `/api/v2/pipelines/${pipeline.id}/script`,
    { script },
  )

  // 直接调用发布端点也不能绕过执行预览和字段全量校验。
  const prematurePublish = await request.post(
    `${API}/api/v2/pipelines/${pipeline.id}/publish`,
    {
      headers: { Authorization: `Bearer ${token}` },
      data: { enable: true },
    },
  )
  expect(prematurePublish.status()).toBe(400)
  await expect(prematurePublish.json()).resolves.toMatchObject({
    detail: expect.stringContaining('执行预览与字段定义'),
  })

  const dryRun = await apiJson<{
    dry_run_id: string
    rows_in: number
    rows_out: number
  }>(
    request,
    token,
    'POST',
    `/api/v2/pipelines/${pipeline.id}/dry-run?max_rows=100`,
  )
  expect(dryRun.rows_in).toBe(2)
  expect(dryRun.rows_out).toBe(2)
  const columnDefinitions = [
    {
      source_key: 'id',
      field_key: 'id',
      field_name: '标识',
      field_type: 'string',
      is_primary_key: true,
      nullable: false,
    },
    {
      source_key: 'name',
      field_key: 'name',
      field_name: '名称',
      field_type: 'string',
      is_primary_key: false,
      nullable: false,
    },
  ]
  const validation = await apiJson<{ valid: boolean }>(
    request,
    token,
    'POST',
    `/api/v2/pipelines/${pipeline.id}/validate-definitions?dry_run_id=${dryRun.dry_run_id}`,
    { column_definitions: columnDefinitions },
  )
  expect(validation.valid).toBe(true)
  await apiJson(
    request,
    token,
    'PUT',
    `/api/v2/pipelines/${pipeline.id}`,
    { column_definitions: columnDefinitions },
  )
  const published = await apiJson<{ status: string; enabled: boolean }>(
    request,
    token,
    'POST',
    `/api/v2/pipelines/${pipeline.id}/publish`,
    { enable: true },
  )
  expect(published).toMatchObject({ status: 'published', enabled: true })

  const task = await apiJson<{ id: string }>(
    request,
    token,
    'POST',
    '/api/v2/pipeline-tasks',
    {
      name: taskName,
      description: '验证真实数据流水线的手动入湖任务',
      pipeline_id: pipeline.id,
      write_mode: 'overwrite',
      schedule_type: 'MANUAL',
      enabled: true,
    },
  )
  const searchedTasks = await apiJson<{
    total: number
    items: Array<{ id: string; pipeline_name: string }>
  }>(
    request,
    token,
    'GET',
    `/api/v2/pipeline-tasks?search=${encodeURIComponent(pipelineName)}`,
  )
  expect(searchedTasks.total).toBe(1)
  expect(searchedTasks.items[0]).toMatchObject({
    id: task.id,
    pipeline_name: pipelineName,
  })

  const firstRun = await apiJson<{ status: string; rows_in: number }>(
    request,
    token,
    'POST',
    `/api/v2/pipeline-tasks/${task.id}/trigger?sync=true`,
  )
  expect(firstRun.status).toBe('ok')

  fixtureRows = [
    { id: 'A-1', name: 'Alpha' },
    { id: 'B-2', name: 'Beta' },
    { id: 'C-3', name: 'Gamma' },
  ]
  const connections = await apiJson<Array<{ id: string; name: string }>>(
    request,
    token,
    'GET',
    '/api/v2/connections',
  )
  const connection = connections.find(item => item.name === connectionName)
  expect(connection).toBeTruthy()
  await apiJson(
    request,
    token,
    'POST',
    `/api/v2/connections/${connection?.id}/sync`,
    {},
  )
  const secondRun = await apiJson<{ status: string; rows_in: number }>(
    request,
    token,
    'POST',
    `/api/v2/pipeline-tasks/${task.id}/trigger?sync=true`,
  )
  expect(secondRun.status).toBe('ok')
  expect(secondRun.rows_in).toBe(3)

  const histories = await apiJson<{
    total: number
    items: Array<{ started_at: string; finished_at: string }>
  }>(
    request,
    token,
    'GET',
    `/api/v2/pipeline-tasks/${task.id}/histories`,
  )
  expect(histories.total).toBe(2)
  expect(histories.items[0].started_at).toMatch(/Z$/)
  expect(histories.items[0].finished_at).toMatch(/Z$/)

  // 发布后脚本编辑页只读；实时执行已封版脚本必须读到上游最新数据（3 行），
  // 不能回退到第一条旧运行（2 行）。
  await page.goto(`/#/data/pipelines/script/${pipeline.id}`)
  await expect(page.getByText('已发布').first()).toBeVisible()
  await page.getByRole('button', { name: /^执行$/ }).click()
  await expect(page.getByText('执行成功 · 输出格式校验通过')).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText('Gamma')).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath('03-latest-run-inspector.png'),
    fullPage: true,
  })

  // 上游真实失败必须显示为失败，不能伪装成 0 行同步成功。
  failFixtureRequests = true
  await page.goto('/#/data/pipelines/connections')
  const failingCard = page.getByText(connectionName, { exact: true })
    .locator('xpath=ancestor::div[contains(@class,"p-4")][1]')
  await failingCard.getByRole('button', { name: '同步', exact: true }).click()
  await expect(failingCard).toContainText('✗')
  await expect(failingCard).not.toContainText('同步成功，共 0 行')

  await page.screenshot({
    path: testInfo.outputPath('04-upstream-failure-visible.png'),
    fullPage: true,
  })
})
