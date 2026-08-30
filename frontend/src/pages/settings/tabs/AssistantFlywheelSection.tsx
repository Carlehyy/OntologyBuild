/**
 * 数据飞轮（助手评估子区）— 基准集 / 噪声校准 / 提案与双臂实验 / 值守 / 时间线。
 *
 * 对应后端 /api/v1/assistant-evaluation 的 M1-M3 能力：评分→归因→
 * 模拟（沙箱双臂验证）→投产（版本化+回退）→值守（定时自动循环）。
 * 颜色一律取语义令牌，不新建色板；列表态用 antd 标准组件。
 */
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, Button, Drawer, Input, InputNumber, Modal, Popconfirm, Progress,
  Select, Space, Switch, Table, Tag, TimePicker, Typography, message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  Activity, FlaskConical, Gauge, History,
  PlayCircle, Plus, Trash2, TrendingUp, Undo2,
} from 'lucide-react'
import dayjs from 'dayjs'
import {
  assistantEvaluationApi,
  type AutopilotConfig,
  type BenchmarkItem,
  type BenchmarkSet,
  type Calibration,
  type Experiment,
  type ExperimentDetail,
  type ProfileVersion,
  type Proposal,
  type TimelineEvent,
} from '@/api/assistantEvaluation'
import { modelApi, ontologyApi } from '@/api/ontologies'

const { Text } = Typography

const VIEWS = [
  { value: 'benchmarks', label: '基准集', icon: Gauge },
  { value: 'calibration', label: '噪声校准', icon: FlaskConical },
  { value: 'proposals', label: '提案与实验', icon: TrendingUp },
  { value: 'autopilot', label: '值守', icon: Activity },
  { value: 'timeline', label: '时间线', icon: History },
] as const

type ViewKey = (typeof VIEWS)[number]['value']

const STATUS_TAG: Record<string, { color: string; label: string }> = {
  queued: { color: 'default', label: '排队中' },
  running: { color: 'processing', label: '执行中' },
  success: { color: 'success', label: '已完成' },
  error: { color: 'error', label: '失败' },
  draft: { color: 'default', label: '草稿' },
  validated: { color: 'success', label: '门禁通过' },
  applied: { color: 'success', label: '已投产' },
  rolled_back: { color: 'warning', label: '已回退' },
  superseded: { color: 'default', label: '已让位' },
  active: { color: 'success', label: '生效中' },
}

const ACTOR_LABEL: Record<string, string> = {
  admin: '管理员', system: '系统', autopilot: '值守',
}

const EVENT_LABEL: Record<string, string> = {
  task_created: '评估发起', task_succeeded: '评估完成', task_failed: '评估失败',
  benchmark_created: '基准集创建', benchmark_deleted: '基准集删除',
  benchmark_items_added: '基准集扩充', benchmark_item_removed: '基准集条目移除',
  calibration_created: '校准发起', calibration_succeeded: '校准完成',
  calibration_failed: '校准失败',
  proposal_created: '提案创建', proposal_applied: '提案投产',
  experiment_created: '实验发起', experiment_succeeded: '实验完成',
  experiment_failed: '实验失败',
  version_rolled_back: '版本回退',
  cycle_started: '值守轮开始', cycle_succeeded: '值守轮完成',
  cycle_skipped: '值守轮跳过', cycle_failed: '值守轮失败',
}

function statusTag(status: string) {
  const conf = STATUS_TAG[status] ?? { color: 'default', label: status }
  return <Tag color={conf.color}>{conf.label}</Tag>
}

export default function AssistantFlywheelSection() {
  const [view, setView] = useState<ViewKey>('benchmarks')

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        {VIEWS.map(({ value, label, icon: Icon }) => (
          <Button
            key={value}
            type={view === value ? 'primary' : 'default'}
            size="small"
            icon={<Icon size={14} />}
            onClick={() => setView(value)}
          >
            {label}
          </Button>
        ))}
      </div>
      {view === 'benchmarks' && <BenchmarkView />}
      {view === 'calibration' && <CalibrationView />}
      {view === 'proposals' && <ProposalView />}
      {view === 'autopilot' && <AutopilotView />}
      {view === 'timeline' && <TimelineView />}
    </div>
  )
}

