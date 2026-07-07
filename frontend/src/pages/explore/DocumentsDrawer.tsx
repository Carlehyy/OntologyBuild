import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X, FileText, Download, Loader2, Wand2 } from 'lucide-react'
import { explorationApi, type BxDocument, type BxDraft } from '@/api/exploration'
import { ontologyApi } from '@/api/ontologies'
import Md from './Md'

/** 需求文档抽屉：版本列表 + markdown 预览 + 生成本体草稿（选择新建/已有本体） */
export default function DocumentsDrawer({ sessionId, onClose, onDraftCreated }: {
  sessionId: string
  onClose: () => void
  onDraftCreated: (draft: BxDraft) => void
}) {
  const { data: docs = [], isLoading } = useQuery({
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

  useEffect(() => {
    if (!activeId && docs.length > 0) setActiveId(docs[0].id)
  }, [docs, activeId])

  useEffect(() => {
    if (!activeId) { setDoc(null); return }
    let cancelled = false
    explorationApi.document(activeId).then(d => { if (!cancelled) setDoc(d) })
    return () => { cancelled = true }
  }, [activeId])

  const download = () => {
    if (!doc) return
    const blob = new Blob([doc.contentMd], { type: 'text/markdown;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${doc.title}.md`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const generateDraft = async () => {
    if (!doc) return
    setError('')
    setGenerating(true)
    try {
      const draft = await explorationApi.generateDraft(doc.id, {
        targetOntologyId: target || undefined,
      })
      onDraftCreated(draft)
    } catch (e: any) {
      setError(e?.detail || e?.message || '草稿生成失败')
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30" onClick={onClose}>
      <div
        className="h-full w-[760px] max-w-[94vw] bg-[var(--color-bg-elevated)] shadow-2xl flex"
        onClick={e => e.stopPropagation()}
      >
        {/* 版本列表 */}
        <div className="w-52 shrink-0 border-r border-[var(--color-border)] flex flex-col">
          <div className="px-4 py-4 border-b border-[var(--color-border)]">
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">需求文档</div>
            <div className="text-xs text-[var(--color-text-tertiary)] mt-0.5">{docs.length} 个版本</div>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {isLoading && <div className="text-xs text-[var(--color-text-tertiary)] px-2 py-1">加载中…</div>}
            {!isLoading && docs.length === 0 && (
              <div className="text-xs text-[var(--color-text-tertiary)] px-2 py-1 leading-relaxed">
                还没有文档。回到会话点「生成需求文档」。
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
              {doc && (
                <button
                  onClick={download}
                  className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
                >
                  <Download size={12} /> .md
                </button>
              )}
              <button onClick={onClose} className="p-1.5 rounded-md hover:bg-[var(--color-bg-hover)] text-[var(--color-text-tertiary)]">
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
                  onClick={generateDraft}
                  disabled={generating}
                  className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-md text-xs font-medium text-white bg-teal-600 hover:bg-teal-700 disabled:opacity-50"
                >
                  {generating ? <Loader2 size={12} className="animate-spin" /> : <Wand2 size={12} />}
                  生成本体草稿
                </button>
              </div>
              <div className="text-[10px] text-[var(--color-text-tertiary)]">
                转化以生成该文档时的画布快照为源：确定性映射为主，LLM 仅补缺；草稿需人工审阅勾选后才会写入本体。
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
