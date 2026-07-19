import { memo } from 'react'
import {
  BaseEdge, EdgeLabelRenderer, Handle, Position,
  type EdgeProps, type NodeProps,
} from '@xyflow/react'
import { Box, Bolt, Braces, KeyRound } from 'lucide-react'
import type { StructureEdge, StructureNode } from './structureGraphModel'

const kindStyle = {
  object: {
    icon: Box,
    iconClass: 'bg-teal-50 text-teal-700 ring-teal-100',
    border: 'border-slate-200',
  },
  property: {
    icon: Braces,
    iconClass: 'bg-violet-50 text-violet-700 ring-violet-100',
    border: 'border-violet-200/80',
  },
  action: {
    icon: Bolt,
    iconClass: 'bg-amber-50 text-amber-700 ring-amber-100',
    border: 'border-amber-200/80',
  },
}

const HANDLE_SIDES = [
  ['top', Position.Top],
  ['right', Position.Right],
  ['bottom', Position.Bottom],
  ['left', Position.Left],
] as const

function DirectionalHandles() {
  return HANDLE_SIDES.map(([side, position]) => (
    <span key={side}>
      <Handle id={`target-${side}`} type="target" position={position} className="!h-2 !w-2 !border-0 !bg-transparent !opacity-0" />
      <Handle id={`source-${side}`} type="source" position={position} className="!h-2 !w-2 !border-0 !bg-transparent !opacity-0" />
    </span>
  ))
}

export const StructureGraphNode = memo(({ data, selected }: NodeProps<StructureNode>) => {
  const style = kindStyle[data.kind]
  const Icon = style.icon
  const emphasisClass = data.emphasis === 'primary'
    ? 'border-fuchsia-500 ring-4 ring-fuchsia-100 shadow-[0_14px_32px_rgba(192,38,211,0.16)]'
    : data.emphasis === 'dependency'
      ? 'border-violet-500 ring-4 ring-violet-100 shadow-[0_14px_32px_rgba(124,58,237,0.16)]'
      : data.emphasis === 'path'
        ? 'border-cyan-500 ring-4 ring-cyan-100 shadow-[0_14px_32px_rgba(6,182,212,0.16)]'
        : data.emphasis === 'search'
          ? 'border-amber-500 ring-4 ring-amber-100 shadow-[0_14px_32px_rgba(245,158,11,0.16)]'
          : data.emphasis === 'context'
            ? 'border-violet-300 ring-2 ring-violet-50'
            : selected
              ? 'border-teal-500 ring-4 ring-teal-100 shadow-[0_14px_32px_rgba(13,148,136,0.14)]'
              : style.border
  const widthClass = data.kind === 'object' ? 'w-[224px]' : data.kind === 'property' ? 'w-[188px]' : 'w-[196px]'

  return (
    <div
      data-testid={`structure-node-${data.kind}`}
      className={`${widthClass} rounded-xl border bg-white px-3.5 py-3 transition-[opacity,border-color,box-shadow,transform] duration-200 ${emphasisClass} ${data.dimmed ? 'opacity-20 grayscale' : 'opacity-100'} hover:-translate-y-0.5 hover:shadow-lg`}
    >
      <DirectionalHandles />
      <div className="flex min-w-0 items-center gap-3">
        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ring-1 ${style.iconClass}`}>
          <Icon size={17} strokeWidth={1.8} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <p className="truncate text-[13px] font-semibold text-slate-800">{data.label}</p>
            {data.emphasis === 'primary' && <KeyRound size={12} className="shrink-0 text-fuchsia-600" />}
          </div>
          <p className="truncate font-mono text-[10px] text-slate-400">{data.technicalName}</p>
          <p className="mt-0.5 truncate text-[10px] text-slate-500">{data.subtitle}</p>
        </div>
      </div>
    </div>
  )
})

StructureGraphNode.displayName = 'StructureGraphNode'

export function StructureGraphEdge({
  id, source, target, sourceX, sourceY, targetX, targetY, markerEnd, data, selected,
}: EdgeProps<StructureEdge>) {
  const offset = Number(data?.offset || 0)
  const relation = data?.kind === 'relation'
  const dx = targetX - sourceX
  const dy = targetY - sourceY
  const distance = Math.hypot(dx, dy) || 1
  const nx = -dy / distance
  const ny = dx / distance
  const midX = (sourceX + targetX) / 2
  const midY = (sourceY + targetY) / 2
  const controlX = midX + nx * offset * 2
  const controlY = midY + ny * offset * 2
  const selfLoop = source === target
  const path = selfLoop
    ? `M ${sourceX},${sourceY} C ${sourceX + 60},${sourceY - 100 - Math.abs(offset)} ${targetX - 60},${targetY - 100 - Math.abs(offset)} ${targetX},${targetY}`
    : `M ${sourceX},${sourceY} Q ${controlX},${controlY} ${targetX},${targetY}`
  const labelX = selfLoop ? midX : midX + nx * offset
  const labelY = selfLoop ? Math.min(sourceY, targetY) - 84 - Math.abs(offset) : midY + ny * offset
  const emphasized = data?.emphasis === 'path' || data?.emphasis === 'dependency' || data?.emphasis === 'search'
  const stroke = data?.emphasis === 'path'
    ? '#0891b2'
    : data?.emphasis === 'dependency'
      ? '#7c3aed'
      : data?.emphasis === 'search'
        ? '#d97706'
        : selected ? '#0f766e' : relation ? '#64748b' : data?.kind === 'property' ? '#c4b5fd' : '#fcd34d'
  const opacity = data?.dimmed ? 0.12 : relation ? 0.9 : 0.62

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        interactionWidth={relation ? 26 : 12}
        style={{ stroke, strokeWidth: emphasized ? 3.2 : relation ? 1.8 : 1.2, opacity, strokeDasharray: relation ? undefined : '5 5' }}
      />
      {relation && data?.label && !data.dimmed && (
        <EdgeLabelRenderer>
          <div
            data-testid="structure-edge-relation"
            className={`nodrag nopan pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 rounded-full border bg-white/95 px-2 py-0.5 text-[10px] font-medium shadow-sm ${emphasized ? 'border-cyan-300 text-cyan-800' : 'border-slate-200 text-slate-600'}`}
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}
