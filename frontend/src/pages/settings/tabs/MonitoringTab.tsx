import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Empty, Segmented, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import { Activity, Gauge, Timer, TriangleAlert } from 'lucide-react'
import {
  monitoringApi,
  type MonitoringWindow,
  type SlowRequestBreakdown,
  type SlowRequestItem,
  type TopRouteItem,
} from '@/api/monitoring'

const REFRESH_MS = 30_000

function formatTime(value: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

function Card({ icon, label, value, extra }: {
  icon: React.ReactNode
  label: string
  value: string
  extra?: string
}) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-gray-200 bg-white px-4 py-3">
      <span className="mt-0.5 text-gray-400">{icon}</span>
      <div className="min-w-0">
        <div className="text-xs text-gray-500">{label}</div>
        <div className="text-xl font-semibold text-gray-900 leading-tight">{value}</div>
        {extra && <div className="text-[11px] text-gray-400 mt-0.5">{extra}</div>}
      </div>
    </div>
  )
}

function BreakdownTags({ breakdown }: { breakdown: SlowRequestBreakdown }) {
  const layers: { key: keyof SlowRequestBreakdown; label: string; color: string }[] = [
    { key: 'db', label: 'DB', color: 'geekblue' },
    { key: 'llm', label: 'LLM', color: 'purple' },
    { key: 'http', label: 'HTTP', color: 'orange' },
  ]
  const entries = layers.filter(layer => breakdown[layer.key])
  if (!entries.length) return <span className="text-xs text-gray-400">无分层数据</span>
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      {entries.map(layer => {
        const span = breakdown[layer.key]!
        return (
          <Tag key={layer.key} color={layer.color} className="!mb-0 !text-[11px]">
            {layer.label} {span.count} 次 · {(span.total_ms / 1000).toFixed(2)}s
          </Tag>
        )
      })}
    </span>
  )
}

