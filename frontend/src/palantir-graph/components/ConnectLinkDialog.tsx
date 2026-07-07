import { useEffect, useState } from 'react';
import { LinkIcon, XMarkIcon } from '@heroicons/react/24/outline';
import { useOntologyStore } from '../store/ontologyStore';
import type { LinkType } from '../types/ontology';

interface Props {
  sourceId: string;
  targetId: string;
  onClose: () => void;
}

const CARDINALITIES: { value: LinkType['cardinality']; label: string }[] = [
  { value: 'one-to-one', label: '1 : 1' },
  { value: 'one-to-many', label: '1 : N' },
  { value: 'many-to-one', label: 'N : 1' },
  { value: 'many-to-many', label: 'N : N' },
];

/**
 * 拖线建关系确认卡片：让用户命名并选择基数后才真正创建 LinkType，
 * 替代旧的"松手即静默创建 A_to_B"行为。
 */
export default function ConnectLinkDialog({ sourceId, targetId, onClose }: Props) {
  const ontology = useOntologyStore((s) => s.ontology);
  const addLinkType = useOntologyStore((s) => s.addLinkType);

  const source = ontology?.objectTypes.find((o) => o.id === sourceId);
  const target = ontology?.objectTypes.find((o) => o.id === targetId);

  const [name, setName] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [cardinality, setCardinality] = useState<LinkType['cardinality']>('one-to-many');

  useEffect(() => {
    if (source && target) {
      setName(`${source.name}_to_${target.name}`);
      setDisplayName(`${source.displayName} → ${target.displayName}`);
    }
  }, [sourceId, targetId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!source || !target) return null;

  const duplicated = !!ontology?.linkTypes.some((lt) => lt.name === name.trim());
  const canCreate = name.trim().length > 0 && displayName.trim().length > 0;

  const handleCreate = () => {
    if (!canCreate) return;
    addLinkType({
      name: name.trim(),
      displayName: displayName.trim(),
      sourceObjectTypeId: sourceId,
      targetObjectTypeId: targetId,
      cardinality,
    });
    onClose();
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={onClose}>
      <div className="bg-surface-800 border border-surface-600 rounded-2xl p-5 w-full max-w-md mx-4 animate-fade-in shadow-2xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="flex items-center gap-2 text-base font-semibold text-surface-100">
            <LinkIcon className="w-5 h-5 text-cyan-400" />
            创建实体关系
          </h3>
          <button onClick={onClose} className="text-surface-500 hover:text-surface-200">
            <XMarkIcon className="w-5 h-5" />
          </button>
        </div>

        <div className="flex items-center justify-center gap-2 mb-4 text-sm">
          <span className="px-2.5 py-1 rounded-lg bg-indigo-500/15 text-indigo-300 border border-indigo-500/25">
            {source.displayName}
          </span>
          <span className="text-surface-500">→</span>
          <span className="px-2.5 py-1 rounded-lg bg-indigo-500/15 text-indigo-300 border border-indigo-500/25">
            {target.displayName}
          </span>
        </div>

        <div className="space-y-3">
          <div>
            <label className="text-xs text-surface-400 mb-1 block">名称（英文标识）</label>
            <input value={name} onChange={(e) => setName(e.target.value)}
              className="input-field w-full font-mono text-sm" placeholder="link_type_name" autoFocus />
            {duplicated && (
              <p className="text-xs text-amber-400 mt-1">已存在同名关系类型，建议换个名称</p>
            )}
          </div>
          <div>
            <label className="text-xs text-surface-400 mb-1 block">显示名称</label>
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)}
              className="input-field w-full text-sm" placeholder="关系显示名称" />
          </div>
          <div>
            <label className="text-xs text-surface-400 mb-1 block">基数</label>
            <div className="grid grid-cols-4 gap-2">
              {CARDINALITIES.map((c) => (
                <button key={c.value} onClick={() => setCardinality(c.value)}
                  className={`py-1.5 rounded-lg text-xs border transition-colors ${
                    cardinality === c.value
                      ? 'bg-cyan-500/20 text-cyan-300 border-cyan-500/40'
                      : 'bg-surface-700/50 text-surface-400 border-transparent hover:bg-surface-700'
                  }`}>
                  {c.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-3 mt-5">
          <button onClick={onClose} className="btn-secondary">取消</button>
          <button onClick={handleCreate} disabled={!canCreate}
            className="btn-primary disabled:opacity-40 disabled:cursor-not-allowed">
            创建关系
          </button>
        </div>
      </div>
    </div>
  );
}
