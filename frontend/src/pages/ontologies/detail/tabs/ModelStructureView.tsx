import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Background, BackgroundVariant, MiniMap, ReactFlow, ReactFlowProvider,
  applyNodeChanges, useReactFlow, type NodeChange, type OnNodeDrag,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  AlertCircle, ArrowLeftRight, ArrowRight, Box, Braces, Check, ChevronDown, ChevronRight,
  Focus, FunctionSquare, GitBranch, KeyRound, Layers3, Loader2,
  Maximize2, Route, Search, ShieldCheck, Sparkles, X, ZoomIn, ZoomOut,
  Bolt, Clock3, Database,
} from 'lucide-react'
import { apiClientV2 } from '@/api/client'
import { saveCanvasLayout } from '@/palantir-graph/api/formalApi'
import { StructureGraphEdge, StructureGraphNode } from './StructureGraphElements'
import {
  actionNodeId, buildStructureGraph, findPaths, functionUsage, propertyNodeId,
  relationEdgeId, routeStructureEdges, sentinelUsage,
  type GraphPath, type HighlightSet, type PublishedWorkspace, type StructureEdge,
  type StructureNode,
} from './structureGraphModel'

type Level = 1 | 2
type Direction = 'outgoing' | 'both'
type SearchKind = 'object' | 'relation' | 'property' | 'action'
type DetailSelection = { kind: SearchKind; id: string; parentObjectId?: string } | null

interface SearchResult {
  id: string
  kind: SearchKind
  label: string
  technicalName: string
  context?: string
}

const NODE_TYPES = { structure: StructureGraphNode }
const EDGE_TYPES = { structure: StructureGraphEdge }
const EMPTY_HIGHLIGHT: HighlightSet = {
  nodes: new Set(), edges: new Set(), contextNodes: new Set(), primaryNodes: new Set(), summary: '',
}

const kindLabel: Record<SearchKind, string> = {
  object: '对象实体', relation: '实体关系', property: '实体属性', action: '执行动作',
}

function errorMessage(error: unknown) {
  const value = error as { detail?: unknown; message?: string; response?: { data?: { detail?: unknown } } }
  const detail = value.response?.data?.detail ?? value.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') return detail.message
  return value.message || '布局保存失败'
}

function objectName(workspace: PublishedWorkspace, id: string) {
  const item = workspace.objectTypes.find(objectType => objectType.id === id)
  return item?.displayName || item?.name || id
}

function DetailRow({ label, value, mono = false }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="grid grid-cols-[82px_minmax(0,1fr)] gap-3 border-b border-slate-100 py-2.5 last:border-0">
      <dt className="text-[11px] text-slate-400">{label}</dt>
      <dd className={`min-w-0 break-words text-xs text-slate-700 ${mono ? 'font-mono' : ''}`}>{value || '—'}</dd>
    </div>
  )
}

function Pill({ children, tone = 'slate' }: { children: React.ReactNode; tone?: 'slate' | 'teal' | 'amber' | 'violet' }) {
  const styles = {
    slate: 'border-slate-200 bg-slate-50 text-slate-600',
    teal: 'border-teal-200 bg-teal-50 text-teal-700',
    amber: 'border-amber-200 bg-amber-50 text-amber-700',
    violet: 'border-violet-200 bg-violet-50 text-violet-700',
  }
  return <span className={`inline-flex rounded-md border px-1.5 py-0.5 text-[10px] ${styles[tone]}`}>{children}</span>
}

interface SegmentItem<T extends string | number> {
  value: T
  label: string
  icon?: React.ElementType
}

