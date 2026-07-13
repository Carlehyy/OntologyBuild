import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X, FileText, Download, Loader2, ShieldAlert, Sparkles, Wand2 } from 'lucide-react'
import { explorationApi, type BxDocument, type BxDraft, type Readiness } from '@/api/exploration'
import { ontologyApi } from '@/api/ontologies'
import Md from './Md'

/** 需求文档工作区：历史版本 + markdown 预览 + 生成本体模型。 */
export default function DocumentsDrawer({
  sessionId, onClose, onDraftCreated, onGenerate, documentGenerating, canGenerateDocument,
}: {
  sessionId: string
  onClose: () => void
  onDraftCreated: (draft: BxDraft) => void
  onGenerate: () => Promise<void>
  documentGenerating: boolean
  canGenerateDocument: boolean
}) {
  const { data: docs = [], isLoading, refetch } = useQuery({
    queryKey: ['bx-documents', sessionId],
    queryFn: () => explorationApi.documents(sessionId),
  })
  const { data: ontologies } = useQuery({
    queryKey: ['ontologies'], queryFn: () => ontologyApi.list() as any,
  })
  const ontologyList: { id: string; name: string }[] = (ontologies as any)?.items || ontologies || []

  const [activeId, setActiveId] = useState<string>('')
  const [doc, setDoc] = useState<BxDocument | null>(null)
  const [target, setTarget] = useState<string>('')      // '' = 新建本体
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')
  // 质量门拦截：后端 422 返回结构化 readiness，展示堵门项并允许显式越权
  const [gateBlock, setGateBlock] = useState<Readiness | null>(null)

  useEffect(() => {
    if (!activeId && docs.length > 0) setActiveId(docs[0].id)
  }, [docs, activeId])

  useEffect(() => {
    if (!activeId) { setDoc(null); return }
    let cancelled = false
    explorationApi.document(activeId).then(d => { if (!cancelled) setDoc(d) })
    return () => { cancelled = true }
  }, [activeId])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const generateRequirements = async () => {
    await onGenerate()
    const result = await refetch()
    if (result.data?.[0]) setActiveId(result.data[0].id)
  }

  const download = () => {
    if (!doc) return
    const blob = new Blob([doc.contentMd], { type: 'text/markdown;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${doc.title}.md`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const generateDraft = async (force = false) => {
    if (!doc) return
    setError('')
    if (!force) setGateBlock(null)
    setGenerating(true)
    try {
      const draft = await explorationApi.generateDraft(doc.id, {
        targetOntologyId: target || undefined,
        force,
      })
      setGateBlock(null)
      onDraftCreated(draft)
    } catch (e: any) {
      const detail = e?.detail
      if (detail?.code === 'quality_gate_blocked' && detail?.readiness) {
        setGateBlock(detail.readiness as Readiness)
      } else {
        setError((typeof detail === 'string' && detail) || e?.message || '本体模型生成失败')
      }
    } finally {
      setGenerating(false)
    }
  }

  const gateBlocking: { gate: string; item: string }[] = (gateBlock?.gates || [])
    .flatMap(g => g.blockingItems.map(item => ({ gate: g.label, item })))

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-slate-950/30 px-4 pt-[7vh] backdrop-blur-[1px]" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="requirements-dialog-title"
        className="flex h-[78vh] min-h-[520px] w-[1040px] max-w-[94vw] overflow-hidden rounded-xl border border-white/60 bg-[var(--color-bg-elevated)] shadow-[0_24px_80px_rgba(15,23,42,0.22)]"
        onMouseDown={e => e.stopPropagation()}
      >
        {/* 版本列表 */}
        <div className="flex w-60 shrink-0 flex-col border-r border-[var(--color-border)] bg-slate-50/55">
          <div className="px-4 py-4 border-b border-[var(--color-border)]">
            <div id="requirements-dialog-title" className="text-sm font-semibold text-[var(--color-text-primary)]">需求文档</div>
            <div className="text-xs text-[var(--color-text-tertiary)] mt-0.5">历史版本 · 共 {docs.length} 个</div>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {isLoading && <div className="text-xs text-[var(--color-text-tertiary)] px-2 py-1">加载中…</div>}
            {!isLoading && docs.length === 0 && (
              <div className="text-xs text-[var(--color-text-tertiary)] px-2 py-1 leading-relaxed">
                还没有需求文档。点击右上角「生成需求文档」开始创建。
              </div>
            )}
            {docs.map(d => (
              <button
                key={d.id}
                onClick={() => setActiveId(d.id)}
                className={`w-full text-left px-2.5 py-2 rounded-md text-xs transition-colors ${d.id === activeId
                  ? 'bg-teal-50 text-teal-800 font-medium'
                  : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'}`}
              >
                <div className="flex items-center gap-1.5">
                  <FileText size={12} className="shrink-0" />
                  <span>v{d.version}</span>
                </div>
                <div className="mt-0.5 text-[10px] text-[var(--color-text-tertiary)]">
                  {new Date(d.createdAt).toLocaleString()}
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* 预览 + 操作 */}
        <div className="flex-1 min-w-0 flex flex-col">
          <div className="flex items-center justify-between gap-3 px-5 py-3.5 border-b border-[var(--color-border)]">
            <div className="text-xs font-medium text-[var(--color-text-primary)] truncate">
              {doc?.title || '选择一个版本预览'}
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button
                onClick={() => void generateRequirements()}
                disabled={!canGenerateDocument || documentGenerating}
                title={canGenerateDocument ? '根据当前业务画布生成新的需求文档版本' : '画布还是空的，先对话沉淀模型'}
                className="inline-flex items-center gap-1.5 rounded-md border border-teal-200 bg-teal-50 px-3 py-1.5 text-xs font-medium text-teal-700 transition-colors hover:bg-teal-100 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {documentGenerating ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                生成需求文档
              </button>
              {doc && (
                <button
                  onClick={download}
                  className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
                >
                  <Download size={12} /> .md
                </button>
              )}
              <button onClick={onClose} aria-label="关闭需求文档" className="p-1.5 rounded-md hover:bg-[var(--color-bg-hover)] text-[var(--color-text-tertiary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400">
                <X size={16} />
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-6 py-4">
            {doc ? <Md text={doc.contentMd} /> : (
              <div className="text-xs text-[var(--color-text-tertiary)]">（无内容）</div>
            )}
          </div>

          {doc && (
            <div className="border-t border-[var(--color-border)] px-5 py-3.5 space-y-2">
              {error && <div className="text-xs text-[var(--color-danger)]">{error}</div>}
              {gateBlock && (
                <div className="rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2.5">
                  <div className="flex items-center gap-1.5 text-xs font-medium text-amber-800">
                    <ShieldAlert size={13} />
                    质量门未通过（{gateBlock.gatesPassed}/{gateBlock.gatesTotal} 门）——
                    还有 {gateBlock.blockingCount} 项口径未定量
                  </div>
                  <ul className="mt-1.5 space-y-0.5 max-h-36 overflow-y-auto">
                    {gateBlocking.slice(0, 10).map((b, i) => (
                      <li key={i} className="text-[11px] leading-relaxed text-amber-800/90">
                        · [{b.gate}] {b.item}
                      </li>
                    ))}
                    {gateBlocking.length > 10 && (
                      <li className="text-[11px] text-amber-700">…共 {gateBlocking.length} 项</li>
                    )}
                  </ul>
                  <div className="mt-2 flex items-center gap-2">
                    <span className="text-[11px] text-amber-800/80 flex-1">
                      建议回到对话，按画布「质量门」提示逐项澄清后重新生成文档。
                    </span>
                    <button
                      onClick={() => void generateDraft(true)}
                      disabled={generating}
                      className="shrink-0 px-2.5 py-1 rounded-md text-[11px] border border-amber-300 text-amber-800 hover:bg-amber-100 disabled:opacity-50"
                    >
                      已知悉风险，越权生成（留痕）
                    </button>
                  </div>
                </div>
              )}
              <div className="flex items-center gap-2">
                <select
                  value={target}
                  onChange={e => setTarget(e.target.value)}
                  className="flex-1 px-2.5 py-1.5 text-xs rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] outline-none"
                >
                  <option value="">生成到：新建本体</option>
                  {ontologyList.map(o => (
                    <option key={o.id} value={o.id}>合并到：{o.name}（保守合并，同名跳过）</option>
                  ))}
                </select>
                <button
                  onClick={() => void generateDraft(false)}
                  disabled={generating}
                  className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-md text-xs font-medium text-white bg-teal-600 hover:bg-teal-700 disabled:opacity-50"
                >
                  {generating ? <Loader2 size={12} className="animate-spin" /> : <Wand2 size={12} />}
                  生成本体模型
                </button>
              </div>
              <div className="text-[10px] text-[var(--color-text-tertiary)]">
                转化以生成该文档时的画布快照为源，确定性映射；质量门（定量澄清）通过后方可生成，越权生成会留痕；草稿需人工审阅勾选后才会写入本体。
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
