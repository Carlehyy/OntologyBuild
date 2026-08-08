import { Pencil, Plus, Search, Trash2, X } from 'lucide-react'
import type { DomainSettingsViewModel } from '../hooks/useDomainSettings'

type DomainSettingsTabProps = {
  settings: DomainSettingsViewModel
}

export default function DomainSettingsTab({ settings }: DomainSettingsTabProps) {
  const {
    domainList,
    domainsLoading,
    domainSearch,
    setDomainSearch,
    showDomainModal,
    setShowDomainModal,
    editingDomain,
    setEditingDomain,
    domainName,
    setDomainName,
    domainDescription,
    setDomainDescription,
    domainMsg,
    setDomainMsg,
    deleteDomainTarget,
    setDeleteDomainTarget,
    createDomainMut,
    updateDomainMut,
    deleteDomainMut,
    openCreateDomain,
    openEditDomain,
    handleSaveDomain,
    handleDeleteDomain,
  } = settings

  return (
        <div>
          {/* Toolbar */}
          <div className="flex items-center gap-3 mb-4 flex-wrap">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                value={domainSearch}
                onChange={e => setDomainSearch(e.target.value)}
                placeholder="按名称搜索"
                className="pl-8 pr-7 py-1.5 border rounded-lg text-sm w-52"
              />
              {domainSearch && (
                <button onClick={() => setDomainSearch('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-black">
                  <X size={12} />
                </button>
              )}
            </div>
            <div className="flex-1" />
            {domainMsg && (
              <span className={`text-xs ${domainMsg.includes('成功') ? 'text-green-600' : 'text-red-500'}`}>
                {domainMsg}
              </span>
            )}
            <button
              onClick={openCreateDomain}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-black text-white rounded-lg text-sm hover:bg-gray-800"
            >
              <Plus size={14} /> 新增领域
            </button>
          </div>

          {/* List */}
          <div className="bg-white border rounded-lg overflow-hidden">
            {domainsLoading ? (
              <p className="text-center text-gray-400 py-6 text-sm">加载中...</p>
            ) : (domainList as any[]).length === 0 ? (
              <p className="text-center text-gray-400 py-6 text-sm">暂无领域，点击"新增领域"开始</p>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">名称</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">描述</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">更新时间</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 w-20">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {(domainList as any[]).map((d: any) => (
                    <tr key={d.id} className="border-b last:border-0 hover:bg-gray-50">
                      <td className="px-4 py-3 font-medium">{d.name}</td>
                      <td className="px-4 py-3 text-gray-500 max-w-xs truncate">{d.description || '—'}</td>
                      <td className="px-4 py-3 text-gray-500">
                        {d.updated_at ? new Date(d.updated_at).toLocaleDateString('zh-CN') : '—'}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <button onClick={() => openEditDomain(d)} className="text-gray-500 hover:text-black">
                            <Pencil size={14} />
                          </button>
                          <button onClick={() => setDeleteDomainTarget(d)} className="text-red-500 hover:text-red-700">
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Create/Edit Modal */}
          {showDomainModal && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
              <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
                <h3 className="text-lg font-semibold mb-4">
                  {editingDomain ? '编辑领域' : '新增领域'}
                </h3>
                <div className="space-y-4">
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">名称 *</label>
                    <input
                      value={domainName}
                      onChange={e => setDomainName(e.target.value)}
                      maxLength={100}
                      placeholder="输入领域名称"
                      className="w-full border rounded-lg px-3 py-2 text-sm"
                      autoFocus
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">描述</label>
                    <textarea
                      value={domainDescription}
                      onChange={e => setDomainDescription(e.target.value)}
                      placeholder="输入领域描述（可选）"
                      rows={3}
                      className="w-full border rounded-lg px-3 py-2 text-sm resize-none"
                    />
                  </div>
                </div>
                <div className="flex justify-end gap-2 mt-6">
                  <button
                    onClick={() => { setShowDomainModal(false); setEditingDomain(null); setDomainMsg('') }}
                    className="px-4 py-1.5 border rounded-lg text-sm text-gray-600"
                  >
                    取消
                  </button>
                  <button
                    onClick={handleSaveDomain}
                    disabled={createDomainMut.isPending || updateDomainMut.isPending}
                    className="px-4 py-1.5 bg-black text-white rounded-lg text-sm disabled:opacity-50"
                  >
                    {createDomainMut.isPending || updateDomainMut.isPending ? '保存中...' : '保存'}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Delete Confirm Modal */}
          {deleteDomainTarget && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
              <div className="bg-white rounded-xl shadow-xl w-full max-w-sm p-6">
                <h3 className="text-lg font-semibold mb-2">确认删除</h3>
                <p className="text-sm text-gray-500 mb-6">
                  确定要删除领域「{deleteDomainTarget.name}」吗？若仍有本体使用该领域，系统会阻止删除；删除后不可撤销。
                </p>
                <div className="flex justify-end gap-2">
                  <button
                    onClick={() => setDeleteDomainTarget(null)}
                    className="px-4 py-1.5 border rounded-lg text-sm text-gray-600"
                  >
                    取消
                  </button>
                  <button
                    onClick={handleDeleteDomain}
                    disabled={deleteDomainMut.isPending}
                    className="px-4 py-1.5 bg-red-600 text-white rounded-lg text-sm disabled:opacity-50"
                  >
                    {deleteDomainMut.isPending ? '删除中...' : '确认删除'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
  )
}
