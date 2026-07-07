import { useEffect } from 'react';
import { TrashIcon, XMarkIcon } from '@heroicons/react/24/outline';
import { useOntologyStore } from '../store/ontologyStore';

export interface DeleteTarget {
  kind: 'objectType' | 'linkType';
  id: string;
}

/**
 * 键盘 Delete / 右键删除的统一确认对话框：
 * 展示级联影响计数后才执行删除（与 Panel 内的删除浮层同语义，轻量版）。
 */
export default function DeleteSelectedDialog({ target, onClose }: { target: DeleteTarget; onClose: () => void }) {
  const ontology = useOntologyStore((s) => s.ontology);
  const deleteObjectType = useOntologyStore((s) => s.deleteObjectType);
  const deleteLinkType = useOntologyStore((s) => s.deleteLinkType);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose(); }
      if (e.key === 'Enter') { e.preventDefault(); handleConfirm(); }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }); // 依赖 handleConfirm 闭包，每次渲染重绑，量级可忽略

  if (!ontology) return null;

  let title = '';
  let impacts: { label: string; count: number }[] = [];

  if (target.kind === 'objectType') {
    const ot = ontology.objectTypes.find((o) => o.id === target.id);
    if (!ot) return null;
    title = `删除对象实体「${ot.displayName || ot.name}」？`;
    const links = ontology.linkTypes.filter(
      (lt) => lt.sourceObjectTypeId === target.id || lt.targetObjectTypeId === target.id);
    const linkTypeIds = new Set(links.map((l) => l.id));
    const instances = ontology.instances.filter((i) => i.objectTypeId === target.id);
    const instanceIds = new Set(instances.map((i) => i.id));
    impacts = [
      { label: '实体关系', count: links.length },
      { label: '执行动作', count: ontology.actions.filter((a) => a.objectTypeId === target.id).length },
      { label: '激活函数', count: ontology.functions.filter((f) => f.targetObjectTypeId === target.id).length },
      { label: '实例数据', count: instances.length },
      {
        label: '链接实例',
        count: ontology.linkInstances.filter((li) =>
          linkTypeIds.has(li.linkTypeId) || instanceIds.has(li.sourceObjectId) || instanceIds.has(li.targetObjectId)).length,
      },
    ].filter((x) => x.count > 0);
  } else {
    const lt = ontology.linkTypes.find((l) => l.id === target.id);
    if (!lt) return null;
    title = `删除实体关系「${lt.displayName || lt.name}」？`;
    impacts = [
      { label: '链接实例', count: ontology.linkInstances.filter((li) => li.linkTypeId === target.id).length },
    ].filter((x) => x.count > 0);
  }

  const handleConfirm = () => {
    if (target.kind === 'objectType') deleteObjectType(target.id);
    else deleteLinkType(target.id);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-surface-800 border border-surface-600 rounded-2xl p-5 w-full max-w-sm mx-4 animate-fade-in shadow-2xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-surface-100">{title}</h3>
          <button onClick={onClose} className="text-surface-500 hover:text-surface-200">
            <XMarkIcon className="w-5 h-5" />
          </button>
        </div>

        {impacts.length > 0 ? (
          <div className="mb-4 rounded-lg bg-red-900/20 border border-red-500/30 p-3 space-y-1">
            <p className="text-xs text-red-300 mb-1.5">以下关联内容将被一并删除：</p>
            {impacts.map((x) => (
              <div key={x.label} className="flex justify-between text-xs text-surface-300">
                <span>{x.label}</span>
                <span className="font-mono text-red-300">{x.count}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="mb-4 text-xs text-surface-400">无级联影响，仅删除该元素本身。可用 Ctrl+Z 撤销。</p>
        )}

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-3 py-1.5 rounded-lg text-xs text-surface-300 hover:bg-surface-700">
            取消
          </button>
          <button onClick={handleConfirm}
            className="px-3 py-1.5 rounded-lg bg-red-600 hover:bg-red-500 text-white text-xs font-medium flex items-center gap-1">
            <TrashIcon className="w-3.5 h-3.5" />
            删除（Enter）
          </button>
        </div>
      </div>
    </div>
  );
}
