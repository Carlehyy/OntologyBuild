import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ChevronDown, ChevronRight, FileText, Languages, ShieldAlert, ShieldCheck,
} from 'lucide-react'
import { ontologyVersionApi } from '@/api/v2/ontology-versions'
import {
  composeBackTranslateMessage, groupSemanticIssues, semanticIssueKindLabel,
} from '@/components/ontology/semanticReadiness'

/**
 * 本体模型视图顶部的一致性面板：展示版本语义层（业务画布/需求文档/本体结构）
 * 三面一致性明细，并提供「回译到业务语义」闭环入口。
 * 查询键与本体详情页结构说明弹窗一致（ontology-structure-doc），共享缓存；
 * 人工修改保存成功后由 GraphWorkspace.onSaved 触发失效重取。
 */
export default function ConsistencyPanel({ ontologyId, versionId, onBackTranslate, onGotoDocs }: {
  ontologyId: string
  versionId: string
  /** 回译入口；对话忙碌时由宿主置为 undefined（与 CanvasPanel.onAsk 同一惯例）。 */
  onBackTranslate?: (message: string) => void
  /** 切到需求文档视图（重新生成需求文档 / 重新「生成本体模型」补齐结构）。 */
  onGotoDocs?: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const { data } = useQuery({
    queryKey: ['ontology-structure-doc', ontologyId, versionId],
    queryFn: () => ontologyVersionApi.versionSemantic(ontologyId, versionId),
  })
  const issues = useMemo(() => data?.issues || [], [data])
  const groups = useMemo(() => groupSemanticIssues(issues), [issues])
  const backTranslateMessage = useMemo(() => composeBackTranslateMessage(issues), [issues])
  const hasStructureMissing = issues.some(issue => issue.code === 'semantic_structure_missing')
  const hasDocumentIssue = issues.some(
    issue => issue.code === 'semantic_document_missing' || issue.code === 'semantic_document_stale',
  )
  // 语义层缺失与「零漂移」是两回事：版本未经过澄清流程沉淀语义层时，
  // 三面比对无从谈起，必须展示为独立状态，不能误报「一致」。
  const noSemanticLayer = Boolean(data && data.overview && data.overview.hasSemanticLayer === false)
  const consistent = !noSemanticLayer && issues.length ===  0

  return (
    <div
      data-testid="consistency-panel"
      className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)]"
    >
      <button
        type="button"
        onClick={() => setExpanded(value => !value)}
        aria-expanded={expanded}
        data-testid="consistency-panel-toggle"
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-[var(--color-bg-hover)]"
      >
        <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md ${noSemanticLayer
          ? 'bg-slate-100 text-slate-500' : consistent
            ? 'bg-teal-50 text-teal-700' : 'bg-amber-50 text-amber-700'}`}>
          {noSemanticLayer ? <FileText size={13} /> : consistent ? <ShieldCheck size={13} /> : <ShieldAlert size={13} />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5 text-xs font-medium text-[var(--color-text-primary)]">
            一致性状态
            <span
              data-testid="consistency-status-badge"
              className={`rounded-full px-1.5 py-0.5 text-[10px] ${noSemanticLayer
                ? 'bg-slate-100 text-slate-600' : consistent
                  ? 'bg-teal-50 text-teal-700' : 'bg-amber-50 text-amber-700'}`}
            >
              {noSemanticLayer ? '未沉淀语义层' : consistent ? '一致' : `${issues.length} 项漂移`}
            </span>
          </span>
          <span className="mt-0.5 block truncate text-[11px] text-[var(--color-text-tertiary)]">
            {noSemanticLayer
              ? '该版本尚未经过业务澄清沉淀语义层，暂无可比对的画布与文档'
              : consistent
                ? '业务画布 · 需求文档 · 本体结构三面一致'
                : '人工修改与业务语义存在出入 · 点击查看明细'}
          </span>
        </span>
        {expanded
          ? <ChevronDown size={13} className="shrink-0 text-[var(--color-text-tertiary)]" />
          : <ChevronRight size={13} className="shrink-0 text-[var(--color-text-tertiary)]" />}
      </button>
      {expanded && noSemanticLayer && (
        <div className="border-t border-[var(--color-border)] px-3 pb-3 pt-2.5">
          <div className="flex flex-wrap items-center gap-2 rounded-md bg-slate-50 px-2.5 py-1.5">
            <FileText size={12} className="shrink-0 text-slate-500" />
            <span className="text-[11px] leading-5 text-slate-600">
              语义层在「生成本体模型」落地时沉淀；生成后此处即可比对业务画布、需求文档与本体结构的三面一致性。
            </span>
            {onGotoDocs && (
              <button
                type="button"
                onClick={onGotoDocs}
                data-testid="goto-docs-no-semantic"
                className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[11px] text-slate-700 transition-colors hover:bg-slate-100"
              >
                前往需求文档视图
              </button>
            )}
          </div>
        </div>
      )}
      {expanded && !noSemanticLayer && !consistent && (
        <div className="space-y-2.5 border-t border-[var(--color-border)] px-3 pb-3 pt-2.5">
          {groups.map(group => (
            <div key={group.code} data-testid={`consistency-group-${group.code}`}>
              <div className="flex items-center gap-1.5 text-[11px] font-medium text-amber-900">
                {group.label}
                <span className="rounded bg-amber-100 px-1 py-px text-[10px] text-amber-700">{group.issues.length}</span>
              </div>
              <ul className="mt-1 space-y-0.5">
                {group.issues.map((issue, index) => (
                  <li
                    key={`${issue.id}-${index}`}
                    className="text-[11px] leading-relaxed text-[var(--color-text-secondary)]"
                  >
                    · <span className="text-[var(--color-text-tertiary)]">{semanticIssueKindLabel(issue.kind)}「{issue.name || issue.id}」</span> {issue.message}
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {hasDocumentIssue && (
            <div className="flex flex-wrap items-center gap-2 rounded-md bg-amber-50/60 px-2.5 py-1.5">
              <FileText size={12} className="shrink-0 text-amber-600" />
              <span className="text-[11px] leading-5 text-amber-800">
                需求文档与画布/结构已不同步，建议到需求文档视图重新生成。
              </span>
              {onGotoDocs && (
                <button
                  type="button"
                  onClick={onGotoDocs}
                  className="rounded border border-amber-300 bg-white px-2 py-0.5 text-[11px] text-amber-800 transition-colors hover:bg-amber-100"
                >
                  前往需求文档视图
                </button>
              )}
            </div>
          )}
          {(backTranslateMessage || hasStructureMissing) && (
            <div className="flex flex-wrap items-center gap-2 pt-0.5">
              {backTranslateMessage && (
                <button
                  type="button"
                  disabled={!onBackTranslate}
                  onClick={() => onBackTranslate?.(backTranslateMessage)}
                  data-testid="back-translate-button"
                  title="把人工修改发给探索助手，回译并同步到业务场景画布"
                  className="inline-flex h-7 items-center gap-1.5 rounded-md bg-teal-600 px-2.5 text-xs font-medium text-white transition-colors hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Languages size={12} /> 回译到业务语义
                </button>
              )}
              {hasStructureMissing && onGotoDocs && (
                <button
                  type="button"
                  onClick={onGotoDocs}
                  data-testid="regenerate-model-hint"
                  className="inline-flex h-7 items-center rounded-md border border-[var(--color-border)] px-2.5 text-xs text-[var(--color-text-secondary)] transition-colors hover:border-teal-400 hover:text-teal-700"
                >
                  前往需求文档视图重新「生成本体模型」补齐结构
                </button>
              )}
            </div>
          )}
          <div
            data-testid="solidify-path-hint"
            className="flex flex-wrap items-center gap-2 rounded-md bg-slate-50 px-2.5 py-1.5"
          >
            <span className="text-[11px] leading-5 text-slate-600">
              消解路径：回译/补齐只更新业务画布，漂移消解需到需求文档视图重新生成文档并「生成本体模型」固化语义层。
            </span>
            {onGotoDocs && (
              <button
                type="button"
                onClick={onGotoDocs}
                className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[11px] text-slate-700 transition-colors hover:bg-slate-100"
              >
                前往固化
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
