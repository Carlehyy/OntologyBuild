/**
 * 三维场景画布：动态引入引擎（three 进独立 chunk），
 * definition 变化重建场景，容器尺寸经 ResizeObserver 自适应；
 * mockPush=true 时对 bindings 的 path 集合做随机游走推送驱动告警演示。
 */
import { useEffect, useRef } from 'react'
import type { RuleHit, SceneDefinition } from '@/types/scene'
import { definitionToEnginePackage } from './adapter'
import { validateDefinition } from './validateDefinition'

export interface SceneCanvasProps {
  definition: SceneDefinition | null
  className?: string
  onSelectObject?: (id: string | null) => void
  onRuleHits?: (hits: RuleHit[]) => void
  mockPush?: boolean
}

const MOCK_PUSH_INTERVAL_MS = 2000

/** 收集 dataBindings 中出现的 path（'a.b.c' 形式）。 */
function collectPaths(definition: SceneDefinition): string[] {
  return [...new Set(
    (definition.dataBindings ?? [])
      .map(b => b.path)
      .filter((p): p is string => typeof p === 'string' && p.length > 0),
  )]
}

function setPath(target: Record<string, unknown>, path: string, value: number) {
  const keys = path.split('.')
  let node = target
  for (let i = 0; i < keys.length - 1; i++) {
    const key = keys[i]
    if (!(key in node) || typeof node[key] !== 'object' || node[key] === null) {
      node[key] = {}
    }
    node = node[key] as Record<string, unknown>
  }
  node[keys[keys.length - 1]] = value
}

export function SceneCanvas({ definition, className, onSelectObject, onRuleHits, mockPush = false }: SceneCanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  // 回调经 ref 取最新值，避免身份变化导致场景重建
  const onSelectRef = useRef(onSelectObject)
  const onHitsRef = useRef(onRuleHits)
  useEffect(() => {
    onSelectRef.current = onSelectObject
    onHitsRef.current = onRuleHits
  })

  useEffect(() => {
    const container = containerRef.current
    if (!container || !definition) return

    let disposed = false
    let engine: import('./engine').WhiteTwinEngine | null = null
    let ro: ResizeObserver | null = null
    let pushTimer: ReturnType<typeof setInterval> | null = null
    let flushTimer: ReturnType<typeof setTimeout> | null = null
    const pendingHits: RuleHit[] = []

    const flushHits = () => {
      flushTimer = null
      if (pendingHits.length === 0) return
      const batch = pendingHits.splice(0, pendingHits.length)
      onHitsRef.current?.(batch)
    }

    void (async () => {
      // three 进独立 chunk：只有真正挂载画布时才加载
      const { WhiteTwinEngine } = await import('./engine')
      if (disposed) return
      const issues = validateDefinition(definition)
      if (issues.length > 0) {
        console.warn('[SceneCanvas] 场景定义存在以下问题（尽力渲染）：', issues)
      }
      engine = new WhiteTwinEngine(container)
      engine.loadPackage(definitionToEnginePackage(definition))
      engine.onEvent(e => {
        if (e.type === 'select') {
          onSelectRef.current?.(e.id)
        } else {
          // statusChange 聚合为 RuleHit 批量上报
          pendingHits.push({
            objectId: e.objectId,
            level: e.level,
            message: e.message,
            path: e.path,
            value: e.value ?? null,
            occurredAt: new Date().toISOString(),
          })
          if (flushTimer === null) flushTimer = setTimeout(flushHits, 0)
        }
      })
      ro = new ResizeObserver(() => engine?.resize())
      ro.observe(container)
      engine.resize()

      if (mockPush) {
        const paths = collectPaths(definition)
        // 每个 path 独立随机游走，起始值落在规则可命中的区间
        const walk = new Map<string, number>(paths.map(p => [p, 40 + Math.random() * 60]))
        pushTimer = setInterval(() => {
          if (!engine || disposed) return
          const payload: Record<string, unknown> = {}
          for (const p of paths) {
            const next = Math.min(150, Math.max(0,
              (walk.get(p) ?? 50) + (Math.random() - 0.5) * 20))
            walk.set(p, next)
            setPath(payload, p, Math.round(next * 10) / 10)
          }
          engine.push(payload)
        }, MOCK_PUSH_INTERVAL_MS)
      }
    })().catch(err => console.error('[SceneCanvas] engine init failed', err))

    return () => {
      disposed = true
      if (pushTimer !== null) clearInterval(pushTimer)
      if (flushTimer !== null) clearTimeout(flushTimer)
      ro?.disconnect()
      engine?.destroy()
    }
  }, [definition, mockPush])

  return (
    <div
      ref={containerRef}
      className={className}
      style={{ position: 'relative', width: '100%', height: '100%' }}
    />
  )
}