function AnimatedSegmentedControl<T extends string | number>({
  value, items, label, onChange,
}: {
  value: T
  items: SegmentItem<T>[]
  label: string
  onChange: (value: T) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [indicator, setIndicator] = useState({ left: 0, width: 0 })

  const measure = useCallback(() => {
    const container = containerRef.current
    if (!container) return
    const active = container.querySelector(`[data-segment-value="${String(value)}"]`) as HTMLElement | null
    if (!active) return
    const containerRect = container.getBoundingClientRect()
    const activeRect = active.getBoundingClientRect()
    setIndicator({ left: activeRect.left - containerRect.left, width: activeRect.width })
  }, [value])

  useLayoutEffect(() => {
    measure()
    const container = containerRef.current
    if (!container || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(container)
    return () => observer.disconnect()
  }, [items.length, measure])

  return (
    <div ref={containerRef} className="relative flex shrink-0 items-center gap-1 rounded-lg border border-slate-200 bg-slate-50/70 p-0.5" aria-label={label}>
      <span
        aria-hidden="true"
        className="absolute top-0.5 h-[calc(100%-4px)] rounded-md bg-teal-600 shadow-sm transition-all duration-300 ease-out motion-reduce:transition-none"
        style={{ left: indicator.left, width: indicator.width }}
      />
      {items.map(item => {
        const active = item.value === value
        const Icon = item.icon
        return (
          <button
            key={item.value}
            type="button"
            data-segment-value={item.value}
            aria-pressed={active}
            onClick={() => onChange(item.value)}
            className={`relative z-10 inline-flex h-8 items-center justify-center gap-1.5 rounded-md px-3 text-xs font-semibold transition-colors duration-200 ${active ? 'text-white' : 'text-slate-500 hover:text-slate-700'}`}
          >
            {Icon && <Icon size={13} />}{item.label}
          </button>
        )
      })}
    </div>
  )
}

interface DependencyOption {
  id: string
  label: string
  technicalName: string
  description?: string
  meta?: string
}

function DependencyPicker({
  kind, items, value, open, onOpenChange, onChange,
}: {
  kind: 'function' | 'sentinel'
  items: DependencyOption[]
  value: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onChange: (id: string) => void
}) {
  const triggerRef = useRef<HTMLButtonElement>(null)
  const [position, setPosition] = useState({ left: 0, top: 0 })
  const selected = items.find(item => item.id === value)
  const isFunction = kind === 'function'
  const Icon = isFunction ? FunctionSquare : ShieldCheck
  const title = isFunction ? '激活函数' : '哨兵规则'
  const helper = isFunction ? '选择后高亮直接使用该函数的对象、属性和动作' : '选择后查看规则绑定、条件属性与动作覆盖范围'
  const tone = isFunction
    ? {
      icon: 'bg-violet-50 text-violet-600 ring-violet-100',
      active: 'border-violet-300 bg-violet-50 text-violet-700',
      dot: 'bg-violet-500',
      selected: 'bg-violet-50/70 text-violet-900',
      selectedIcon: 'bg-violet-100 text-violet-700',
    }
    : {
      icon: 'bg-fuchsia-50 text-fuchsia-600 ring-fuchsia-100',
      active: 'border-fuchsia-300 bg-fuchsia-50 text-fuchsia-700',
      dot: 'bg-fuchsia-500',
      selected: 'bg-fuchsia-50/70 text-fuchsia-900',
      selectedIcon: 'bg-fuchsia-100 text-fuchsia-700',
    }

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current
    if (!trigger) return
    const rect = trigger.getBoundingClientRect()
    const width = 360
    setPosition({
      left: Math.max(16, Math.min(rect.left, window.innerWidth - width - 16)),
      top: rect.bottom + 10,
    })
  }, [])

  useLayoutEffect(() => {
    if (!open) return
    updatePosition()
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [open, updatePosition])

  return (
    <div className="shrink-0">
      <button
        ref={triggerRef}
        type="button"
        aria-label={isFunction ? '查看激活函数使用关系' : '查看哨兵覆盖范围'}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => onOpenChange(!open)}
        onKeyDown={event => {
          if (event.key === 'Escape') onOpenChange(false)
          if (event.key === 'ArrowDown') onOpenChange(true)
        }}
        className={`inline-flex h-9 w-[196px] items-center gap-2 rounded-lg border px-2.5 text-left text-xs outline-none transition-all duration-200 focus-visible:ring-2 focus-visible:ring-offset-1 ${open || selected ? tone.active : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50 focus-visible:ring-teal-300'}`}
      >
        <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md ring-1 ${tone.icon}`}><Icon size={13} /></span>
        <span className="min-w-0 flex-1 truncate">{selected?.label || (isFunction ? '激活函数 · 查看使用关系' : '哨兵 · 查看覆盖范围')}</span>
        <ChevronDown size={13} className={`shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && createPortal(
        <>
          <button type="button" aria-label={`关闭${title}选择`} className="fixed inset-0 z-[70] cursor-default" onClick={() => onOpenChange(false)} />
          <section
            role="dialog"
            aria-label={`选择${title}`}
            className="fixed z-[80] w-[360px] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_18px_52px_rgba(15,23,42,0.16)] animate-slide-up"
            style={{ left: position.left, top: position.top }}
          >
            <header className="flex items-start gap-3 border-b border-slate-100 px-4 py-3">
              <span className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ring-1 ${tone.icon}`}><Icon size={16} /></span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2 text-sm font-semibold text-slate-800">{title}<span className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} /><span className="text-xs font-medium tabular-nums text-slate-400">{items.length}</span></span>
                <span className="mt-0.5 block text-[11px] leading-4 text-slate-400">{helper}</span>
              </span>
              {selected && <button type="button" data-testid={`${kind}-dependency-clear`} onClick={() => { onChange(''); onOpenChange(false) }} className="mt-1 shrink-0 rounded-md px-2 py-1 text-[11px] text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700">清除</button>}
            </header>
            <div className="scrollbar-thin max-h-[360px] overflow-y-auto py-1.5">
              {items.length ? items.map(item => {
                const active = item.id === value
                return (
                  <button
                    key={item.id}
                    type="button"
                    role="option"
                    data-testid={`${kind}-dependency-option-${item.id}`}
                    aria-selected={active}
                    onClick={() => { onChange(item.id); onOpenChange(false) }}
                    className={`group flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors ${active ? tone.selected : 'hover:bg-slate-50'}`}
                  >
                    <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${active ? tone.selectedIcon : 'bg-slate-100 text-slate-500 group-hover:bg-white'}`}><Icon size={14} /></span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-slate-700">{item.label}</span>
                      <span className="mt-0.5 block truncate font-mono text-[10px] text-slate-400">{item.technicalName}{item.meta ? ` · ${item.meta}` : ''}</span>
                      {item.description && <span className="mt-0.5 block truncate text-[10px] text-slate-400">{item.description}</span>}
                    </span>
                    {active && <Check size={15} className="shrink-0" />}
                  </button>
                )
              }) : (
                <div className="flex flex-col items-center px-6 py-10 text-center">
                  <span className={`mb-3 flex h-11 w-11 items-center justify-center rounded-full ${tone.icon}`}><Icon size={18} /></span>
                  <p className="text-sm font-medium text-slate-600">当前发布版暂无{title}</p>
                  <p className="mt-1 text-xs text-slate-400">发布包含{title}的版本后，可在这里查看依赖关系。</p>
                </div>
              )}
            </div>
          </section>
        </>,
        document.body,
      )}
    </div>
  )
}

