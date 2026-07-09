import { useState } from 'react'
import { X, Loader2, Plus, Trash2, KeyRound, Table2, XCircle } from 'lucide-react'
import datasetsApi, { FIELD_TYPE_LABELS, type CreateTableResult } from '@/api/v2/datasets'
import { CONTRACT_FIELD_TYPES } from '@/api/v2/pipelines'

interface ColDraft {
  name: string
  type: string
  pk: boolean
}

const emptyCol = (): ColDraft => ({ name: '', type: 'string', pk: false })

/** 在线新建空表格（人工数据集）：定义列名/类型/主键，创建后在「维护数据」中逐行录入 */
export default function CreateTableModal({ onClose, onCreated }: {
  onClose: () => void
  onCreated: (res: CreateTableResult) => void
}) {
  const [name, setName] = useState('')
  const [cols, setCols] = useState<ColDraft[]>([emptyCol(), emptyCol()])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const setCol = (i: number, patch: Partial<ColDraft>) =>
    setCols(list => list.map((c, idx) => (idx === i ? { ...c, ...patch } : c)))
  const addCol = () => setCols(list => [...list, emptyCol()])
  const removeCol = (i: number) => setCols(list => list.filter((_, idx) => idx !== i))

  const filledCols = () =>
    cols.map(c => ({ ...c, name: c.name.trim() })).filter(c => c.name)

  const validate = (): string => {
    if (!name.trim()) return '请填写表格名称'
    const filled = filledCols()
    if (filled.length === 0) return '至少需要定义一列'
    const seen = new Set<string>()
    for (const c of filled) {
      if (seen.has(c.name)) return `列名「${c.name}」重复`
      seen.add(c.name)
    }
    return ''
  }

  const handleSubmit = async () => {
    const msg = validate()
    if (msg) { setError(msg); return }
    const filled = filledCols()
    setSubmitting(true)
    setError('')
    try {
      const res = await datasetsApi.createTable({
        name: name.trim(),
        columns: filled.map(c => ({ name: c.name, type: c.type })),
        primary_key: filled.filter(c => c.pk).map(c => c.name).join(','),
      })
      onCreated(res)
    } catch (err: unknown) {
      const er = err as { detail?: string; message?: string }
      setError(typeof er?.detail === 'string' ? er.detail : (er?.message || '创建失败，请重试'))
      setSubmitting(false)
    }
  }

  const pkNames = filledCols().filter(c => c.pk).map(c => c.name)

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-[min(94vw,640px)] max-h-[85vh] flex flex-col overflow-hidden">
        {/* 头部 */}
        <div className="flex items-start gap-3 px-5 py-3.5 border-b">
          <Table2 size={16} className="text-[var(--color-nav-bg)] shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <h3 className="font-semibold text-sm">在线新建表格</h3>
            <p className="text-xs text-gray-400 mt-0.5">
              没有现成文件也能建人工数据集：定义列结构后逐行录入，声明主键即可被本体映射灌入，也可作为流水线数据源
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 shrink-0"><X size={16} /></button>
        </div>

        <div className="flex-1 overflow-auto px-5 py-4 space-y-4">
          {/* 名称 */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">表格名称</label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="例如：设备台账"
              autoFocus
              className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:border-[var(--color-nav-bg)]"
            />
          </div>

          {/* 列定义 */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs font-medium text-gray-600">列定义</label>
              <span className="text-[11px] text-gray-400">类型在录入时校验（如整数列不接受文字），空白列名会被忽略</span>
            </div>
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-gray-50 border-b">
                  <tr>
                    <th className="text-left px-3 py-1.5 font-medium text-gray-500">列名</th>
                    <th className="text-left px-3 py-1.5 font-medium text-gray-500 w-36">类型</th>
                    <th className="px-2 py-1.5 font-medium text-gray-500 w-14" title="勾选后创建即声明主键契约（列存在 · 值非空 · 组合唯一）">
                      <span className="inline-flex items-center gap-0.5"><KeyRound size={10} className="text-amber-500" />主键</span>
                    </th>
                    <th className="w-9" />
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {cols.map((c, i) => (
                    <tr key={i}>
                      <td className="px-1 py-1">
                        <input
                          value={c.name}
                          onChange={e => setCol(i, { name: e.target.value })}
                          placeholder={i === 0 ? '例如：编号' : '列名'}
                          className="w-full px-2 py-1.5 border rounded font-mono focus:outline-none focus:border-[var(--color-nav-bg)]"
                        />
                      </td>
                      <td className="px-1 py-1">
                        <select
                          value={c.type}
                          onChange={e => setCol(i, { type: e.target.value })}
                          className="w-full px-2 py-1.5 border rounded bg-white focus:outline-none"
                        >
                          {CONTRACT_FIELD_TYPES.map(t => (
                            <option key={t} value={t}>{t}（{FIELD_TYPE_LABELS[t] ?? t}）</option>
                          ))}
                        </select>
                      </td>
                      <td className="px-2 py-1 text-center">
                        <input
                          type="checkbox"
                          checked={c.pk}
                          onChange={e => setCol(i, { pk: e.target.checked })}
                          className="accent-amber-500"
                        />
                      </td>
                      <td className="px-1 py-1 text-center">
                        <button
                          onClick={() => removeCol(i)}
                          disabled={cols.length <= 1}
                          className="p-1 rounded text-gray-300 hover:text-red-500 hover:bg-red-50 disabled:opacity-30 disabled:hover:text-gray-300 disabled:hover:bg-transparent"
                          title="移除该列"
                        >
                          <Trash2 size={12} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <button
              onClick={addCol}
              className="mt-2 flex items-center gap-1 text-xs px-2.5 py-1.5 border rounded-lg bg-white hover:bg-gray-50 text-gray-600"
            >
              <Plus size={12} /> 添加列
            </button>
          </div>

          {/* 主键提示 */}
          <p className="text-xs text-gray-400 flex items-start gap-1.5">
            <KeyRound size={12} className="text-amber-500 shrink-0 mt-0.5" />
            <span>
              {pkNames.length > 0
                ? <>创建后即声明主键契约：<span className="font-mono text-amber-700">{pkNames.join(', ')}</span>（修改/删除行、被本体映射灌入都依赖它）</>
                : '暂未选择主键：创建后仅能新增行，之后可随时在「维护数据」中声明主键契约'}
            </span>
          </p>

          {/* 错误提示 */}
          {error && (
            <div className="px-3 py-2 rounded-lg border text-xs flex items-center gap-2 bg-red-50 border-red-200 text-red-600">
              <XCircle size={13} className="shrink-0" />
              <span className="flex-1">{error}</span>
              <button onClick={() => setError('')} className="text-gray-400 hover:text-gray-600"><X size={12} /></button>
            </div>
          )}
        </div>

        {/* 底部操作栏 */}
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t bg-gray-50/60">
          <button onClick={onClose} className="text-xs px-3 py-1.5 border rounded-lg bg-white hover:bg-gray-50 text-gray-600">
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="flex items-center gap-1.5 text-xs px-3.5 py-1.5 bg-[var(--color-nav-bg)] text-white rounded-lg hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? <Loader2 size={12} className="animate-spin" /> : <Table2 size={12} />}
            创建表格
          </button>
        </div>
      </div>
    </div>
  )
}