// ---------------------------------------------------------------- 本体选择（飞轮各视图共用）

function useOntologies() {
  return useQuery({
    queryKey: ['assistant-eval', 'ontologies'],
    queryFn: () => ontologyApi.list({ page: 1, page_size: 100 }),
    staleTime: 60_000,
  })
}

// ---------------------------------------------------------------- 基准集

function BenchmarkView() {
  const queryClient = useQueryClient()
  const [detailId, setDetailId] = useState<string | null>(null)
  const [fromTaskOpen, setFromTaskOpen] = useState(false)
  const [fromTaskId, setFromTaskId] = useState<string>('')
  const [fromInclude, setFromInclude] = useState<'badcase' | 'all'>('badcase')

  const benchmarksQuery = useQuery({
    queryKey: ['assistant-eval', 'benchmarks'],
    queryFn: () => assistantEvaluationApi.benchmarks(),
  })
  const tasksQuery = useQuery({
    queryKey: ['assistant-eval', 'tasks'],
    queryFn: () => assistantEvaluationApi.tasks(),
  })
  const detailQuery = useQuery({
    queryKey: ['assistant-eval', 'benchmark-detail', detailId],
    queryFn: () => assistantEvaluationApi.benchmarkDetail(detailId!),
    enabled: !!detailId,
  })

  const fromTaskMutation = useMutation({
    mutationFn: () => assistantEvaluationApi.createBenchmarkFromTask({
      task_id: fromTaskId, include: fromInclude,
    }),
    onSuccess: () => {
      message.success('基准集已从评估任务沉淀')
      setFromTaskOpen(false)
      queryClient.invalidateQueries({ queryKey: ['assistant-eval', 'benchmarks'] })
    },
    onError: (error: Error) => message.error(error.message),
  })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => assistantEvaluationApi.deleteBenchmark(id),
    onSuccess: () => {
      message.success('基准集已删除')
      queryClient.invalidateQueries({ queryKey: ['assistant-eval', 'benchmarks'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const columns: ColumnsType<BenchmarkSet> = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '助手', dataIndex: 'assistant_key', key: 'assistant_key', width: 120 },
    {
      title: '切分', key: 'split', width: 160,
      render: (_, row) => (
        <Space size={4}>
          <Tag>训练 {row.train_count}</Tag>
          <Tag color="purple">留出 {row.heldout_count}</Tag>
        </Space>
      ),
    },
    { title: '条目', dataIndex: 'item_count', key: 'item_count', width: 80 },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 150,
      render: (value: string | null) => (value ? dayjs(value).format('MM-DD HH:mm') : '-') },
    {
      title: '操作', key: 'actions', width: 150,
      render: (_, row) => (
        <Space size={4}>
          <Button size="small" type="link" onClick={() => setDetailId(row.id)}>查看</Button>
          <Popconfirm title="确认删除该基准集？" onConfirm={() => deleteMutation.mutate(row.id)}>
            <Button size="small" type="link" danger icon={<Trash2 size={13} />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const itemColumns: ColumnsType<BenchmarkItem> = [
    { title: '会话', dataIndex: 'conversation_title', key: 'conversation_title', ellipsis: true },
    {
      title: '切分', dataIndex: 'split', key: 'split', width: 90,
      render: (split: string) => (
        <Tag color={split === 'heldout' ? 'purple' : undefined}>
          {split === 'heldout' ? '留出' : '训练'}
        </Tag>
      ),
    },
    {
      title: '来源', dataIndex: 'origin', key: 'origin', width: 90,
      render: (origin: string) =>
        origin === 'badcase' ? <Tag color="red">坏例</Tag> : <Tag>{origin}</Tag>,
    },
  ]

  const successTasks = (tasksQuery.data ?? []).filter(t => t.status === 'success')

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <Text type="secondary">
          从评估坏例沉淀固定复评集合：训练/留出稳定哈希切分，留出集只作投产门禁。
        </Text>
        <Button size="small" type="primary" icon={<Plus size={14} />}
                disabled={!successTasks.length} onClick={() => setFromTaskOpen(true)}>
          从评估任务沉淀
        </Button>
      </div>
      <Table rowKey="id" size="small" columns={columns}
             dataSource={benchmarksQuery.data ?? []}
             loading={benchmarksQuery.isLoading} pagination={{ pageSize: 8 }} />

      <Modal
        title="从评估任务沉淀基准集" open={fromTaskOpen} onCancel={() => setFromTaskOpen(false)}
        confirmLoading={fromTaskMutation.isPending}
        onOk={() => fromTaskId && fromTaskMutation.mutate()}
      >
        <Space direction="vertical" className="w-full" size={8}>
          <Select className="w-full" placeholder="选择已成功的评估任务"
                  value={fromTaskId || undefined} onChange={setFromTaskId}
                  options={successTasks.map(t => ({
                    value: t.id,
                    label: `${t.title}（${t.created_at ? dayjs(t.created_at).format('MM-DD HH:mm') : '-'}）`,
                  }))} />
          <Select className="w-full" value={fromInclude} onChange={setFromInclude}
                  options={[
                    { value: 'badcase', label: '仅坏例（低于 60 分）' },
                    { value: 'all', label: '全部评分会话' },
                  ]} />
        </Space>
      </Modal>

      <Drawer title="基准集条目" width={520} open={!!detailId} onClose={() => setDetailId(null)}>
        <Table rowKey="id" size="small" columns={itemColumns}
               dataSource={detailQuery.data?.items ?? []}
               loading={detailQuery.isLoading} pagination={{ pageSize: 12 }} />
      </Drawer>
    </div>
  )
}

// ---------------------------------------------------------------- 噪声校准

function CalibrationView() {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [repeats, setRepeats] = useState<number>(2)
  const [benchmarkSetId, setBenchmarkSetId] = useState<string>('')

  const calibrationsQuery = useQuery({
    queryKey: ['assistant-eval', 'calibrations'],
    queryFn: () => assistantEvaluationApi.calibrations(),
  })
  const benchmarksQuery = useQuery({
    queryKey: ['assistant-eval', 'benchmarks'],
    queryFn: () => assistantEvaluationApi.benchmarks(),
  })

  const createMutation = useMutation({
    mutationFn: () => assistantEvaluationApi.createCalibration({
      assistant_key: 'ontology_agent',
      benchmark_set_id: benchmarkSetId || null,
      repeats,
      dimension_keys: [],
    }),
    onSuccess: () => {
      message.success('校准任务已发起（同一批会话重复评分度量方差）')
      setOpen(false)
      queryClient.invalidateQueries({ queryKey: ['assistant-eval', 'calibrations'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const columns: ColumnsType<Calibration> = [
    { title: '助手', dataIndex: 'assistant_key', key: 'assistant_key', width: 120 },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90,
      render: (status: string) => statusTag(status) },
    {
      title: '噪声地板（overall）', key: 'noise', width: 140,
      render: (_, row) => row.result?.overall_noise != null
        ? <Text strong>{row.result.overall_noise.toFixed(1)}</Text>
        : <Text type="secondary">-</Text>,
    },
    {
      title: '各维度方差', key: 'per_dim', ellipsis: true,
      render: (_, row) => (
        <Space size={4} wrap>
          {Object.entries(row.result?.per_dim ?? {}).map(([dim, stat]) => (
            <Tag key={dim}>{dim} ±{stat.noise.toFixed(1)}</Tag>
          ))}
        </Space>
      ),
    },
    { title: '重复次数', key: 'repeats', width: 90,
      render: (_, row) => row.result?.repeats ?? row.params?.repeats ?? '-' },
    { title: '完成时间', dataIndex: 'finished_at', key: 'finished_at', width: 150,
      render: (value: string | null) => (value ? dayjs(value).format('MM-DD HH:mm') : '-') },
  ]

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <Text type="secondary">
          同一批会话重复评分度量 judge 方差——自动投产阈值必须显著大于噪声地板。
        </Text>
        <Button size="small" type="primary" icon={<PlayCircle size={14} />} onClick={() => setOpen(true)}>
          发起校准
        </Button>
      </div>
      <Table rowKey="id" size="small" columns={columns}
             dataSource={calibrationsQuery.data ?? []}
             loading={calibrationsQuery.isLoading} pagination={{ pageSize: 8 }} />

      <Modal title="发起噪声校准" open={open} onCancel={() => setOpen(false)}
             confirmLoading={createMutation.isPending}
             onOk={() => createMutation.mutate()}>
        <Space direction="vertical" className="w-full" size={8}>
          <Select className="w-full" placeholder="选择基准集（本体助手）"
                  value={benchmarkSetId || undefined} onChange={setBenchmarkSetId}
                  options={(benchmarksQuery.data ?? [])
                    .filter(b => b.assistant_key === 'ontology_agent')
                    .map(b => ({ value: b.id, label: `${b.name}（${b.item_count} 条）` }))} />
          <InputNumber min={2} max={5} value={repeats}
                       onChange={value => setRepeats(value ?? 2)}
                       addonBefore="重复次数" className="w-full" />
        </Space>
      </Modal>
    </div>
  )
}

// ---------------------------------------------------------------- 提案与实验

function ProposalView() {
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [type, setType] = useState<'prompt_patch' | 'model_swap'>('prompt_patch')
  const [ontologyId, setOntologyId] = useState<string>('')
  const [systemPromptExtra, setSystemPromptExtra] = useState('')
  const [modelConfigId, setModelConfigId] = useState<string>('')
  const [experimentId, setExperimentId] = useState<string | null>(null)
  const [experimentProposalId, setExperimentProposalId] = useState<string>('')
  const [benchmarkSetId, setBenchmarkSetId] = useState<string>('')
  const [threshold, setThreshold] = useState<number>(5)

  const ontologiesQuery = useOntologies()
  const proposalsQuery = useQuery({
    queryKey: ['assistant-eval', 'proposals'],
    queryFn: () => assistantEvaluationApi.proposals(),
  })
  const benchmarksQuery = useQuery({
    queryKey: ['assistant-eval', 'benchmarks'],
    queryFn: () => assistantEvaluationApi.benchmarks(),
  })
  const modelsQuery = useQuery({
    queryKey: ['assistant-eval', 'models'],
    queryFn: () => modelApi.list(),
    staleTime: 60_000,
  })
  const experimentQuery = useQuery({
    queryKey: ['assistant-eval', 'experiment-detail', experimentId],
    queryFn: () => assistantEvaluationApi.experimentDetail(experimentId!),
    enabled: !!experimentId,
  })

  const createMutation = useMutation({
    mutationFn: () => assistantEvaluationApi.createProposal({
      ontology_id: ontologyId, type,
      payload: type === 'prompt_patch'
        ? { system_prompt_extra: systemPromptExtra }
        : { model_config_id: modelConfigId },
    }),
    onSuccess: () => {
      message.success('提案已创建（草稿）')
      setCreateOpen(false)
      queryClient.invalidateQueries({ queryKey: ['assistant-eval', 'proposals'] })
    },
    onError: (error: Error) => message.error(error.message),
  })
  const experimentMutation = useMutation({
    mutationFn: () => assistantEvaluationApi.createExperiment({
      proposal_id: experimentProposalId, benchmark_set_id: benchmarkSetId, threshold,
    }),
    onSuccess: () => {
      message.success('双臂实验已发起（沙箱回放 + 留出集门禁）')
      queryClient.invalidateQueries({ queryKey: ['assistant-eval', 'proposals'] })
    },
    onError: (error: Error) => message.error(error.message),
  })
  const applyMutation = useMutation({
    mutationFn: (id: string) => assistantEvaluationApi.applyProposal(id),
    onSuccess: () => {
      message.success('已投产（版本快照已登记，可回退）')
      queryClient.invalidateQueries({ queryKey: ['assistant-eval', 'proposals'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const ontologyName = useMemo(() => new Map(
    (ontologiesQuery.data?.items ?? []).map(o => [o.id, o.name])), [ontologiesQuery.data])

  const columns: ColumnsType<Proposal> = [
    { title: '提案', dataIndex: 'title', key: 'title', ellipsis: true },
    { title: '本体', key: 'ontology', width: 130, ellipsis: true,
      render: (_, row) => ontologyName.get(row.ontology_id) ?? row.ontology_id.slice(0, 8) },
    { title: '杠杆', dataIndex: 'type', key: 'type', width: 110,
      render: (value: string) => value === 'prompt_patch'
        ? <Tag color="blue">提示词</Tag> : <Tag color="cyan">换模型</Tag> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: (status: string) => statusTag(status) },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 140,
      render: (value: string | null) => (value ? dayjs(value).format('MM-DD HH:mm') : '-') },
    {
      title: '操作', key: 'actions', width: 170,
      render: (_, row) => (
        <Space size={4}>
          <Button size="small" type="link" icon={<FlaskConical size={13} />}
                  disabled={row.status !== 'draft' && row.status !== 'validated'}
                  onClick={() => {
                    setExperimentProposalId(row.id)
                    const bench = (benchmarksQuery.data ?? []).find(
                      b => b.ontology_id === row.ontology_id)
                    if (bench) setBenchmarkSetId(bench.id)
                  }}>
            验证
          </Button>
          {row.status === 'validated' && (
            <Popconfirm title="投产到生产配置？（登记版本快照，可回退）"
                        onConfirm={() => applyMutation.mutate(row.id)}>
              <Button size="small" type="link">投产</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  const detail = experimentQuery.data as ExperimentDetail | undefined
  const gate = detail?.result?.gate

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <Text type="secondary">
          草稿提案 → 沙箱双臂回放 → 留出集门禁 → 投产。验证的与投产的永远是同一份 payload。
        </Text>
        <Button size="small" type="primary" icon={<Plus size={14} />} onClick={() => setCreateOpen(true)}>
          新建提案
        </Button>
      </div>
      <Table rowKey="id" size="small" columns={columns}
             dataSource={proposalsQuery.data ?? []}
             loading={proposalsQuery.isLoading} pagination={{ pageSize: 8 }} />

      <Modal title="新建优化提案" open={createOpen} onCancel={() => setCreateOpen(false)}
             confirmLoading={createMutation.isPending}
             onOk={() => ontologyId && createMutation.mutate()}>
        <Space direction="vertical" className="w-full" size={8}>
          <Select className="w-full" placeholder="选择本体" showSearch optionFilterProp="label"
                  value={ontologyId || undefined} onChange={setOntologyId}
                  options={(ontologiesQuery.data?.items ?? []).map(o => ({
                    value: o.id, label: o.name }))} />
          <Select className="w-full" value={type} onChange={setType}
                  options={[
                    { value: 'prompt_patch', label: '提示词补丁（system_prompt_extra 全量替换）' },
                    { value: 'model_swap', label: '更换默认模型' },
                  ]} />
          {type === 'prompt_patch' ? (
            <Input.TextArea rows={5} placeholder="替换后的完整 system_prompt_extra"
                            value={systemPromptExtra}
                            onChange={e => setSystemPromptExtra(e.target.value)} />
          ) : (
            <Select className="w-full" placeholder="选择目标模型配置" showSearch optionFilterProp="label"
                    value={modelConfigId || undefined} onChange={setModelConfigId}
                    options={(modelsQuery.data ?? [])
                      .filter(m => m.config_type === 'llm')
                      .map(m => ({ value: m.id, label: m.name }))} />
          )}
        </Space>
      </Modal>

      <Modal title="发起双臂实验" open={!!experimentProposalId}
             onCancel={() => setExperimentProposalId('')}
             confirmLoading={experimentMutation.isPending}
             onOk={() => experimentProposalId && benchmarkSetId && experimentMutation.mutate()}>
        <Space direction="vertical" className="w-full" size={8}>
          <Select className="w-full" placeholder="选择该本体的基准集（须含留出集）"
                  value={benchmarkSetId || undefined} onChange={setBenchmarkSetId}
                  options={(benchmarksQuery.data ?? [])
                    .filter(b => b.ontology_id === (proposalsQuery.data ?? [])
                      .find(p => p.id === experimentProposalId)?.ontology_id)
                    .map(b => ({ value: b.id, label: `${b.name}（留出 ${b.heldout_count}）` }))} />
          <InputNumber min={0} step={0.5} value={threshold}
                       onChange={value => setThreshold(value ?? 5)}
                       addonBefore="门禁阈值" className="w-full" />
        </Space>
      </Modal>

      <Drawer title="实验对比" width={640} open={!!experimentId}
              onClose={() => setExperimentId(null)}>
        {detail && gate ? (
          <div className="flex flex-col gap-4">
            <Alert
              type={gate.passed ? 'success' : 'warning'}
              showIcon
              message={gate.passed ? '留出集门禁通过' : '门禁未通过'}
              description={`留出集增量 ${gate.heldout_delta ?? '-'} / 有效阈值 ${gate.effective_threshold}（含 2×噪声地板 ${gate.noise_floor}）`}
            />
            {(['baseline', 'trial'] as const).map(arm => {
              const stats = detail.result[arm]
              return (
                <div key={arm} className="flex flex-col gap-1">
                  <Text strong>{arm === 'baseline' ? '当前配置臂' : '草稿配置臂'}
                    <Tag className="ml-2">{stats.scored} 条评分 · 均分 {stats.overall ?? '-'}</Tag>
                  </Text>
                  {Object.entries(stats.per_dim).map(([dim, stat]) => (
                    <Progress key={dim} percent={stat.avg} size="small"
                              strokeColor={stat.avg >= 80 ? '#52c41a' : stat.avg >= 60 ? '#faad14' : '#ff4d4f'}
                              format={() => `${dim} ${stat.avg}`} />
                  ))}
                </div>
              )
            })}
          </div>
        ) : <Text type="secondary">加载中…</Text>}
      </Drawer>

      <ExperimentListWidget onOpenDetail={setExperimentId} />
    </div>
  )
}

function ExperimentListWidget({ onOpenDetail }: { onOpenDetail: (id: string) => void }) {
  const experimentsQuery = useQuery({
    queryKey: ['assistant-eval', 'experiments'],
    queryFn: () => assistantEvaluationApi.experiments(),
  })
  const columns: ColumnsType<Experiment> = [
    { title: '实验', key: 'title', ellipsis: true,
      render: (_, row) => `双臂实验 · ${row.id.slice(0, 8)}` },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90,
      render: (status: string) => statusTag(status) },
    {
      title: '门禁', key: 'gate', width: 200,
      render: (_, row) => {
        const gate = row.result?.gate
        if (!gate) return <Text type="secondary">-</Text>
        return (
          <Space size={4}>
            {gate.passed ? <Tag color="success">通过</Tag> : <Tag color="warning">未过</Tag>}
            <Text type="secondary">Δ{gate.heldout_delta ?? '-'} / {gate.effective_threshold}</Text>
          </Space>
        )
      },
    },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 140,
      render: (value: string | null) => (value ? dayjs(value).format('MM-DD HH:mm') : '-') },
    {
      title: '操作', key: 'actions', width: 80,
      render: (_, row) => (
        <Button size="small" type="link" onClick={() => onOpenDetail(row.id)}>对比</Button>
      ),
    },
  ]
  return (
    <Table rowKey="id" size="small" columns={columns}
           dataSource={experimentsQuery.data ?? []}
           loading={experimentsQuery.isLoading} pagination={{ pageSize: 5 }} />
  )
}

// ---------------------------------------------------------------- 值守

function AutopilotView() {
  const queryClient = useQueryClient()
  const [ontologyId, setOntologyId] = useState<string>('')
  const ontologiesQuery = useOntologies()
  const configQuery = useQuery({
    queryKey: ['assistant-eval', 'autopilot', ontologyId],
    queryFn: () => assistantEvaluationApi.autopilotConfig(ontologyId),
    enabled: !!ontologyId,
  })
  const benchmarksQuery = useQuery({
    queryKey: ['assistant-eval', 'benchmarks'],
    queryFn: () => assistantEvaluationApi.benchmarks(),
    enabled: !!ontologyId,
  })
  const versionsQuery = useQuery({
    queryKey: ['assistant-eval', 'versions', ontologyId],
    queryFn: () => assistantEvaluationApi.profileVersions(ontologyId),
    enabled: !!ontologyId,
  })

  const config: AutopilotConfig | null = configQuery.data ?? null
  const [enabled, setEnabled] = useState(false)
  const [runAt, setRunAt] = useState('03:00')
  const [benchmarkSetId, setBenchmarkSetId] = useState('')
  const [threshold, setThreshold] = useState(5)
  const [maxApplies, setMaxApplies] = useState(3)
  const [hydratedFor, setHydratedFor] = useState<string>('')

  // 配置加载后填充表单（一次性）
  if (configQuery.data !== undefined && ontologyId && hydratedFor !== ontologyId) {
    setHydratedFor(ontologyId)
    setEnabled(config?.enabled ?? false)
    setRunAt(config?.run_at ?? '03:00')
    setBenchmarkSetId(config?.benchmark_set_id ?? '')
    setThreshold(config?.threshold ?? 5)
    setMaxApplies(config?.max_applies_per_week ?? 3)
  }

  const saveMutation = useMutation({
    mutationFn: () => assistantEvaluationApi.saveAutopilotConfig(ontologyId, {
      enabled, run_at: runAt, benchmark_set_id: benchmarkSetId || null,
      threshold, max_applies_per_week: maxApplies,
    }),
    onSuccess: () => {
      message.success(enabled ? '值守已开启' : '值守配置已保存')
      queryClient.invalidateQueries({ queryKey: ['assistant-eval', 'autopilot', ontologyId] })
    },
    onError: (error: Error) => message.error(error.message),
  })
  const triggerMutation = useMutation({
    mutationFn: () => assistantEvaluationApi.triggerAutopilot(ontologyId),
    onSuccess: () => message.success('已派发一轮值守循环（NATS）'),
    onError: (error: Error) => message.error(error.message),
  })
  const rollbackMutation = useMutation({
    mutationFn: (versionId: string) => assistantEvaluationApi.rollbackVersion(versionId, '管理员手动回退'),
    onSuccess: () => {
      message.success('已回退到上一版本')
      queryClient.invalidateQueries({ queryKey: ['assistant-eval', 'versions', ontologyId] })
      queryClient.invalidateQueries({ queryKey: ['assistant-eval', 'autopilot', ontologyId] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const versionColumns: ColumnsType<ProfileVersion> = [
    { title: '版本', key: 'version', width: 70,
      render: (_, row) => <Text strong>v{row.version}</Text> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: (status: string) => statusTag(status) },
    { title: '来源', key: 'trigger', width: 90,
      render: (_, row) => row.source?.trigger === 'autopilot'
        ? <Tag color="geekblue">值守</Tag> : <Tag>人工</Tag> },
    { title: '看守', key: 'verified', width: 90,
      render: (_, row) => row.verified ? <Tag color="success">已确认</Tag> : <Tag color="processing">观察中</Tag> },
    { title: '投产前基线', key: 'baseline', width: 120,
      render: (_, row) => {
        const stats = row.pre_apply_stats as { overall?: number | null } | undefined
        return stats?.overall != null ? stats.overall.toFixed(1) : '-'
      },
    },
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 140,
      render: (value: string | null) => (value ? dayjs(value).format('MM-DD HH:mm') : '-') },
    {
      title: '操作', key: 'actions', width: 90,
      render: (_, row) => row.status === 'active' ? (
        <Popconfirm title="回退到上一版本的生产配置？"
                    onConfirm={() => rollbackMutation.mutate(row.id)}>
          <Button size="small" type="link" danger icon={<Undo2 size={13} />}>回退</Button>
        </Popconfirm>
      ) : null,
    },
  ]

  return (
    <div className="flex flex-col gap-3">
      <Select className="max-w-80" placeholder="选择本体（值守按本体配置）" showSearch
              optionFilterProp="label" value={ontologyId || undefined} onChange={setOntologyId}
              options={(ontologiesQuery.data?.items ?? []).map(o => ({
                value: o.id, label: o.name }))} />

      {ontologyId && (
        <div className="flex flex-col gap-3">
          {config?.suspended && (
            <Alert type="error" showIcon
                   message="值守已熔断"
                   description={config.suspend_reason || '连续失败 3 轮，等待人工介入后重新保存配置解除。'} />
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Switch checked={enabled} onChange={setEnabled} checkedChildren="值守开" unCheckedChildren="值守关" />
            <TimePicker format="HH:mm"
                        value={runAt ? dayjs(runAt, 'HH:mm') : null}
                        onChange={value => setRunAt(value ? value.format('HH:mm') : '03:00')} />
            <InputNumber min={0} step={0.5} value={threshold}
                         onChange={v => setThreshold(v ?? 5)} addonBefore="门禁阈值" />
            <InputNumber min={1} max={7} value={maxApplies}
                         onChange={v => setMaxApplies(v ?? 3)} addonBefore="周投产上限" />
          </div>
          <Select className="max-w-md" placeholder="绑定基准集（须含留出集，开启值守必填）"
                  value={benchmarkSetId || undefined} onChange={setBenchmarkSetId}
                  options={(benchmarksQuery.data ?? [])
                    .filter(b => b.ontology_id === ontologyId)
                    .map(b => ({ value: b.id, label: `${b.name}（留出 ${b.heldout_count}）` }))} />
          <div className="flex items-center gap-2">
            <Button size="small" type="primary" loading={saveMutation.isPending}
                    onClick={() => saveMutation.mutate()}>
              保存配置
            </Button>
            {config?.id && (
              <Button size="small" icon={<PlayCircle size={14} />}
                      loading={triggerMutation.isPending}
                      onClick={() => triggerMutation.mutate()}>
                立即跑一轮
              </Button>
            )}
            {config?.last_cycle_status && (
              <Text type="secondary">
                上轮：{config.last_cycle_status}
                {config.last_cycle_at ? ` · ${dayjs(config.last_cycle_at).format('MM-DD HH:mm')}` : ''}
              </Text>
            )}
          </div>

          <div>
            <Text strong>版本链（投产快照 · 可回退）</Text>
            <Table className="mt-2" rowKey="id" size="small" columns={versionColumns}
                   dataSource={versionsQuery.data ?? []}
                   loading={versionsQuery.isLoading} pagination={{ pageSize: 5 }} />
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- 时间线

function TimelineView() {
  const [refType, setRefType] = useState<string | undefined>(undefined)
  const timelineQuery = useQuery({
    queryKey: ['assistant-eval', 'timeline', refType ?? 'all'],
    queryFn: () => assistantEvaluationApi.timeline({ ref_type: refType, limit: 100 }),
  })

  const columns: ColumnsType<TimelineEvent> = [
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 150,
      render: (value: string | null) => (value ? dayjs(value).format('MM-DD HH:mm:ss') : '-') },
    {
      title: '事件', key: 'event', width: 150,
      render: (_, row) => (
        <Space size={4}>
          {EVENT_LABEL[row.event_type] ?? row.event_type}
          <Tag color={row.actor === 'autopilot' ? 'geekblue'
            : row.actor === 'system' ? 'default' : 'blue'}>
            {ACTOR_LABEL[row.actor] ?? row.actor}
          </Tag>
        </Space>
      ),
    },
    {
      title: '详情', key: 'detail', ellipsis: true,
      render: (_, row) => {
        const detail = row.detail ?? {}
        const summary = detail.title ?? detail.reason ?? detail.status
          ?? ((detail.gate as { passed?: boolean } | undefined)
            ? `门禁 ${(detail.gate as { passed?: boolean }).passed ? '通过' : '未过'}` : '')
        return <Text type="secondary">{String(summary) || JSON.stringify(detail).slice(0, 80)}</Text>
      },
    },
    { title: '对象', key: 'ref', width: 120,
      render: (_, row) => row.ref_type ? <Tag>{row.ref_type}</Tag> : null },
  ]

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Select size="small" className="w-52" allowClear placeholder="按对象类型过滤"
                value={refType} onChange={value => setRefType(value)}
                options={[
                  { value: 'task', label: '评估任务' },
                  { value: 'benchmark_set', label: '基准集' },
                  { value: 'calibration', label: '校准' },
                  { value: 'proposal', label: '提案' },
                  { value: 'experiment', label: '实验' },
                  { value: 'profile_version', label: '版本' },
                  { value: 'autopilot_config', label: '值守' },
                ]} />
        <Text type="secondary">飞轮每一步的留痕：分析 → 提案 → 验证 → 投产 → 回退。</Text>
      </div>
      <Table rowKey="id" size="small" columns={columns}
             dataSource={timelineQuery.data ?? []}
             loading={timelineQuery.isLoading} pagination={{ pageSize: 12 }} />
    </div>
  )
}
