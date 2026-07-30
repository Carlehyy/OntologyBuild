/**
 * 三领域对比测试：供应链 / 医疗 / 财务
 * 每个业务域跑 Pipeline Mapping + 简易 LLM 两条路径，
 * 最终输出实体数、边数、逻辑数、动作数汇总表。
 */

/// <reference types="node" />

import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  STACK_ADMIN_PASSWORD,
  STACK_ADMIN_USERNAME,
} from './support/stack-credentials'

const API = (
  process.env.PLAYWRIGHT_API_URL
  || process.env.E2E_API_BASE
  || 'http://localhost:8000'
).replace(/\/+$/, '')
const __filename = fileURLToPath(import.meta.url)
const __dirname  = path.dirname(__filename)
const TEST_DATA  = path.resolve(__dirname, '../../../../test_data')

const DOMAINS = ['供应链', '医疗', '财务'] as const
type Domain = typeof DOMAINS[number]
const RUN_REAL_LLM = process.env.PLAYWRIGHT_THREE_DOMAINS_REAL === '1'
const PIPELINE_FIXTURE: Record<Domain, string> = {
  供应链: 'logistics_performance.csv',
  医疗: 'adverse_events.csv',
  财务: 'cash_flow.csv',
}

// ── 固定 ID（匹配当前 DB 状态） ───────────────────────────────────────────
const MODEL_ID   = '8f347f97-e844-4d62-b81b-8c655cd3b410'   // deepseek
const MODEL_NAME = 'deepseek-v4-flash'

const PROMPT_BY_DOMAIN: Record<Domain, string> = {
  '供应链': '9dad1123-72eb-4b9b-b5b3-1777c54ca3cd',
  '医疗':   'd9bf7a9a-5313-4be3-b941-88c33f280566',
  '财务':   'bff40feb-6f53-460e-97d1-b5e8d4f4a9be',
}

interface DryRunOutput {
  columns: string[]
  sample: Array<Record<string, unknown>>
  rows_out: number
}

interface ColumnDefinition {
  source_key: string
  field_key: string
  field_name: string
  field_type: 'string'
  is_primary_key: boolean
  nullable: boolean
}

// ── 工具 ─────────────────────────────────────────────────────────────────

async function loginViaApi(request: APIRequestContext): Promise<string> {
  const response = await request.post(`${API}/api/v1/auth/login`, {
    data: {
      username: STACK_ADMIN_USERNAME,
      password: STACK_ADMIN_PASSWORD,
    },
  })
  expect(response.ok(), `login API returned ${response.status()}`).toBeTruthy()
  const body = await response.json()
  const token: string = body.data?.access_token ?? ''
  expect(token, 'JWT token must be set').toBeTruthy()
  return token
}

async function authenticatePage(page: Page, token: string): Promise<void> {
  await page.addInitScript(({ tok, username }: { tok: string; username: string }) => {
    localStorage.setItem('token', tok)
    localStorage.setItem('auth-store', JSON.stringify({
      state: { token: tok, user: { username, role: 'admin' } },
      version: 0,
    }))
  }, { tok: token, username: STACK_ADMIN_USERNAME })
}

