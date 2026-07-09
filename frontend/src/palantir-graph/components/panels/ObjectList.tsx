import { useState } from 'react';
import {
  CubeIcon,
  PencilIcon,
  TrashIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';
import { useOntologyStore } from '../../store/ontologyStore';

interface ObjectListProps {
  isOpen?: boolean;
  onClose?: () => void;
}

export default function ObjectList({ isOpen, onClose }: ObjectListProps) {
  const { ontology, deleteObjectType, setSelectedNode, openPanel } = useOntologyStore();
  const [internalOpen, setInternalOpen] = useState(false);

  const panelOpen = isOpen !== undefined ? isOpen : internalOpen;
  const closePanel = () => {
    setInternalOpen(false);
    onClose?.();
  };

  const handleEdit = (id: string) => {
    setSelectedNode(id);
    openPanel('edit', 'objectType');
    closePanel();
  };

  if (!ontology) return null;

  const objectTypes = ontology.objectTypes;

  const handleDelete = (id: string, name: string) => {
    if (confirm(`确定要删除对象「${name}」吗？删除后相关的关系和实例也会被清理。`)) {
      deleteObjectType(id);
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
                <div className="w-8 h-8 rounded-lg bg-indigo-500/20 flex items-center justify-center">
                  <CubeIcon className="w-5 h-5 text-indigo-500" />
                </div>
                <div>
                  <h2 className="font-display font-semibold text-surface-100">对象实体列表</h2>
                  <p className="text-xs text-surface-500">共 {objectTypes.length} 个对象</p>
                </div>
              </div>
              <button
                onClick={closePanel}
                className="p-2 text-surface-400 hover:text-surface-200 hover:bg-surface-700 rounded-lg transition-colors"
              >
                <XMarkIcon className="w-5 h-5" />
              </button>
            </div>

            {/* Object Type List */}
            <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3">
              {objectTypes.length === 0 ? (
                <div className="text-center py-12">
                  <CubeIcon className="w-12 h-12 text-surface-600 mx-auto mb-3" />
                  <p className="text-surface-400">暂无对象实体</p>
                  <p className="text-sm text-surface-500 mt-1">点击左侧工具栏的"对象"按钮创建</p>
                </div>
              ) : (
                objectTypes.map((obj) => (
                  <div
                    key={obj.id}
                    className="p-4 bg-surface-800/50 border border-surface-700 rounded-xl hover:border-surface-600 transition-all"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-center gap-3 min-w-0 flex-1">
                        <div
                          className="w-9 h-9 shrink-0 rounded-lg flex items-center justify-center text-lg"
                          style={{ backgroundColor: obj.color ? `${obj.color}20` : undefined }}
                        >
                          {obj.icon || '📦'}
                        </div>
                        <div className="min-w-0 flex-1">
                          <h3 className="font-medium text-surface-100 truncate">
                            {obj.displayName}
                          </h3>
                          <p className="text-xs text-surface-500 font-mono mt-0.5">
                            {obj.name}
                          </p>
                        </div>
                      </div>
                      <button
                        onClick={() => handleEdit(obj.id)}
                        className="p-1.5 text-surface-500 hover:text-onto-400 hover:bg-onto-500/10 rounded transition-colors shrink-0"
                        title="编辑对象"
                      >
                        <PencilIcon className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => handleDelete(obj.id, obj.displayName)}
                        className="p-1.5 text-surface-500 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors shrink-0"
                        title="删除对象"
                      >
                        <TrashIcon className="w-4 h-4" />
                      </button>
                    </div>

                    {obj.description && (
                      <p className="text-sm text-surface-400 mt-2 line-clamp-2">
                        {obj.description}
                      </p>
                    )}

                    <div className="flex flex-wrap items-center gap-2 mt-3">
                      <span className="text-xs text-surface-500">主键：</span>
                      <span className="px-2 py-0.5 text-xs bg-indigo-500/20 text-indigo-400 rounded-full font-mono">
                        {obj.primaryKey}
                      </span>
                      <span className="px-2 py-0.5 text-xs bg-surface-700 text-surface-400 rounded-full">
                        {obj.properties?.length || 0} 个属性
                      </span>
                    </div>

                    {obj.properties && obj.properties.length > 0 && (
                      <div className="mt-3 pt-3 border-t border-surface-700">
                        <p className="text-xs text-surface-500 mb-2">
                          属性列表 ({obj.properties.length})：
                        </p>
                        <div className="space-y-1">
                          {obj.properties.slice(0, 8).map((prop) => (
                            <div key={prop.id} className="flex items-center gap-2">
                              <span className="text-xs text-surface-300">{prop.displayName || prop.name}</span>
                              <span className="text-xs text-surface-600">{prop.type}</span>
                              {prop.required && (
                                <span className="text-xs text-red-400">必填</span>
                              )}
                            </div>
                          ))}
                          {obj.properties.length > 8 && (
                            <p className="text-xs text-surface-600">
                              ...还有 {obj.properties.length - 8} 个属性
                            </p>
                          )}
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
