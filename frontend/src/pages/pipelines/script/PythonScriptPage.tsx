import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft, Play, Save, Loader2, CheckCircle2, XCircle, AlertTriangle,
  FileCode2, Terminal,
} from 'lucide-react'
import pipelinesApi from '@/api/v2/pipelines'
import type { Pipeline, ScriptExecutionResult } from '@/api/v2/pipelines'
import { useToast } from '@/components/ui/Toast'
import { PYTHON_SCRIPT_TEMPLATE } from './template'

const PREVIEW_ROWS = 20

function cellText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export default function PythonScriptPage() {
  const { pipelineId } = useParams<{ pipelineId: string }>()
  const navigate = useNavigate()
  const { toast } = useToast()

  const [pipeline, setPipeline] = useState<Pipeline | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [script, setScript] = useState('')
  const [executing, setExecuting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<ScriptExecutionResult | null>(null)
  const [resultKind, setResultKind] = useState<'execute' | 'save'>('execute')
  // 客户端门槛：只有「当前编辑器内容」执行通过且格式合法，保存才可点；
  // 服务端保存时仍会重跑复验（双重保障）。
  const [validatedScript, setValidatedScript] = useState<string | null>(null)

  const isPublished = pipeline?.status === 'published'
  const wrongEngine = pipeline && (pipeline.definition as { engine?: string } | null)?.engine !== 'python'
  const scriptDirty = validatedScript === null || script !== validatedScript
  const canSave = !!pipeline && !isPublished && !executing && !saving && !scriptDirty && script.trim().length > 0

  const load = useCallback(async () => {
    if (!pipelineId) return
    setLoading(true)
    setLoadError('')
    try {
      const pl = await pipelinesApi.get(pipelineId)
      setPipeline(pl)
      const saved = (pl.definition as { python?: { script?: string } } | null)?.python?.script || ''
      setScript(saved || PYTHON_SCRIPT_TEMPLATE)
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

  const handleExecute = async () => {
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
        ok: false,
        format_valid: false,
        format_error: null,
        row_count: 0,
        columns: [],
        sample: [],
        stdout: '',
        error: err?.detail || err?.message || '执行请求失败',
        traceback: '',
        duration_ms: 0,
      })
    } finally {
      setExecuting(false)
    }
  }

  const handleSave = async () => {
    if (!pipeline || !canSave) return
    setSaving(true)
    setResultKind('save')
    try {
      const res = await pipelinesApi.saveScript(pipeline.id, script)
      setPipeline(res.pipeline)
      setResult(res.execution)
      toast({ tone: 'success', title: '脚本已保存', description: `输出 ${res.execution.row_count} 行，格式校验通过。` })
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setResult({
        ok: false,
        format_valid: false,
        format_error: null,
        row_count: 0,
        columns: [],
        sample: [],
        stdout: '',
        error: err?.detail || err?.message || '保存请求失败',
        traceback: '',
        duration_ms: 0,
      })
    } finally {
      setSaving(false)
    }
  }

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
    return <div className="text-gray-400 text-sm p-8 text-center">加载中...</div>
  }
  if (loadError || !pipeline) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-16 text-center">
        <AlertTriangle size={28} className="text-amber-500" />
        <p className="text-sm text-gray-600">{loadError || '流水线不存在'}</p>
        <button
          onClick={() => navigate('/data/pipelines')}
          className="px-4 py-2 rounded-lg border text-sm hover:bg-gray-50"
        >
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
        <button
          onClick={() => navigate('/data/pipelines')}
          className="px-4 py-2 rounded-lg border text-sm hover:bg-gray-50"
        >
          返回数据流水线
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-4 pb-4">
      {/* 页头 */}
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3">
        <button
          onClick={() => navigate('/data/pipelines')}
          className="flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
        >
          <ArrowLeft size={13} /> 返回
        </button>
        <div className="min-w-0">
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
            <p className="truncate text-xs text-gray-400 mt-0.5">{pipeline.description}</p>
          )}
        </div>
      </div>

      {isPublished && (
        <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <AlertTriangle size={13} className="shrink-0" />
          流水线已发布，脚本已封版只读；仍可执行脚本核对输出。如需变更，请新建流水线。
        </div>
      )}

      {/* 脚本编辑区 */}
      <div className="rounded-2xl border border-slate-200 bg-white p-4">
        <div className="mb-2 flex items-center justify-between">
          <label className="text-xs font-medium text-gray-600">
            Python 脚本（最终结果请赋值给变量 <code className="rounded bg-slate-100 px-1">result</code>，list[dict] 行式结构）
          </label>
          <span className="text-[11px] text-gray-400">执行环境自带 requests / httpx / pandas / pymysql 等依赖</span>
        </div>
        <textarea
          value={script}
          onChange={e => setScript(e.target.value)}
          readOnly={isPublished}
          spellCheck={false}
          className={`h-[42vh] min-h-[280px] w-full resize-y rounded-xl border border-slate-200 bg-slate-950 p-4 font-mono text-xs leading-5 text-slate-100 outline-none transition focus:border-teal-500 focus:ring-4 focus:ring-teal-500/10 ${
            isPublished ? 'opacity-80' : ''
          }`}
        />
      </div>

      {/* 操作按钮 */}
      <div className="flex items-center gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3">
        <button
          onClick={handleExecute}
          disabled={executing || saving}
          className="flex items-center gap-1.5 rounded-xl bg-teal-700 px-4 py-2 text-sm font-medium text-white transition hover:bg-teal-800 disabled:opacity-50"
        >
          {executing ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
          {executing ? '执行中...' : '执行'}
        </button>
        {!isPublished && (
          <button
            onClick={handleSave}
            disabled={!canSave}
            title={scriptDirty ? '脚本已修改或尚未执行：请先执行并通过输出格式校验' : '保存脚本（平台会重新执行并复验输出格式）'}
            className="flex items-center gap-1.5 rounded-xl border border-teal-700 px-4 py-2 text-sm font-medium text-teal-700 transition hover:bg-teal-50 disabled:cursor-not-allowed disabled:border-gray-200 disabled:text-gray-400"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            {saving ? '校验并保存中...' : '保存'}
          </button>
        )}
        <p className="text-xs text-gray-400">
          {isPublished
            ? '已发布流水线脚本只读'
            : scriptDirty
              ? '保存前需先执行：输出通过平台格式校验（list[dict] 行式结构）后保存才可点'
              : '格式校验已通过，可以保存；保存时平台会重新执行并复验'}
        </p>
      </div>

      {/* 执行结果 */}
      {result && (
        <div className="rounded-2xl border border-slate-200 bg-white p-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {result.ok && result.format_valid ? (
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
            {result.ok && (
              <span className="text-xs text-gray-500">
                {result.row_count} 行 · {result.columns.length} 列 · 耗时 {(result.duration_ms / 1000).toFixed(1)}s
              </span>
            )}
          </div>

          {result.error && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{result.error}</p>
          )}
          {result.traceback && (
            <pre className="max-h-56 overflow-auto rounded-lg bg-slate-950 p-3.5 font-mono text-[11px] leading-5 text-red-200">{result.traceback}</pre>
          )}
          {result.format_error && (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">{result.format_error}</p>
          )}

          {result.ok && result.columns.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {result.columns.map(col => (
                <span key={col} className="rounded border border-slate-200 bg-slate-50 px-2 py-0.5 font-mono text-[11px] text-gray-600">
                  {col}
                </span>
              ))}
            </div>
          )}

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
                    <tr key={idx}>
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
                  仅展示前 {PREVIEW_ROWS} 行样本，共 {result.row_count} 行
                </p>
              )}
            </div>
          )}

          {result.ok && result.row_count === 0 && (
            <p className="text-xs text-gray-400">脚本输出 0 行。</p>
          )}

          {result.stdout && (
            <details className="rounded-xl border border-slate-200">
              <summary className="flex cursor-pointer items-center gap-1.5 px-3 py-2 text-xs text-gray-500 hover:text-black">
                <Terminal size={12} /> 脚本打印输出（stdout 尾部）
              </summary>
              <pre className="max-h-56 overflow-auto border-t border-slate-200 bg-slate-950 p-3.5 font-mono text-[11px] leading-5 text-slate-100">{result.stdout}</pre>
            </details>
          )}
        </div>
      )}
    </div>
  )
}
