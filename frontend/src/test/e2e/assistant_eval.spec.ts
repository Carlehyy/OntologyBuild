import { expect, test, type Page, type Route } from '@playwright/test'

/**
 * 助手评估（系统设置子页）mocked E2E：发起抽样评估 → 任务列表 → 报告抽屉。
 * 全部接口离线 mock，不依赖真实后端/LLM。
 */

const now = '2026-08-26T06:00:00+00:00'

const assistants = [
  { key: 'ontology_agent', label: '本体助手', description: '/agent 本体智能体', conversation_count: 12, supported_dimension_keys: [] },
  { key: 'super_assistant', label: '超级助手', description: '/super-assistant 通用智能协作入口', conversation_count: 8, supported_dimension_keys: [] },
  { key: 'exploration', label: '建模对话', description: '/explore 对话式业务建模', conversation_count: 3, supported_dimension_keys: [] },
  { key: 'steward', label: '数据管家', description: '数据集成管家对话', conversation_count: 2, supported_dimension_keys: [] },
  { key: 'scene_assistant', label: '场景建模助手', description: '三维场景白模对话', conversation_count: 1, supported_dimension_keys: [] },
]

const dimensions = [
  { key: 'relevance', label: '相关性', kind: 'llm', description: '答复是否切题' },
  { key: 'hallucination', label: '幻觉控制', kind: 'llm', description: '是否存在无依据编造' },
  { key: 'instruction_following', label: '指令遵循', kind: 'llm', description: '是否遵循指令' },
  { key: 'harmfulness', label: '安全合规', kind: 'llm', description: '是否有害内容' },
  { key: 'trajectory', label: '轨迹质量', kind: 'llm', description: '执行路径质量' },
  { key: 'tool_call_success', label: '工具调用成功', kind: 'llm', description: '工具技术性成败' },
  { key: 'action_loop', label: '循环检测', kind: 'code', description: '动作重复度' },
  { key: 'response_repetition', label: '答复重复度', kind: 'code', description: 'n-gram 重复惩罚' },
]

const baseDimensionKeys = ['relevance', 'hallucination', 'instruction_following', 'response_repetition']

const taskSummary = {
  overall: 100,
  dimensions: {
    response_repetition: { label: '答复重复度', avg: 100, min: 100, max: 100, count: 1 },
  },
  badcase_conversation_ids: [],
  evaluated: 1,
  failed: 0,
  skipped: 0,
  llm_calls: 0,
  engine: 'code-only',
}

async function mockAssistantEvalApi(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: { id: 'admin', username: 'admin', email: 'admin@example.com', role: 'admin' },
      },
      version: 0,
    }))
  })
  await page.route('**/api/v2/inbox/summary', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      data: { openAlertCount: 0, actionableCount: 0, unreadCount: 0, resolvedCount: 0 },
    }),
  }))

  let tasks: Array<Record<string, unknown>> = []

  await page.route('**/api/v1/**', async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    const json = (data: unknown, status = 200) => route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify({ data }),
    })

    if (method === 'GET' && path === '/api/v1/assistant-evaluation/meta') {
      return json({ engine: 'builtin', assistants, dimension_catalog: dimensions, base_dimension_keys: baseDimensionKeys })
    }
    if (method === 'GET' && path === '/api/v1/assistant-evaluation/rubrics') return json([])
    if (method === 'GET' && path === '/api/v1/models') return json([])
    if (method === 'GET' && path === '/api/v1/assistant-evaluation/tasks') return json(tasks)
    if (method === 'POST' && path === '/api/v1/assistant-evaluation/tasks') {
      const task = {
        id: 'task-1',
        assistant_key: 'super_assistant',
        assistant_label: '超级助手',
        title: '超级助手 · 1 条会话',
        status: 'success',
        params: {
          mode: 'sample',
          dimension_keys: baseDimensionKeys,
          conversation_ids: ['c1'],
        },
        judge_model_name: '（仅代码型维度，无需 judge 模型）',
        conversation_count: 1,
        completed_conversations: 1,
        summary: taskSummary,
        error: null,
        created_at: now,
        finished_at: now,
        duration_ms: 120,
      }
      tasks = [task]
      return json(task)
    }
    if (method === 'GET' && path === '/api/v1/assistant-evaluation/tasks/task-1') {
      return json({
        ...tasks[0],
        items: [{
          id: 'item-1',
          conversation_id: 'c1',
          conversation_title: '销量分析',
          overall_score: 100,
          scores: { response_repetition: 100 },
          reasons: { response_repetition: { score: 100, reason: '无重复' } },
          flags: {},
          root_cause: '整体良好',
          created_at: now,
        }],
      })
    }
    if (method === 'GET' && path === '/api/v1/assistant-evaluation/trend') {
      return json([{
        id: 'task-1', title: '超级助手 · 1 条会话', created_at: now,
        overall: 100, dimensions: taskSummary.dimensions,
        judge_model_name: '（仅代码型维度，无需 judge 模型）',
      }])
    }
    return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ data: null }) })
  })
}

test('助手评估：发起抽样评估并查看质量报告', async ({ page }) => {
  await mockAssistantEvalApi(page)
  await page.goto('/#/settings/assistant-eval')

  await expect(page.getByText('发起评估')).toBeVisible()
  await expect(page.getByText('评分标准（可选，自定义 rubric 维度）')).toBeVisible()

  // 选择助手（超级助手）
  await page.getByRole('combobox').first().click()
  await page.getByText('超级助手 · 8 个会话', { exact: false }).click()
  await expect(page.getByText('/super-assistant 通用智能协作入口', { exact: false })).toBeVisible()

  // 默认抽样模式直接发起
  await page.getByRole('button', { name: '开始评估' }).click()
  await expect(page.getByText('评估任务已创建：超级助手 · 1 条会话', { exact: false })).toBeVisible()

  // 任务列表出现已完成任务，打开报告抽屉
  const taskRow = page.getByText('超级助手 · 1 条会话', { exact: true })
  await expect(taskRow).toBeVisible()
  await page.getByRole('button', { name: '报告' }).first().click()

  await expect(page.getByText('综合得分')).toBeVisible()
  await expect(page.getByText('100', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('答复重复度')).toBeVisible()
  await expect(page.getByText('整体良好', { exact: true })).toBeVisible()

  // 导出按钮存在（success 状态）
  await expect(page.getByRole('button', { name: '导出 Markdown' })).toBeVisible()
})
