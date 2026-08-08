import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import CodeMirror from '@uiw/react-codemirror'
import { python } from '@codemirror/lang-python'
import {
  ArrowLeft, Play, Save, Loader2, CheckCircle2, XCircle, AlertTriangle,
  FileCode2, Terminal, RotateCcw, Keyboard,
} from 'lucide-react'
import pipelinesApi from '@/api/v2/pipelines'
import type { Pipeline, ScriptExecutionResult } from '@/api/v2/pipelines'
import { useToast } from '@/components/ui/Toast'
import { PYTHON_SCRIPT_TEMPLATE } from './template'

const PREVIEW_ROWS = 20

const draftKey = (pipelineId: string) => `ob:python-script-draft:${pipelineId}`

interface Draft {
  script: string
  updatedAt: string
}

function readDraft(pipelineId: string): Draft | null {
  try {
    const raw = localStorage.getItem(draftKey(pipelineId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as Draft
    return typeof parsed?.script === 'string' ? parsed : null
  } catch {
    return null
  }
}

function cellText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function formatClock(iso?: string): string {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return iso
  }
}

const EMPTY_RESULT: Omit<ScriptExecutionResult, 'ok' | 'error'> = {
  format_valid: false,
  format_error: null,
  row_count: 0,
  columns: [],
  sample: [],
  stdout: '',
  traceback: '',
  duration_ms: 0,
}

export default function PythonScriptPage() {
  const { pipelineId } = useParams<{ pipelineId: string }>()
  const navigate = useNavigate()
  const { toast } = useToast()

  const [pipeline, setPipeline] = useState<Pipeline | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [script, setScript] = useState('')
  const [savedScript, setSavedScript] = useState('')
  const [executing, setExecuting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<ScriptExecutionResult | null>(null)
  const [resultKind, setResultKind] = useState<'execute' | 'save'>('execute')
  // 客户端门槛：只有「当前编辑器内容」执行通过且格式合法，保存才可点；
  // 服务端保存时仍会重跑复验（双重保障）。
  const [validatedScript, setValidatedScript] = useState<string | null>(null)
  const [draftRestoredAt, setDraftRestoredAt] = useState<string | null>(null)
  const draftTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const isPublished = pipeline?.status === 'published'
  const wrongEngine = pipeline && (pipeline.definition as { engine?: string } | null)?.engine !== 'python'
  const dirty = script !== savedScript
  const scriptDirtySinceValidation = validatedScript === null || script !== validatedScript
  const canSave = !!pipeline && !isPublished && !executing && !saving && !scriptDirtySinceValidation && script.trim().length > 0

  const load = useCallback(async () => {
    if (!pipelineId) return
    setLoading(true)
    setLoadError('')
    try {
      const pl = await pipelinesApi.get(pipelineId)
      setPipeline(pl)
      const saved = (pl.definition as { python?: { script?: string } } | null)?.python?.script || ''
      setSavedScript(saved)
      const draft = readDraft(pipelineId)
      // 草稿优先：用户上次关闭浏览器前的未保存改动不丢
      if (!saved && !draft) {
        setScript(PYTHON_SCRIPT_TEMPLATE)
      } else if (draft && draft.script !== saved) {
        setScript(draft.script)
        setDraftRestoredAt(draft.updatedAt)
      } else {
        setScript(saved)
      }
      setValidatedScript(null)
      setResult(null)
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setLoadError(err?.detail || err?.message || '流水线加载失败')
    } finally {
      setLoading(false)
    }
  }, [pipelineId])

  useEffect(() => { load() }, [load])

  // 编辑即缓存草稿（防抖 500ms）；已发布只读不写草稿
  useEffect(() => {
    if (!pipelineId || isPublished || !dirty) return
    if (draftTimer.current) clearTimeout(draftTimer.current)
    draftTimer.current = setTimeout(() => {
      try {
        localStorage.setItem(
          draftKey(pipelineId),
          JSON.stringify({ script, updatedAt: new Date().toISOString() } satisfies Draft),
        )
      } catch { /* 存储满等异常不影响编辑 */ }
    }, 500)
    return () => { if (draftTimer.current) clearTimeout(draftTimer.current) }
  }, [script, dirty, isPublished, pipelineId])

  const clearDraft = useCallback(() => {
    if (pipelineId) localStorage.removeItem(draftKey(pipelineId))
    setDraftRestoredAt(null)
  }, [pipelineId])

  const handleExecute = useCallback(async () => {
    if (!pipeline || executing) return
    if (!script.trim()) {
      toast({ tone: 'warning', title: '脚本内容为空，无法执行' })
      return
    }
    setExecuting(true)
    setResultKind('execute')
    try {
      const res = await pipelinesApi.executeScript(pipeline.id, script)
      setResult(res)
      setValidatedScript(res.ok && res.format_valid ? script : null)
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setValidatedScript(null)
      setResult({
        ...EMPTY_RESULT,
        ok: false,
        error: err?.detail || err?.message || '执行请求失败',
      })
    } finally {
      setExecuting(false)
    }
  }, [pipeline, executing, script, toast])

  const handleSave = useCallback(async () => {
    if (!pipeline || !canSave) return
    setSaving(true)
    setResultKind('save')
    try {
      const res = await pipelinesApi.saveScript(pipeline.id, script)
      setPipeline(res.pipeline)
      setSavedScript(script)
      clearDraft()
      setResult(res.execution)
      toast({ tone: 'success', title: '脚本已保存', description: `输出 ${res.execution.row_count} 行，格式校验通过。` })
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setResult({
        ...EMPTY_RESULT,
        ok: false,
        error: err?.detail || err?.message || '保存请求失败',
      })
    } finally {
      setSaving(false)
    }
  }, [pipeline, canSave, script, clearDraft, toast])

  const handleRevert = () => {
    setScript(savedScript || PYTHON_SCRIPT_TEMPLATE)
    clearDraft()
    setValidatedScript(null)
  }

  const handleDiscardDraft = () => {
    setScript(savedScript || PYTHON_SCRIPT_TEMPLATE)
    clearDraft()
    setValidatedScript(null)
    toast({ tone: 'info', title: '已放弃草稿，恢复为已保存版本' })
  }

  // 快捷键：Ctrl/⌘+Enter 执行，Ctrl/⌘+S 保存
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return
      if (e.key === 'Enter') {
        e.preventDefault()
        handleExecute()
      } else if (e.key.toLowerCase() === 's') {
        e.preventDefault()
        handleSave()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [handleExecute, handleSave])

  const sampleColumns = useMemo(() => {
    if (!result) return []
    if (result.columns.length > 0) return result.columns
    const cols: string[] = []
    for (const row of result.sample.slice(0, PREVIEW_ROWS)) {
      for (const key of Object.keys(row)) {
        if (!cols.includes(key)) cols.push(key)
      }
    }
    return cols
  }, [result])

  if (loading) {
    return <div className="p-8 text-center text-sm text-gray-400">加载中...</div>
  }
  if (loadError || !pipeline) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-16 text-center">
        <AlertTriangle size={28} className="text-amber-500" />
        <p className="text-sm text-gray-600">{loadError || '流水线不存在'}</p>
        <button onClick={() => navigate('/data/pipelines')} className="rounded-lg border px-4 py-2 text-sm hover:bg-gray-50">
          返回数据流水线
        </button>
      </div>
    )
  }
  if (wrongEngine) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-16 text-center">
        <FileCode2 size={28} className="text-gray-400" />
        <p className="text-sm text-gray-600">「{pipeline.name}」不是 Python 脚本流水线。</p>
        <button onClick={() => navigate('/data/pipelines')} className="rounded-lg border px-4 py-2 text-sm hover:bg-gray-50">
          返回数据流水线
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-4 pb-4">
      {/* 页头：返回 + 名称 + 徽章 + 保存状态 */}
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3">
        <button
          onClick={() => navigate('/data/pipelines')}
          className="flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-gray-600 transition hover:bg-gray-50"
        >
          <ArrowLeft size={13} /> 返回
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate font-semibold text-gray-900">{pipeline.name}</h2>
            <span className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-700">
              <FileCode2 size={10} /> Python 脚本
            </span>
            <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] ${
              isPublished
                ? 'border-teal-200 bg-teal-50 text-teal-700'
                : 'border-slate-200 bg-slate-100 text-slate-600'}`}
            >
              {isPublished ? '已发布' : '未发布'}
            </span>
          </div>
          {pipeline.description && (
            <p className="mt-0.5 truncate text-xs text-gray-400">{pipeline.description}</p>
          )}
        </div>
        <div className="shrink-0 text-right text-[11px] leading-4">
          {isPublished ? (
            <span className="text-slate-400">脚本已封版，只读</span>
          ) : dirty ? (
            <span className="flex items-center gap-1 text-amber-600">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-500" />
              有未保存修改 · 草稿已自动缓存
            </span>
          ) : (
            <span className="flex items-center gap-1 text-slate-400">
              <CheckCircle2 size={11} className="text-teal-600" />
              已保存{pipeline.updated_at ? ` · ${formatClock(pipeline.updated_at)}` : ''}
            </span>
          )}
        </div>
      </div>

      {isPublished && (
        <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <AlertTriangle size={13} className="shrink-0" />
          流水线已发布，脚本已封版只读；仍可执行脚本核对输出。如需变更，请新建流水线。
        </div>
      )}
      {!isPublished && draftRestoredAt && (
        <div className="flex items-center gap-2 rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-800">
          <FileCode2 size={13} className="shrink-0" />
          <span className="flex-1">已恢复你上次未保存的编辑草稿（{formatClock(draftRestoredAt)}）。</span>
          <button onClick={handleDiscardDraft} className="shrink-0 rounded border border-sky-300 bg-white px-2 py-0.5 font-medium hover:bg-sky-100">
            放弃草稿
          </button>
        </div>
      )}

      {/* 脚本编辑区（浅色 IDE） */}
      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-2.5">
          <span className="text-xs font-medium text-gray-700">
            Python 脚本
          </span>
          <span className="text-[11px] text-gray-400">
            最终结果请赋值给 <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[11px] text-indigo-600">result</code>（list[dict] 行式结构）
          </span>
          <span className="ml-auto hidden items-center gap-2 text-[11px] text-gray-400 sm:flex">
            <Keyboard size={12} />
            <span><kbd className="rounded border border-slate-200 bg-slate-50 px-1 font-mono">⌘/Ctrl+Enter</kbd> 执行</span>
            <span><kbd className="rounded border border-slate-200 bg-slate-50 px-1 font-mono">⌘/Ctrl+S</kbd> 保存</span>
          </span>
        </div>
        <CodeMirror
          value={script}
          onChange={setScript}
          extensions={[python()]}
          theme="light"
          readOnly={isPublished}
          height="44vh"
          placeholder="# 在此编写取数脚本，最终结果赋值给 result"
          basicSetup={{
            lineNumbers: true,
            foldGutter: true,
            autocompletion: true,
            bracketMatching: true,
            closeBrackets: true,
            indentOnInput: true,
            highlightActiveLine: true,
            highlightActiveLineGutter: true,
          }}
          style={{
            fontSize: '13px',
            backgroundColor: isPublished ? '#f8fafc' : undefined,
          }}
        />
        <div className="flex items-center justify-between border-t border-slate-100 px-4 py-1.5 text-[11px] text-gray-400">
          <span>执行环境自带 requests / httpx / pandas / pymysql / openpyxl 等依赖</span>
          <span className="font-mono">{script.split('\n').length} 行</span>
        </div>
      </div>

      {/* 操作按钮 */}
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3">
        <button
          onClick={handleExecute}
          disabled={executing || saving}
          className="flex items-center gap-1.5 rounded-xl bg-teal-700 px-4 py-2 text-sm font-medium text-white transition hover:bg-teal-800 active:translate-y-px disabled:opacity-50"
        >
          {executing ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
          {executing ? '执行中...' : '执行'}
        </button>
        {!isPublished && (
          <>
            <button
              onClick={handleSave}
              disabled={!canSave}
              title={scriptDirtySinceValidation ? '脚本已修改或尚未执行：请先执行并通过输出格式校验' : '保存脚本（平台会重新执行并复验输出格式）'}
              className="flex items-center gap-1.5 rounded-xl border border-teal-700 px-4 py-2 text-sm font-medium text-teal-700 transition hover:bg-teal-50 active:translate-y-px disabled:cursor-not-allowed disabled:border-gray-200 disabled:text-gray-400"
            >
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
              {saving ? '校验并保存中...' : '保存'}
            </button>
            {dirty && (
              <button
                onClick={handleRevert}
                disabled={executing || saving}
                className="flex items-center gap-1.5 rounded-xl border border-slate-200 px-3 py-2 text-sm text-gray-600 transition hover:bg-gray-50 disabled:opacity-50"
                title="放弃当前修改，恢复为已保存的脚本"
              >
                <RotateCcw size={13} /> 放弃修改
              </button>
            )}
          </>
        )}
        <p className="text-xs text-gray-400">
          {isPublished
            ? '已发布流水线脚本只读'
            : scriptDirtySinceValidation
              ? '保存前需先执行：输出通过平台格式校验（list[dict] 行式结构）后保存才可点'
              : '格式校验已通过，可以保存；保存时平台会重新执行并复验'}
        </p>
      </div>

      {/* 执行结果 */}
      <ResultPanel
        result={result}
        resultKind={resultKind}
        executing={executing}
        saving={saving}
        sampleColumns={sampleColumns}
      />
    </div>
  )
}

function ResultPanel({
  result,
  resultKind,
  executing,
  saving,
  sampleColumns,
}: {
  result: ScriptExecutionResult | null
  resultKind: 'execute' | 'save'
  executing: boolean
  saving: boolean
  sampleColumns: string[]
}) {
  if (executing || saving) {
    return (
      <div className="flex items-center justify-center gap-3 rounded-2xl border border-slate-200 bg-white p-10 text-sm text-gray-500">
        <Loader2 size={16} className="animate-spin text-teal-700" />
        {executing ? '正在内核中执行脚本，请稍候…' : '正在重新执行并校验输出格式…'}
      </div>
    )
  }
  if (!result) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-2xl border-2 border-dashed border-slate-200 bg-white/60 p-10 text-center">
        <Play size={26} className="text-slate-300" />
        <p className="text-sm font-medium text-gray-500">点击「执行」查看脚本输出</p>
        <p className="max-w-md text-xs leading-5 text-gray-400">
          平台会把 <code className="rounded bg-slate-100 px-1 font-mono">result</code> 的每一行
          {' {列名: 值} '}对象写入数据资产湖；执行结果会在这里展示行数、列结构、数据样本与打印输出。
        </p>
      </div>
    )
  }

  const success = result.ok && result.format_valid
  return (
    <div className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4">
      {/* 状态头 */}
      <div className="flex flex-wrap items-center gap-2">
        {success ? (
          <span className="inline-flex items-center gap-1 rounded-lg border border-teal-200 bg-teal-50 px-2.5 py-1 text-xs font-medium text-teal-700">
            <CheckCircle2 size={12} />
            {resultKind === 'save' ? '保存成功' : '执行成功'} · 输出格式校验通过
          </span>
        ) : result.ok ? (
          <span className="inline-flex items-center gap-1 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">
            <AlertTriangle size={12} /> 执行成功，但输出格式不符合平台要求
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded-lg border border-red-200 bg-red-50 px-2.5 py-1 text-xs font-medium text-red-600">
            <XCircle size={12} /> {resultKind === 'save' ? '保存失败' : '执行失败'}
          </span>
        )}
      </div>

      {/* 成功指标 */}
      {result.ok && (
        <div className="grid grid-cols-3 gap-2 sm:max-w-md">
          {[
            { label: '输出行数', value: result.row_count.toLocaleString() },
            { label: '输出列数', value: String(result.columns.length) },
            { label: '执行耗时', value: `${(result.duration_ms / 1000).toFixed(1)}s` },
          ].map(item => (
            <div key={item.label} className="rounded-xl border border-slate-100 bg-slate-50/70 px-3 py-2">
              <div className="text-[11px] text-gray-400">{item.label}</div>
              <div className="font-mono text-lg font-semibold tabular-nums text-gray-800">{item.value}</div>
            </div>
          ))}
        </div>
      )}

      {result.error && (
        <p className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-700">{result.error}</p>
      )}
      {result.traceback && (
        <pre className="max-h-56 overflow-auto rounded-lg border border-red-100 bg-red-50/60 p-3.5 font-mono text-[11px] leading-5 text-red-900">{result.traceback}</pre>
      )}
      {result.format_error && (
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">{result.format_error}</p>
      )}

      {/* 列结构 */}
      {result.ok && result.columns.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {result.columns.map(col => (
            <span key={col} className="rounded border border-indigo-100 bg-indigo-50/60 px-2 py-0.5 font-mono text-[11px] text-indigo-700">
              {col}
            </span>
          ))}
        </div>
      )}

      {/* 数据样本 */}
      {result.ok && result.sample.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-slate-200">
          <table className="w-full text-xs">
            <thead className="border-b border-slate-200 bg-slate-50">
              <tr>
                {sampleColumns.map(col => (
                  <th key={col} className="whitespace-nowrap px-3 py-2 text-left font-medium text-gray-600">{col}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y">
              {result.sample.slice(0, PREVIEW_ROWS).map((row, idx) => (
                <tr key={idx} className="transition-colors hover:bg-slate-50/70">
                  {sampleColumns.map(col => (
                    <td key={col} className="max-w-[240px] truncate px-3 py-1.5 font-mono text-gray-700" title={cellText(row[col])}>
                      {cellText(row[col])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {result.row_count > PREVIEW_ROWS && (
            <p className="border-t border-slate-100 px-3 py-1.5 text-[11px] text-gray-400">
              仅展示前 {PREVIEW_ROWS} 行样本，共 {result.row_count.toLocaleString()} 行
            </p>
          )}
        </div>
      )}

      {result.ok && result.row_count === 0 && (
        <p className="text-xs text-gray-400">脚本输出 0 行。</p>
      )}

      {result.stdout && (
        <details className="rounded-xl border border-slate-200">
          <summary className="flex cursor-pointer items-center gap-1.5 px-3 py-2 text-xs text-gray-500 transition hover:text-black">
            <Terminal size={12} /> 脚本打印输出（stdout 尾部）
          </summary>
          <pre className="max-h-56 overflow-auto border-t border-slate-200 bg-slate-50 p-3.5 font-mono text-[11px] leading-5 text-slate-700">{result.stdout}</pre>
        </details>
      )}
    </div>
  )
}
