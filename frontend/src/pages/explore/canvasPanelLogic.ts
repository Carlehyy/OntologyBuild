/* 业务画布面板的纯逻辑：元素徽标与图示 target 选项解析。
   与 CanvasPanel.tsx 解耦（无 React 依赖），便于 node:test 单测。 */
import type { BusinessCanvas, CanvasElement, DiagramKind } from '@/api/exploration'

export type CanvasKey = 'objects' | 'actors' | 'behaviors' | 'events' | 'rules' | 'processes' | 'scenarios'

/** 图示 target 口径：scenario = 场景或流程（后端在两类集合中解析）；object = 状态图对象 */
export type DiagramTargetKind = 'scenario' | 'object' | 'process'

export interface DiagramTab {
  kind: DiagramKind
  label: string
  needsTarget?: DiagramTargetKind
}

export const DIAGRAM_TABS: DiagramTab[] = [
  { kind: 'er', label: 'ER 图' },
  { kind: 'flow', label: '流程图', needsTarget: 'scenario' },
  { kind: 'sequence', label: '时序图', needsTarget: 'scenario' },
  { kind: 'state', label: '状态图', needsTarget: 'object' },
]

/** 元素卡片徽标：结构性要点的压缩展示（流程含步骤/分支/指标与异常路径标记） */
export function elementBadges(key: CanvasKey, el: CanvasElement): string[] {
  const badges: string[] = []
  if (key === 'objects') {
    const attrs = (el.attributes as unknown[] | undefined)?.length || 0
    const rels = (el.relations as unknown[] | undefined)?.length || 0
    badges.push(`${attrs} 属性`)
    if (rels) badges.push(`${rels} 关系`)
    if (el.key_attribute) badges.push(`主键 ${el.key_attribute}`)
  } else if (key === 'actors') {
    const attrs = (el.attributes as unknown[] | undefined)?.length || 0
    const resp = (el.responsibilities as unknown[] | undefined)?.length || 0
    if (el.kind) badges.push(String(el.kind))
    if (attrs) badges.push(`${attrs} 属性`)
    if (resp) badges.push(`${resp} 职责`)
  } else if (key === 'behaviors') {
    if (el.actor) badges.push(String(el.actor))
    if (el.object) badges.push(`→ ${el.object}`)
    if (el.needs_approval) badges.push('需审批')
  } else if (key === 'events') {
    if (el.source) badges.push(`来源 ${el.source}`)
  } else if (key === 'rules') {
    if (el.kind) badges.push(String(el.kind))
    if (el.applies_to) badges.push(`→ ${el.applies_to}`)
  } else if (key === 'processes') {
    const steps = (el.steps as unknown[] | undefined)?.length || 0
    const branches = (el.branches as { kind?: string }[] | undefined) || []
    const metrics = (el.metrics as unknown[] | undefined)?.length || 0
    if (steps) badges.push(`${steps} 步`)
    if (branches.length) badges.push(`${branches.length} 分支`)
    if (metrics) badges.push(`${metrics} 指标`)
    if (branches.some(branch => branch?.kind === 'exception')) badges.push('含异常路径')
  } else if (key === 'scenarios') {
    const steps = (el.steps as unknown[] | undefined)?.length || 0
    if (steps) badges.push(`${steps} 步`)
  }
  return badges
}

/** 画布流程名清单（display_name 优先）：供图示 target 下拉使用 */
export function canvasProcessNames(canvas: BusinessCanvas | null): string[] {
  return (canvas?.processes || []).map(p => String(p.display_name || p.name))
}

export interface DiagramTargetNames {
  scenarioNames: string[]
  objectNames: string[]
  processNames: string[]
}

/** target 下拉选项：flow/sequence（scenario 口径）同时列出场景名与流程名 */
export function diagramTargetOptions(
  spec: DiagramTargetKind | undefined,
  names: DiagramTargetNames,
): string[] {
  if (spec === 'scenario') return [...names.scenarioNames, ...names.processNames]
  if (spec === 'object') return names.objectNames
  if (spec === 'process') return names.processNames
  return []
}

/** target 下拉默认选项文案（空值 = 交给后端自动选取） */
export function diagramTargetPlaceholder(spec: DiagramTargetKind | undefined): string {
  if (spec === 'scenario') return '默认场景或流程（第一个）'
  if (spec === 'object') return '自动选择对象'
  return '默认流程（第一个）'
}
