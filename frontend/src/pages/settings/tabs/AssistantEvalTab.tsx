/**
 * 助手评估（系统设置子页）— 基于 OpenJudge 的助手会话质量评估。
 *
 * 结构：发起评估表单（单一提交边界）→ 评估任务列表（对象携带状态）→
 * 报告详情抽屉（总览 / 维度得分 / 会话明细下钻 / 导出）。
 */
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  InputNumber,
  Modal,
  Progress,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { Download, FlaskConical, Trash2 } from 'lucide-react'
import {
  assistantEvaluationApi,
  type AssistantMeta,
  type EvalMeta,
  type EvalTask,
  type EvalTaskDetail,
  type EvalTaskItem,
} from '@/api/assistantEvaluation'
import { modelApi } from '@/api/ontologies'

const { Text } = Typography

function formatTime(value: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

const STATUS_TAG: Record<string, { color: string; label: string }> = {
  queued: { color: 'default', label: '排队中' },
  running: { color: 'processing', label: '评估中' },
  success: { color: 'success', label: '已完成' },
  error: { color: 'error', label: '失败' },
}

function scoreColor(score: number): string {
  if (score >= 80) return '#52c41a'
  if (score >= 60) return '#faad14'
  return '#ff4d4f'
}

export default function AssistantEvalTab() {
  const queryClient = useQueryClient()
  const [assistantKey, setAssistantKey] = useState<string>('')
  const [mode, setMode] = useState<'manual' | 'sample'>('sample')
  const [selectedConversationIds, setSelectedConversationIds] = useState<string[]>([])
  const [sampleSize, setSampleSize] = useState<number>(10)
  const [sampleDays, setSampleDays] = useState<number>(30)
  const [dimensionKeys, setDimensionKeys] = useState<string[]>([])
  const [modelConfigId, setModelConfigId] = useState<string | undefined>(undefined)
  const [detailTaskId, setDetailTaskId] = useState<string | null>(null)

  const metaQuery = useQuery({
    queryKey: ['assistant-eval', 'meta'],
    queryFn: () => assistantEvaluationApi.meta(),
  })

  const meta: EvalMeta | undefined = metaQuery.data
  const assistants: AssistantMeta[] = useMemo(() => meta?.assistants ?? [], [meta])
  const activeAssistant = assistants.find(a => a.key === assistantKey)

  const effectiveDimensions = useMemo(() => {
    if (!meta) return []
    if (dimensionKeys.length) return dimensionKeys
    return meta.base_dimension_keys
  }, [meta, dimensionKeys])

  const conversationsQuery = useQuery({
    queryKey: ['assistant-eval', 'conversations', assistantKey],
    queryFn: () => assistantEvaluationApi.conversations(assistantKey, 100, 0),
    enabled: !!assistantKey && mode === 'manual',
  })

  const modelsQuery = useQuery({
    queryKey: ['assistant-eval', 'models'],
    queryFn: () => modelApi.list(),
    staleTime: 60_000,
  })

  const llmModels = useMemo(
    () => (modelsQuery.data ?? []).filter(m => m.config_type === 'llm' && m.enabled !== false),
    [modelsQuery.data],
  )

  const tasksQuery = useQuery({
    queryKey: ['assistant-eval', 'tasks'],
    queryFn: () => assistantEvaluationApi.tasks(),
    refetchInterval: (query) => {
      const tasks = query.state.data ?? []
      const inFlight = tasks.some(t => t.status === 'queued' || t.status === 'running')
      return inFlight ? 3_000 : false
    },
  })

  const createTaskMutation = useMutation({
    mutationFn: () =>
      assistantEvaluationApi.createTask({
        assistant_key: assistantKey,
        conversation_ids: selectedConversationIds,
        sample_size: sampleSize,
        sample_days: sampleDays,
        dimension_keys: effectiveDimensions,
        model_config_id: modelConfigId ?? null,
      }),
    onSuccess: (task) => {
      void queryClient.invalidateQueries({ queryKey: ['assistant-eval', 'tasks'] })
      message.success(`评估任务已创建：${task.title}`)
      setSelectedConversationIds([])
    },
  })

  const deleteTaskMutation = useMutation({
    mutationFn: (taskId: string) => assistantEvaluationApi.deleteTask(taskId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['assistant-eval', 'tasks'] })
      if (detailTaskId) setDetailTaskId(null)
    },
  })

  const detailQuery = useQuery({
    queryKey: ['assistant-eval', 'task', detailTaskId],
    queryFn: () => assistantEvaluationApi.taskDetail(detailTaskId!),
    enabled: !!detailTaskId,
  })

  const handleAssistantChange = (key: string) => {
    setAssistantKey(key)
    setSelectedConversationIds([])
  }

  const canSubmit =
    !!activeAssistant &&
    effectiveDimensions.length > 0 &&
    (mode === 'sample' || selectedConversationIds.length > 0)

  const conversationColumns: ColumnsType<{ id: string; title: string; created_at: string | null; message_count: number }> = [
    { title: '会话标题', dataIndex: 'title', ellipsis: true },
    { title: '消息数', dataIndex: 'message_count', width: 80 },
    { title: '创建时间', dataIndex: 'created_at', width: 120, render: formatTime },
  ]

  const taskColumns: ColumnsType<EvalTask> = [
    {
      title: '助手',
      dataIndex: 'assistant_label',
      width: 110,
      render: (label: string, record) => (
        <Space size={4}>
          <span>{label}</span>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {record.params.mode === 'sample' ? '抽样' : '指定'}
          </Text>
        </Space>
      ),
    },
    { title: '任务', dataIndex: 'title', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'status',
      width: 130,
      render: (_: string, record) => {
        const tag = STATUS_TAG[record.status] ?? { color: 'default', label: record.status }
        const progress =
          record.status === 'running' ? `${record.completed_conversations}/${record.conversation_count}` : null
        return (
          <Space size={4}>
            <Tag color={tag.color}>{tag.label}</Tag>
            {progress && <Text type="secondary" style={{ fontSize: 11 }}>{progress}</Text>}
          </Space>
        )
      },
    },
    {
      title: '综合分',
      width: 90,
      dataIndex: ['summary', 'overall'],
      render: (score: number | null | undefined) =>
        score == null ? '-' : <Text strong style={{ color: scoreColor(score) }}>{score}</Text>,
    },
    { title: 'judge 模型', dataIndex: 'judge_model_name', width: 150, ellipsis: true },
    { title: '创建时间', dataIndex: 'created_at', width: 120, render: formatTime },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (_, record) => (
        <Space size={4}>
          <Button type="link" size="small" onClick={() => setDetailTaskId(record.id)}>
            报告
          </Button>
          {record.status !== 'queued' && record.status !== 'running' && (
            <Button
              type="text"
              size="small"
              aria-label="删除任务"
              icon={<Trash2 size={14} />}
              onClick={() => {
                Modal.confirm({
                  title: '删除该评估任务？',
                  content: '任务报告与明细将一并删除，历史对比基线随之失效。',
                  okText: '删除',
                  okButtonProps: { danger: true },
                  cancelText: '取消',
                  onOk: () => deleteTaskMutation.mutateAsync(record.id),
                })
              }}
            />
          )}
        </Space>
      ),
    },
  ]


  const detail = detailQuery.data

  return (
    <div className="space-y-6">
      {/* 发起评估 */}
      <section className="rounded-lg border border-gray-200 bg-white p-4">
        <div className="mb-3 flex items-center gap-2">
          <FlaskConical size={16} className="text-gray-400" />
          <span className="text-sm font-medium text-gray-900">发起评估</span>
          {meta && (
            <Tag>{meta.engine === 'openjudge' ? 'OpenJudge 引擎' : '内置引擎'}</Tag>
          )}
        </div>
        <div className="grid gap-x-6 gap-y-3 md:grid-cols-2">
          <div>
            <div className="mb-1 text-xs text-gray-500">评估对象</div>
            <Select
              className="w-full"
              placeholder="选择助手"
              value={assistantKey || undefined}
              onChange={handleAssistantChange}
              options={assistants.map(a => ({
                value: a.key,
                label: `${a.label} · ${a.conversation_count} 个会话`,
              }))}
            />
            {activeAssistant && (
              <div className="mt-1 text-[11px] text-gray-400">{activeAssistant.description}</div>
            )}
          </div>
          <div>
            <div className="mb-1 text-xs text-gray-500">judge 模型（LLM 型维度使用）</div>
            <Select
              className="w-full"
              placeholder="默认：平台默认模型"
              allowClear
              value={modelConfigId}
              onChange={setModelConfigId}
              options={llmModels.map(m => ({
                value: m.id,
                label: m.is_default ? `${m.name}（默认）` : m.name,
              }))}
              notFoundContent={<Text type="secondary" style={{ fontSize: 12 }}>暂无可用 LLM 配置，请先到「模型配置」添加</Text>}
            />
          </div>
          <div className="md:col-span-2">
            <div className="mb-1 text-xs text-gray-500">会话范围</div>
            <Segmented
              value={mode}
              onChange={(v) => setMode(v as 'manual' | 'sample')}
              options={[
                { value: 'sample', label: '批量抽样' },
                { value: 'manual', label: '手动选择' },
              ]}
            />
            {mode === 'sample' ? (
              <Space className="mt-2" size={16} wrap>
                <Space size={4}>
                  <Text type="secondary" style={{ fontSize: 12 }}>最近</Text>
                  <InputNumber min={1} max={180} value={sampleDays} onChange={(v) => setSampleDays(v ?? 30)} />
                  <Text type="secondary" style={{ fontSize: 12 }}>天内</Text>
                </Space>
                <Space size={4}>
                  <Text type="secondary" style={{ fontSize: 12 }}>抽取</Text>
                  <InputNumber min={1} max={30} value={sampleSize} onChange={(v) => setSampleSize(v ?? 10)} />
                  <Text type="secondary" style={{ fontSize: 12 }}>条会话</Text>
                </Space>
              </Space>
            ) : (
              <Table
                className="mt-2"
                size="small"
                rowKey="id"
                loading={conversationsQuery.isLoading}
                columns={conversationColumns}
                dataSource={conversationsQuery.data?.items ?? []}
                pagination={false}
                scroll={{ y: 220 }}
                rowSelection={{
                  selectedRowKeys: selectedConversationIds,
                  onChange: (keys) => setSelectedConversationIds(keys as string[]),
                }}
                locale={{ emptyText: assistantKey ? '该助手暂无会话' : '请先选择助手' }}
              />
            )}
          </div>
          <div className="md:col-span-2">
            <div className="mb-1 text-xs text-gray-500">
              评分维度<span className="ml-2 text-[11px] text-gray-400">LLM 型消耗 judge 模型；代码型零成本固定执行</span>
            </div>
            <Checkbox.Group
              className="flex flex-col gap-1"
              value={effectiveDimensions}
              onChange={(keys) => setDimensionKeys(keys as string[])}
              options={(meta?.dimension_catalog ?? [])
                .filter(d => !assistantKey || activeAssistant?.supported_dimension_keys.includes(d.key))
                .map(d => ({ value: d.key, label: `${d.label}（${d.kind === 'llm' ? 'LLM' : '代码'}）· ${d.description}` }))}
            />
          </div>
        </div>
        <div className="mt-4 flex items-center justify-between">
          <Text type="secondary" style={{ fontSize: 11 }}>
            {mode === 'sample'
              ? `将从最近 ${sampleDays} 天的会话中抽取最多 ${sampleSize} 条`
              : `已选 ${selectedConversationIds.length} 条会话`}
            {' · '}单次最多 50 条
          </Text>
          <Button
            type="primary"
            disabled={!canSubmit}
            loading={createTaskMutation.isPending}
            onClick={() => createTaskMutation.mutate()}
          >
            开始评估
          </Button>
        </div>
        {createTaskMutation.isError && (
          <Alert
            className="mt-2"
            type="error"
            showIcon
            message={createTaskMutation.error instanceof Error ? createTaskMutation.error.message : '创建评估任务失败'}
          />
        )}
      </section>

      {/* 任务列表 */}
      <section>
        <Table
          rowKey="id"
          size="small"
          loading={tasksQuery.isLoading}
          columns={taskColumns}
          dataSource={tasksQuery.data ?? []}
          pagination={{ pageSize: 8, hideOnSinglePage: true }}
          locale={{ emptyText: '暂无评估任务，先在上方发起一次评估' }}
          expandable={{
            expandedRowRender: (record) =>
              record.status === 'error' ? (
                <Alert type="error" showIcon message="任务执行失败" description={record.error} />
              ) : (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  维度：{(record.params.dimension_keys ?? []).join('、')} · 会话{' '}
                  {record.completed_conversations}/{record.conversation_count}
                </Text>
              ),
            rowExpandable: (record) => record.status === 'error' || record.status === 'success',
          }}
        />
      </section>

      {/* 报告详情 */}
      <Drawer
        title={`质量报告 · ${detail?.title ?? ''}`}
        placement="right"
        width={720}
        open={!!detailTaskId}
        onClose={() => setDetailTaskId(null)}
        extra={
          detail?.status === 'success' && (
            <Button
              size="small"
              icon={<Download size={14} />}
              onClick={() => detail && void assistantEvaluationApi.exportReport(detail.id)}
            >
              导出 Markdown
            </Button>
          )
        }
      >
        {!detail ? (
          <Text type="secondary">加载中…</Text>
        ) : detail.status === 'running' || detail.status === 'queued' ? (
          <Alert
            type="info"
            showIcon
            message={`评估进行中：${detail.completed_conversations}/${detail.conversation_count} 条会话`}
          />
        ) : detail.status === 'error' ? (
          <Alert type="error" showIcon message="任务执行失败" description={detail.error} />
        ) : (
          <EvalReportBody detail={detail} />
        )}
      </Drawer>
    </div>
  )
}

