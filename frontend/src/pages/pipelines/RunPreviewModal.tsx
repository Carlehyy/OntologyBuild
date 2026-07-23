import { useEffect, useState } from 'react'
import {
  X, Loader2, CheckCircle2, XCircle, Table2, ArrowRight,
  FlaskConical, RefreshCw,
} from 'lucide-react'
import pipelinesApi from '@/api/v2/pipelines'
import type { Pipeline, DryRunResult } from '@/api/v2/pipelines'
import { pipelineFileRefsIn } from '@/api/fileAssets'
import FileRefActions from '@/components/pipelines/FileRefActions'
import { useToast } from '@/components/ui/Toast'

function displayValue(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'object') {
    try { return JSON.stringify(value) } catch { return String(value) }
  }
  return String(value)
}

/**
 * 列表页「试执行」弹窗：只负责真实执行与输出预览。
 * 试执行结果不提供入湖入口，正式入湖统一由数据任务池负责。
 */
export default function RunPreviewModal({ pipeline, onClose }: {
  pipeline: Pipeline
  onClose: () => void
}) {
  const { toast } = useToast()
  const [phase, setPhase] = useState<'running' | 'preview' | 'error'>('running')
  const [result, setResult] = useState<DryRunResult | null>(null)
  const [error, setError] = useState('')

  const runPreview = async () => {
    setPhase('running')
    setError('')
    try {
      const res = await pipelinesApi.dryRun(pipeline.id)
      setResult(res)
      setPhase('preview')
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      const message = err?.detail || err?.message || '请稍后重试。'
      setError(message)
      setPhase('error')
      toast({
        tone: 'error',
        title: '流水线试执行失败',
        description: message,
      })
    }
  }

  useEffect(() => { void runPreview() }, [])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4 backdrop-blur-[2px] sm:p-6" onClick={onClose}>
      <div
        className="flex max-h-[88vh] w-[820px] max-w-full flex-col overflow-hidden rounded-2xl border border-white/70 bg-white shadow-[0_28px_90px_rgba(15,23,42,0.24)]"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-slate-100 px-6 py-4">
          <div className="min-w-0">
            <h3 className="flex items-center gap-2.5 text-base font-semibold tracking-tight text-slate-950">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-teal-700 text-white">
                <FlaskConical size={15} />
              </span>
              <span className="truncate">试执行「{pipeline.name}」</span>
            </h3>
            <p className="ml-10 mt-0.5 text-xs text-slate-500">
              执行流水线并查看本次输出；数据入湖统一由数据任务池负责
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="关闭"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-slate-400 transition hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"
          >
            <X size={18} />
          </button>
        </div>

        <div className="scrollbar-thin flex-1 overflow-y-auto px-6 py-5">
          {phase === 'running' && (
            <div className="space-y-3 py-16 text-center text-sm text-slate-500">
              <Loader2 size={28} className="mx-auto animate-spin text-teal-700" />
              <p>正在执行流水线并整理输出预览…</p>
            </div>
          )}

          {phase === 'error' && (
            <div className="flex flex-col items-center py-14 text-center">
              <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-red-50 text-red-600">
                <XCircle size={21} />
              </span>
              <p className="mt-3 text-sm font-medium text-slate-900">本次试执行未完成</p>
              <p className="mt-1 max-w-lg break-all text-xs leading-5 text-slate-500">{error}</p>
              <button
                onClick={() => void runPreview()}
                className="mt-5 inline-flex items-center gap-1.5 rounded-xl border border-slate-200 px-3.5 py-2 text-sm font-medium text-slate-700 transition hover:border-teal-200 hover:bg-teal-50 hover:text-teal-800"
              >
                <RefreshCw size={13} /> 重新执行
              </button>
            </div>
          )}

          {phase === 'preview' && result && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 py-1.5 text-xs font-medium text-emerald-700">
                  <CheckCircle2 size={12} /> 执行完成
                </span>
                <span className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-700">
                  输入 <b>{result.rows_in}</b> 行 <ArrowRight size={11} /> 输出 <b>{result.rows_out}</b> 行
                </span>
                <span className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-700">
                  <Table2 size={11} /> {result.outputs.length} 个输出结果
                </span>
              </div>

              {result.outputs.map((output, outputIndex) => (
                <section key={`${output.dataset_name}-${outputIndex}`} className="overflow-hidden rounded-xl border border-slate-200">
                  <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 bg-slate-50/70 px-3.5 py-2.5">
                    <Table2 size={13} className="shrink-0 text-slate-400" />
                    <span className="text-sm font-medium text-slate-900">
                      {output.dataset_name || `输出 ${outputIndex + 1}`}
                    </span>
                    <span className="text-xs text-slate-400">
                      {output.rows_out} 行 · {output.columns.length} 列
                    </span>
                  </div>

                  {output.sample.length > 0 ? (
                    <div className="max-h-64 overflow-auto">
                      <table className="w-full text-xs">
                        <thead className="sticky top-0 border-b border-slate-100 bg-white">
                          <tr>
                            {output.columns.slice(0, 12).map(column => (
                              <th key={column} className="whitespace-nowrap px-3 py-2 text-left font-medium text-slate-500">
                                {column}
                              </th>
                            ))}
                            {output.columns.length > 12 && (
                              <th className="px-3 py-2 font-normal text-slate-300">+{output.columns.length - 12} 列</th>
                            )}
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-50">
                          {output.sample.slice(0, 8).map((row, rowIndex) => (
                            <tr key={rowIndex} className="hover:bg-slate-50/70">
                              {output.columns.slice(0, 12).map(column => (
                                <td key={column} className="max-w-[220px] px-3 py-2 text-slate-700">
                                  {pipelineFileRefsIn(row[column]).length > 0 ? (
                                    <div className="flex max-w-[300px] flex-col items-start gap-1">
                                      {pipelineFileRefsIn(row[column]).slice(0, 4).map(ref => (
                                        <FileRefActions key={ref.id} file={ref} />
                                      ))}
                                    </div>
                                  ) : (
                                    <span
                                      className="block max-w-[180px] truncate whitespace-nowrap"
                                      title={displayValue(row[column])}
                                    >
                                      {displayValue(row[column])}
                                    </span>
                                  )}
                                </td>
                              ))}
                              {output.columns.length > 12 && <td className="px-3 py-2 text-slate-300">…</td>}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {output.rows_out > 8 && (
                        <p className="border-t border-slate-50 px-3 py-2 text-[10px] text-slate-400">
                          当前展示前 8 行，共 {output.rows_out} 行
                        </p>
                      )}
                    </div>
                  ) : (
                    <p className="px-3.5 py-5 text-center text-xs text-slate-400">本次输出为空</p>
                  )}
                </section>
              ))}
            </div>
          )}
        </div>

        {phase === 'preview' && result && (
          <div className="flex items-center gap-3 border-t border-slate-100 bg-white px-6 py-4">
            <p className="flex-1 text-[11px] leading-5 text-slate-400">
              试执行只验证流水线输出，不会创建或更新资产湖数据。正式入湖请在数据任务池配置并执行任务。
            </p>
            <button
              onClick={() => void runPreview()}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-xl border border-slate-200 px-3.5 py-2 text-sm font-medium text-slate-700 transition hover:border-teal-200 hover:bg-teal-50 hover:text-teal-800"
            >
              <RefreshCw size={13} /> 重新执行
            </button>
            <button
              onClick={onClose}
              className="shrink-0 rounded-xl bg-teal-700 px-4 py-2 text-sm font-medium text-white transition hover:bg-teal-800 active:translate-y-px"
            >
              完成
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