function DetailPanel({ workspace, selection, onClose }: {
  workspace: PublishedWorkspace
  selection: DetailSelection
  onClose: () => void
}) {
  if (!selection) return null
  let panel: { title: string; technicalName: string; icon: React.ReactNode; content: React.ReactNode }

  if (selection.kind === 'object') {
    const item = workspace.objectTypes.find(objectType => objectType.id === selection.id)
    if (!item) return null
    const actions = workspace.actions.filter(action => action.objectTypeId === item.id)
    const primary = item.properties.find(property => property.id === item.primaryKey || property.name === item.primaryKey)
    panel = {
      title: item.displayName || item.name,
      technicalName: item.name,
      icon: <Box size={17} />,
      content: (
      <>
        <dl><DetailRow label="类型" value="对象实体" /><DetailRow label="描述" value={item.description} /><DetailRow label="主键" value={primary?.displayName || primary?.name} mono /></dl>
        <div className="mt-5">
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">属性 · {item.properties.length}</p>
          <div className="space-y-1.5">
            {item.properties.map(property => (
              <div key={property.id || property.name} className="flex items-center gap-2 rounded-lg border border-slate-100 bg-slate-50/70 px-2.5 py-2">
                {(property.id === item.primaryKey || property.name === item.primaryKey) ? <KeyRound size={12} className="text-amber-600" /> : <Braces size={12} className="text-violet-500" />}
                <span className="min-w-0 flex-1 truncate text-xs text-slate-700">{property.displayName || property.name}</span>
                <Pill tone={property.source === 'computed' ? 'violet' : 'slate'}>{property.type || 'unknown'}</Pill>
              </div>
            ))}
          </div>
        </div>
        <div className="mt-5">
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">动作 · {actions.length}</p>
          {actions.length ? actions.map(action => <div key={action.id} className="mb-1.5 flex items-center gap-2 rounded-lg border border-amber-100 bg-amber-50/60 px-2.5 py-2 text-xs text-slate-700"><Bolt size={12} className="text-amber-600" />{action.displayName || action.name}</div>) : <p className="text-xs text-slate-400">暂无执行动作</p>}
        </div>
      </>
      ),
    }
  } else if (selection.kind === 'relation') {
    const item = workspace.linkTypes.find(link => link.id === selection.id)
    if (!item) return null
    panel = {
      title: item.displayName || item.name,
      technicalName: item.name,
      icon: <GitBranch size={17} />,
      content: (
      <dl>
        <DetailRow label="类型" value="实体关系" />
        <DetailRow label="描述" value={item.description} />
        <DetailRow label="方向" value={<span className="inline-flex items-center gap-1.5">{objectName(workspace, item.sourceObjectTypeId)}<ArrowRight size={12} />{objectName(workspace, item.targetObjectTypeId)}</span>} />
        <DetailRow label="基数" value={<Pill tone="teal">{item.cardinality || '—'}</Pill>} />
        <DetailRow label="源角色" value={item.sourceRole} />
        <DetailRow label="目标角色" value={item.targetRole} />
        <DetailRow label="关系属性" value={(item.properties || []).length ? (item.properties || []).map(property => property.displayName || property.name).join('、') : '无'} />
      </dl>
      ),
    }
  } else if (selection.kind === 'property') {
    const parent = workspace.objectTypes.find(objectType => objectType.id === selection.parentObjectId)
    const item = parent?.properties.find(property => (property.id || property.name) === selection.id)
    if (!parent || !item) return null
    const fn = workspace.functions.find(candidate => candidate.id === item.functionId)
    panel = {
      title: item.displayName || item.name,
      technicalName: item.name,
      icon: <Braces size={17} />,
      content: <dl><DetailRow label="类型" value="实体属性" /><DetailRow label="所属对象" value={parent.displayName || parent.name} /><DetailRow label="数据类型" value={<Pill tone="violet">{item.type || 'unknown'}</Pill>} /><DetailRow label="是否必填" value={item.required ? '是' : '否'} /><DetailRow label="来源" value={item.source === 'computed' ? '函数派生' : '存储字段'} /><DetailRow label="激活函数" value={fn?.displayName || fn?.name} /><DetailRow label="描述" value={item.description} /></dl>,
    }
  } else {
    const item = workspace.actions.find(action => action.id === selection.id)
    if (!item) return null
    const fn = workspace.functions.find(candidate => candidate.id === item.validationFunctionId)
    panel = {
      title: item.displayName || item.name,
      technicalName: item.name,
      icon: <Bolt size={17} />,
      content: <dl><DetailRow label="类型" value="执行动作" /><DetailRow label="所属对象" value={objectName(workspace, item.objectTypeId)} /><DetailRow label="描述" value={item.description} /><DetailRow label="人工审批" value={item.requiresApproval ? '需要' : '不需要'} /><DetailRow label="校验函数" value={fn?.displayName || fn?.name} /><DetailRow label="参数" value={`${(item.parameters || []).length} 个`} /><DetailRow label="规则" value={`${(item.rules || []).length} 条`} /></dl>,
    }
  }

  return (
    <aside data-testid="structure-detail-panel" className="absolute bottom-3 right-3 top-3 z-30 flex w-[340px] max-w-[calc(100%-24px)] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white/95 shadow-[0_20px_60px_rgba(15,23,42,0.18)] backdrop-blur-xl">
      <div className="flex shrink-0 items-start gap-3 border-b border-slate-100 px-4 py-4">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-teal-50 text-teal-700 ring-1 ring-teal-100">{panel.icon}</span>
        <div className="min-w-0 flex-1"><h3 className="truncate text-sm font-semibold text-slate-800">{panel.title}</h3><p className="truncate font-mono text-[10px] text-slate-400">{panel.technicalName}</p></div>
        <button type="button" onClick={onClose} aria-label="关闭详情" className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"><X size={15} /></button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-4 py-3">{panel.content}</div>
    </aside>
  )
}