async function apiCall(
  request: APIRequestContext,
  method: 'GET' | 'POST' | 'PUT' | 'DELETE',
  url: string,
  token: string,
  data?: unknown,
): Promise<any> {
  const res = await request.fetch(`${API}${url}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    data: data ? JSON.stringify(data) : undefined,
  })
  const body = await res.json()
  if (!res.ok()) throw new Error(`${method} ${url} → ${res.status()}: ${JSON.stringify(body).slice(0, 300)}`)
  return body
}

function columnDefinitions(output: DryRunOutput): ColumnDefinition[] {
  const rows = output.sample
  expect(rows.length, 'dry-run must return all rows used by the contract check').toBe(output.rows_out)

  const completeColumns = output.columns.filter(sourceKey =>
    rows.every(row => row[sourceKey] !== null && row[sourceKey] !== undefined && String(row[sourceKey]).trim() !== ''),
  )
  const uniqueColumns = completeColumns.filter(sourceKey => {
    const values = rows.map(row => JSON.stringify(row[sourceKey]))
    return new Set(values).size === values.length
  })
  const primaryKeyColumns = uniqueColumns.length > 0 ? [uniqueColumns[0]] : completeColumns
  expect(primaryKeyColumns.length, 'output must expose at least one complete identity column').toBeGreaterThan(0)
  const compositeValues = rows.map(row => JSON.stringify(primaryKeyColumns.map(key => row[key])))
  expect(new Set(compositeValues).size, 'selected identity columns must be unique').toBe(rows.length)

  const used = new Set<string>()
  return output.columns.map((sourceKey, index) => {
    const normalized = sourceKey.replace(/[^A-Za-z0-9_]/g, '_')
    const initial = (
      /^[A-Za-z_]/.test(normalized)
      && /[A-Za-z0-9]/.test(normalized)
      && !normalized.startsWith('__')
    ) ? normalized : `field_${index + 1}`
    let fieldKey = initial
    let suffix = 2
    while (used.has(fieldKey)) fieldKey = `${initial}_${suffix++}`
    used.add(fieldKey)
    const isPrimaryKey = primaryKeyColumns.includes(sourceKey)
    return {
      source_key: sourceKey,
      field_key: fieldKey,
      field_name: sourceKey,
      field_type: 'string',
      is_primary_key: isPrimaryKey,
      nullable: !isPrimaryKey,
    }
  })
}

async function shot(page: Page, outDir: string, name: string) {
  await page.screenshot({ path: path.join(outDir, `${name}.jpg`), type: 'jpeg', quality: 75 })
}

// 统一统计函数：读取当前发布快照，避免旧 build-all 直接写正式实例。
async function collectStats(request: APIRequestContext, token: string, ontologyId: string) {
  const body = await apiCall(request, 'GET', `/api/v2/formal/ontologies/${ontologyId}/full`, token)
  const snapshot = body.data ?? body
  return {
    entities: (snapshot.instances ?? []).length,
    edges: (snapshot.linkInstances ?? []).length,
    logic: (snapshot.functions ?? []).length,
    actions: (snapshot.actions ?? []).length,
  }
}

async function pollExtraction(
  request: APIRequestContext,
  token: string,
  ontologyId: string,
  taskId: string,
  timeoutMs = 360_000,
): Promise<string> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 4000))
    const body = await apiCall(request, 'GET', `/api/v1/ontologies/${ontologyId}/execute/status?task_id=${taskId}`, token)
    const status: string = body.data?.status ?? body.status
    console.log(`    polling: status=${status} pct=${body.data?.progress?.pct ?? 0}%`)
    if (status === 'completed' || status === 'failed') return status
  }
  throw new Error('LLM extraction timed out')
}

// ── Pipeline Mapping 路径 ─────────────────────────────────────────────────

async function runPipelineMapping(
  page: Page,
  request: APIRequestContext,
  token: string,
  domain: Domain,
  ts: number,
  outDir: string,
) {
  await authenticatePage(page, token)
  const domainDir = path.join(TEST_DATA, domain)
  const files = fs.readdirSync(domainDir).filter(f => fs.statSync(path.join(domainDir, f)).isFile()).sort()
  const sourceFile = PIPELINE_FIXTURE[domain]
  expect(files).toContain(sourceFile)
  console.log(`\n  [${domain}][Pipeline] 单产物发布夹具: ${sourceFile}`)

  // 1. 上传一个代表性结构化文件。当前字段契约与发布凭证以单产物为粒度；
  // 多文件全量覆盖由独立的供应链 golden flow 逐文件验证。
  const uploadResponse = await request.post(`${API}/api/v2/datasets/upload`, {
    headers: { Authorization: `Bearer ${token}` },
    multipart: {
      file: {
        name: sourceFile,
        mimeType: 'application/octet-stream',
        buffer: fs.readFileSync(path.join(domainDir, sourceFile)),
      },
    },
  })
  const uploadBody = await uploadResponse.json()
  expect(uploadResponse.ok(), `upload ${sourceFile}: ${JSON.stringify(uploadBody)}`).toBeTruthy()
  const uploaded = { name: sourceFile, dataset_id: uploadBody.data.id as string }
  console.log('    上传完成: 1 个文件')

  // 2. 创建 Pipeline
  const plBody = await apiCall(request, 'POST', '/api/v2/pipelines', token, {
    name: `E2E_${domain}_Pipeline_${ts}`,
    domain,
    description: `三领域对比 E2E — ${domain} Pipeline`,
    route: 'A',
    definition: {
      schema_version: '2.0',
      nodes: [
        { id: 'connector', type: 'connector', label: `${domain}数据源`, position: { x: 80, y: 180 },
          config: { source_type: 'file', files: [uploaded] } },
        { id: 'storage',   type: 'storage',   label: '分类存储',       position: { x: 330, y: 180 },
          config: { storage_mode: 'auto' } },
        { id: 'transform', type: 'transform', label: '数据转换',       position: { x: 580, y: 180 },
          config: { path: 'auto', steps: [] } },
        { id: 'output',    type: 'output',    label: '结构化输出',     position: { x: 830, y: 180 },
          config: { dataset_type: 'curated_dataset', primary_key: [] } },
      ],
      edges: [
        { id: 'e1', source: 'connector', target: 'storage' },
        { id: 'e2', source: 'storage',   target: 'transform' },
        { id: 'e3', source: 'transform', target: 'output' },
      ],
    },
  })
  const pipelineId: string = plBody.id ?? plBody.data?.id
  expect(pipelineId).toBeTruthy()
  console.log(`    Pipeline: ${pipelineId.slice(0, 8)}`)

  // 3. 前端截图 Pipeline Builder
  await page.goto(`/#/data/pipelines/${pipelineId}`)
  await page.waitForTimeout(1500)
  await shot(page, outDir, `${domain}_pm_01_pipeline_builder`)

  // 4. 当前发布契约：先执行预览，再对完整暂存输出做字段与主键校验，
  // 保存完全一致的字段定义后才允许发布。
  const dryRun = await apiCall(
    request,
    'POST',
    `/api/v2/pipelines/${pipelineId}/dry-run?max_rows=500`,
    token,
  )
  expect(dryRun.outputs).toHaveLength(1)
  const definitions = columnDefinitions(dryRun.outputs[0] as DryRunOutput)
  const validation = await apiCall(
    request,
    'POST',
    `/api/v2/pipelines/${pipelineId}/validate-definitions?dry_run_id=${dryRun.dry_run_id}`,
    token,
    { column_definitions: definitions },
  )
  expect(validation.valid, JSON.stringify(validation.errors || [])).toBe(true)
  await apiCall(request, 'PUT', `/api/v2/pipelines/${pipelineId}`, token, {
    column_definitions: definitions,
  })
  const published = await apiCall(request, 'POST', `/api/v2/pipelines/${pipelineId}/publish`, token, {
    enable: true,
  })
  expect(published).toMatchObject({ status: 'published', enabled: true })

  // 5. 启用并发布后运行，产物才进入资产湖；生产运行必须保持 fail-closed。
  console.log('    运行 Pipeline...')
  const runBody = await apiCall(request, 'POST', `/api/v2/pipelines/${pipelineId}/run-sync`, token)
  expect(runBody.status, `Pipeline 运行失败: ${JSON.stringify(runBody).slice(0, 200)}`).toBe('success')
  const curatedIds: string[] = runBody.stats?.curated_dataset_ids ?? []
  expect(curatedIds).toHaveLength(1)
  console.log(`    Pipeline 完成: ${curatedIds.length} 个 curated dataset`)

  // 6. 批准全部 curated datasets
  for (const id of curatedIds) {
    await apiCall(request, 'POST', `/api/v2/curated/${id}/review?action=approve`, token)
  }

  // 7. 创建 Pipeline Mapping 本体
  const ontoBody = await apiCall(request, 'POST', '/api/v1/ontologies', token, {
    name:        `E2E_${domain}_PipelineMapping_${ts}`,
    domain,
    description: `三领域对比 — ${domain} Pipeline Mapping`,
    build_mode:  'pipeline_mapping',
  })
  const ontologyId: string = ontoBody.data?.id ?? ontoBody.id
  expect(ontologyId).toBeTruthy()
  console.log(`    本体: ${ontologyId.slice(0, 8)}`)

  // 8. 将数据映射放入隔离草稿，试跑通过后晋级；不允许旧 build-all
  // 绕过三态版本边界直接写正式实例。
  const output = runBody.stats?.meta?.outputs?.[0] as {
    curated_dataset_id?: string
    source_file?: string
  } | undefined
  const entityClass = (output?.source_file || sourceFile)
    .replace(/\.[^.]+$/, '')
    .split(/[_\-\s]+/)
    .map((word: string) => word.charAt(0).toUpperCase() + word.slice(1))
    .join('')
  const primary = definitions.find(definition => definition.is_primary_key)
  expect(primary, `${sourceFile} must expose a single primary key`).toBeTruthy()
  const objectType = {
    id: `ot-${domain}-${ts}`,
    name: entityClass,
    displayName: entityClass,
    icon: 'database',
    primaryKey: primary!.field_key,
    positionX: 0,
    positionY: 0,
    properties: definitions.map((definition, index) => ({
      id: `prop-${domain}-${ts}-${index}`,
      name: definition.field_key,
      displayName: definition.field_name,
      type: 'string',
      required: definition.is_primary_key,
    })),
  }
  const tree = await apiCall(request, 'GET', `/api/v2/ontologies/${ontologyId}/version-tree`, token)
  const root = tree.data.versions.find((item: { version_number: string }) => item.version_number === 'v0')
  expect(root).toBeTruthy()
  const draftBody = await apiCall(
    request,
    'POST',
    `/api/v2/ontologies/${ontologyId}/versions/${root.id}/drafts`,
    token,
    {
      versionLabel: `${domain} Pipeline Mapping`,
      description: '单产物发布数据在隔离草稿中完成映射试跑',
    },
  )
  const draft = draftBody.data
  const saved = await apiCall(
    request,
    'PUT',
    `/api/v2/ontologies/${ontologyId}/versions/${draft.id}/workspace`,
    token,
    {
      baseRevision: `${draft.revision}:${draft.snapshot_hash}`,
      version: draft.version_number,
      objectTypes: [objectType],
      linkTypes: [],
      actions: [],
      functions: [],
      instances: [],
      linkInstances: [],
    },
  )
  await apiCall(
    request,
    'PUT',
    `/api/v2/ontologies/${ontologyId}/versions/${draft.id}/workspace/mappings`,
    token,
    {
      baseRevision: saved.data.revision,
      mappings: [{
        id: `mapping-${domain}-${ts}`,
        curatedDatasetId: curatedIds[0],
        entityClass,
        targetObjectTypeId: objectType.id,
        fieldMapping: {
          ...Object.fromEntries(definitions.map(definition => [
            definition.field_key,
            definition.field_key,
          ])),
          __primary_key__: primary!.field_key,
        },
        status: 'draft',
        confidence: 1,
      }],
      linkMappings: [],
      sentinels: [],
    },
  )
  const trialBody = await apiCall(
    request,
    'POST',
    `/api/v2/ontologies/${ontologyId}/versions/${draft.id}/trial-runs`,
    token,
    {},
  )
  const trial = trialBody.data
  expect(trial.status, JSON.stringify(trial.result?.errors || [])).toBe('passed')
  expect(trial.result.counts.datasets).toBe(1)
  expect(trial.result.counts.objects).toBeGreaterThan(0)
  const impactBody = await apiCall(
    request,
    'GET',
    `/api/v2/ontologies/${ontologyId}/versions/${draft.id}/impact`,
    token,
  )
  const impact = impactBody.data
  expect(impact.releaseReadiness).toMatchObject({ ready: true, blockingCount: 0 })
  const promotedBody = await apiCall(
    request,
    'POST',
    `/api/v2/ontologies/${ontologyId}/versions/${draft.id}/promote`,
    token,
    { trialRunId: trial.id, impactHash: impact.impactHash },
  )
  expect(promotedBody.data).toMatchObject({ version_number: 'v1', lifecycle_status: 'released' })
  console.log(`    发布完成: objects=${trial.result.counts.objects}`)

  // 9. 前端截图本体详情 + 图谱
  await page.goto(`/#/ontologies/${ontologyId}`)
  await page.waitForTimeout(1500)
  await shot(page, outDir, `${domain}_pm_02_ontology`)

  await page.getByRole('button', { name: '查看当前发布图谱', exact: true }).click()
  await expect(page.getByTestId('graph-workspace-stage')).toContainText('当前发布 v1')
  await shot(page, outDir, `${domain}_pm_03_graph`)

  // 10. 统一统计（当前发布快照）
  const stats = await collectStats(request, token, ontologyId)
  return { pipelineId, ontologyId, stats }
}

// ── 简易 LLM 路径 ────────────────────────────────────────────────────────

async function runSimpleLLM(
  page: Page,
  request: APIRequestContext,
  token: string,
  domain: Domain,
  ts: number,
  outDir: string,
) {
  const domainDir = path.join(TEST_DATA, domain)
  const files = fs.readdirSync(domainDir).filter(f => fs.statSync(path.join(domainDir, f)).isFile()).sort()
  console.log(`\n  [${domain}][简易LLM] 文件数: ${files.length}`)

  // 1. 创建本体
  const ontoBody = await apiCall(request, 'POST', '/api/v1/ontologies', token, {
    name:        `E2E_${domain}_SimpleLLM_${ts}`,
    domain,
    description: `三领域对比 — ${domain} 简易LLM`,
    build_mode:  'simple_llm',
  })
  const ontologyId: string = ontoBody.data?.id ?? ontoBody.id
  expect(ontologyId).toBeTruthy()
  console.log(`    本体: ${ontologyId.slice(0, 8)}`)

  // 2. 截图文件上传 tab
  await authenticatePage(page, token)
  await page.goto(`/#/ontologies/${ontologyId}?tab=files`)
  await page.waitForTimeout(1500)
  await shot(page, outDir, `${domain}_llm_01_files_tab`)

  // 3. 上传所有领域文件
  const fileIds: string[] = []
  for (const filename of files) {
    const res = await request.post(`${API}/api/v1/ontologies/${ontologyId}/files`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: { file: { name: filename, mimeType: 'application/octet-stream', buffer: fs.readFileSync(path.join(domainDir, filename)) } },
    })
    const body = await res.json()
    expect(res.ok(), `upload ${filename}: ${JSON.stringify(body)}`).toBeTruthy()
    fileIds.push(body.data.id)
  }
  console.log(`    上传完成: ${fileIds.length} 个文件`)

  await page.reload(); await page.waitForTimeout(1000)
  await shot(page, outDir, `${domain}_llm_02_files_uploaded`)

  // 4. 触发 LLM 提取
  const execBody = await apiCall(request, 'POST', `/api/v1/ontologies/${ontologyId}/execute`, token, {
    prompt_id:  PROMPT_BY_DOMAIN[domain],
    model_id:   MODEL_ID,
    model_name: MODEL_NAME,
    file_ids:   fileIds,
    constraints: [],
  })
  const taskId: string = execBody.data?.task_id ?? execBody.task_id
  expect(taskId).toBeTruthy()
  console.log(`    提取任务: ${taskId.slice(0, 8)}, 等待完成...`)

  // 截图提取中
  await page.goto(`/#/ontologies/${ontologyId}?tab=files`)
  await page.waitForTimeout(1000)
  await shot(page, outDir, `${domain}_llm_03_extracting`)

  // 5. 轮询
  const finalStatus = await pollExtraction(request, token, ontologyId, taskId, 1800_000)
  console.log(`    提取结果: ${finalStatus}`)

  // 6. 前端截图结果
  await page.goto(`/#/ontologies/${ontologyId}`)
  await page.waitForTimeout(1500)
  await shot(page, outDir, `${domain}_llm_04_ontology`)

  await page.goto(`/#/ontologies/${ontologyId}?tab=graph`)
  await page.waitForTimeout(3000)
  await shot(page, outDir, `${domain}_llm_05_graph`)

  // 7. 统计
  const stats = await collectStats(request, token, ontologyId)
  return { ontologyId, taskId, finalStatus, stats }
}

