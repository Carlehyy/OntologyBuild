import { useState, useEffect, useCallback } from 'react'
import {
  X, Loader2, CheckCircle2, XCircle, AlertTriangle, ChevronLeft, ChevronRight,
  KeyRound, Eye, Save, Rocket, Info,
} from 'lucide-react'
import pipelinesApi from '@/api/v2/pipelines'
import type { Pipeline, DryRunResult, ColumnDefinition, ValidateDefinitionsResult } from '@/api/v2/pipelines'

const FIELD_TYPES = ['string', 'int', 'float', 'bool', 'datetime'] as const

interface Props {
  pipeline: Pipeline
  onClose: () => void
  onSaved: () => void
}

export default function PipelineEditWizard({ pipeline, onClose, onSaved }: Props) {
  const isPublished = pipeline.status === 'published'
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1)

  // ── 阶段 1: 流水线信息 ──
  const [name, setName] = useState(pipeline.name || '')
  const [description, setDescription] = useState(pipeline.description || '')

  // ── 阶段 2: 执行预览 ──
  const [dryRunPhase, setDryRunPhase] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [dryRunResult, setDryRunResult] = useState<DryRunResult | null>(null)
  const [dryRunError, setDryRunError] = useState('')
  // 缓存 columns 和 sample，让 isPublished 时也能看
  const [cachedColumns, setCachedColumns] = useState<string[]>([])
  const [cachedSample, setCachedSample] = useState<Record<string, unknown>[]>([])
  const [totalRows, setTotalRows] = useState(0)

  // ── 阶段 3: 设置主键组 ──
  const [columnDefs, setColumnDefs] = useState<ColumnDefinition[]>([])
  const [validateResult, setValidateResult] = useState<ValidateDefinitionsResult | null>(null)
  const [validating, setValidating] = useState(false)

  // ── 阶段 4: 确认配置 ──
  const [saving, setSaving] = useState(false)
  const [publishing, setPublishing] = useState(false)

  // ── 初始化 column definitions ──
  const initColumnDefs = useCallback((columns: string[], existing?: ColumnDefinition[] | null) => {
    if (existing && existing.length > 0) {
      // 合并：已有定义保留，新列用默认值
      const existingMap = new Map(existing.map(d => [d.field_key, d]))
      return columns.map(col => existingMap.get(col) || {
        field_key: col,
        field_name: col,
        field_type: 'string',
        is_primary_key: false,
        nullable: true,
      })
    }
    return columns.map(col => ({
      field_key: col,
      field_name: col,
      field_type: 'string',
      is_primary_key: false,
      nullable: true,
    }))
  }, [])

  // ── 阶段 2: 执行 dryRun ──
  const runDryRun = async () => {
    setDryRunPhase('running')
    setDryRunError('')
    try {
      const res = await pipelinesApi.dryRun(pipeline.id, 100)
      setDryRunResult(res)
      const allCols: string[] = []
      const allSample: Record<string, unknown>[] = []
      let total = 0
      for (const o of res.outputs) {
        for (const c of o.columns) {
          if (!allCols.includes(c)) allCols.push(c)
        }
        // 合并 sample
        if (o.sample && o.sample.length > 0) {
          for (const row of o.sample) {
            allSample.push(row)
          }
        }
        total += o.rows_out
      }
      setCachedColumns(allCols)
      setCachedSample(allSample)
      setTotalRows(total)
      setColumnDefs(initColumnDefs(allCols, pipeline.column_definitions))
      setDryRunPhase('done')
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setDryRunError(err?.detail || err?.message || '试运行失败')
      setDryRunPhase('error')
    }
  }

  // 如果是已发布且还没执行过，进阶段 2 时自动触发一次
  useEffect(() => {
    if (step === 2 && dryRunPhase === 'idle') {
      // 已发布：用已有的 column_definitions 初始化
      if (isPublished && pipeline.column_definitions) {
        const cols = pipeline.column_definitions.map((d: ColumnDefinition) => d.field_key)
        setCachedColumns(cols)
        setCachedSample([])
        setTotalRows(0)
        setColumnDefs(pipeline.column_definitions)
        setDryRunPhase('done') // 只读模式，不需要真的跑
      } else {
        runDryRun()
      }
    }
  }, [step])  // eslint-disable-line react-hooks/exhaustive-deps

  // ── 阶段 3: 校验 ──
  const runValidate = async () => {
    setValidating(true)
    setValidateResult(null)
    try {
      const res = await pipelinesApi.validateDefinitions(pipeline.id, { column_definitions: columnDefs }, 100)
      setValidateResult(res)
      if (res.valid) {
        setStep(4)
      }
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setValidateResult({ valid: false, errors: [{ field_key: '', message: err?.detail || err?.message || '校验请求失败', severity: 'error' }] })
    } finally {
      setValidating(false)
    }
  }

  // ── 阶段 4: 保存 / 发布 ──
  const handleSave = async () => {
    setSaving(true)
    try {
      await pipelinesApi.update(pipeline.id, {
        name,
        description,
        column_definitions: isPublished ? undefined : columnDefs,
      })
      onSaved()
      onClose()
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      alert(err?.detail || err?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handlePublish = async () => {
    setPublishing(true)
    try {
      // 先保存 column_definitions
      await pipelinesApi.update(pipeline.id, {
        name,
        description,
        column_definitions: columnDefs,
      })
      // 再发布
      await pipelinesApi.publish(pipeline.id)
      onSaved()
      onClose()
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      alert(err?.detail || err?.message || '发布失败')
    } finally {
      setPublishing(false)
    }
  }

  // ── 辅助 ──
  const pkFields = columnDefs.filter(d => d.is_primary_key)
  const hasErrors = validateResult && !validateResult.valid
  const errorMap = new Map((validateResult?.errors || []).map(e => [e.field_key, e]))

  const updateColDef = (index: number, patch: Partial<ColumnDefinition>) => {
    setColumnDefs(prev => prev.map((d, i) => i === index ? { ...d, ...patch } : d))
  }

  // ── 步骤指示器 ──
  const steps = [
    { num: 1, label: '流水线信息' },
    { num: 2, label: '执行预览' },
    { num: 3, label: '设置主键组' },
    { num: 4, label: '确认配置' },
  ]

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-6" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl w-[900px] max-w-full max-h-[90vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between px-5 pt-4 pb-3 border-b border-gray-100 shrink-0">
          <div>
            <h3 className="font-semibold text-gray-900">编辑流水线「{pipeline.name}」</h3>
            <p className="text-xs text-gray-400 mt-0.5">
              {isPublished ? '已发布 · 仅可修改名称和描述' : `状态：${pipeline.status}`}
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-black shrink-0">
            <X size={16} />
          </button>
        </div>

        {/* 步骤指示器 */}
        <div className="flex items-center px-6 py-3 gap-1 shrink-0 border-b border-gray-50">
          {steps.map((s, i) => (
            <div key={s.num} className="flex items-center gap-1">
              {i > 0 && <div className={`h-px w-6 ${step > i + 1 ? 'bg-[var(--color-nav-bg)]' : 'bg-gray-200'}`} />}
              <button
                disabled={s.num > step || (s.num === 3 && !cachedColumns.length)}
                onClick={() => setStep(s.num as 1 | 2 | 3 | 4)}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                  step === s.num
                    ? 'bg-[var(--color-nav-bg)] text-white'
                    : step > s.num
                      ? 'bg-emerald-50 text-emerald-700 cursor-pointer'
                      : 'bg-gray-100 text-gray-400 cursor-not-allowed'
                }`}
              >
                {step > s.num ? <CheckCircle2 size={12} /> : <span className="w-4 h-4 rounded-full border border-current flex items-center justify-center text-[10px]">{s.num}</span>}
                {s.label}
              </button>
            </div>
          ))}
        </div>

        {/* 内容区 */}
        <div className="flex-1 overflow-auto p-6">
          {/* ──────── 阶段 1: 流水线信息 ──────── */}
          {step === 1 && (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">流水线名称</label>
                <input
                  value={name}
                  onChange={e => setName(e.target.value)}
                  className="w-full px-3 py-2 border rounded-lg text-sm"
                  placeholder="输入流水线名称"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">描述</label>
                <textarea
                  value={description}
                  onChange={e => setDescription(e.target.value)}
                  className="w-full px-3 py-2 border rounded-lg text-sm h-24 resize-none"
                  placeholder="输入流水线描述（可选）"
                />
              </div>
            </div>
          )}

          {/* ──────── 阶段 2: 执行预览 ──────── */}
          {step === 2 && (
            <div className="space-y-3">
              {dryRunPhase === 'running' && (
                <div className="flex items-center gap-3 text-blue-600 py-8 justify-center">
                  <Loader2 size={20} className="animate-spin" />
                  <span className="text-sm">正在执行流水线，获取预览数据…</span>
                </div>
              )}

              {dryRunPhase === 'error' && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-2">
                  <XCircle size={16} className="text-red-500 mt-0.5 shrink-0" />
                  <div>
                    <p className="text-sm text-red-700 font-medium">试运行失败</p>
                    <p className="text-xs text-red-600 mt-0.5">{dryRunError}</p>
                    <button
                      onClick={runDryRun}
                      className="mt-2 text-xs text-red-600 underline hover:text-red-800"
                    >
                      重试
                    </button>
                  </div>
                </div>
              )}

              {dryRunPhase === 'done' && !isPublished && (
                <div className="flex items-center justify-between text-xs text-gray-400 mb-1">
                  <span>共 {totalRows} 行，展示前 {cachedSample.length} 行{totalRows > 100 ? '（超出部分未加载）' : ''}</span>
                  <button
                    onClick={runDryRun}
                    className="text-[var(--color-nav-bg)] hover:underline flex items-center gap-1"
                  >
                    <Eye size={12} /> 重新执行
                  </button>
                </div>
              )}

              {dryRunPhase === 'done' && isPublished && (
                <div className="flex items-center gap-2 text-xs text-gray-400 mb-1">
                  <Info size={12} />
                  <span>已发布流水线，仅查看字段定义（不可修改）</span>
                </div>
              )}

              {dryRunPhase === 'done' && cachedColumns.length > 0 && (
                <div className="border rounded-lg overflow-hidden">
                  <div className="overflow-x-auto max-h-[400px]">
                    <table className="w-full min-w-[600px] text-xs table-fixed">
                      <thead className="bg-gray-50 border-b sticky top-0">
                        <tr>
                          <th className="text-left px-3 py-2 font-medium text-gray-600 w-10">#</th>
                          {cachedColumns.map(col => (
                            <th key={col} className="text-left px-3 py-2 font-medium text-gray-600 whitespace-nowrap" style={{ minWidth: 120 }}>
                              {col}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y">
                        {cachedSample.length === 0 ? (
                          <tr>
                            <td colSpan={cachedColumns.length + 1} className="px-3 py-8 text-center text-gray-400">
                              {isPublished ? '已发布流水线未缓存预览数据' : '暂无数据'}
                            </td>
                          </tr>
                        ) : (
                          cachedSample.slice(0, 100).map((row, i) => (
                            <tr key={i} className="hover:bg-gray-50">
                              <td className="px-3 py-1.5 text-gray-400">{i + 1}</td>
                              {cachedColumns.map(col => (
                                <td key={col} className="px-3 py-1.5 text-gray-700 whitespace-nowrap overflow-hidden text-ellipsis" title={String(row[col] ?? '')}>
                                  {row[col] === null || row[col] === undefined ? (
                                    <span className="text-gray-300 italic">null</span>
                                  ) : (
                                    String(row[col])
                                  )}
                                </td>
                              ))}
                            </tr>
                          ))
                        )}
                        {cachedSample.length > 100 && (
                          <tr>
                            <td colSpan={cachedColumns.length + 1} className="px-3 py-2 text-center text-gray-400 text-xs">
                              仅展示前 100 行，完整数据在保存时写入资产湖
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {dryRunPhase === 'done' && cachedColumns.length === 0 && (
                <div className="text-center py-8 text-gray-400 text-sm">
                  <AlertTriangle size={24} className="mx-auto mb-2 opacity-40" />
                  <p>流水线执行未产生任何数据列</p>
                </div>
              )}
            </div>
          )}

          {/* ──────── 阶段 3: 设置主键组 ──────── */}
          {step === 3 && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-xs text-gray-400">
                <KeyRound size={12} />
                <span>定义每个字段的元数据：入湖列名、显示名称、类型、是否主键、是否允许空值</span>
                {isPublished && <span className="text-amber-600 font-medium">（已发布 · 只读）</span>}
              </div>

              {pkFields.length > 0 && (
                <div className="bg-blue-50 border border-blue-200 rounded-lg px-3 py-2 text-xs text-blue-700">
                  主键：{pkFields.map(d => d.field_key).join('、')}
                  {pkFields.length > 1 && '（复合主键）'}
                </div>
              )}

              <div className="border rounded-lg overflow-x-auto">
                <table className="w-full min-w-[700px] text-xs">
                  <thead className="bg-gray-50 border-b">
                    <tr>
                      <th className="text-left px-3 py-2 font-medium text-gray-600 w-28">原始列名</th>
                      <th className="text-left px-3 py-2 font-medium text-gray-600 w-32">字段标识</th>
                      <th className="text-left px-3 py-2 font-medium text-gray-600 w-32">字段名称</th>
                      <th className="text-left px-3 py-2 font-medium text-gray-600 w-24">字段类型</th>
                      <th className="text-center px-3 py-2 font-medium text-gray-600 w-16">主键</th>
                      <th className="text-center px-3 py-2 font-medium text-gray-600 w-16">允许空</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {columnDefs.map((d, i) => {
                      const fieldErrors = errorMap.get(d.field_key)
                      const globalErrors = (validateResult?.errors || []).filter(e => e.field_key === '' && d.is_primary_key)
                      const allErrors = [fieldErrors, ...globalErrors].filter(Boolean)
                      return (
                        <tr key={d.field_key} className={allErrors.length > 0 ? 'bg-red-50' : 'hover:bg-gray-50'}>
                          <td className="px-3 py-1.5 text-gray-700 font-mono text-[11px]">{d.field_key}</td>
                          <td className="px-1 py-1">
                            <input
                              value={d.field_key}
                              disabled={isPublished}
                              onChange={e => updateColDef(i, { field_key: e.target.value })}
                              className={`w-full px-2 py-1 border rounded text-xs ${isPublished ? 'bg-gray-50 text-gray-400 cursor-not-allowed' : ''}`}
                            />
                          </td>
                          <td className="px-1 py-1">
                            <input
                              value={d.field_name}
                              disabled={isPublished}
                              onChange={e => updateColDef(i, { field_name: e.target.value })}
                              className={`w-full px-2 py-1 border rounded text-xs ${isPublished ? 'bg-gray-50 text-gray-400 cursor-not-allowed' : ''}`}
                            />
                          </td>
                          <td className="px-1 py-1">
                            <select
                              value={d.field_type}
                              disabled={isPublished}
                              onChange={e => updateColDef(i, { field_type: e.target.value })}
                              className={`w-full px-2 py-1 border rounded text-xs ${isPublished ? 'bg-gray-50 text-gray-400 cursor-not-allowed' : ''}`}
                            >
                              {FIELD_TYPES.map(t => (
                                <option key={t} value={t}>{t}</option>
                              ))}
                            </select>
                          </td>
                          <td className="px-1 py-1 text-center">
                            <input
                              type="checkbox"
                              checked={d.is_primary_key}
                              disabled={isPublished}
                              onChange={e => updateColDef(i, { is_primary_key: e.target.checked })}
                              className={isPublished ? 'opacity-50 cursor-not-allowed' : ''}
                            />
                          </td>
                          <td className="px-1 py-1 text-center">
                            <input
                              type="checkbox"
                              checked={d.nullable}
                              disabled={isPublished}
                              onChange={e => updateColDef(i, { nullable: e.target.checked })}
                              className={isPublished ? 'opacity-50 cursor-not-allowed' : ''}
                            />
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              {/* 校验错误展示 */}
              {hasErrors && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-3 space-y-1">
                  {(validateResult?.errors || []).map((e, i) => (
                    <div key={i} className="flex items-start gap-1.5 text-xs">
                      <XCircle size={12} className="text-red-500 mt-0.5 shrink-0" />
                      <span className="text-red-700">
                        {e.field_key && <span className="font-mono mr-1">[{e.field_key}]</span>}
                        {e.message}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {validateResult?.valid && (
                <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 flex items-center gap-2 text-xs text-emerald-700">
                  <CheckCircle2 size={14} />
                  校验通过，可以进入下一步
                </div>
              )}
            </div>
          )}

          {/* ──────── 阶段 4: 确认配置 ──────── */}
          {step === 4 && (
            <div className="space-y-4">
              {/* 信息汇总 */}
              <div className="grid grid-cols-2 gap-3">
                <div className="border rounded-lg p-3">
                  <p className="text-xs text-gray-400 mb-1">流水线名称</p>
                  <p className="text-sm font-medium text-gray-800">{name || '—'}</p>
                </div>
                <div className="border rounded-lg p-3">
                  <p className="text-xs text-gray-400 mb-1">描述</p>
                  <p className="text-sm text-gray-800">{description || '—'}</p>
                </div>
                <div className="border rounded-lg p-3">
                  <p className="text-xs text-gray-400 mb-1">列数</p>
                  <p className="text-sm font-medium text-gray-800">{columnDefs.length}</p>
                </div>
                <div className="border rounded-lg p-3">
                  <p className="text-xs text-gray-400 mb-1">主键</p>
                  <p className="text-sm font-medium text-gray-800">
                    {pkFields.length > 0 ? pkFields.map(d => d.field_key).join('、') : '未设置'}
                  </p>
                </div>
              </div>

              {/* 字段定义预览表 */}
              <div>
                <p className="text-xs text-gray-400 mb-2">字段定义预览</p>
                <div className="border rounded-lg overflow-x-auto max-h-[200px]">
                  <table className="w-full min-w-[500px] text-xs">
                    <thead className="bg-gray-50 border-b sticky top-0">
                      <tr>
                        <th className="text-left px-3 py-1.5 font-medium text-gray-600">字段标识</th>
                        <th className="text-left px-3 py-1.5 font-medium text-gray-600">字段名称</th>
                        <th className="text-left px-3 py-1.5 font-medium text-gray-600">类型</th>
                        <th className="text-center px-3 py-1.5 font-medium text-gray-600">主键</th>
                        <th className="text-center px-3 py-1.5 font-medium text-gray-600">允许空</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {columnDefs.map(d => (
                        <tr key={d.field_key} className="hover:bg-gray-50">
                          <td className="px-3 py-1 font-mono text-[11px]">{d.field_key}</td>
                          <td className="px-3 py-1">{d.field_name}</td>
                          <td className="px-3 py-1">
                            <span className="px-1.5 py-0.5 bg-gray-100 rounded text-[11px]">{d.field_type}</span>
                          </td>
                          <td className="px-3 py-1 text-center">
                            {d.is_primary_key ? <KeyRound size={12} className="text-amber-500 inline" /> : '—'}
                          </td>
                          <td className="px-3 py-1 text-center">
                            {d.nullable ? '是' : <span className="text-red-500 font-medium">否</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* 底部按钮 */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-gray-100 shrink-0">
          <div>
            {step > 1 && (
              <button
                onClick={() => setStep((step - 1) as 1 | 2 | 3 | 4)}
                className="flex items-center gap-1 px-3 py-1.5 text-sm text-gray-500 hover:text-gray-800 border rounded-lg"
              >
                <ChevronLeft size={14} /> 上一步
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
            {step < 4 && (
              <button
                onClick={() => {
                  setValidateResult(null)
                  setStep((step + 1) as 1 | 2 | 3 | 4)
                }}
                disabled={step === 2 && dryRunPhase !== 'done'}
                className={`flex items-center gap-1 px-4 py-1.5 text-sm rounded-lg font-medium transition-colors ${
                  (step === 2 && dryRunPhase !== 'done')
                    ? 'bg-gray-200 text-gray-400 cursor-not-allowed'
                    : 'bg-[var(--color-nav-bg)] text-white hover:opacity-90'
                }`}
              >
                下一步 <ChevronRight size={14} />
              </button>
            )}
            {step === 3 && (
              <button
                onClick={runValidate}
                disabled={validating || isPublished}
                className={`flex items-center gap-1 px-4 py-1.5 text-sm rounded-lg font-medium transition-colors ${
                  (validating || isPublished)
                    ? 'bg-gray-200 text-gray-400 cursor-not-allowed'
                    : 'bg-emerald-600 text-white hover:bg-emerald-700'
                }`}
              >
                {validating ? (
                  <><Loader2 size={14} className="animate-spin" /> 校验中…</>
                ) : (
                  '校验并下一步'
                )}
              </button>
            )}
            {step === 4 && !isPublished && (
              <>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg font-medium border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-60"
                >
                  {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                  保存草稿
                </button>
                <button
                  onClick={handlePublish}
                  disabled={publishing}
                  className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg font-medium bg-[var(--color-nav-bg)] text-white hover:opacity-90 disabled:opacity-60"
                >
                  {publishing ? <Loader2 size={14} className="animate-spin" /> : <Rocket size={14} />}
                  发布
                </button>
              </>
            )}
            {step === 4 && isPublished && (
              <button
                onClick={handleSave}
                disabled={saving}
                className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg font-medium bg-[var(--color-nav-bg)] text-white hover:opacity-90 disabled:opacity-60"
              >
                {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                保存
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
