import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  X, Loader2, CheckCircle2, XCircle, AlertTriangle, Table2,
  KeyRound, ArrowRight, Database, FlaskConical,
} from 'lucide-react'
import pipelinesApi from '@/api/v2/pipelines'
import type { Pipeline, DryRunResult } from '@/api/v2/pipelines'

/**
 * 列表页「执行」弹窗：试运行（真实执行、不写资产湖）→ 展示产物预览与
 * 入湖闸门预检（主键契约/列漂移）→ 用户确认后按原样保存入湖。
 */
export default function RunPreviewModal({ pipeline, onClose, onSaved }: {
  pipeline: Pipeline
  onClose: () => void
  onSaved: () => void
}) {
  const navigate = useNavigate()
  const [phase, setPhase] = useState<'running' | 'preview' | 'saving' | 'saved' | 'error'>('running')
  const [result, setResult] = useState<DryRunResult | null>(null)
  const [error, setError] = useState('')
  const [savedRows, setSavedRows] = useState(0)

  const runPreview = async () => {
    setPhase('running')
    setError('')
    try {
      const res = await pipelinesApi.dryRun(pipeline.id)
      setResult(res)
      setPhase('preview')
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setError(err?.detail || err?.message || '试运行失败')
      setPhase('error')
    }
  }

  useEffect(() => { runPreview() }, [])

  const handleSave = async () => {
    if (!result) return
    setPhase('saving')
    try {
      const res = await pipelinesApi.commitDryRun(pipeline.id, result.dry_run_id)
      setSavedRows(res.lake_rows)
      setPhase('saved')
      onSaved()
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setError(err?.detail || err?.message || '入湖失败')
      setPhase('error')
    }
  }

  const isN8n = (result?.engine || pipeline.engine) === 'n8n'

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-6" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl w-[720px] max-w-full max-h-[85vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-start justify-between px-5 pt-4 pb-3 border-b border-gray-100">
          <div>
            <h3 className="font-semibold text-gray-900 flex items-center gap-2">
              <FlaskConical size={16} className="text-[var(--color-nav-bg)]" />
              执行流水线「{pipeline.name}」
            </h3>
            <p className="text-xs text-gray-400 mt-0.5">
              先试运行看输出，确认无误后再写入资产湖{isN8n ? '（n8n 流水线的试运行会真实触发其工作流）' : ''}
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-black shrink-0 mt-0.5">
            <X size={16} />
          </button>
        </div>

        {/* 内容 */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {phase === 'running' && (
            <div className="py-14 text-center text-gray-500 text-sm space-y-3">
              <Loader2 size={26} className="animate-spin mx-auto text-[var(--color-nav-bg)]" />
              <p>正在执行采集与加工（不写资产湖）...</p>
            </div>
          )}

          {phase === 'error' && (
            <div className="py-8 space-y-4">
              <div className="flex items-start gap-2.5 p-3.5 bg-red-50 border border-red-200 rounded-lg text-sm text-red-600">
                <XCircle size={16} className="shrink-0 mt-0.5" />
                <span className="break-all">{error}</span>
              </div>
              <div className="text-center">
                <button onClick={runPreview}
                  className="px-4 py-2 text-sm border border-gray-200 rounded-lg hover:bg-gray-50">
                  重新试运行
                </button>
              </div>
            </div>
          )}

          {phase === 'saved' && (
            <div className="py-12 text-center space-y-3">
              <CheckCircle2 size={34} className="mx-auto text-emerald-500" />
              <p className="text-sm font-medium text-gray-900">已保存到数据资产湖</p>
              <p className="text-xs text-gray-500">共写入 {savedRows} 行，生成新的数据集版本</p>
              <button
                onClick={() => navigate(`/data/structured?pipeline=${encodeURIComponent(pipeline.name)}`)}
                className="inline-flex items-center gap-1 text-sm text-[var(--color-nav-bg)] hover:underline"
              >
                去资产湖查看产物 <ArrowRight size={13} />
              </button>
            </div>
          )}

          {(phase === 'preview' || phase === 'saving') && result && (
            <div className="space-y-4">
              {/* 总览 */}
              <div className="flex items-center gap-2 text-sm text-gray-700">
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-gray-50 border border-gray-200 rounded-lg text-xs">
                  输入 <b>{result.rows_in}</b> 行 → 输出 <b>{result.rows_out}</b> 行
                </span>
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-gray-50 border border-gray-200 rounded-lg text-xs">
                  <Database size={11} /> {result.outputs.length} 个产物数据集
                </span>
                <span className="text-[11px] text-amber-600 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1">
                  尚未写入资产湖
                </span>
              </div>

              {/* 逐产物 */}
              {result.outputs.map(o => (
                <div key={o.dataset_name} className="border border-gray-200 rounded-xl overflow-hidden">
                  <div className="flex items-center gap-2 px-3.5 py-2.5 bg-gray-50/70 border-b border-gray-100 flex-wrap">
                    <Table2 size={13} className="text-gray-400 shrink-0" />
                    <span className="text-sm font-medium text-gray-900">{o.dataset_name}</span>
                    <span className="text-xs text-gray-400">{o.rows_out} 行 · {o.columns.length} 列</span>
                    {o.pk ? (
                      <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-px rounded border border-blue-200 bg-blue-50 text-blue-600"
                        title={o.pk_source === 'lake' ? '资产湖已固化的主键契约' : '本次任务声明的主键'}>
                        <KeyRound size={9} /> 主键 {o.pk}
                      </span>
                    ) : (
                      <span className="text-[10px] px-1.5 py-px rounded border border-gray-200 bg-gray-50 text-gray-400"
                        title="未声明主键：本体实例身份将按整行内容推断，建议在任务池为该流水线的调度任务设置主键">
                        无主键契约
                      </span>
                    )}
                    <span className="ml-auto text-[10px] text-gray-400">
                      {o.dataset_exists ? '追加新版本' : '将新建数据集'}
                    </span>
                  </div>

                  {/* 闸门预检 */}
                  {o.gate_error && (
                    <div className="flex items-start gap-2 px-3.5 py-2.5 bg-red-50 border-b border-red-100 text-xs text-red-600">
                      <XCircle size={13} className="shrink-0 mt-0.5" />
                      <span className="break-all">入湖校验不通过：{o.gate_error}</span>
                    </div>
                  )}
                  {!o.gate_error && o.warnings.length > 0 && (
                    <div className="px-3.5 py-2 bg-amber-50/70 border-b border-amber-100 space-y-1">
                      {o.warnings.map((w, i) => (
                        <p key={i} className="flex items-start gap-1.5 text-[11px] text-amber-700">
                          <AlertTriangle size={11} className="shrink-0 mt-0.5" /> {w}
                        </p>
                      ))}
                    </div>
                  )}

                  {/* 样例行 */}
                  {o.sample.length > 0 ? (
                    <div className="overflow-x-auto max-h-52 overflow-y-auto">
                      <table className="w-full text-xs">
                        <thead className="bg-white sticky top-0">
                          <tr className="border-b border-gray-100">
                            {o.columns.slice(0, 12).map(c => (
                              <th key={c} className="text-left px-3 py-1.5 font-medium text-gray-500 whitespace-nowrap">{c}</th>
                            ))}
                            {o.columns.length > 12 && <th className="px-3 py-1.5 text-gray-300 font-normal">+{o.columns.length - 12} 列</th>}
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-50">
                          {o.sample.slice(0, 8).map((row, ri) => (
                            <tr key={ri}>
                              {o.columns.slice(0, 12).map(c => (
                                <td key={c} className="px-3 py-1.5 text-gray-700 whitespace-nowrap max-w-[180px] truncate"
                                  title={String(row[c] ?? '')}>
                                  {String(row[c] ?? '')}
                                </td>
                              ))}
                              {o.columns.length > 12 && <td className="px-3 py-1.5 text-gray-300">…</td>}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {o.rows_out > 8 && (
                        <p className="text-[10px] text-gray-300 px-3 py-1.5">预览前 8 行，共 {o.rows_out} 行</p>
                      )}
                    </div>
                  ) : (
                    <p className="text-xs text-gray-300 px-3.5 py-3">本产物 0 行输出</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 底部操作 */}
        {(phase === 'preview' || phase === 'saving') && result && (
          <div className="flex items-center gap-3 px-5 py-3.5 border-t border-gray-100">
            <p className="text-[11px] text-gray-400 flex-1">
              {result.can_save
                ? '保存后将按流水线入湖通道写入（主键契约校验 + 版本化），不会重新执行流水线'
                : result.rows_out === 0
                  ? '输出为 0 行，无内容可保存'
                  : '存在未通过入湖校验的产物，修正流水线或主键配置后重新试运行'}
            </p>
            <button onClick={onClose}
              className="px-4 py-2 text-sm border border-gray-200 rounded-lg hover:bg-gray-50 shrink-0">
              取消（不入湖）
            </button>
            <button
              onClick={handleSave}
              disabled={!result.can_save || phase === 'saving'}
              className="flex items-center gap-1.5 px-4 py-2 bg-[var(--color-nav-bg)] text-white text-sm font-medium rounded-lg hover:opacity-90 disabled:opacity-40 shrink-0"
            >
              {phase === 'saving' && <Loader2 size={13} className="animate-spin" />}
              {phase === 'saving' ? '写入中...' : '保存到资产湖'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
