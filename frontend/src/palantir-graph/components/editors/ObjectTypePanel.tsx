import { useEffect, useState } from 'react';
import { TrashIcon, XMarkIcon } from '@heroicons/react/24/outline';
import { useOntologyStore } from '../../store/ontologyStore';
import type { Property } from '../../types/ontology';
import { sanitizeIdentifier } from '../../utils/identifier';
import { objectTypeIconGlyph } from '../../utils/objectTypeIcon';
import PropertyEditor from './PropertyEditor';
import { PanelFooter, PanelHeader } from './DefinitionPanelShell';

// Color options for objects
const colorOptions = [
  '#6366f1', // Indigo
  '#8b5cf6', // Purple
  '#06b6d4', // Cyan
  '#10b981', // Emerald
  '#f59e0b', // Amber
  '#ef4444', // Red
  '#ec4899', // Pink
  '#3b82f6', // Blue
];

// Icon options
const iconOptions = ['📦', '👤', '🏢', '📄', '💰', '🚀', '⚙️', '📊', '🔗', '📱', '🖥️', '🎯'];

// Object Type Panel
export default function ObjectTypePanel({
  mode,
  onClose,
  selectedId
}: {
  mode: 'create' | 'edit';
  onClose: () => void;
  selectedId: string | null;
}) {
  const { ontology, addObjectType, updateObjectType, deleteObjectType } = useOntologyStore();
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const existingObject = mode === 'edit' && selectedId
    ? ontology?.objectTypes.find(o => o.id === selectedId)
    : null;

  const [name, setName] = useState(existingObject?.name || '');
  const [displayName, setDisplayName] = useState(existingObject?.displayName || '');
  const [description, setDescription] = useState(existingObject?.description || '');
  const [color, setColor] = useState(existingObject?.color || colorOptions[0]);
  const [icon, setIcon] = useState(objectTypeIconGlyph(existingObject?.icon));
  const [properties, setProperties] = useState<Property[]>(existingObject?.properties || []);
  const [primaryKey, setPrimaryKey] = useState(existingObject?.primaryKey || '');

  useEffect(() => {
    if (existingObject) {
      setName(existingObject.name);
      setDisplayName(existingObject.displayName);
      setDescription(existingObject.description || '');
      setColor(existingObject.color || colorOptions[0]);
      setIcon(objectTypeIconGlyph(existingObject.icon));
      setProperties(existingObject.properties);
      setPrimaryKey(existingObject.primaryKey);
    }
  }, [existingObject]);

  const handleSave = () => {
    if (!name || !displayName) return;

    if (mode === 'create') {
      addObjectType({
        name,
        displayName,
        description,
        color,
        icon,
        properties,
        primaryKey: primaryKey || properties[0]?.id || '',
      });
    } else if (selectedId) {
      updateObjectType(selectedId, {
        name,
        displayName,
        description,
        color,
        icon,
        properties,
        primaryKey,
      });
    }
    onClose();
  };

  const handleDelete = () => {
    // 不再直接 confirm 删除，而是打开二次确认浮层，展示将连带删除的关联内容
    if (selectedId) setShowDeleteConfirm(true);
  };

  const handleConfirmDelete = () => {
    if (selectedId) {
      deleteObjectType(selectedId);
      setShowDeleteConfirm(false);
      onClose();
    }
  };

  // 计算与当前对象实体关联的内容（用于浮层展示）
  const relatedLinks = selectedId
    ? (ontology?.linkTypes || []).filter(lt => lt.sourceObjectTypeId === selectedId || lt.targetObjectTypeId === selectedId)
    : [];
  const relatedActions = selectedId
    ? (ontology?.actions || []).filter(a => a.objectTypeId === selectedId)
    : [];
  const relatedFunctions = selectedId
    ? (ontology?.functions || []).filter(f => f.targetObjectTypeId === selectedId)
    : [];
  const relatedInstances = selectedId
    ? (ontology?.instances || []).filter(i => i.objectTypeId === selectedId)
    : [];
  // 连带删除的链接实例：属于被删关系类型的，或端点是被删实例的
  const relatedLinkInstances = selectedId
    ? (() => {
        const linkTypeIds = new Set(relatedLinks.map(lt => lt.id));
        const instanceIds = new Set(relatedInstances.map(i => i.id));
        return (ontology?.linkInstances || []).filter(li =>
          linkTypeIds.has(li.linkTypeId) || instanceIds.has(li.sourceObjectId) || instanceIds.has(li.targetObjectId));
      })()
    : [];
  const hasAnyRelation = relatedLinks.length || relatedActions.length || relatedFunctions.length || relatedInstances.length || relatedLinkInstances.length;

  return (
    <>
      <PanelHeader
        title={mode === 'create' ? '创建对象实体' : '编辑对象实体'}
        onClose={onClose}
      />

      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Basic Info */}
        <div className="space-y-4">
          <div>
            <label className="input-label">实体标识 *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(sanitizeIdentifier(e.target.value))}
              className="input-field font-mono"
              placeholder="object_entity_name"
            />
          </div>

          <div>
            <label className="input-label">实体名称 *</label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="input-field"
              placeholder="对象实体显示名称"
            />
          </div>

          <div>
            <label className="input-label">实体描述</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="input-field resize-none h-20"
              placeholder="描述这个对象实体..."
            />
          </div>
        </div>

        {/* Appearance */}
        <div className="space-y-4">
          <div>
            <label className="input-label">颜色</label>
            <div className="flex gap-2 flex-wrap">
              {colorOptions.map((c) => (
                <button
                  key={c}
                  onClick={() => setColor(c)}
                  className={`w-8 h-8 rounded-lg transition-all ${
                    color === c ? 'ring-2 ring-white ring-offset-2 ring-offset-surface-800' : ''
                  }`}
                  style={{ backgroundColor: c }}
                />
              ))}
            </div>
          </div>

          <div>
            <label className="input-label">图标</label>
            <div className="flex gap-2 flex-wrap">
              {iconOptions.map((i) => (
                <button
                  key={i}
                  onClick={() => setIcon(i)}
                  className={`w-10 h-10 rounded-lg bg-surface-700 hover:bg-surface-600 text-xl flex items-center justify-center transition-all ${
                    icon === i ? 'ring-2 ring-onto-500' : ''
                  }`}
                >
                  {i}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Properties */}
        <PropertyEditor
          properties={properties}
          onChange={setProperties}
          primaryKey={primaryKey}
          onPrimaryKeyChange={setPrimaryKey}
          objectTypeId={mode === 'edit' ? selectedId || undefined : undefined}
        />
      </div>

      <PanelFooter
        onSave={handleSave}
        onDelete={mode === 'edit' ? handleDelete : undefined}
        saveDisabled={!name || !displayName}
      />

      {/* 删除二次确认浮层：展示将连带删除的关联内容 */}
      {showDeleteConfirm && selectedId && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
          <div className="relative w-full max-w-lg bg-gradient-to-b from-surface-800 to-surface-900 border border-red-500/30 rounded-2xl shadow-2xl flex flex-col max-h-[85vh]">
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-surface-700">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-red-500/20">
                  <TrashIcon className="w-5 h-5 text-red-400" />
                </div>
                <h3 className="font-display font-semibold text-lg text-surface-100">
                  删除对象实体「{existingObject?.displayName || name}」
                </h3>
              </div>
              <button
                onClick={() => setShowDeleteConfirm(false)}
                className="p-2 text-surface-400 hover:text-surface-200 hover:bg-surface-700 rounded-lg transition-colors"
              >
                <XMarkIcon className="w-5 h-5" />
              </button>
            </div>

            {/* Body */}
            <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
              {hasAnyRelation ? (
                <>
                  <p className="text-sm text-surface-300">此操作不可撤销。删除该对象实体将一并清除以下关联内容：</p>
                  {relatedLinks.length > 0 && (
                    <div className="space-y-1.5">
                      <div className="text-xs text-cyan-400 font-medium">🔗 实体关系（{relatedLinks.length} 条）</div>
                      <div className="pl-4 space-y-1">
                        {relatedLinks.map(lt => (
                          <div key={lt.id} className="text-sm text-surface-400 truncate">
                            • {lt.displayName || lt.name}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {relatedActions.length > 0 && (
                    <div className="space-y-1.5">
                      <div className="text-xs text-yellow-400 font-medium">⚡ 执行动作（{relatedActions.length} 个）</div>
                      <div className="pl-4 space-y-1">
                        {relatedActions.map(a => (
                          <div key={a.id} className="text-sm text-surface-400 truncate">
                            • {a.displayName || a.name}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {relatedFunctions.length > 0 && (
                    <div className="space-y-1.5">
                      <div className="text-xs text-pink-400 font-medium">🧮 激活函数（{relatedFunctions.length} 个）</div>
                      <div className="pl-4 space-y-1">
                        {relatedFunctions.map(f => (
                          <div key={f.id} className="text-sm text-surface-400 truncate">
                            • {f.displayName || f.name}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {relatedInstances.length > 0 && (
                    <div className="space-y-1.5">
                      <div className="text-xs text-indigo-400 font-medium">📦 实例数据（{relatedInstances.length} 条）</div>
                      <p className="pl-4 text-xs text-surface-500">数量较多，将随对象实体一并删除，不再展示逐条名称</p>
                    </div>
                  )}
                  {relatedLinkInstances.length > 0 && (
                    <div className="space-y-1.5">
                      <div className="text-xs text-cyan-400 font-medium">🔗 链接实例（{relatedLinkInstances.length} 条）</div>
                      <p className="pl-4 text-xs text-surface-500">涉及被删关系类型或被删实例的链接实例将一并清除</p>
                    </div>
                  )}
                </>
              ) : (
                <p className="text-sm text-surface-300">
                  该对象实体无关联的实体关系、执行动作或激活函数。
                  将仅删除对象实体本体，无级联影响。
                </p>
              )}
            </div>

            {/* Footer */}
            <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-surface-700">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                className="px-4 py-2 rounded-lg text-sm text-surface-300 hover:bg-surface-700 transition-colors"
              >
                取消
              </button>
              <button
                onClick={handleConfirmDelete}
                className="px-4 py-2 bg-red-600 hover:bg-red-500 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5"
              >
                <TrashIcon className="w-4 h-4" />
                {hasAnyRelation ? '确认全部删除' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
