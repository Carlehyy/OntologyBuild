/**
 * 技能中心 — 平台级智能体能力包管理（能力注册中心 P1）
 *
 * Skill = markdown 指令 + 输出契约，按作用域挂载到各智能体
 * （业务探索等）。目录（描述）注入系统提示，全文由 use_skill 按需激活。
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Wand2, Plus, Pencil, Trash2, X, Loader2, ShieldCheck, Compass, Bot,
} from 'lucide-react'
import {
  capabilitiesApi, type CapSkill, type SkillCreate, type SkillScope,
} from '@/api/capabilities'

const SCOPE_META: Record<SkillScope, { label: string; icon: React.ElementType }> = {
  exploration: { label: '业务探索', icon: Compass },
  agent: { label: '智能助手', icon: Bot },
}

interface EditorState {
  id?: string
  builtin?: boolean
  name: string
  displayName: string
  description: string
  instructions: string
  scopes: SkillScope[]
  enabled: boolean
}

const EMPTY: EditorState = {
  name: '', displayName: '', description: '', instructions: '',
  scopes: ['exploration'], enabled: true,
}

export default function SkillCenterPage() {
  const qc = useQueryClient()
  const { data: skills = [], isLoading } = useQuery({
    queryKey: ['cap-skills'], queryFn: () => capabilitiesApi.skills(),
  })
  const [editor, setEditor] = useState<EditorState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const refresh = () => qc.invalidateQueries({ queryKey: ['cap-skills'] })

  const save = async () => {
    if (!editor) return
    setError('')
    if (!editor.id && !/^[a-z][a-z0-9_]{1,63}$/.test(editor.name)) {
      setError('name 必须是小写英文标识符（字母开头，可含数字/下划线）')
      return
    }
    if (!editor.displayName.trim()) { setError('请填写显示名'); return }
    setBusy(true)
    try {
      if (editor.id) {
        await capabilitiesApi.updateSkill(editor.id, {
          displayName: editor.displayName, description: editor.description,
          instructions: editor.instructions, scopes: editor.scopes, enabled: editor.enabled,
        })
      } else {
        const body: SkillCreate = {
          name: editor.name, displayName: editor.displayName,
          description: editor.description, instructions: editor.instructions,
          scopes: editor.scopes, enabled: editor.enabled,
        }
        await capabilitiesApi.createSkill(body)
      }
      await refresh()
      setEditor(null)
    } catch (e: any) {
      setError(e?.detail || e?.message || '保存失败')
    } finally {
      setBusy(false)
    }
  }

  const toggleEnabled = async (s: CapSkill) => {
    await capabilitiesApi.updateSkill(s.id, { enabled: !s.enabled })
    refresh()
  }

  const remove = async (s: CapSkill) => {
    if (!window.confirm(`确定删除技能「${s.displayName}」？`)) return
    try {
      await capabilitiesApi.deleteSkill(s.id)
      refresh()
    } catch (e: any) {
      window.alert(e?.detail || '删除失败')
    }
  }

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h2 className="text-lg font-semibold text-[var(--color-text-primary)] flex items-center gap-2">
            <Wand2 size={18} className="text-teal-600" /> 技能中心
          </h2>
          <p className="text-xs text-[var(--color-text-tertiary)] mt-1">
            平台级智能体能力包：markdown 指令 + 输出契约，按作用域挂载。智能体在对话中通过 use_skill 按需激活（渐进披露，不占系统提示篇幅）。
          </p>
        </div>
        <button
          onClick={() => { setError(''); setEditor({ ...EMPTY }) }}
          className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-md text-xs font-medium text-white bg-teal-600 hover:bg-teal-700"
        >
          <Plus size={14} /> 新建技能
        </button>
      </div>

      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-[var(--color-bg-base)] text-xs text-[var(--color-text-secondary)]">
              <th className="text-left px-4 py-2.5 font-medium">技能</th>
              <th className="text-left px-4 py-2.5 font-medium">描述（注入目录）</th>
              <th className="text-left px-4 py-2.5 font-medium">作用域</th>
              <th className="text-left px-4 py-2.5 font-medium">启用</th>
              <th className="px-4 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={5} className="px-4 py-6 text-center text-xs text-[var(--color-text-tertiary)]">加载中…</td></tr>
            )}
            {!isLoading && skills.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-6 text-center text-xs text-[var(--color-text-tertiary)]">暂无技能</td></tr>
            )}
            {skills.map(s => (
              <tr key={s.id} className="border-t border-[var(--color-border)]">
                <td className="px-4 py-3">
                  <div className="flex items-center gap-1.5 text-xs font-medium text-[var(--color-text-primary)]">
                    {s.displayName}
                    {s.builtin && (
                      <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-px rounded bg-teal-50 text-teal-700">
                        <ShieldCheck size={10} /> 内置
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] font-mono text-[var(--color-text-tertiary)] mt-0.5">{s.name}</div>
                </td>
                <td className="px-4 py-3 text-xs text-[var(--color-text-secondary)] max-w-[320px]">
                  <div className="line-clamp-2">{s.description || '—'}</div>
                </td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1">
                    {s.scopes.map(sc => {
                      const meta = SCOPE_META[sc]
                      const Icon = meta?.icon || Wand2
                      return (
                        <span key={sc} className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-bg-base)] border border-[var(--color-border)] text-[var(--color-text-secondary)]">
                          <Icon size={10} /> {meta?.label || sc}
                        </span>
                      )
                    })}
                    {s.scopes.length === 0 && <span className="text-[11px] text-[var(--color-text-tertiary)]">未挂载</span>}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <button
                    onClick={() => void toggleEnabled(s)}
                    className={`relative h-5 w-9 rounded-full transition-colors ${s.enabled ? 'bg-teal-600' : 'bg-gray-300'}`}
                    title={s.enabled ? '点击停用' : '点击启用'}
                  >
                    <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${s.enabled ? 'left-[18px]' : 'left-0.5'}`} />
                  </button>
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center justify-end gap-1">
                    <button
                      onClick={() => { setError(''); setEditor({ id: s.id, builtin: s.builtin, name: s.name, displayName: s.displayName, description: s.description, instructions: s.instructions, scopes: s.scopes, enabled: s.enabled }) }}
                      className="p-1.5 rounded-md text-[var(--color-text-tertiary)] hover:text-teal-700 hover:bg-teal-50"
                    >
                      <Pencil size={13} />
                    </button>
                    <button
                      onClick={() => void remove(s)}
                      disabled={s.builtin}
                      title={s.builtin ? '内置技能不可删除（可停用）' : '删除'}
                      className="p-1.5 rounded-md text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)] hover:bg-[var(--color-danger-bg)] disabled:opacity-30"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 编辑抽屉 */}
      {editor && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={() => setEditor(null)}>
          <div className="h-full w-[560px] max-w-[92vw] bg-[var(--color-bg-elevated)] shadow-2xl flex flex-col"
               onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-border)]">
              <div className="text-sm font-semibold text-[var(--color-text-primary)]">
                {editor.id ? `编辑技能：${editor.displayName || editor.name}` : '新建技能'}
                {editor.builtin && <span className="ml-2 text-[10px] px-1.5 py-px rounded bg-teal-50 text-teal-700">内置</span>}
              </div>
              <button onClick={() => setEditor(null)} className="p-1.5 rounded-md hover:bg-[var(--color-bg-hover)] text-[var(--color-text-tertiary)]">
                <X size={16} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="text-xs text-[var(--color-text-secondary)]">name（英文标识符）</span>
                  <input
                    value={editor.name}
                    disabled={!!editor.id}
                    onChange={e => setEditor({ ...editor, name: e.target.value })}
                    placeholder="er_diagram"
                    className="mt-1 w-full px-3 py-2 text-xs font-mono rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] outline-none focus:border-teal-500 disabled:opacity-60"
                  />
                </label>
                <label className="block">
                  <span className="text-xs text-[var(--color-text-secondary)]">显示名</span>
                  <input
                    value={editor.displayName}
                    onChange={e => setEditor({ ...editor, displayName: e.target.value })}
                    placeholder="ER 图绘制"
                    className="mt-1 w-full px-3 py-2 text-xs rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] outline-none focus:border-teal-500"
                  />
                </label>
              </div>

              <label className="block">
                <span className="text-xs text-[var(--color-text-secondary)]">描述（一句话，进智能体的技能目录 —— 写清楚"什么时候该用"）</span>
                <input
                  value={editor.description}
                  onChange={e => setEditor({ ...editor, description: e.target.value })}
                  placeholder="用户想看实体关系图/ER 图时使用：…"
                  className="mt-1 w-full px-3 py-2 text-xs rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] outline-none focus:border-teal-500"
                />
              </label>

              <div>
                <span className="text-xs text-[var(--color-text-secondary)]">作用域</span>
                <div className="mt-1.5 flex gap-3">
                  {(Object.keys(SCOPE_META) as SkillScope[]).map(sc => (
                    <label key={sc} className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)] cursor-pointer">
                      <input
                        type="checkbox"
                        className="accent-teal-600"
                        checked={editor.scopes.includes(sc)}
                        onChange={e => setEditor({
                          ...editor,
                          scopes: e.target.checked
                            ? [...editor.scopes, sc]
                            : editor.scopes.filter(x => x !== sc),
                        })}
                      />
                      {SCOPE_META[sc].label}
                      {sc === 'agent' && <span className="text-[10px] text-[var(--color-text-tertiary)]">（接入中，P3）</span>}
                    </label>
                  ))}
                </div>
              </div>

              <label className="block">
                <span className="text-xs text-[var(--color-text-secondary)]">
                  指令全文（markdown，use_skill 激活后交给模型 —— 建议包含「输出契约」，如 ```mermaid 代码块格式）
                </span>
                <textarea
                  value={editor.instructions}
                  onChange={e => setEditor({ ...editor, instructions: e.target.value })}
                  rows={16}
                  placeholder={'# 技能名\n\n何时使用、步骤、输出契约、示例…'}
                  className="mt-1 w-full px-3 py-2 text-xs font-mono leading-relaxed rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] outline-none focus:border-teal-500 resize-y"
                />
              </label>
            </div>

            <div className="border-t border-[var(--color-border)] px-5 py-3.5 flex items-center justify-between">
              {error ? <span className="text-xs text-[var(--color-danger)]">{error}</span> : <span />}
              <div className="flex items-center gap-2">
                <label className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)] cursor-pointer">
                  <input type="checkbox" className="accent-teal-600" checked={editor.enabled}
                         onChange={e => setEditor({ ...editor, enabled: e.target.checked })} />
                  启用
                </label>
                <button
                  onClick={() => void save()}
                  disabled={busy}
                  className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-md text-xs font-medium text-white bg-teal-600 hover:bg-teal-700 disabled:opacity-50"
                >
                  {busy && <Loader2 size={12} className="animate-spin" />} 保存
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
