import { Loader2, Pencil, Plus, Search, Sparkles, Trash2, X } from 'lucide-react'
import type { PromptSettingsViewModel } from '../hooks/usePromptSettings'

type PromptSettingsTabProps = {
  settings: PromptSettingsViewModel
}

export default function PromptSettingsTab({ settings }: PromptSettingsTabProps) {
  const {
    prompts,
    promptsLoading,
    showPromptModal,
    setShowPromptModal,
    editingPrompt,
    promptMsg,
    promptName,
    setPromptName,
    promptDomain,
    setPromptDomain,
    promptContent,
    setPromptContent,
    isGenerating,
    promptSaving,
    promptSearch,
    setPromptSearch,
    promptDomainFilter,
    setPromptDomainFilter,
    deletePromptTarget,
    setDeletePromptTarget,
    deletePromptMut,
    openCreatePrompt,
    openEditPrompt,
    handleSavePrompt,
    handleGenerateTemplate,
  } = settings

  return (
        <div>
          {/* Toolbar */}
          <div className="flex items-center gap-3 mb-4 flex-wrap">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                value={promptSearch}
                onChange={e => setPromptSearch(e.target.value)}
                placeholder="按名称 / ID 筛选"
                aria-label="按名称或 ID 筛选提示词"
                className="pl-8 pr-7 py-1.5 border rounded-lg text-sm w-52"
              />
              {promptSearch && (
                <button onClick={() => setPromptSearch('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-black">
                  <X size={12} />
                </button>
              )}
            </div>
            <select
              value={promptDomainFilter}
              onChange={e => setPromptDomainFilter(e.target.value)}
              aria-label="按业务域筛选提示词"
              className="border rounded-lg px-3 py-1.5 text-sm"
            >
              <option value="">全部领域</option>
              {['供应链', '法律', '医疗', 'HR', '财务', '教育', '通用', '其他'].map(d => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
            <div className="flex-1" />
            {promptMsg && (
              <span className={`text-xs ${promptMsg.includes('成功') || promptMsg.includes('更新') ? 'text-green-600' : 'text-red-500'}`}>
                {promptMsg}
              </span>
            )}
            <button
              onClick={openCreatePrompt}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-black text-white rounded-lg text-sm"
            >
              <Plus size={14} /> 新建提示词
            </button>
          </div>

          {/* Table */}
          <div className="border rounded-xl overflow-hidden bg-white">
            {promptsLoading ? (
              <p className="text-center text-gray-400 py-8 text-sm">加载中...</p>
            ) : (prompts as any[]).filter((p: any) => {
              const q = promptSearch.toLowerCase()
              const matchSearch = !q || p.name?.toLowerCase().includes(q) || p.id?.toLowerCase().includes(q)
              const matchDomain = !promptDomainFilter || p.domain === promptDomainFilter
              return matchSearch && matchDomain
            }).length === 0 ? (
              <p className="text-center text-gray-400 py-8 text-sm">
                {(prompts as any[]).length === 0 ? '暂无提示词模版' : '没有匹配的模版'}
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b">
                  <tr>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">模版 ID</th>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">名称</th>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">业务域</th>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">版本号</th>
                    <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {(prompts as any[])
                    .filter((p: any) => {
                      const q = promptSearch.toLowerCase()
                      const matchSearch = !q || p.name?.toLowerCase().includes(q) || p.id?.toLowerCase().includes(q)
                      const matchDomain = !promptDomainFilter || p.domain === promptDomainFilter
                      return matchSearch && matchDomain
                    })
                    .map((p: any) => (
                      <tr key={p.id} className="hover:bg-gray-50">
                        <td className="px-4 py-3 font-mono text-xs text-gray-400" title={p.id}>
                          {p.id?.slice(0, 8)}
                        </td>
                        <td className="px-4 py-3 font-medium text-gray-800 max-w-[200px] truncate">{p.name}</td>
                        <td className="px-4 py-3">
                          <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">{p.domain}</span>
                        </td>
                        <td className="px-4 py-3 text-xs text-gray-500">v{p.version}</td>
                        <td className="px-4 py-3 text-right">
                          <div className="flex items-center gap-2 justify-end">
                            <button
                              onClick={() => openEditPrompt(p)}
                              className="p-1.5 rounded hover:bg-gray-100 text-gray-500 hover:text-black"
                              title="编辑"
                            >
                              <Pencil size={13} />
                            </button>
                            <button
                              onClick={() => setDeletePromptTarget(p)}
                              className="p-1.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-500"
                              title="删除"
                            >
                              <Trash2 size={13} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Create / Edit Modal */}
          {showPromptModal && (
            <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-6" onClick={() => setShowPromptModal(false)}>
              <div
                role="dialog"
                aria-modal="true"
                aria-labelledby="prompt-template-dialog-title"
                className="bg-white rounded-xl shadow-xl w-full max-w-2xl flex flex-col"
                style={{ maxHeight: 'calc(100vh - 3rem)' }}
                onClick={e => e.stopPropagation()}
              >
                <div className="flex items-center justify-between px-6 py-4 border-b">
                  <h3 id="prompt-template-dialog-title" className="font-semibold">{editingPrompt ? '编辑提示词模版' : '新建提示词模版'}</h3>
                  <button aria-label="关闭提示词弹窗" onClick={() => setShowPromptModal(false)} className="text-gray-400 hover:text-black"><X size={16} /></button>
                </div>
                <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <div className="mb-1 flex text-xs font-medium text-gray-600">
                        <label htmlFor="prompt-template-name">名称</label>
                        <span aria-hidden="true" className="ml-0.5">*</span>
                      </div>
                      <input
                        id="prompt-template-name"
                        value={promptName}
                        onChange={e => setPromptName(e.target.value)}
                        placeholder="提示词模版名称"
                        className="w-full border rounded-lg px-3 py-2 text-sm"
                      />
                    </div>
                    <div>
                      <div className="mb-1 flex text-xs font-medium text-gray-600">
                        <label htmlFor="prompt-template-domain">业务域</label>
                        <span aria-hidden="true" className="ml-0.5">*</span>
                      </div>
                      <select
                        id="prompt-template-domain"
                        value={promptDomain}
                        onChange={e => setPromptDomain(e.target.value)}
                        className="w-full border rounded-lg px-3 py-2 text-sm"
                      >
                        {['供应链', '法律', '医疗', 'HR', '财务', '教育', '通用', '其他'].map(d => (
                          <option key={d} value={d}>{d}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex text-xs font-medium text-gray-600">
                        <label htmlFor="prompt-template-content">内容</label>
                        <span aria-hidden="true" className="ml-0.5">*</span>
                      </div>
                      <button
                        type="button"
                        onClick={handleGenerateTemplate}
                        disabled={isGenerating}
                        className="flex items-center gap-1 px-2.5 py-1 border border-gray-300 rounded text-xs text-gray-700 hover:bg-gray-100 disabled:opacity-50"
                      >
                        {isGenerating ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}
                        {isGenerating ? '生成中...' : '一键生成模版'}
                      </button>
                    </div>
                    <textarea
                      id="prompt-template-content"
                      value={promptContent}
                      onChange={e => setPromptContent(e.target.value)}
                      placeholder="输入提示词内容，或点击右上角一键生成..."
                      rows={10}
                      className="w-full border rounded-lg px-3 py-2 text-sm font-mono resize-y"
                    />
                  </div>
                  {promptMsg && showPromptModal && (
                    <p className="text-xs text-red-500">{promptMsg}</p>
                  )}
                </div>
                <div className="flex justify-end gap-3 px-6 py-4 border-t">
                  <button onClick={() => setShowPromptModal(false)} className="px-4 py-2 border rounded-lg text-sm">取消</button>
                  <button
                    onClick={handleSavePrompt}
                    disabled={promptSaving || !promptName.trim() || !promptContent.trim()}
                    className="flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50"
                  >
                    {promptSaving && <Loader2 size={13} className="animate-spin" />}
                    {promptSaving ? '保存中...' : '确认保存'}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Delete confirm */}
          {deletePromptTarget && (
            <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center">
              <div className="bg-white rounded-xl shadow-lg p-6 w-96">
                <h3 className="font-semibold mb-2">删除提示词模版</h3>
                <p className="text-sm text-gray-600 mb-5">
                  确认删除「{deletePromptTarget.name}」？此操作不可撤销。
                </p>
                <div className="flex justify-end gap-3">
                  <button onClick={() => setDeletePromptTarget(null)} className="px-4 py-2 border rounded-lg text-sm">取消</button>
                  <button
                    onClick={() => deletePromptMut.mutate(deletePromptTarget.id)}
                    disabled={deletePromptMut.isPending}
                    className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm disabled:opacity-50"
                  >
                    {deletePromptMut.isPending ? '删除中...' : '确认删除'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
  )
}