export default function MonitoringTab() {
  const [window, setWindow] = useState<MonitoringWindow>('24h')
  const [topSort, setTopSort] = useState('slow_count')
  const [slowPage, setSlowPage] = useState(1)
  const [slowRoute, setSlowRoute] = useState('')

  const overview = useQuery({
    queryKey: ['monitoring', 'overview', window],
    queryFn: () => monitoringApi.overview(window),
    refetchInterval: REFRESH_MS,
  })
  const trend = useQuery({
    queryKey: ['monitoring', 'trend', window],
    queryFn: () => monitoringApi.trend(window),
    refetchInterval: REFRESH_MS,
  })
  const top = useQuery({
    queryKey: ['monitoring', 'top', window, topSort],
    queryFn: () => monitoringApi.top(window, topSort, 20),
    refetchInterval: REFRESH_MS,
  })
  const slow = useQuery({
    queryKey: ['monitoring', 'slow', slowPage, slowRoute],
    queryFn: () =>
      monitoringApi.slowRequests({ route: slowRoute, page: slowPage, size: 10 }),
    refetchInterval: REFRESH_MS,
  })

  const chartOption = useMemo<EChartsOption>(() => {
    const points = trend.data?.points ?? []
    return {
      aria: { enabled: true, description: '接口请求量、p95 耗时与错误率趋势' },
      grid: { left: 8, right: 10, top: 34, bottom: 4, containLabel: true },
      legend: {
        top: 0,
        right: 0,
        icon: 'roundRect',
        itemWidth: 8,
        itemHeight: 8,
        itemGap: 12,
        textStyle: { color: '#7d899a', fontSize: 10 },
      },
      tooltip: { trigger: 'axis', borderColor: '#dfe6ed' },
      xAxis: {
        type: 'category',
        data: points.map(point => point.t),
        axisLabel: {
          color: '#7d899a',
          fontSize: 9,
          formatter: (value: string) => {
            const date = new Date(value)
            if (Number.isNaN(date.getTime())) return ''
            return window === '24h'
              ? date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
              : date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
          },
          hideOverlap: true,
        },
        axisLine: { lineStyle: { color: '#e3e8ef' } },
      },
      yAxis: [
        { type: 'value', name: '请求', nameTextStyle: { color: '#7d899a', fontSize: 9 }, splitLine: { lineStyle: { color: '#eef1f5' } } },
        { type: 'value', name: 'ms', nameTextStyle: { color: '#7d899a', fontSize: 9 }, splitLine: { show: false } },
        { type: 'value', name: '%', nameTextStyle: { color: '#7d899a', fontSize: 9 }, splitLine: { show: false }, max: 100 },
      ],
      series: [
        {
          name: '请求量',
          type: 'bar',
          data: points.map(point => point.count),
          itemStyle: { color: '#0891b2' },
          barMaxWidth: 8,
        },
        {
          name: 'p95 耗时',
          type: 'line',
          yAxisIndex: 1,
          data: points.map(point => point.p95_ms),
          smooth: true,
          symbol: 'none',
          lineStyle: { color: '#d97706', width: 1.4 },
        },
        {
          name: '错误率',
          type: 'line',
          yAxisIndex: 2,
          data: points.map(point => point.error_rate),
          smooth: true,
          symbol: 'none',
          lineStyle: { color: '#e11d48', width: 1.4 },
        },
      ],
    }
  }, [trend.data, window])

  const topColumns: ColumnsType<TopRouteItem> = [
    { title: '接口', dataIndex: 'route', key: 'route', ellipsis: true, render: (_, row) => <span className="font-mono text-xs">{row.method} {row.route}</span> },
    { title: '请求量', dataIndex: 'requests', key: 'requests', width: 90, align: 'right' },
    { title: '错误率', dataIndex: 'error_rate', key: 'error_rate', width: 90, align: 'right', render: value => value + '%' },
    { title: '平均', dataIndex: 'avg_ms', key: 'avg_ms', width: 90, align: 'right', render: value => (value == null ? '-' : value + 'ms') },
    { title: 'p95', dataIndex: 'p95_ms', key: 'p95_ms', width: 90, align: 'right', render: value => (value == null ? '-' : value + 'ms') },
    { title: '最大', dataIndex: 'max_ms', key: 'max_ms', width: 90, align: 'right', render: value => value + 'ms' },
    { title: '慢请求', dataIndex: 'slow_count', key: 'slow_count', width: 90, align: 'right', render: value => <span className={value ? 'text-red-500 font-medium' : ''}>{value}</span> },
  ]

  const slowColumns: ColumnsType<SlowRequestItem> = [
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 150, render: formatTime },
    { title: '接口', dataIndex: 'route', key: 'route', ellipsis: true, render: (_, row) => <span className="font-mono text-xs">{row.method} {row.route}</span> },
    { title: '耗时', dataIndex: 'duration_ms', key: 'duration_ms', width: 110, align: 'right', sorter: (a, b) => a.duration_ms - b.duration_ms, render: value => <span className="text-red-500 font-medium">{(value / 1000).toFixed(2)}s</span> },
    { title: '状态', dataIndex: 'status_code', key: 'status_code', width: 70, align: 'center' },
    { title: '用户', dataIndex: 'username', key: 'username', width: 110, ellipsis: true, render: value => value || '-' },
    { title: '来源', dataIndex: 'source_ip', key: 'source_ip', width: 120, render: value => value || '-' },
    { title: 'request_id', dataIndex: 'request_id', key: 'request_id', width: 120, render: value => <span className="font-mono text-[10px] text-gray-400">{value || '-'}</span> },
  ]

  const overviewData = overview.data

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3 flex-wrap">
        <Segmented
          value={window}
          onChange={value => setWindow(value as MonitoringWindow)}
          options={[{ label: '近 24 小时', value: '24h' }, { label: '近 7 天', value: '7d' }]}
        />
        <span className="text-xs text-gray-400">
          数据每 30 秒自动刷新 · 慢接口阈值 {overviewData?.slow_threshold_ms ?? 1000}ms · 明细保留 7 天
        </span>
      </div>

      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
        <Card icon={<Activity size={16} />} label="请求总量" value={overviewData ? overviewData.requests.toLocaleString() : '-'} extra={window === '24h' ? '近 24 小时' : '近 7 天'} />
        <Card icon={<Gauge size={16} />} label="成功率" value={overviewData ? overviewData.success_rate + '%' : '-'} extra={'服务器错误率 ' + (overviewData?.server_error_rate ?? '-') + '%'} />
        <Card icon={<Timer size={16} />} label="p95 耗时" value={overviewData?.p95_ms != null ? overviewData.p95_ms + 'ms' : '-'} extra={'平均 ' + (overviewData?.avg_ms ?? '-') + 'ms · 最大耗时见排行表'} />
        <Card icon={<TriangleAlert size={16} />} label="慢请求" value={overviewData ? String(overviewData.slow_requests) : '-'} extra={'≥ ' + (overviewData?.slow_threshold_ms ?? 1000) + 'ms 的请求次数'} />
      </div>

      <div className="rounded-lg border border-gray-200 bg-white p-4">
        <div className="text-sm font-medium text-gray-800 mb-2">请求趋势（请求量 / p95 耗时 / 错误率）</div>
        <ReactECharts option={chartOption} style={{ height: 240 }} notMerge />
      </div>

      <div className="rounded-lg border border-gray-200 bg-white p-4">
        <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
          <span className="text-sm font-medium text-gray-800">接口排行（针对性优化依据）</span>
          <Segmented
            size="small"
            value={topSort}
            onChange={value => setTopSort(value as string)}
            options={[
              { label: '慢请求数', value: 'slow_count' },
              { label: 'p95', value: 'p95_ms' },
              { label: '错误率', value: 'error_rate' },
              { label: '请求量', value: 'requests' },
            ]}
          />
        </div>
        <Table
          rowKey="route"
          size="small"
          loading={top.isLoading}
          columns={topColumns}
          dataSource={top.data?.items ?? []}
          pagination={false}
          locale={{ emptyText: <Empty description="该时间窗暂无请求记录" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
        />
      </div>

      <div className="rounded-lg border border-gray-200 bg-white p-4">
        <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
          <span className="text-sm font-medium text-gray-800">慢请求明细</span>
          <input
            value={slowRoute}
            onChange={event => {
              setSlowRoute(event.target.value)
              setSlowPage(1)
            }}
            placeholder="按接口路径筛选"
            className="px-2.5 py-1 border rounded-lg text-xs w-56"
          />
        </div>
        <Table
          rowKey="id"
          size="small"
          loading={slow.isLoading}
          columns={slowColumns}
          dataSource={slow.data?.items ?? []}
          expandable={{
            expandedRowRender: row => (
              <div className="text-xs text-gray-600">
                <div className="mb-1"><span className="text-gray-400">UA：</span>{row.user_agent || '-'}</div>
                <BreakdownTags breakdown={row.breakdown} />
              </div>
            ),
          }}
          pagination={{
            current: slowPage,
            pageSize: slow.data?.size ?? 10,
            total: slow.data?.total ?? 0,
            showSizeChanger: false,
            onChange: page => setSlowPage(page),
          }}
          locale={{ emptyText: <Empty description="暂无慢请求记录" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
        />
      </div>
    </div>
  )
}

