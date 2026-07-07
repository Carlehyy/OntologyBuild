import { useState } from 'react'
import {
  Box, Users, Play, Zap, Scale, Map as MapIcon, ChevronDown, ChevronRight, CircleAlert,
  Share2, X, Copy, Loader2,
} from 'lucide-react'
import { explorationApi, type BusinessCanvas, type CanvasElement, type Completeness } from '@/api/exploration'
import MermaidBlock from '@/components/MermaidBlock'
import ElementDetailModal from './ElementDetailModal'

const SECTIONS: {
  key: keyof BusinessCanvas
  label: string
  icon: React.ElementType
  tint: string
}[] = [
  { key: 'objects', label: '对象模型', icon: Box, tint: 'text-sky-600 bg-sky-50' },
  { key: 'actors', label: '主体模型', icon: Users, tint: 'text-violet-600 bg-violet-50' },
  { key: 'behaviors', label: '行为模型', icon: Play, tint: 'text-teal-600 bg-teal-50' },
  { key: 'events', label: '事件模型', icon: Zap, tint: 'text-amber-600 bg-amber-50' },
  { key: 'rules', label: '规则模型', icon: Scale, tint: 'text-rose-600 bg-rose-50' },
  { key: 'scenarios', label: '场景模型', icon: MapIcon, tint: 'text-emerald-600 bg-emerald-50' },
]

function elementBadges(key: keyof BusinessCanvas, el: CanvasElement): string[] {
  const badges: string[] = []
  if (key === 'objects') {
    const attrs = (el.attributes as unknown[] | undefined)?.length || 0
    const rels = (el.relations as unknown[] | undefined)?.length || 0
    badges.push(`${attrs} 属性`)
    if (rels) badges.push(`${rels} 关系`)
    if (el.key_attribute) badges.push(`主键 ${el.key_attribute}`)
  } else if (key === 'actors') {
    if (el.kind) badges.push(String(el.kind))
  } else if (key === 'behaviors') {
    if (el.actor) badges.push(String(el.actor))
    if (el.object) badges.push(`→ ${el.object}`)
    if (el.needs_approval) badges.push('需审批')
  } else if (key === 'events') {
    if (el.source) badges.push(`来源 ${el.source}`)
  } else if (key === 'rules') {
    if (el.kind) badges.push(String(el.kind))
    if (el.applies_to) badges.push(`→ ${el.applies_to}`)
  } else if (key === 'scenarios') {
    const steps = (el.steps as unknown[] | undefined)?.length || 0
    if (steps) badges.push(`${steps} 步`)
  }
  return badges
}