function StructureGraph({ ontologyId, workspace }: { ontologyId: string; workspace: PublishedWorkspace }) {
  const { fitView, zoomIn, zoomOut } = useReactFlow<StructureNode, StructureEdge>()
  const [level, setLevel] = useState<Level>(1)
  const builtGraph = useMemo(() => buildStructureGraph(workspace, level), [level, workspace])
  const [allNodes, setAllNodes] = useState<StructureNode[]>(builtGraph.nodes)
  const [mode, setMode] = useState<'browse' | 'path'>('browse')
  const [detail, setDetail] = useState<DetailSelection>(null)
  const [searchText, setSearchText] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchFocus, setSearchFocus] = useState<{ type: 'node' | 'edge'; id: string; context: string[] } | null>(null)
  const [pathSource, setPathSource] = useState('')
  const [pathTarget, setPathTarget] = useState('')
  const [direction, setDirection] = useState<Direction>('both')
  const [paths, setPaths] = useState<GraphPath[]>([])
  const [activePathIndex, setActivePathIndex] = useState(0)
  const [pathAttempted, setPathAttempted] = useState(false)
  const [functionId, setFunctionId] = useState('')
  const [sentinelId, setSentinelId] = useState('')
  const [openDependency, setOpenDependency] = useState<'function' | 'sentinel' | null>(null)
  const [saveState, setSaveState] = useState<'idle' | 'pending' | 'saving' | 'saved' | 'error'>('idle')
  const [saveError, setSaveError] = useState('')
  const groupDrag = useRef<{
    objectId: string
    parentStart: { x: number; y: number }
    childStarts: Record<string, { x: number; y: number }>
  } | null>(null)
  const pendingPositions = useRef<Record<string, { x: number; y: number }>>({})
  const saveTimer = useRef<number | null>(null)
  const saveInFlight = useRef(false)

  useEffect(() => {
    setAllNodes(builtGraph.nodes)
    const timer = window.setTimeout(() => void fitView({ padding: 0.2, minZoom: level === 2 ? 0.32 : 0.24, maxZoom: 0.9, duration: 260 }), 80)
    return () => window.clearTimeout(timer)
  }, [builtGraph, fitView, level])

  useEffect(() => {
    setDetail(null)
    setSearchText('')
    setSearchFocus(null)
    setPaths([])
    setFunctionId('')
    setSentinelId('')
    setOpenDependency(null)
  }, [workspace.versionId])

  const flushLayout = useCallback(async () => {
    if (saveInFlight.current || Object.keys(pendingPositions.current).length === 0) return
    const batch = pendingPositions.current
    pendingPositions.current = {}
    saveInFlight.current = true
    setSaveState('saving')
    setSaveError('')
    try {
      await saveCanvasLayout(ontologyId, batch, workspace.versionId)
      setSaveState('saved')
    } catch (error) {
      pendingPositions.current = { ...batch, ...pendingPositions.current }
      setSaveState('error')
      setSaveError(errorMessage(error))
    } finally {
      saveInFlight.current = false
      if (Object.keys(pendingPositions.current).length > 0) {
        saveTimer.current = window.setTimeout(() => void flushLayout(), 3000)
      }
    }
  }, [ontologyId, workspace.versionId])

  const schedulePositionSave = useCallback((positions: Record<string, { x: number; y: number }>) => {
    Object.entries(positions).forEach(([nodeId, position]) => {
      pendingPositions.current[`l${level}:${nodeId}`] = position
    })
    setSaveState('pending')
    setSaveError('')
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => void flushLayout(), 3000)
  }, [flushLayout, level])

  const scheduleLayoutSave = useCallback((node: StructureNode) => {
    schedulePositionSave({ [node.id]: node.position })
  }, [schedulePositionSave])

  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') void flushLayout()
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange)
      if (saveTimer.current !== null) window.clearTimeout(saveTimer.current)
      void flushLayout()
    }
  }, [flushLayout])

  const onNodesChange = useCallback((changes: NodeChange<StructureNode>[]) => {
    setAllNodes(nodes => applyNodeChanges(changes, nodes))
  }, [])

  const startNodeDrag = useCallback<OnNodeDrag<StructureNode>>((_event, node) => {
    if (level !== 2 || node.data.kind !== 'object') {
      groupDrag.current = null
      return
    }
    groupDrag.current = {
      objectId: node.id,
      parentStart: { ...node.position },
      childStarts: Object.fromEntries(
        allNodes
          .filter(candidate => candidate.data.parentObjectId === node.id)
          .map(candidate => [candidate.id, { ...candidate.position }]),
      ),
    }
  }, [allNodes, level])

  const dragNodeGroup = useCallback<OnNodeDrag<StructureNode>>((_event, node) => {
    const drag = groupDrag.current
    if (!drag || drag.objectId !== node.id) return
    const dx = node.position.x - drag.parentStart.x
    const dy = node.position.y - drag.parentStart.y
    setAllNodes(nodes => nodes.map(candidate => {
      const start = drag.childStarts[candidate.id]
      return start
        ? { ...candidate, position: { x: start.x + dx, y: start.y + dy } }
        : candidate
    }))
  }, [])

  const stopNodeDrag = useCallback<OnNodeDrag<StructureNode>>((_event, node) => {
    const drag = groupDrag.current
    if (!drag || drag.objectId !== node.id) {
      scheduleLayoutSave(node)
      groupDrag.current = null
      return
    }
    const dx = node.position.x - drag.parentStart.x
    const dy = node.position.y - drag.parentStart.y
    const positions: Record<string, { x: number; y: number }> = { [node.id]: { ...node.position } }
    Object.entries(drag.childStarts).forEach(([id, start]) => {
      positions[id] = { x: start.x + dx, y: start.y + dy }
    })
    setAllNodes(nodes => nodes.map(candidate => positions[candidate.id]
      ? { ...candidate, position: positions[candidate.id] }
      : candidate))
    schedulePositionSave(positions)
    groupDrag.current = null
  }, [scheduleLayoutSave, schedulePositionSave])

  const searchIndex = useMemo<SearchResult[]>(() => {
    const objectResults = workspace.objectTypes.map(item => ({ id: item.id, kind: 'object' as const, label: item.displayName || item.name, technicalName: item.name }))
    const relationResults = workspace.linkTypes.map(item => ({ id: relationEdgeId(item.id), kind: 'relation' as const, label: item.displayName || item.name, technicalName: item.name, context: `${objectName(workspace, item.sourceObjectTypeId)} → ${objectName(workspace, item.targetObjectTypeId)}` }))
    if (level === 1) return [...objectResults, ...relationResults]
    const properties = workspace.objectTypes.flatMap(objectType => objectType.properties.map(property => ({ id: propertyNodeId(objectType.id, property), kind: 'property' as const, label: property.displayName || property.name, technicalName: property.name, context: objectType.displayName || objectType.name })))
    const actions = workspace.actions.map(item => ({ id: actionNodeId(item.id), kind: 'action' as const, label: item.displayName || item.name, technicalName: item.name, context: objectName(workspace, item.objectTypeId) }))
    return [...objectResults, ...relationResults, ...properties, ...actions]
  }, [level, workspace])

  const searchResults = useMemo(() => {
    const query = searchText.trim().toLocaleLowerCase()
    if (!query) return []
    return searchIndex.filter(item => `${item.label} ${item.technicalName} ${item.context || ''}`.toLocaleLowerCase().includes(query)).slice(0, 8)
  }, [searchIndex, searchText])

  const functionOptions = useMemo<DependencyOption[]>(() => workspace.functions.map(item => ({
    id: item.id,
    label: item.displayName || item.name,
    technicalName: item.name,
    description: item.description,
    meta: `${item.functionType || 'function'} · ${item.language || 'unknown'}`,
  })), [workspace.functions])

  const sentinelOptions = useMemo<DependencyOption[]>(() => workspace.sentinels.map(item => ({
    id: item.id,
    label: item.displayName || item.name,
    technicalName: item.name,
    description: item.description,
    meta: item.enabled === false ? '已停用' : item.onSchedule ? '定时扫描' : item.onChange ? '变更触发' : '规则触发',
  })), [workspace.sentinels])

  const chooseSearchResult = useCallback((result: SearchResult) => {
    setSearchText(result.label)
    setSearchOpen(false)
    setPaths([])
    setFunctionId('')
    setSentinelId('')
    if (result.kind === 'relation') {
      const edge = builtGraph.edges.find(item => item.id === result.id)
      if (!edge) return
      setSearchFocus({ type: 'edge', id: edge.id, context: [edge.source, edge.target] })
      void fitView({ nodes: allNodes.filter(node => node.id === edge.source || node.id === edge.target), padding: 0.6, maxZoom: 1.25, duration: 320 })
      return
    }
    const node = allNodes.find(item => item.id === result.id)
    if (!node) return
    setSearchFocus({ type: 'node', id: node.id, context: node.data.parentObjectId ? [node.data.parentObjectId] : [] })
    void fitView({ nodes: [node], padding: 1.4, maxZoom: 1.35, duration: 320 })
  }, [allNodes, builtGraph.edges, fitView])

  const runPathSearch = useCallback(() => {
    setPathAttempted(true)
    setSearchFocus(null)
    setFunctionId('')
    setSentinelId('')
    const nextPaths = findPaths(workspace.linkTypes, pathSource, pathTarget, direction)
    setPaths(nextPaths)
    setActivePathIndex(0)
    if (nextPaths[0]) {
      void fitView({ nodes: allNodes.filter(node => nextPaths[0].nodes.includes(node.id)), padding: 0.45, maxZoom: 1.05, duration: 340 })
    }
  }, [allNodes, direction, fitView, pathSource, pathTarget, workspace.linkTypes])

  const dependencyHighlight = useMemo(() => {
    if (functionId) return functionUsage(workspace, functionId)
    if (sentinelId) return sentinelUsage(workspace, sentinelId)
    return EMPTY_HIGHLIGHT
  }, [functionId, sentinelId, workspace])
  const activePath = paths[activePathIndex]
  const hasDependency = Boolean(functionId || sentinelId)
  const hasHighlight = hasDependency || Boolean(activePath) || Boolean(searchFocus)

  const visibleNodes = useMemo(() => allNodes
    .filter(node => level === 2 || node.data.kind === 'object')
    .map(node => {
      let emphasis = null as StructureNode['data']['emphasis']
      if (hasDependency) {
        if (dependencyHighlight.primaryNodes.has(node.id)) emphasis = 'primary'
        else if (dependencyHighlight.nodes.has(node.id)) emphasis = 'dependency'
        else if (dependencyHighlight.contextNodes.has(node.id)) emphasis = 'context'
      } else if (activePath?.nodes.includes(node.id)) emphasis = 'path'
      else if (searchFocus?.type === 'node' && searchFocus.id === node.id) emphasis = 'search'
      else if (searchFocus?.context.includes(node.id)) emphasis = 'context'
      return { ...node, data: { ...node.data, emphasis, dimmed: hasHighlight && !emphasis } }
    }), [activePath, allNodes, dependencyHighlight, hasDependency, hasHighlight, level, searchFocus])

  const routedEdges = useMemo(() => routeStructureEdges(builtGraph.edges, allNodes), [allNodes, builtGraph.edges])
  const visibleEdges = useMemo(() => routedEdges
    .filter(edge => level === 2 || edge.data?.kind === 'relation')
    .map((edge): StructureEdge => {
      let emphasis = null as NonNullable<StructureEdge['data']>['emphasis']
      if (hasDependency && dependencyHighlight.edges.has(edge.id)) emphasis = 'dependency'
      else if (!hasDependency && activePath?.edges.includes(edge.id)) emphasis = 'path'
      else if (!hasDependency && searchFocus?.type === 'edge' && searchFocus.id === edge.id) emphasis = 'search'
      const contextualRelation = searchFocus?.type === 'node' && searchFocus.context.length === 0 && (edge.source === searchFocus.id || edge.target === searchFocus.id)
      return { ...edge, data: { ...edge.data!, emphasis, dimmed: hasHighlight && !emphasis && !contextualRelation } }
    }), [activePath, dependencyHighlight.edges, hasDependency, hasHighlight, level, routedEdges, searchFocus])

  const selectNode = useCallback((node: StructureNode) => {
    if (node.data.kind === 'property') setDetail({ kind: 'property', id: node.data.entityId, parentObjectId: node.data.parentObjectId })
    else if (node.data.kind === 'action') setDetail({ kind: 'action', id: node.data.entityId, parentObjectId: node.data.parentObjectId })
    else setDetail({ kind: 'object', id: node.data.entityId })
  }, [])

  const changeLevel = useCallback((nextLevel: Level) => {
    setLevel(nextLevel)
    setSearchText('')
    setSearchFocus(null)
    setFunctionId('')
    setSentinelId('')
    setOpenDependency(null)
    setPaths([])
    setDetail(null)
  }, [])

  const changeMode = useCallback((nextMode: 'browse' | 'path') => {
    setMode(nextMode)
    setOpenDependency(null)
    if (nextMode === 'path') {
      setSearchFocus(null)
      setFunctionId('')
      setSentinelId('')
    }
  }, [])

  const chooseDependency = useCallback((kind: 'function' | 'sentinel', id: string) => {
    setOpenDependency(null)
    if (!id) {
      if (kind === 'function') setFunctionId('')
      else setSentinelId('')
      return
    }
    setLevel(2)
    setMode('browse')
    setSearchText('')
    setSearchFocus(null)
    setPaths([])
    setPathAttempted(false)
    if (kind === 'function') { setFunctionId(id); setSentinelId('') }
    else { setSentinelId(id); setFunctionId('') }
    window.setTimeout(() => void fitView({ padding: 0.2, maxZoom: 0.88, duration: 280 }), 30)
  }, [fitView])

  const organizeGraph = useCallback(() => {
    const organized = buildStructureGraph(workspace, level, { ignoreSaved: true })
    setAllNodes(organized.nodes)
    schedulePositionSave(Object.fromEntries(organized.nodes.map(node => [node.id, node.position])))
    groupDrag.current = null
    setDetail(null)
    window.setTimeout(() => void fitView({ padding: level === 1 ? 0.26 : 0.14, minZoom: level === 1 ? 0.24 : 0.34, maxZoom: level === 1 ? 1.05 : 0.86, duration: 420 }), 40)
  }, [fitView, level, schedulePositionSave, workspace])

  const saveLabel = saveState === 'pending' ? '3 秒后自动保存' : saveState === 'saving' ? '正在保存布局' : saveState === 'saved' ? '布局已保存' : saveState === 'error' ? '保存失败' : '拖动后自动保存布局'

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-50/70" data-testid="ontology-structure-graph">
      <div className="z-40 flex shrink-0 items-center gap-2 overflow-x-auto border-b border-slate-200 bg-white px-3 py-2.5" style={{ scrollbarWidth: 'none' }}>
        <AnimatedSegmentedControl<Level>
          value={level}
          label="图谱视角"
          items={[{ value: 1, label: 'L1' }, { value: 2, label: 'L2' }]}
          onChange={changeLevel}
        />
        <AnimatedSegmentedControl<'browse' | 'path'>
          value={mode}
          label="图谱模式"
          items={[{ value: 'browse', label: '浏览', icon: Focus }, { value: 'path', label: '路径', icon: Route }]}
          onChange={changeMode}
        />
        <div className="relative w-[260px] shrink-0">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input value={searchText} onChange={event => { setSearchText(event.target.value); setSearchOpen(true); setSearchFocus(null) }} onFocus={() => setSearchOpen(true)} onKeyDown={event => { if (event.key === 'Enter' && searchResults[0]) chooseSearchResult(searchResults[0]); if (event.key === 'Escape') setSearchOpen(false) }} placeholder={level === 1 ? '搜索对象实体或实体关系' : '搜索对象、关系、属性或动作'} aria-label="搜索本体结构" className="h-9 w-full rounded-lg border border-slate-200 bg-slate-50 pl-9 pr-8 text-xs text-slate-700 outline-none transition focus:border-teal-400 focus:bg-white focus:ring-2 focus:ring-teal-100" />
          {searchText && <button type="button" aria-label="清空搜索" onClick={() => { setSearchText(''); setSearchFocus(null); setSearchOpen(false) }} className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-slate-400 hover:bg-slate-200"><X size={12} /></button>}
          {searchOpen && searchText.trim() && (
            <div className="absolute left-0 top-11 z-50 max-h-72 w-[320px] overflow-auto rounded-xl border border-slate-200 bg-white p-1.5 shadow-xl">
              {searchResults.length ? searchResults.map(result => <button key={`${result.kind}:${result.id}`} type="button" onMouseDown={event => event.preventDefault()} onClick={() => chooseSearchResult(result)} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left hover:bg-slate-50"><Pill tone={result.kind === 'relation' ? 'teal' : result.kind === 'property' ? 'violet' : result.kind === 'action' ? 'amber' : 'slate'}>{kindLabel[result.kind]}</Pill><span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium text-slate-700">{result.label}</span><span className="block truncate font-mono text-[10px] text-slate-400">{result.technicalName}{result.context ? ` · ${result.context}` : ''}</span></span><ChevronRight size={13} className="text-slate-300" /></button>) : <p className="px-3 py-5 text-center text-xs text-slate-400">当前 L{level} 视角没有匹配项</p>}
            </div>
          )}
        </div>
        <div className="h-6 w-px shrink-0 bg-slate-200" />
        <DependencyPicker
          kind="function"
          items={functionOptions}
          value={functionId}
          open={openDependency === 'function'}
          onOpenChange={open => setOpenDependency(open ? 'function' : null)}
          onChange={id => chooseDependency('function', id)}
        />
        <DependencyPicker
          kind="sentinel"
          items={sentinelOptions}
          value={sentinelId}
          open={openDependency === 'sentinel'}
          onOpenChange={open => setOpenDependency(open ? 'sentinel' : null)}
          onChange={id => chooseDependency('sentinel', id)}
        />
        <div className="ml-auto flex shrink-0 items-center gap-1">
          <span data-testid="published-structure-readonly" title="当前页面只允许调整并保存画布布局，不允许修改本体模型结构" className="mr-1 inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-2.5 text-[11px] font-medium text-slate-600">
            <ShieldCheck size={13} className="text-teal-700" />发布快照 · 结构只读
          </span>
          <button type="button" onClick={organizeGraph} aria-label="智能整理图谱" title="按实体关系力导向展开，并将属性与动作分层排列" className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-teal-200 bg-teal-50 px-2.5 text-xs font-medium text-teal-700 transition-colors hover:border-teal-300 hover:bg-teal-100 active:translate-y-px"><Sparkles size={13} />智能整理</button>
          <button type="button" onClick={() => void zoomOut({ duration: 160 })} aria-label="缩小" className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-50"><ZoomOut size={14} /></button>
          <button type="button" onClick={() => void zoomIn({ duration: 160 })} aria-label="放大" className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-50"><ZoomIn size={14} /></button>
          <button type="button" onClick={() => void fitView({ padding: 0.2, minZoom: level === 2 ? 0.32 : 0.24, maxZoom: 0.92, duration: 260 })} aria-label="适应画布" className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-50"><Maximize2 size={14} /></button>
        </div>
      </div>

      {mode === 'path' && (
        <div className="z-30 flex shrink-0 items-center gap-2 border-b border-cyan-100 bg-cyan-50/70 px-3 py-2">
          <Route size={14} className="shrink-0 text-cyan-700" /><span className="shrink-0 text-xs font-semibold text-cyan-800">对象路径</span>
          <select value={pathSource} onChange={event => { setPathSource(event.target.value); setPaths([]); setPathAttempted(false) }} aria-label="路径起点" className="h-8 min-w-[160px] rounded-lg border border-cyan-200 bg-white px-2 text-xs text-slate-700"><option value="">选择起点（对象实体）</option>{workspace.objectTypes.map(item => <option key={item.id} value={item.id}>{item.displayName || item.name}</option>)}</select>
          <ArrowRight size={13} className="text-cyan-500" />
          <select value={pathTarget} onChange={event => { setPathTarget(event.target.value); setPaths([]); setPathAttempted(false) }} aria-label="路径终点" className="h-8 min-w-[160px] rounded-lg border border-cyan-200 bg-white px-2 text-xs text-slate-700"><option value="">选择终点（对象实体）</option>{workspace.objectTypes.map(item => <option key={item.id} value={item.id}>{item.displayName || item.name}</option>)}</select>
          <div className="ml-1 flex rounded-lg border border-cyan-200 bg-white p-0.5">
            <button type="button" onClick={() => { setDirection('outgoing'); setPaths([]) }} className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] ${direction === 'outgoing' ? 'bg-cyan-100 font-semibold text-cyan-800' : 'text-slate-500'}`}><ArrowRight size={11} />单向</button>
            <button type="button" onClick={() => { setDirection('both'); setPaths([]) }} className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] ${direction === 'both' ? 'bg-cyan-100 font-semibold text-cyan-800' : 'text-slate-500'}`}><ArrowLeftRight size={11} />双向</button>
          </div>
          <button type="button" disabled={!pathSource || !pathTarget || pathSource === pathTarget} onClick={runPathSearch} className="h-8 rounded-lg bg-cyan-700 px-3 text-xs font-semibold text-white hover:bg-cyan-800 disabled:cursor-not-allowed disabled:opacity-40">查找路径</button>
          {paths.length > 0 && <span className="ml-1 text-[11px] text-cyan-700">找到 {paths.length} 条（最多展示 5 条）</span>}
          {pathAttempted && paths.length === 0 && <span className="ml-1 text-[11px] text-amber-700">未找到可达路径</span>}
        </div>
      )}

      <div className="relative min-h-0 flex-1 overflow-hidden">
        <ReactFlow<StructureNode, StructureEdge>
          nodes={visibleNodes} edges={visibleEdges} nodeTypes={NODE_TYPES} edgeTypes={EDGE_TYPES}
          onNodesChange={onNodesChange} onNodeDragStart={startNodeDrag} onNodeDrag={dragNodeGroup} onNodeDragStop={stopNodeDrag}
          onNodeClick={(_event, node) => selectNode(node)}
          onEdgeClick={(_event, edge) => { if (edge.data?.kind === 'relation' && edge.data.entityId) setDetail({ kind: 'relation', id: edge.data.entityId }) }}
          onPaneClick={() => { setDetail(null); setSearchOpen(false) }}
          nodesDraggable nodesConnectable={false} elementsSelectable minZoom={0.2} maxZoom={2.4}
          defaultViewport={{ x: 0, y: 0, zoom: 0.78 }} proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} color="#cbd5e1" />
          <MiniMap pannable zoomable position="bottom-left" className="!m-3 !h-[96px] !w-[150px] !rounded-xl !border !border-slate-200 !bg-white/90 !shadow-sm" nodeColor={node => node.data?.kind === 'object' ? '#0f766e' : node.data?.kind === 'property' ? '#8b5cf6' : '#f59e0b'} maskColor="rgba(241,245,249,0.72)" />
        </ReactFlow>

        <div className="pointer-events-none absolute left-3 top-3 z-20 flex items-center gap-2 rounded-xl border border-slate-200 bg-white/90 px-3 py-2 text-[10px] text-slate-500 shadow-sm backdrop-blur">
          <Layers3 size={13} className="text-teal-600" /><span>L{level} · {workspace.objectTypes.length} 对象 · {workspace.linkTypes.length} 关系{level === 2 ? ` · ${workspace.objectTypes.reduce((sum, item) => sum + item.properties.length, 0)} 属性 · ${workspace.actions.length} 动作` : ''}</span>
          <span className="h-3 w-px bg-slate-200" />
          <span className={`inline-flex items-center gap-1 ${saveState === 'error' ? 'text-red-600' : saveState === 'saved' ? 'text-emerald-600' : ''}`} title={saveError}><Clock3 size={11} />{saveLabel}</span>
          <span className="h-3 w-px bg-slate-200" />
          <span>发布版 <span className="font-mono font-semibold text-teal-700" data-testid="published-structure-version">{workspace.version}</span></span>
        </div>

        {(hasDependency || paths.length > 0) && (
          <div className="absolute bottom-4 left-1/2 z-20 max-w-[min(720px,calc(100%-360px))] -translate-x-1/2 rounded-xl border border-slate-200 bg-white/95 px-3 py-2 shadow-lg backdrop-blur">
            {hasDependency ? (
              <div className="flex items-center gap-2 text-xs text-slate-700"><Sparkles size={14} className={sentinelId ? 'text-fuchsia-600' : 'text-violet-600'} /><span className="font-medium">{dependencyHighlight.summary || '没有找到直接使用节点'}</span><button type="button" onClick={() => { setFunctionId(''); setSentinelId('') }} className="ml-1 rounded p-1 text-slate-400 hover:bg-slate-100"><X size={12} /></button></div>
            ) : (
              <div className="flex items-center gap-2"><span className="text-[11px] font-medium text-slate-500">路径</span>{paths.map((path, index) => <button key={`${path.edges.join(':')}:${index}`} type="button" onClick={() => { setActivePathIndex(index); void fitView({ nodes: allNodes.filter(node => path.nodes.includes(node.id)), padding: 0.45, maxZoom: 1.05, duration: 280 }) }} className={`rounded-md px-2 py-1 text-[11px] ${activePathIndex === index ? 'bg-cyan-100 font-semibold text-cyan-800' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>{index + 1} · {path.edges.length} 跳</button>)}</div>
            )}
          </div>
        )}

        <DetailPanel workspace={workspace} selection={detail} onClose={() => setDetail(null)} />
      </div>
    </div>
  )
}

export default function ModelStructureView({ ontologyId }: { ontologyId: string }) {
  const releaseQuery = useQuery<PublishedWorkspace>({
    queryKey: ['current-release-workspace', ontologyId],
    queryFn: () => apiClientV2.get(`/ontologies/${ontologyId}/current-release/workspace`),
  })
  if (releaseQuery.isLoading) return <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-500"><Loader2 className="animate-spin" size={18} />正在构建本体结构图谱…</div>
  if (releaseQuery.isError || !releaseQuery.data?.isCurrentRelease || releaseQuery.data.workspaceMode !== 'release' || releaseQuery.data.editable !== false) return <div className="flex h-full items-center justify-center gap-2 bg-red-50 text-sm text-red-700"><AlertCircle size={18} />当前发布快照读取失败，已停止展示可变模型数据。</div>
  if (releaseQuery.data.objectTypes.length === 0) return <div className="flex h-full flex-col items-center justify-center gap-3 text-slate-500"><span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-100"><Database size={22} /></span><p className="text-sm">当前发布版还没有对象实体</p></div>
  return <ReactFlowProvider><StructureGraph ontologyId={ontologyId} workspace={releaseQuery.data} /></ReactFlowProvider>
}
