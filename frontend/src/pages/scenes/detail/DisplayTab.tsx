/**
 * 场景展示标签 — Three.js 白模在线演示与交互。
 *
 * 版本下拉选择要渲染的版本定义；「模拟数据推送」驱动绑定规则命中
 * （仅用于演示，不写真实数据源）；发布态场景的规则命中批量上报运行日志。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Info } from 'lucide-react'
import { scenesApi } from '@/api/scenes'
import type { RuleHit, SceneDefinition, SceneDetail, SceneVersionMeta } from '@/types/scene'
import { SceneCanvas } from '@/lib/scene3d/SceneCanvas'
import { EmptyState } from '@/components/ui/LoadingState'

const DEDUPE_WINDOW_MS = 30_000

export function DisplayTab({ scene }: { scene: SceneDetail }) {
  const versionsQuery = useQuery({
    queryKey: ['scenes', scene.id, 'versions'],
    queryFn: () => scenesApi.versions(scene.id),
  })
  const versions: SceneVersionMeta[] = versionsQuery.data?.items ?? []
  const defaultVersionNo = scene.published_version_no ?? scene.current_version_no
  const [selectedNo, setSelectedNo] = useState<number>(defaultVersionNo)
  const [mockPush, setMockPush] = useState(false)
  const lastReportedRef = useRef(new Map<string, number>())

  // 数据到位后把选中版本对齐到默认生效版本
  useEffect(() => {
    if (defaultVersionNo >= 1) setSelectedNo(defaultVersionNo)
  }, [scene.id, defaultVersionNo])

  const versionQuery = useQuery({
    queryKey: ['scenes', scene.id, 'version', selectedNo],
    queryFn: () => scenesApi.version(scene.id, selectedNo),
    enabled: selectedNo >= 1,
  })
  const definition = (versionQuery.data?.definition ?? null) as SceneDefinition | null

  const handleRuleHits = (hits: RuleHit[]) => {
    const now = Date.now()
    const fresh = hits.filter(hit => {
      const key = hit.objectId + '|' + hit.level + '|' + hit.message
      const last = lastReportedRef.current.get(key) ?? 0
      if (now - last < DEDUPE_WINDOW_MS) return false
      lastReportedRef.current.set(key, now)
      return true
    })
    if (fresh.length === 0) return
    // 发布态才落库：草稿态的命中只影响本地展示
    if (scene.status !== 'published') return
    void scenesApi.appendRuntimeLogs(
      scene.id,
      fresh.map(hit => ({
        level: hit.level,
        object_id: hit.objectId,
        event_key: 'binding.rule',
        message: hit.message,
        payload: hit.value != null ? { value: hit.value } : undefined,
        occurred_at: hit.occurredAt,
      })),
    ).catch(() => { /* 上报失败不打断演示 */ })
  }

  const versionOptions = useMemo(
    () => [...versions].sort((a, b) => b.version_no - a.version_no),
    [versions],
  )

  if (versions.length === 0) {
    return (
      <div className="rounded-xl border border-[var(--color-border)] bg-card p-10">
        <EmptyState
          title="该场景还没有版本定义"
          description="可通过场景助手对话生成场景定义"
        />
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 p-3">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
          渲染版本
          <Select
            value={String(selectedNo)}
            onValueChange={value => setSelectedNo(Number(value))}
          >
            <SelectTrigger className="h-8 rounded-md bg-card px-2 text-sm" aria-label="选择版本">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {versionOptions.map(version => (
                <SelectItem key={version.version_no} value={String(version.version_no)}>
                  v{version.version_no} · {version.note || version.source}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label className="flex cursor-pointer items-center gap-1.5 text-sm text-[var(--color-text-secondary)]">
          <input
            type="checkbox"
            checked={mockPush}
            onChange={event => setMockPush(event.target.checked)}
            className="accent-[var(--color-nav-bg)]"
          />
          模拟数据推送
        </label>
        <span className="inline-flex items-center gap-1 text-[11px] text-[var(--color-text-tertiary)]">
          <Info size={12} /> 模拟数据仅用于演示规则命中，不会写入真实数据源
        </span>
      </div>
      <div className="relative min-h-0 flex-1 overflow-hidden rounded-xl border border-[var(--color-border)] bg-card">
        {definition
          ? (
            <SceneCanvas
              definition={definition}
              mockPush={mockPush}
              onRuleHits={handleRuleHits}
              className="absolute inset-0"
            />
          )
          : <LoadingInline />}
      </div>
    </div>
  )
}

function LoadingInline() {
  return (
    <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-secondary)]">
      正在加载三维场景…
    </div>
  )
}
