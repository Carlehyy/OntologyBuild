import { useState } from 'react';
import {
  CodeBracketIcon,
  PencilIcon,
  TrashIcon,
  XMarkIcon,
  PlayIcon,
  CubeIcon,
  Squares2X2Icon,
  ShieldCheckIcon,
  MagnifyingGlassIcon,
} from '@heroicons/react/24/outline';
import { useOntologyStore } from '../../store/ontologyStore';
import type { FunctionType } from '../../types/ontology';

const functionTypeMeta: Record<FunctionType, { label: string; icon: React.ElementType; color: string; bgColor: string }> = {
  object: { label: '对象函数', icon: CubeIcon, color: 'text-cyan-400', bgColor: 'bg-cyan-500/20' },
  object_set: { label: '集合函数', icon: Squares2X2Icon, color: 'text-violet-400', bgColor: 'bg-violet-500/20' },
  action_validation: { label: '校验函数', icon: ShieldCheckIcon, color: 'text-red-400', bgColor: 'bg-red-500/20' },
  query: { label: '查询函数', icon: MagnifyingGlassIcon, color: 'text-emerald-400', bgColor: 'bg-emerald-500/20' },
};

interface FunctionListProps {
  onTestFunction?: (fnId?: string) => void;
  isOpen?: boolean;
  onClose?: () => void;
}

export default function FunctionList({ onTestFunction, isOpen, onClose }: FunctionListProps) {
  const [internalOpen, setInternalOpen] = useState(false);

  const panelOpen = isOpen !== undefined ? isOpen : internalOpen;
  const closePanel = () => {
    setInternalOpen(false);
    onClose?.();
  };
  const {
    ontology,
    deleteFunction,
    setSelectedFunction,
    openPanel,
  } = useOntologyStore();

  if (!ontology) return null;
  const functions = ontology.functions;
  const objectTypes = ontology.objectTypes;
  const actions = ontology.actions;

  const getTargetName = (fn: typeof functions[number]) => {
    if (fn.functionType === 'object' || fn.functionType === 'object_set') {
      const ot = objectTypes.find((o) => o.id === fn.targetObjectTypeId);
      return ot?.displayName || '未绑定';
    }
    if (fn.functionType === 'action_validation') {
      const a = actions.find((ac) => ac.id === fn.targetActionId);
      return a?.displayName || '未绑定';
    }
    return '全局';
  };

  const handleEdit = (id: string) => {
    closePanel();
    setSelectedFunction(id);
    openPanel('edit', 'function');
  };

  const handleDelete = (id: string) => {
    if (confirm('确定要删除这个函数吗？派生属性和引用该函数的规则会被禁用。')) {
      deleteFunction(id);
    }
  };

  const handleTest = (id: string) => {
    if (onTestFunction) {
      onTestFunction(id);
    }
    closePanel();
  };

  return (
    <>
      {panelOpen && (
        <>
          <div className="fixed inset-0 z-[60] bg-black/30" onClick={closePanel} />

          <div className="fixed right-0 top-0 bottom-0 w-[400px] z-[70] glass border-l border-surface-700 animate-slide-in-right flex flex-col">
            <div className="flex items-center justify-between px-6 py-4 border-b border-surface-700 flex-shrink-0">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-cyan-500/20 flex items-center justify-center">
                  <CodeBracketIcon className="w-5 h-5 text-cyan-500" />
                </div>
                <div>
                  <h2 className="font-display font-semibold text-surface-100">函数列表</h2>
                  <p className="text-xs text-surface-500">共 {functions.length} 个函数</p>
                </div>
              </div>
              <button
                onClick={closePanel}
                className="p-2 text-surface-400 hover:text-surface-200 hover:bg-surface-700 rounded-lg transition-colors"
              >
                <XMarkIcon className="w-5 h-5" />
              </button>
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3">
              {functions.length === 0 ? (
                <div className="text-center py-12">
                  <CodeBracketIcon className="w-12 h-12 text-surface-600 mx-auto mb-3" />
                  <p className="text-surface-400">暂无函数</p>
                  <p className="text-sm text-surface-500 mt-1">点击左侧工具栏的「函数」按钮创建</p>
                </div>
              ) : (
                functions.map((fn) => {
                  const meta = functionTypeMeta[fn.functionType];
                  const MetaIcon = meta.icon;
                  return (
                    <div
                      key={fn.id}
                      className="p-4 bg-surface-800/50 border border-surface-700 rounded-xl hover:border-surface-600 transition-all"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] rounded ${meta.bgColor} ${meta.color}`}>
                              <MetaIcon className="w-3 h-3" />
                              {meta.label}
                            </span>
                            <h3 className="font-medium text-surface-100 truncate">{fn.displayName}</h3>
                          </div>
                          <p className="text-xs text-surface-500 font-mono mt-0.5 truncate">{fn.name}()</p>
                        </div>
                        <div className="flex gap-1 flex-shrink-0">
                          <button
                            onClick={() => handleTest(fn.id)}
                            className="p-1.5 text-surface-500 hover:text-emerald-400 hover:bg-emerald-500/10 rounded transition-colors"
                            title="测试函数"
                          >
                            <PlayIcon className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => handleEdit(fn.id)}
                            className="p-1.5 text-surface-500 hover:text-cyan-400 hover:bg-cyan-500/10 rounded transition-colors"
                            title="编辑"
                          >
                            <PencilIcon className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => handleDelete(fn.id)}
                            className="p-1.5 text-surface-500 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors"
                            title="删除"
                          >
                            <TrashIcon className="w-4 h-4" />
                          </button>
                        </div>
                      </div>

                      {fn.description && (
                        <p className="text-sm text-surface-400 mt-2 line-clamp-2">{fn.description}</p>
                      )}

                      <div className="flex items-center gap-2 mt-3 flex-wrap">
                        <span className="text-xs text-surface-500">绑定：</span>
                        <span className="px-2 py-0.5 text-xs bg-surface-700/70 text-surface-300 rounded-full">
                          {getTargetName(fn)}
                        </span>
                        <span className="text-xs text-surface-500">→</span>
                        <span className={`px-2 py-0.5 text-xs rounded-full ${
                          fn.returnType === 'number' ? 'bg-blue-500/20 text-blue-400'
                          : fn.returnType === 'string' ? 'bg-green-500/20 text-green-400'
                          : fn.returnType === 'boolean' ? 'bg-yellow-500/20 text-yellow-400'
                          : fn.returnType === 'array' ? 'bg-purple-500/20 text-purple-400'
                          : 'bg-pink-500/20 text-pink-400'
                        }`}>
                          {fn.returnType}
                        </span>
                      </div>

                      {fn.parameters.length > 0 && (
                        <div className="mt-3 pt-3 border-t border-surface-700">
                          <p className="text-xs text-surface-500 mb-1">参数：</p>
                          <div className="space-y-0.5">
                            {fn.parameters.slice(0, 3).map((p) => (
                              <div key={p.id} className="flex items-center justify-between text-xs">
                                <span className="text-surface-300">
                                  {p.name}
                                  {p.required && <span className="text-red-400 ml-0.5">*</span>}
                                </span>
                                <span className="text-surface-500">{p.type}</span>
                              </div>
                            ))}
                            {fn.parameters.length > 3 && (
                              <div className="text-xs text-surface-500">+{fn.parameters.length - 3} 更多...</div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </>
      )}
    </>
  );
}
