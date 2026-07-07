import { useState } from 'react';
import { 
  BoltIcon,
  PencilIcon,
  TrashIcon,
  XMarkIcon,
  PlayIcon,
} from '@heroicons/react/24/outline';
import { useOntologyStore } from '../../store/ontologyStore';

interface ActionListProps {
  onRunAction?: (actionId: string, instanceId?: string) => void;
  isOpen?: boolean;
  onClose?: () => void;
}

export default function ActionList({ onRunAction, isOpen, onClose }: ActionListProps) {
  const { ontology, deleteAction, setSelectedAction, openPanel } = useOntologyStore();
  const [internalOpen, setInternalOpen] = useState(false);

  const panelOpen = isOpen !== undefined ? isOpen : internalOpen;
  const closePanel = () => {
    setInternalOpen(false);
    onClose?.();
  };

  const handleEdit = (id: string) => {
    setSelectedAction(id);
    openPanel('edit', 'action');
    closePanel();
  };

  if (!ontology) return null;

  const actions = ontology.actions;
  const objectTypes = ontology.objectTypes;

  const getObjectTypeName = (id: string) => {
    return objectTypes.find(ot => ot.id === id)?.displayName || '未知对象';
  };

  const handleDelete = (id: string) => {
    if (confirm('确定要删除这个动作吗？')) {
      deleteAction(id);
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
                <div className="w-8 h-8 rounded-lg bg-yellow-500/20 flex items-center justify-center">
                  <BoltIcon className="w-5 h-5 text-yellow-500" />
                </div>
                <div>
                  <h2 className="font-display font-semibold text-surface-100">动作列表</h2>
                  <p className="text-xs text-surface-500">共 {actions.length} 个动作</p>
                </div>
              </div>
              <button
                onClick={closePanel}
                className="p-2 text-surface-400 hover:text-surface-200 hover:bg-surface-700 rounded-lg transition-colors"
              >
                <XMarkIcon className="w-5 h-5" />
              </button>
            </div>

            {/* Action List */}
            <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3">
              {actions.length === 0 ? (
                <div className="text-center py-12">
                  <BoltIcon className="w-12 h-12 text-surface-600 mx-auto mb-3" />
                  <p className="text-surface-400">暂无动作</p>
                  <p className="text-sm text-surface-500 mt-1">点击左侧工具栏的"动作"按钮创建</p>
                </div>
              ) : (
                actions.map((action) => (
                  <div 
                    key={action.id}
                    className="p-4 bg-surface-800/50 border border-surface-700 rounded-xl hover:border-surface-600 transition-all"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <h3 className="font-medium text-surface-100 truncate">
                          {action.displayName}
                        </h3>
                        <p className="text-xs text-surface-500 font-mono mt-0.5">
                          {action.name}
                        </p>
                      </div>
                      <button
                        onClick={() => handleEdit(action.id)}
                        className="p-1.5 text-surface-500 hover:text-onto-400 hover:bg-onto-500/10 rounded transition-colors"
                        title="编辑动作"
                      >
                        <PencilIcon className="w-4 h-4" />
                      </button>
                      {onRunAction && (
                        <button
                          onClick={() => { closePanel(); onRunAction(action.id); }}
                          className="p-1.5 text-surface-500 hover:text-green-400 hover:bg-green-500/10 rounded transition-colors"
                          title="执行动作"
                        >
                          <PlayIcon className="w-4 h-4" />
                        </button>
                      )}
                      <button
                        onClick={() => handleDelete(action.id)}
                        className="p-1.5 text-surface-500 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors"
                        title="删除动作"
                      >
                        <TrashIcon className="w-4 h-4" />
                      </button>
                    </div>
                    
                    {action.description && (
                      <p className="text-sm text-surface-400 mt-2 line-clamp-2">
                        {action.description}
                      </p>
                    )}
                    
                    <div className="flex items-center gap-2 mt-3">
                      <span className="text-xs text-surface-500">关联对象：</span>
                      <span className="px-2 py-0.5 text-xs bg-indigo-500/20 text-indigo-400 rounded-full">
                        {getObjectTypeName(action.objectTypeId)}
                      </span>
                    </div>

                    {action.parameters.length > 0 && (
                      <div className="mt-3 pt-3 border-t border-surface-700">
                        <p className="text-xs text-surface-500 mb-2">参数列表：</p>
                        <div className="space-y-1">
                          {action.parameters.map((param) => (
                            <div key={param.id} className="flex items-center justify-between text-xs">
                              <span className="text-surface-300">
                                {param.name}
                                {param.required && <span className="text-red-400 ml-0.5">*</span>}
                              </span>
                              <span className={`type-badge type-${param.type}`}>
                                {param.type}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {action.rules && action.rules.length > 0 && (
                      <div className="mt-3 pt-3 border-t border-surface-700">
                        <p className="text-xs text-surface-500 mb-2">执行规则：</p>
                        <div className="space-y-1">
                          {action.rules.map((rule) => (
                            <div key={rule.id} className="flex items-center gap-2 text-xs">
                              <span className={`w-2 h-2 rounded-full ${rule.enabled ? 'bg-green-400' : 'bg-surface-500'}`} />
                              <span className="text-surface-300">{rule.name}</span>
                              <span className="text-surface-500">({rule.type})</span>
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
