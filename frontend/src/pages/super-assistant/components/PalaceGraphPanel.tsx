import { useEffect, useMemo, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { AlertCircle, Brain, Loader2, RefreshCw, Search, X } from 'lucide-react'

import { superAssistantApi, type PalaceGraph, type PalaceGraphNode } from '@/api/superAssistant'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { filterPalaceGraph, palaceOneHopNeighbors } from './palaceGraphFilter'
import { palaceGraphOption } from './palaceGraphOption'

interface PalaceGraphPanelProps {
  graph: PalaceGraph | null
  loading: boolean
  /** 文件库是否已有文档：区分「先上传」与「抽取中」两种空态文案 */
  hasFiles: boolean
  onRefresh: () => void
  /** 面板内动作失败时上抛（429 等错误 detail 由弹窗统一格式化展示） */
  onError: (err: unknown, fallback: string) => void
}

const EMPTY_GRAPH: PalaceGraph = {
  available: false, nodes: [], edges: [], totals: { entities: 0, relations: 0 }, truncated: false,
}

/**
 * 知识图谱页签：关键词客户端过滤（300ms 防抖）、节点点击详情（一跳邻居）、
 * 邻域检索高亮（命中集之外的节点静态降透明）。
 */
export default function PalaceGraphPanel({ graph, loading, hasFiles, onRefresh, onError }: PalaceGraphPanelProps) {
  const [filterInput, setFilterInput] = useState('')
  const [filterKeyword, setFilterKeyword] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [highlightIds, setHighlightIds] = useState<Set<string> | null>(null)
  const [highlightInfo, setHighlightInfo] = useState<{ query: string; count: number } | null>(null)
  const [searching, setSearching] = useState(false)

  // 过滤输入 300ms 防抖（与全局搜索 Command 面板同节奏）
  useEffect(() => {
    const timer = window.setTimeout(() => setFilterKeyword(filterInput), 300)
    return () => window.clearTimeout(timer)
  }, [filterInput])

  const baseGraph = graph ?? EMPTY_GRAPH
  const view = useMemo(() => filterPalaceGraph(baseGraph, filterKeyword), [baseGraph, filterKeyword])
  const selectedNode: PalaceGraphNode | null = useMemo(
    () => graph?.nodes.find(node => node.id === selectedId) ?? null,
    [graph, selectedId],
  )
  const neighbors = useMemo(
    () => (graph && selectedId ? palaceOneHopNeighbors(graph, selectedId) : []),
    [graph, selectedId],
  )
  const option = useMemo(
    () => palaceGraphOption({ ...baseGraph, nodes: view.nodes, edges: view.edges }, highlightIds ?? undefined),
    [baseGraph, view, highlightIds],
  )
  const chartEvents = useMemo(() => ({
    click: (params: { dataType?: string; data?: { id?: string } }) => {
      if (params.dataType === 'node' && params.data?.id) setSelectedId(String(params.data.id))
    },
  }), [])

  const handleSearchNeighborhood = async () => {
    if (!selectedNode) return
    setSearching(true)
    try {
      const result = await superAssistantApi.palaceGraphSearch(selectedNode.name)
      if (!result.available) {
        onError(new Error('图谱检索暂不可用，请稍后重试'), '图谱检索暂不可用')
        return
      }
      const ids = new Set(result.entities.map(entity => entity.id))
      setHighlightIds(ids)
      setHighlightInfo({ query: selectedNode.name, count: ids.size })
    } catch (err) {
      onError(err, '图谱检索失败')
    } finally {
      setSearching(false)
    }
  }

  const clearHighlight = () => {
    setHighlightIds(null)
    setHighlightInfo(null)
  }

  const filtering = filterKeyword.trim() !== ''

  return (
    <section
      aria-label="记忆宫殿知识图谱"
      data-testid="super-assistant-palace-graph"
      className="flex min-h-[300px] flex-1 flex-col"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 pb-1.5">
        <h3 className="text-xs font-medium text-[var(--color-text-secondary)]">
          知识图谱
          {graph && graph.totals.entities > 0 && (
            <span className="ml-1 tabular-nums text-[var(--color-text-tertiary)]">
              （{graph.totals.entities} 实体 / {graph.totals.relations} 关系{graph.truncated ? '，按提及数展示前 ' + graph.nodes.length : ''}）
            </span>
          )}
        </h3>
        <div className="flex items-center gap-2">
          {graph?.available && graph.nodes.length > 0 && (
            <div className="w-56">
              <Input
                value={filterInput}
                onChange={event => setFilterInput(event.target.value)}
                placeholder="按名称、别名或类型过滤"
                aria-label="过滤图谱实体"
                data-testid="palace-graph-filter"
                className="h-8 text-xs"
              />
            </div>
          )}
          <Button variant="outline" size="sm" onClick={onRefresh} disabled={loading} aria-label="刷新知识图谱">
            <RefreshCw size={13} className={loading ? 'animate-spin' : undefined} /> 刷新
          </Button>
        </div>
      </div>

      {graph?.available && filtering && (
        <p data-testid="palace-graph-filter-count" className="pb-1.5 text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
          {view.nodes.length > 0
            ? `显示 ${view.nodes.length} / ${graph.nodes.length} 实体 · ${view.edges.length} 关系`
            : `没有匹配「${filterKeyword.trim()}」的实体`}
        </p>
      )}

      {!graph ? (
        <div className="flex flex-1 items-center justify-center rounded-xl border border-[var(--color-border)] text-xs text-[var(--color-text-tertiary)]">
          <Loader2 size={14} className="mr-1.5 animate-spin" /> 图谱加载中…
        </div>
      ) : !graph.available ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-1 rounded-xl border border-[var(--color-border)] px-4 py-8 text-center">
          <AlertCircle size={18} className="text-amber-500" />
          <p className="text-xs text-[var(--color-text-tertiary)]">图谱服务暂不可用，文件库不受影响，可稍后刷新重试。</p>
        </div>
      ) : graph.nodes.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-[var(--color-border)] px-4 py-8 text-center">
          <Brain size={18} className="text-[var(--color-text-tertiary)]" />
          <p className="text-xs text-[var(--color-text-tertiary)]">
            {hasFiles
              ? '文档抽取完成后，这里会展示知识图谱。'
              : '上传文档后，这里会长出你的知识图谱。'}
          </p>
        </div>
      ) : view.nodes.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-[var(--color-border)] px-4 py-8 text-center">
          <Search size={18} className="text-[var(--color-text-tertiary)]" />
          <p className="text-xs text-[var(--color-text-tertiary)]">没有匹配「{filterKeyword.trim()}」的实体，换个关键词试试。</p>
        </div>
      ) : (
        <>
          <div className="h-[360px] overflow-hidden rounded-xl border border-[var(--color-border)]">
            <ReactECharts
              option={option}
              notMerge
              style={{ height: '100%', width: '100%' }}
              onEvents={chartEvents}
            />
          </div>

          {selectedNode ? (
            <div data-testid="palace-node-detail" className="mt-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-[var(--color-text-primary)]" data-palace-node-name={selectedNode.id}>
                    {selectedNode.name}
                  </p>
                  <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
                    {selectedNode.type} · 提及 {selectedNode.mention_count} 次 · 被引用 {selectedNode.match_count} 次
                  </p>
                  {selectedNode.aliases.length > 0 && (
                    <p className="mt-0.5 text-[11px] text-[var(--color-text-tertiary)]">别名：{selectedNode.aliases.join('、')}</p>
                  )}
                  {selectedNode.source_files.length > 0 && (
                    <p className="mt-0.5 text-[11px] text-[var(--color-text-tertiary)]">来源：{selectedNode.source_files.join('、')}</p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedId(null)}
                  aria-label="关闭节点详情"
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
                >
                  <X size={12} />
                </button>
              </div>
              <div className="mt-2 border-t border-[var(--color-border)] pt-2">
                <p className="text-[11px] font-medium text-[var(--color-text-secondary)]">一跳邻居（{neighbors.length}）</p>
                {neighbors.length === 0 ? (
                  <p className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">当前图谱中没有与该实体直接相连的关系。</p>
                ) : (
                  <ul className="mt-1 flex flex-wrap gap-1">
                    {neighbors.map(neighbor => (
                      <li
                        key={`${neighbor.nodeId}-${neighbor.relation}`}
                        title={`${neighbor.name}（${neighbor.relation}）`}
                        className="rounded-full bg-[var(--color-bg-hover)] px-2 py-0.5 text-[11px] text-[var(--color-text-secondary)]"
                      >
                        {neighbor.name}（{neighbor.relation}）
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-[var(--color-border)] pt-2">
                <Button size="sm" onClick={() => { void handleSearchNeighborhood() }} loading={searching}>
                  <Search size={13} /> 在图谱中检索邻域
                </Button>
                {highlightInfo && (
                  <span className="flex items-center gap-1.5 text-[11px] text-[var(--color-text-tertiary)]">
                    <span data-testid="palace-graph-highlight-count">
                      「{highlightInfo.query}」命中 {highlightInfo.count} 个实体已高亮，其余节点已淡化
                    </span>
                    <button
                      type="button"
                      onClick={clearHighlight}
                      className="rounded-md px-1.5 py-0.5 text-[11px] text-[var(--color-primary)] transition-colors hover:bg-[var(--color-bg-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
                    >
                      清除高亮
                    </button>
                  </span>
                )}
              </div>
            </div>
          ) : (
            <p className="mt-2 text-[11px] text-[var(--color-text-tertiary)]">点击图谱中的节点，可查看实体详情与一跳邻居。</p>
          )}
        </>
      )}
    </section>
  )
}