// ── 测试入口 ──────────────────────────────────────────────────────────────

test.describe.configure({ mode: 'serial' })

test.describe('三领域对比：Pipeline Mapping vs 简易 LLM', () => {
  const ts = Date.now()
  const outDir = path.resolve(
    __dirname,
    '../../../../.artifacts/playwright/stack/three-domains-comparison',
    String(ts),
  )
  let token = ''

  interface Row { domain: string; path: string; entities: number; edges: number; logic: number; actions: number; ontologyId: string; error?: string }
  const rows: Row[] = []

  test.beforeAll(async ({ request }) => {
    fs.mkdirSync(outDir, { recursive: true })
    token = await loginViaApi(request)
    console.log(`\n输出目录: ${outDir}`)
  })

  test.afterAll(() => {
    // 保存 JSON
    fs.writeFileSync(path.join(outDir, 'summary.json'), JSON.stringify(rows, null, 2), 'utf-8')

    // Markdown 表格
    const header = '| 业务域 | 路径 | 实体数 | 边数 | 逻辑规则数 | 动作数 | 本体ID |'
    const sep    = '|--------|------|--------|------|-----------|--------|--------|'
    const body   = rows.map(r =>
      r.error
        ? `| ${r.domain} | ${r.path} | ❌ | ❌ | ❌ | ❌ | ${r.error?.slice(0, 60)} |`
        : `| ${r.domain} | ${r.path} | ${r.entities} | ${r.edges} | ${r.logic} | ${r.actions} | \`${r.ontologyId.slice(0, 8)}\` |`
    )

    const table = [header, sep, ...body].join('\n')
    fs.writeFileSync(path.join(outDir, 'summary.md'), table + '\n', 'utf-8')

    console.log('\n\n======================================')
    console.log('         三领域 Ontology 汇总表')
    console.log('======================================')
    console.log(table)
    console.log('======================================\n')
    console.log(`完整结果: ${path.join(outDir, 'summary.md')}`)
  })

  // ── Pipeline Mapping 路径（3个域）──────────────────────────────────────
  for (const domain of DOMAINS) {
    test(`Pipeline Mapping — ${domain}`, async ({ page, request }) => {
      test.setTimeout(600_000)
      try {
        const result = await runPipelineMapping(page, request, token, domain, ts, outDir)
        rows.push({ domain, path: 'Pipeline Mapping', ...result.stats, ontologyId: result.ontologyId })
        expect(result.stats.entities, '应有至少 1 个实体').toBeGreaterThan(0)
      } catch (err: any) {
        rows.push({ domain, path: 'Pipeline Mapping', entities: 0, edges: 0, logic: 0, actions: 0, ontologyId: '', error: err.message })
        await page.screenshot({ path: path.join(outDir, `${domain}_pipeline_ERROR.jpg`), type: 'jpeg', quality: 75 }).catch(() => {})
        throw err
      }
    })
  }

  // ── 简易 LLM 路径（3个域）─────────────────────────────────────────────
  for (const domain of DOMAINS) {
    test(`简易 LLM — ${domain}`, async ({ page, request }) => {
      test.skip(
        !RUN_REAL_LLM,
        '需要 PLAYWRIGHT_THREE_DOMAINS_REAL=1、固定模型/提示词夹具及真实付费外部 LLM',
      )
      test.setTimeout(3_600_000) // 60 min: deepseek processes 8 files × ~3 min each
      try {
        const result = await runSimpleLLM(page, request, token, domain, ts, outDir)
        rows.push({ domain, path: '简易 LLM', ...result.stats, ontologyId: result.ontologyId })
        expect(result.finalStatus, '提取应成功').toBe('completed')
        expect(result.stats.entities, '应有至少 1 个实体').toBeGreaterThan(0)
      } catch (err: any) {
        rows.push({ domain, path: '简易 LLM', entities: 0, edges: 0, logic: 0, actions: 0, ontologyId: '', error: err.message })
        await page.screenshot({ path: path.join(outDir, `${domain}_llm_ERROR.jpg`), type: 'jpeg', quality: 75 }).catch(() => {})
        throw err
      }
    })
  }
})