/** 业务画布面板：六类模型分组卡片 + 完整度缺口，随 SSE canvas 事件实时刷新 */
export default function CanvasPanel({ sessionId, canvas, completeness }: {
  sessionId?: string
  canvas: BusinessCanvas | null
  completeness: Completeness | null
}) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [detail, setDetail] = useState<{ section: typeof SECTIONS[number]; el: CanvasElement } | null>(null)
  const [erOpen, setErOpen] = useState(false)
  const [erMermaid, setErMermaid] = useState('')
  const [erBusy, setErBusy] = useState(false)
  const [erError, setErError] = useState('')
  const counts = completeness?.counts || {}
  const total = Object.values(counts).reduce((a, b) => a + b, 0)

  const openEr = async () => {
    if (!sessionId || erBusy) return
    setErBusy(true)
    setErError('')
    setErOpen(true)
    try {
      const { mermaid } = await explorationApi.erDiagram(sessionId)
      setErMermaid(mermaid)
    } catch (e: any) {
      setErError(e?.detail || e?.message || 'ER 图生成失败')
    } finally {
      setErBusy(false)
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-4 pt-4 pb-2">
        <div>
          <div className="text-sm font-semibold text-[var(--color-text-primary)]">业务画布</div>
          <div className="text-xs text-[var(--color-text-tertiary)] mt-0.5">
            对话中确认的知识实时沉淀于此，转化为需求文档与本体的唯一来源
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            onClick={openEr}
            disabled={!sessionId || (counts.objects || 0) === 0}
            title={(counts.objects || 0) === 0 ? '画布还没有对象模型' : '从对象模型确定性生成 ER 图（不经 LLM）'}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-40"
          >
            <Share2 size={11} /> ER 图
          </button>
          <span className="text-xs px-2 py-0.5 rounded-full bg-[var(--color-bg-base)] border border-[var(--color-border)] text-[var(--color-text-secondary)]">
            {total} 元素
          </span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-3 space-y-2">
        {SECTIONS.map((section) => {
          const { key, label, icon: Icon, tint } = section
          const items = canvas?.[key] || []
          const isCollapsed = collapsed[key] ?? items.length === 0
          return (
            <div key={key} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)]">
              <button
                onClick={() => setCollapsed(c => ({ ...c, [key]: !isCollapsed }))}
                className="w-full flex items-center gap-2 px-3 py-2 text-left"
              >
                <span className={`w-5 h-5 rounded-md flex items-center justify-center ${tint}`}>
                  <Icon size={12} />
                </span>
                <span className="text-xs font-medium text-[var(--color-text-primary)] flex-1">{label}</span>
                <span className="text-[11px] text-[var(--color-text-tertiary)]">{items.length}</span>
                {isCollapsed
                  ? <ChevronRight size={13} className="text-[var(--color-text-tertiary)]" />
                  : <ChevronDown size={13} className="text-[var(--color-text-tertiary)]" />}
              </button>
              {!isCollapsed && items.length > 0 && (
                <div className="px-3 pb-2.5 space-y-1.5">
                  {items.map(el => (
                    <button
                      key={el.id}
                      onClick={() => setDetail({ section, el })}
                      title="查看详情"
                      className="group block w-full text-left rounded-md bg-[var(--color-bg-base)] px-2.5 py-1.5 transition-colors hover:bg-[var(--color-bg-hover)] hover:ring-1 hover:ring-teal-200"
                    >
                      <div className="flex items-center gap-1 text-xs font-medium text-[var(--color-text-primary)]">
                        <span className="truncate">
                          {el.display_name || el.name}
                          {el.display_name && el.display_name !== el.name && (
                            <span className="ml-1.5 font-normal text-[var(--color-text-tertiary)] font-mono text-[11px]">{el.name}</span>
                          )}
                        </span>
                        <ChevronRight size={12} className="ml-auto shrink-0 text-[var(--color-text-tertiary)] opacity-0 transition-opacity group-hover:opacity-100" />
                      </div>
                      {(el.description || elementBadges(key, el).length > 0) && (
                        <div className="mt-0.5 flex flex-wrap items-center gap-1">
                          {elementBadges(key, el).map((b, i) => (
                            <span key={i} className="text-[10px] px-1.5 py-px rounded bg-black/[0.04] text-[var(--color-text-secondary)]">{b}</span>
                          ))}
                          {el.description && (
                            <span className="text-[11px] text-[var(--color-text-tertiary)] truncate max-w-full">{el.description}</span>
                          )}
                        </div>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )
        })}

        {(completeness?.gaps?.length || 0) > 0 && (
          <div className="rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2.5">
            <div className="flex items-center gap-1.5 text-xs font-medium text-amber-700 mb-1.5">
              <CircleAlert size={13} /> 待澄清缺口
            </div>
            <ul className="space-y-1">
              {completeness!.gaps.slice(0, 8).map((g, i) => (
                <li key={i} className="text-[11px] leading-relaxed text-amber-800/90">· {g}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* 元素详情弹窗：点击画布卡片查看完整字段，引用可下钻 */}
      {detail && (
        <ElementDetailModal
          sectionKey={detail.section.key}
          el={detail.el}
          canvas={canvas}
          onClose={() => setDetail(null)}
          onNavigate={(key, el) => {
            const sec = SECTIONS.find(s => s.key === key)
            if (sec) setDetail({ section: sec, el })
          }}
        />
      )}

      {/* ER 图模态：确定性生成（画布对象模型 → mermaid） */}
      {erOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6"
             onClick={() => setErOpen(false)}>
          <div className="w-[820px] max-w-[94vw] max-h-[86vh] rounded-xl bg-[var(--color-bg-elevated)] shadow-2xl flex flex-col"
               onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-[var(--color-border)]">
              <div>
                <div className="text-sm font-semibold text-[var(--color-text-primary)]">ER 图（实体关系图）</div>
                <div className="text-[11px] text-[var(--color-text-tertiary)] mt-0.5">
                  由画布对象模型确定性生成，与画布严格一致
                </div>
              </div>
              <div className="flex items-center gap-2">
                {erMermaid && (
                  <button
                    onClick={() => { void navigator.clipboard.writeText(erMermaid) }}
                    className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
                  >
                    <Copy size={12} /> 复制源码
                  </button>
                )}
                <button onClick={() => setErOpen(false)}
                        className="p-1.5 rounded-md hover:bg-[var(--color-bg-hover)] text-[var(--color-text-tertiary)]">
                  <X size={15} />
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-auto px-5 py-4">
              {erBusy && (
                <div className="flex items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
                  <Loader2 size={13} className="animate-spin" /> 生成中…
                </div>
              )}
              {erError && <div className="text-xs text-[var(--color-danger)]">{erError}</div>}
              {!erBusy && !erError && erMermaid && <MermaidBlock chart={erMermaid} />}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
