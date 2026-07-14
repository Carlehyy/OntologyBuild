import { useState } from 'react';
import {
  LinkIcon,
  EyeIcon,
  PencilIcon,
  TrashIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';
import { useOntologyStore } from '../../store/ontologyStore';

interface LinkListProps {
  isOpen?: boolean;
  onClose?: () => void;
  readOnly?: boolean;
}

export default function LinkList({ isOpen, onClose, readOnly = false }: LinkListProps) {
  const { ontology, deleteLinkType, setSelectedEdge, openPanel } = useOntologyStore();
  const [internalOpen, setInternalOpen] = useState(false);

  const panelOpen = isOpen !== undefined ? isOpen : internalOpen;
  const closePanel = () => {
    setInternalOpen(false);
    onClose?.();
  };

  const handleOpen = (id: string) => {
    setSelectedEdge(id);
    openPanel('edit', 'linkType');
    closePanel();
  };

  if (!ontology) return null;

  const linkTypes = ontology.linkTypes;
  const objectTypes = ontology.objectTypes;

  const getObjectTypeName = (id: string) => {
    return objectTypes.find(ot => ot.id === id)?.displayName || '未知对象';
  };

  const getCardinalityLabel = (cardinality: string) => {
    const labels: Record<string, string> = {
      'one-to-one': '1:1',
      'one-to-many': '1:N',
      'many-to-one': 'N:1',
      'many-to-many': 'N:N',
    };
    return labels[cardinality] || cardinality;
  };

  const handleDelete = (id: string, name: string) => {
    if (confirm(`确定要删除关系「${name}」吗？`)) {
      deleteLinkType(id);
    }
  };

  return (
    <>
      {panelOpen && (
        <>
          <div
            className="fixed inset-0 z-[60] bg-black/30"
            onClick={closePanel}
          />

          <div className="fixed right-0 top-0 bottom-0 w-[380px] z-[70] glass border-l border-surface-700 animate-slide-in-right flex flex-col">
            <div className="flex items-center justify-between px-6 py-4 border-b border-surface-700 flex-shrink-0">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-cyan-500/20 flex items-center justify-center">
                  <LinkIcon className="w-5 h-5 text-cyan-500" />
                </div>
                <div>
                  <h2 className="font-display font-semibold text-surface-100">实体关系列表</h2>
                  <p className="text-xs text-surface-500">共 {linkTypes.length} 个关系</p>
                </div>
              </div>
              <button
                aria-label="关闭实体关系列表"
                onClick={closePanel}
                className="p-2 text-surface-400 hover:text-surface-200 hover:bg-surface-700 rounded-lg transition-colors"
              >
                <XMarkIcon className="w-5 h-5" />
              </button>
            </div>

            {/* Link Type List */}
            <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3">
              {linkTypes.length === 0 ? (
                <div className="text-center py-12">
                  <LinkIcon className="w-12 h-12 text-surface-600 mx-auto mb-3" />
                  <p className="text-surface-400">暂无实体关系</p>
                  <p className="text-sm text-surface-500 mt-1">{readOnly ? '该版本没有实体关系定义' : '点击左侧工具栏的"关系"按钮创建'}</p>
                </div>
              ) : (
                linkTypes.map((link) => (
                  <div
                    key={link.id}
                    className="p-4 bg-surface-800/50 border border-surface-700 rounded-xl hover:border-surface-600 transition-all"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <h3 className="font-medium text-surface-100 truncate">
                          {link.displayName}
                        </h3>
                        <p className="text-xs text-surface-500 font-mono mt-0.5">
                          {link.name}
                        </p>
                      </div>
                      <button
                        onClick={() => handleOpen(link.id)}
                        className="p-1.5 text-surface-500 hover:text-onto-400 hover:bg-onto-500/10 rounded transition-colors"
                        title={readOnly ? '查看关系详情' : '编辑关系'}
                      >
                        {readOnly ? <EyeIcon className="w-4 h-4" /> : <PencilIcon className="w-4 h-4" />}
                      </button>
                      {!readOnly && (
                        <button
                          onClick={() => handleDelete(link.id, link.displayName)}
                          className="p-1.5 text-surface-500 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors"
                          title="删除关系"
                        >
                          <TrashIcon className="w-4 h-4" />
                        </button>
                      )}
                    </div>

                    {link.description && (
                      <p className="text-sm text-surface-400 mt-2 line-clamp-2">
                        {link.description}
                      </p>
                    )}

                    <div className="flex flex-wrap items-center gap-2 mt-3">
                      <span className="px-2 py-0.5 text-xs bg-indigo-500/20 text-indigo-400 rounded-full">
                        {getObjectTypeName(link.sourceObjectTypeId)}
                      </span>
                      <span className="text-xs text-surface-500">→</span>
                      <span className="px-2 py-0.5 text-xs bg-indigo-500/20 text-indigo-400 rounded-full">
                        {getObjectTypeName(link.targetObjectTypeId)}
                      </span>
                      <span className="px-1.5 py-0.5 text-xs bg-cyan-500/20 text-cyan-400 rounded-full">
                        {getCardinalityLabel(link.cardinality)}
                      </span>
                    </div>

                    {link.sourceRole && (
                      <div className="flex items-center gap-2 mt-2">
                        <span className="text-xs text-surface-500">源角色：</span>
                        <span className="text-xs text-surface-300">{link.sourceRole}</span>
                      </div>
                    )}
                    {link.targetRole && (
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-xs text-surface-500">目标角色：</span>
                        <span className="text-xs text-surface-300">{link.targetRole}</span>
                      </div>
                    )}

                    {link.properties && link.properties.length > 0 && (
                      <div className="mt-3 pt-3 border-t border-surface-700">
                        <p className="text-xs text-surface-500 mb-2">
                          关系属性 ({link.properties.length})：
                        </p>
                        <div className="space-y-1">
                          {link.properties.map((prop) => (
                            <div key={prop.id} className="flex items-center gap-2">
                              <span className="text-xs text-surface-300">{prop.displayName || prop.name}</span>
                              <span className="text-xs text-surface-600">{prop.type}</span>
                              {prop.required && (
                                <span className="text-xs text-red-400">必填</span>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        </>
      )}
    </>
  );
}
