/**
 * 超级助手（系统设置子页）— 悬浮 AI 助手的页面可见范围配置。
 *
 * 管理员在左导航目录树（一级/二级菜单）上勾选哪些目录显示右下角的
 * 悬浮 AI 助手；未勾选目录下的页面（含其详情页）不再渲染悬浮入口。
 * 存储为"隐藏名单"（hidden_menu_keys），平台级单例配置，对所有登录
 * 用户生效；未保存过配置时全部页面可见，与功能上线前行为一致。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Loader2, Sparkles, X } from 'lucide-react'

import { superAssistantApi, type AssistantWidgetConfig } from '@/api/superAssistant'
import { PLATFORM_NAV_ITEMS } from '@/config/navigation'
import { errorMessage } from '@/components/assistant-widget/logic'

/** 参与配置的目录：与管理员左侧导航看到的菜单一致（不含隐藏入口） */
const CONFIGURABLE_ITEMS = PLATFORM_NAV_ITEMS.filter(item => !item.hiddenFromNavigation)

/** 每个一级目录实际参与存储的叶子菜单键：有子菜单取子菜单键，否则取自身 */
function leafKeysOf(item: (typeof CONFIGURABLE_ITEMS)[number]): string[] {
  return item.subItems?.length ? item.subItems.map(child => child.key) : [item.key]
}

const ALL_LEAF_KEYS = CONFIGURABLE_ITEMS.flatMap(leafKeysOf)

export default function AssistantWidgetTab() {
  const qc = useQueryClient()
  const [hiddenDraft, setHiddenDraft] = useState<string[] | null>(null)
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)

  const { data: config, isLoading } = useQuery({
    queryKey: ['assistant-widget-config'],
    queryFn: () => superAssistantApi.widgetConfig() as Promise<AssistantWidgetConfig>,
  })
  const serverHidden = Array.isArray(config?.hidden_menu_keys) ? config.hidden_menu_keys : []
  const hidden = hiddenDraft ?? serverHidden
  const visible = (key: string) => !hidden.includes(key)

  const toggleBranch = (leafKeys: string[]) => {
    setNotice(null)
    const allVisible = leafKeys.every(visible)
    setHiddenDraft(allVisible
      ? Array.from(new Set([...hidden, ...leafKeys]))
      : hidden.filter(key => !leafKeys.includes(key)))
  }
  const toggleLeaf = (key: string) => {
    setNotice(null)
    setHiddenDraft(visible(key) ? [...hidden, key] : hidden.filter(item => item !== key))
  }

  const save = useMutation({
    mutationFn: () => superAssistantApi.updateWidgetConfig(hidden),
    onSuccess: saved => {
      setHiddenDraft(null)
      qc.setQueryData(['assistant-widget-config'], saved)
      setNotice({ kind: 'success', text: '已保存，全平台即时生效' })
    },
    onError: error => setNotice({ kind: 'error', text: errorMessage(error, '保存失败') }),
  })

  return (
    <div className="min-h-full">
      {notice && (
        <div role="status" className={`fixed right-6 top-20 z-[80] flex max-w-sm items-center gap-2 rounded-xl border bg-white px-4 py-3 text-sm shadow-[0_18px_48px_rgba(15,23,42,0.14)] ${notice.kind === 'success' ? 'border-teal-200 text-teal-800' : 'border-red-200 text-red-700'}`}>
          {notice.kind === 'success' ? <Check size={15} /> : <X size={15} />}{notice.text}
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">悬浮 AI 助手的页面显示范围</h2>
            <p className="mt-1 text-xs text-slate-500">勾选一级或二级目录，表示在这些页面显示右下角的 AI 助手；未勾选目录下的页面（含详情页）将隐藏该入口。配置对全平台用户生效。</p>
          </div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => { setNotice(null); setHiddenDraft([]) }} className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50">全部显示</button>
            <button type="button" onClick={() => { setNotice(null); setHiddenDraft(ALL_LEAF_KEYS) }} className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50">全部隐藏</button>
            <button type="button" disabled={save.isPending} onClick={() => save.mutate()} className="inline-flex items-center gap-1.5 rounded-lg bg-teal-700 px-3.5 py-2 text-xs font-medium text-white transition-colors hover:bg-teal-800 disabled:opacity-60">
              {save.isPending ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}保存配置
            </button>
          </div>
        </header>
        <div className="p-5">
          <div className="mb-4 flex items-start gap-3 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
            <Sparkles size={16} className="mt-0.5 shrink-0 text-teal-700" />
            <div>
              <p className="text-xs font-medium text-slate-800">未列入左侧导航的页面不受影响</p>
              <p className="mt-0.5 text-[11px] leading-5 text-slate-500">收件箱、平台概览、超级助手主页等不在导航目录中的页面始终显示 AI 助手；此处的勾选只控制导航目录覆盖的页面。</p>
            </div>
          </div>
          {isLoading ? (
            <div className="flex items-center justify-center gap-2 py-14 text-sm text-slate-400"><Loader2 size={16} className="animate-spin" />正在加载配置...</div>
          ) : (
            <div className="grid gap-3 xl:grid-cols-2" data-testid="assistant-widget-visibility-tree">
              {CONFIGURABLE_ITEMS.map(item => {
                const children = item.subItems ?? []
                const leafKeys = leafKeysOf(item)
                const visibleCount = leafKeys.filter(visible).length
                const branchChecked = visibleCount === leafKeys.length
                const partiallyChecked = visibleCount > 0 && !branchChecked
                const Icon = item.icon
                return (
                  <article key={item.key} className={`rounded-xl border p-4 transition-colors ${visibleCount ? 'border-teal-200 bg-teal-50/30' : 'border-slate-200 bg-white'}`}>
                    <button type="button" role="checkbox" aria-checked={partiallyChecked ? 'mixed' : branchChecked} onClick={() => toggleBranch(leafKeys)} className="flex w-full items-start gap-3 text-left">
                      <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded border ${visibleCount ? 'border-teal-600 bg-teal-600 text-white' : 'border-slate-300 bg-white'}`}>
                        {branchChecked ? <Check size={12} strokeWidth={3} /> : partiallyChecked ? <span className="h-0.5 w-2 rounded bg-white" /> : null}
                      </span>
                      <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${visibleCount ? 'bg-teal-100 text-teal-700' : 'bg-slate-100 text-slate-500'}`}>
                        <Icon size={17} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-slate-800">{item.label}</span>
                        {item.description && <span className="mt-0.5 block text-[11px] leading-5 text-slate-400">{item.description}</span>}
                      </span>
                    </button>
                    {children.length > 0 && (
                      <div className="ml-8 mt-3 space-y-1 border-l border-slate-200 pl-4">
                        {children.map(child => {
                          const ChildIcon = child.icon
                          const checked = visible(child.key)
                          return (
                            <button key={child.key} type="button" role="checkbox" aria-checked={checked} onClick={() => toggleLeaf(child.key)} className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left transition-colors hover:bg-white">
                              <span className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${checked ? 'border-teal-600 bg-teal-600 text-white' : 'border-slate-300 bg-white'}`}>
                                {checked && <Check size={10} strokeWidth={3} />}
                              </span>
                              <ChildIcon size={14} className={checked ? 'text-teal-700' : 'text-slate-400'} />
                              <span className={`text-xs ${checked ? 'font-medium text-slate-700' : 'text-slate-500'}`}>{child.label}</span>
                            </button>
                          )
                        })}
                      </div>
                    )}
                  </article>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