function EvalReportBody({ detail }: { detail: EvalTaskDetail }) {
  const itemColumns: ColumnsType<EvalTaskItem> = [
    { title: '会话', dataIndex: 'conversation_title', ellipsis: true },
    {
      title: '总分',
      dataIndex: 'overall_score',
      width: 90,
      render: (score: number | null) =>
        score == null ? <Text type="secondary">未产出</Text> : <Text strong style={{ color: scoreColor(score) }}>{score}</Text>,
    },
    { title: '根因归类', dataIndex: 'root_cause', width: 200, ellipsis: true },
    {
      title: '标记',
      key: 'flags',
      width: 160,
      render: (_, record) => {
        const notes: string[] = []
        if (record.flags.loop_detected) notes.push('动作循环')
        if (record.flags.tool_error_count) notes.push(`${record.flags.tool_error_count} 次工具失败`)
        if (record.flags.engine_error) notes.push('评分执行异常')
        return notes.length ? <Tag color="warning">{notes.join('、')}</Tag> : <Text type="secondary">-</Text>
      },
    },
  ]

  const summary = detail.summary
  const overall = summary.overall
  const itemReasons = (item: EvalTaskItem) => (
    <div className="space-y-1 pl-2">
      {Object.entries(item.reasons).map(([key, reason]) => (
        <div key={key} className="text-xs text-gray-600">
          {reason.score != null && (
            <span className="mr-1 inline-block min-w-9 font-medium">{reason.score}分</span>
          )}
          {reason.reason || '-'}
        </div>
      ))}
      {!!item.flags.low_dims?.length && (
        <div className="text-xs text-red-500">薄弱维度：{item.flags.low_dims.join('、')}</div>
      )}
    </div>
  )

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-gray-200 px-4 py-3">
          <div className="text-xs text-gray-500">综合得分</div>
          <div className="text-2xl font-semibold leading-tight" style={{ color: overall != null ? scoreColor(overall) : undefined }}>
            {overall ?? '-'}
          </div>
        </div>
        <div className="rounded-lg border border-gray-200 px-4 py-3">
          <div className="text-xs text-gray-500">成功 / 失败会话</div>
          <div className="text-2xl font-semibold leading-tight text-gray-900">
            {summary.evaluated}
            <span className="text-sm text-gray-400"> / {summary.failed}</span>
          </div>
        </div>
        <div className="rounded-lg border border-gray-200 px-4 py-3">
          <div className="text-xs text-gray-500">judge 模型调用</div>
          <div className="text-2xl font-semibold leading-tight text-gray-900">{summary.llm_calls}</div>
        </div>
      </div>

      <div>
        <div className="mb-2 text-sm font-medium text-gray-900">维度得分</div>
        <div className="space-y-2">
          {Object.entries(summary.dimensions ?? {}).map(([key, stat]) => (
            <div key={key} className="flex items-center gap-3">
              <span className="w-20 shrink-0 text-xs text-gray-500">{stat.label}</span>
              <Progress
                percent={stat.avg}
                size="small"
                strokeColor={scoreColor(stat.avg)}
                format={() => `${stat.avg}`}
              />
              <span className="shrink-0 text-[11px] text-gray-400">
                最低 {stat.min} · 最高 {stat.max} · n={stat.count}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div>
        <div className="mb-2 flex items-baseline justify-between">
          <span className="text-sm font-medium text-gray-900">会话明细</span>
          <span className="text-[11px] text-gray-400">
            引擎 {summary.engine} · judge：{detail.judge_model_name || '-'} · 耗时{' '}
            {detail.duration_ms != null ? `${(detail.duration_ms / 1000).toFixed(1)}s` : '-'}
          </span>
        </div>
        <Table
          rowKey="id"
          size="small"
          columns={itemColumns}
          dataSource={detail.items}
          pagination={false}
          expandable={{ expandedRowRender: itemReasons }}
        />
      </div>
    </div>
  )
}
