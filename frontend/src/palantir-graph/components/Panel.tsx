import { useState, useEffect } from 'react';
import { XMarkIcon, TrashIcon, PlayIcon, BeakerIcon } from '@heroicons/react/24/outline';
import { useOntologyStore } from '../store/ontologyStore';
import PropertyEditor from './editors/PropertyEditor';
import ParameterEditor from './editors/ParameterEditor';
import RuleEditor from './editors/RuleEditor';
import FunctionParameterEditor from './editors/FunctionParameterEditor';
import type { Property, ActionParameter, ActionRule, FunctionParameter, FunctionType, FunctionLanguage, CacheStrategy as FunctionCacheStrategy, PropertyType } from '../types/ontology';
import { sanitizeIdentifier } from '../utils/identifier';

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

export default function Panel() {
  const {
    isPanelOpen,
    panelMode,
    panelType,
    closePanel,
    selectedNodeId,
    selectedEdgeId,
    selectedActionId,
    selectedFunctionId,
  } = useOntologyStore();

  if (!isPanelOpen || !panelType) return null;

  return (
    <div className="fixed right-0 top-0 bottom-0 w-[420px] z-50 panel-enter">
      <div className="h-full glass border-l border-surface-700 flex flex-col">
        {panelType === 'objectType' && (
          <ObjectTypePanel
            mode={panelMode!}
            onClose={closePanel}
            selectedId={selectedNodeId}
          />
        )}
        {panelType === 'linkType' && (
          <LinkTypePanel
            mode={panelMode!}
            onClose={closePanel}
            selectedId={selectedEdgeId}
          />
        )}
        {panelType === 'action' && (
          <ActionPanel
            mode={panelMode!}
            onClose={closePanel}
            selectedId={selectedActionId}
          />
        )}
        {panelType === 'function' && (
          <FunctionPanel
            mode={panelMode!}
            onClose={closePanel}
            selectedId={selectedFunctionId}
          />
        )}
      </div>
    </div>
  );
}

// Object Type Panel
function ObjectTypePanel({
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
  const [icon, setIcon] = useState(existingObject?.icon || '📦');
  const [properties, setProperties] = useState<Property[]>(existingObject?.properties || []);
  const [primaryKey, setPrimaryKey] = useState(existingObject?.primaryKey || '');

  useEffect(() => {
    if (existingObject) {
      setName(existingObject.name);
      setDisplayName(existingObject.displayName);
      setDescription(existingObject.description || '');
      setColor(existingObject.color || colorOptions[0]);
      setIcon(existingObject.icon || '📦');
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

function LinkTypePanel({
  mode,
  onClose,
  selectedId
}: {
  mode: 'create' | 'edit';
  onClose: () => void;
  selectedId: string | null;
}) {
  const { ontology, addLinkType, updateLinkType, deleteLinkType } = useOntologyStore();

  const existingLink = mode === 'edit' && selectedId
    ? ontology?.linkTypes.find(l => l.id === selectedId)
    : null;

  const [name, setName] = useState(existingLink?.name || '');
  const [displayName, setDisplayName] = useState(existingLink?.displayName || '');
  const [description, setDescription] = useState(existingLink?.description || '');
  const [sourceObjectTypeId, setSourceObjectTypeId] = useState(existingLink?.sourceObjectTypeId || '');
  const [targetObjectTypeId, setTargetObjectTypeId] = useState(existingLink?.targetObjectTypeId || '');
  const [properties, setProperties] = useState<Property[]>(existingLink?.properties || []);
  const [cardinality, setCardinality] = useState<'one-to-one' | 'one-to-many' | 'many-to-one' | 'many-to-many'>(
    existingLink?.cardinality || 'one-to-many'
  );

  useEffect(() => {
    if (existingLink) {
      setName(existingLink.name);
      setDisplayName(existingLink.displayName);
      setDescription(existingLink.description || '');
      setSourceObjectTypeId(existingLink.sourceObjectTypeId);
      setTargetObjectTypeId(existingLink.targetObjectTypeId);
      setProperties(existingLink.properties || []);
      setCardinality(existingLink.cardinality);
    }
  }, [existingLink]);

  const handleSave = () => {
    if (!name || !displayName || !sourceObjectTypeId || !targetObjectTypeId) return;

    if (mode === 'create') {
      addLinkType({
        name,
        displayName,
        description,
        sourceObjectTypeId,
        targetObjectTypeId,
        cardinality,
        properties,
      });
    } else if (selectedId) {
      updateLinkType(selectedId, {
        name,
        displayName,
        description,
        sourceObjectTypeId,
        targetObjectTypeId,
        cardinality,
        properties,
      });
    }
    onClose();
  };

  const handleDelete = () => {
    if (!selectedId) return;
    const instanceCount = (ontology?.linkInstances || [])
      .filter(li => li.linkTypeId === selectedId).length;
    const msg = instanceCount > 0
      ? `确定要删除这个实体关系吗？其下 ${instanceCount} 条链接实例将一并删除。`
      : '确定要删除这个实体关系吗？';
    if (confirm(msg)) {
      deleteLinkType(selectedId);
      onClose();
    }
  };

  return (
    <>
      <PanelHeader
        title={mode === 'create' ? '创建实体关系' : '编辑实体关系'}
        onClose={onClose}
      />

      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        <div className="space-y-4">
          <div>
            <label className="input-label">实体关系名称 *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value.replace(/\s/g, '_').toLowerCase())}
              className="input-field font-mono"
              placeholder="link_type_name"
            />
          </div>

          <div>
            <label className="input-label">显示名称 *</label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="input-field"
              placeholder="实体关系显示名称"
            />
          </div>

          <div>
            <label className="input-label">描述</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="input-field resize-none h-20"
              placeholder="描述这个实体关系..."
            />
          </div>

          <div>
            <label className="input-label">源对象实体 *</label>
            <select
              value={sourceObjectTypeId}
              onChange={(e) => setSourceObjectTypeId(e.target.value)}
              className="select-field"
            >
              <option value="">选择源对象实体</option>
              {ontology?.objectTypes.map((ot) => (
                <option key={ot.id} value={ot.id}>{ot.displayName}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="input-label">目标对象实体 *</label>
            <select
              value={targetObjectTypeId}
              onChange={(e) => setTargetObjectTypeId(e.target.value)}
              className="select-field"
            >
              <option value="">选择目标对象实体</option>
              {ontology?.objectTypes.map((ot) => (
                <option key={ot.id} value={ot.id}>{ot.displayName}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="input-label">基数</label>
            <select
              value={cardinality}
              onChange={(e) => setCardinality(e.target.value as typeof cardinality)}
              className="select-field"
            >
              <option value="one-to-one">一对一</option>
              <option value="one-to-many">一对多</option>
              <option value="many-to-one">多对一</option>
              <option value="many-to-many">多对多</option>
            </select>
          </div>
        </div>

        <PropertyEditor
          properties={properties}
          onChange={setProperties}
          showPrimaryKey={false}
          allowComputed={false}
          showDataBinding={false}
        />
      </div>

      <PanelFooter
        onSave={handleSave}
        onDelete={mode === 'edit' ? handleDelete : undefined}
        saveDisabled={!name || !displayName || !sourceObjectTypeId || !targetObjectTypeId}
      />
    </>
  );
}

// Action Panel
function ActionPanel({
  mode,
  onClose,
  selectedId
}: {
  mode: 'create' | 'edit';
  onClose: () => void;
  selectedId?: string | null;
}) {
  const { ontology, addAction, updateAction, deleteAction } = useOntologyStore();

  const existingAction = mode === 'edit' && selectedId
    ? ontology?.actions.find(a => a.id === selectedId)
    : null;

  const [name, setName] = useState(existingAction?.name || '');
  const [displayName, setDisplayName] = useState(existingAction?.displayName || '');
  const [description, setDescription] = useState(existingAction?.description || '');
  const [objectTypeId, setObjectTypeId] = useState(existingAction?.objectTypeId || '');
  const [parameters, setParameters] = useState<ActionParameter[]>(existingAction?.parameters || []);
  const [rules, setRules] = useState<ActionRule[]>(existingAction?.rules || []);
  const [requiresApproval, setRequiresApproval] = useState<boolean>(existingAction?.requiresApproval || false);

  useEffect(() => {
    if (existingAction) {
      setName(existingAction.name);
      setDisplayName(existingAction.displayName);
      setDescription(existingAction.description || '');
      setObjectTypeId(existingAction.objectTypeId);
      setParameters(existingAction.parameters);
      setRules(existingAction.rules || []);
      setRequiresApproval(existingAction.requiresApproval || false);
    }
  }, [existingAction]);

  const handleSave = () => {
    if (!name || !displayName || !objectTypeId) return;

    if (mode === 'create') {
      addAction({
        name,
        displayName,
        description,
        objectTypeId,
        parameters,
        rules,
        requiresApproval,
      });
    } else if (selectedId) {
      updateAction(selectedId, {
        name,
        displayName,
        description,
        objectTypeId,
        parameters,
        rules,
        requiresApproval,
      });
    }
    onClose();
  };

  const handleDelete = () => {
    if (selectedId && confirm('确定要删除这个动作吗？')) {
      deleteAction(selectedId);
      onClose();
    }
  };

  return (
    <>
      <PanelHeader
        title={mode === 'create' ? '创建动作' : '编辑动作'}
        onClose={onClose}
      />

      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        <div className="space-y-4">
          <div>
            <label className="input-label">动作名称 *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value.replace(/\s/g, '_').toLowerCase())}
              className="input-field font-mono"
              placeholder="action_name"
            />
          </div>

          <div>
            <label className="input-label">显示名称 *</label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="input-field"
              placeholder="动作显示名称"
            />
          </div>

          <div>
            <label className="input-label">描述</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="input-field resize-none h-20"
              placeholder="描述这个动作..."
            />
          </div>

          <div>
            <label className="input-label">关联对象实体 *</label>
            <select
              value={objectTypeId}
              onChange={(e) => setObjectTypeId(e.target.value)}
              className="select-field"
            >
              <option value="">选择对象实体</option>
              {ontology?.objectTypes.map((ot) => (
                <option key={ot.id} value={ot.id}>{ot.displayName}</option>
              ))}
            </select>
          </div>

          {/* HITL 审批闸门 */}
          <label className="flex items-start gap-3 p-3 rounded-lg border border-surface-700 hover:border-surface-500 cursor-pointer transition-colors">
            <input
              type="checkbox"
              checked={requiresApproval}
              onChange={(e) => setRequiresApproval(e.target.checked)}
              className="mt-0.5 accent-amber-500"
            />
            <span className="text-sm">
              <span className="text-surface-200 font-medium">需人工审批（HITL 闸门）</span>
              <span className="block text-[11px] text-surface-500 mt-0.5 leading-relaxed">
                开启后，真实执行（含哨兵自动触发）先挂起为「待审批」，在运行历史面板由人批准/拒绝；
                批准与拒绝都会写入决策事实，执行产生的变更以决策为因果指针，全程可回放。
              </span>
            </span>
          </label>
        </div>

        {/* Parameter Editor */}
        <ParameterEditor
          parameters={parameters}
          onChange={setParameters}
        />

        {/* Rule Editor */}
        {objectTypeId && (
          <RuleEditor
            rules={rules}
            onChange={setRules}
            objectTypeId={objectTypeId}
          />
        )}
      </div>

      <PanelFooter
        onSave={handleSave}
        onDelete={mode === 'edit' ? handleDelete : undefined}
        saveDisabled={!name || !displayName || !objectTypeId}
      />
    </>
  );
}

// Shared Components
function PanelHeader({ title, onClose }: { title: string; onClose: () => void }) {
  return (
    <div className="flex items-center justify-between px-6 py-4 border-b border-surface-700">
      <h2 className="font-display font-semibold text-lg text-surface-100">{title}</h2>
      <button
        onClick={onClose}
        className="p-2 text-surface-400 hover:text-surface-200 hover:bg-surface-700 rounded-lg transition-colors"
      >
        <XMarkIcon className="w-5 h-5" />
      </button>
    </div>
  );
}

// Function Panel
function FunctionPanel({
  mode,
  onClose,
  selectedId,
}: {
  mode: 'create' | 'edit';
  onClose: () => void;
  selectedId: string | null;
}) {
  const { ontology, addFunction, updateFunction, deleteFunction, openFunctionTester } = useOntologyStore();

  const existingFn = mode === 'edit' && selectedId
    ? ontology?.functions.find(f => f.id === selectedId)
    : null;

  const functionTypes: { id: FunctionType; label: string; desc: string; color: string }[] = [
    { id: 'object', label: '对象函数', desc: '绑定单个对象，计算派生属性', color: 'cyan' },
    { id: 'object_set', label: '集合函数', desc: '面向对象集合的聚合计算', color: 'violet' },
    { id: 'action_validation', label: '校验函数', desc: 'Action 执行前业务校验', color: 'red' },
  ];

  const returnTypes: PropertyType[] = ['string', 'number', 'boolean', 'date', 'datetime', 'array', 'object'];

  const [name, setName] = useState(existingFn?.name || '');
  const [displayName, setDisplayName] = useState(existingFn?.displayName || '');
  const [description, setDescription] = useState(existingFn?.description || '');
  const [functionType, setFunctionType] = useState<FunctionType>(existingFn?.functionType || 'object');
  const [targetObjectTypeId, setTargetObjectTypeId] = useState(existingFn?.targetObjectTypeId || '');
  const [targetActionId, setTargetActionId] = useState(existingFn?.targetActionId || '');
  const [parameters, setParameters] = useState<FunctionParameter[]>(existingFn?.parameters || []);
  type FnReturnType = PropertyType | 'object' | 'object_set' | 'void' | 'validation_result';
  const [returnType, setReturnType] = useState<FnReturnType>(existingFn?.returnType || 'string');
  // 新建默认 expression：后端权威引擎（派生重算/动作校验/哨兵联动）只执行 expression；
  // typescript 仅前端模拟。旧函数无 language 字段的视为 typescript（其函数体是 TS 语句）。
  const [language, setLanguage] = useState<FunctionLanguage>(
    existingFn ? (existingFn.language || 'typescript') : 'expression');
  const [body, setBody] = useState(existingFn?.body || defaultBodyFor(functionType, language));
  const [cacheStrategy, setCacheStrategy] = useState<FunctionCacheStrategy>(existingFn?.cacheStrategy || 'none');
  const [cacheTTL, setCacheTTL] = useState<number>(existingFn?.cacheTTL || 60);

  // 代码模板
  function defaultBodyFor(ft: FunctionType, lang: FunctionLanguage = 'expression'): string {
    if (lang === 'expression') {
      switch (ft) {
        case 'object':
          return '(object.unit_price or 0) * (object.quantity or 1)';
        case 'object_set':
          return '[o for o in objects if o.status == "active"]';
        case 'action_validation':
          return '(params.quantity or 0) <= 100';
        default:
          return 'object.value';
      }
    }
    switch (ft) {
      case 'object':
        return '// object: 当前对象实例的属性\n// params: 传入的参数\n// 示例: 计算总价\nreturn (object.unit_price || 0) * (object.quantity || 1);';
      case 'object_set':
        return '// objectSet: 绑定类型的所有实例数组\n// params: 传入的参数\n// 示例: 过滤低库存\nreturn (objectSet || []).filter(item => item.status === "active");';
      case 'action_validation':
        return '// object: 目标对象（可选）\n// params: Action 参数\n// 返回 { valid: boolean, message?: string } 或 boolean\n// 示例: 校验库存\nif (params.quantity > 100) return { valid: false, message: "数量超过上限" };\nreturn { valid: true };';
      default:
        return '// return your value here';
    }
  }

  useEffect(() => {
    if (existingFn) {
      setName(existingFn.name);
      setDisplayName(existingFn.displayName);
      setDescription(existingFn.description || '');
      setFunctionType(existingFn.functionType);
      setTargetObjectTypeId(existingFn.targetObjectTypeId || '');
      setTargetActionId(existingFn.targetActionId || '');
      setParameters(existingFn.parameters);
      setReturnType(existingFn.returnType);
      setBody(existingFn.body);
      setLanguage(existingFn.language || 'typescript');
      setCacheStrategy(existingFn.cacheStrategy || 'none');
      setCacheTTL(existingFn.cacheTTL || 60);
    } else {
      setBody(defaultBodyFor(functionType, language));
    }

  }, [existingFn]);

  // 切换函数类型/语言时重置 body（仅新建时）
  useEffect(() => {
    if (mode === 'create' && !existingFn) {
      setBody(defaultBodyFor(functionType, language));
    }

  }, [functionType, language]);

  const handleSave = () => {
    if (!name || !displayName) return;
    const payload = {
      name, displayName, description, functionType,
      targetObjectTypeId: (functionType === 'object' || functionType === 'object_set') ? targetObjectTypeId : undefined,
      targetActionId: functionType === 'action_validation' ? targetActionId : undefined,
      parameters,
      returnType: returnType,
      body,
      language,
      enabled: true,
      cacheStrategy,
      cacheTTL: cacheStrategy === 'ttl' ? cacheTTL : undefined,
    };
    if (mode === 'create') {
      addFunction(payload);
    } else if (selectedId) {
      updateFunction(selectedId, payload);
    }
    onClose();
  };

  const handleDelete = () => {
    if (selectedId && confirm('确定删除这个函数吗？引用它的派生属性和规则会被禁用。')) {
      deleteFunction(selectedId);
      onClose();
    }
  };

  const handleTest = () => {
    onClose();
    if (selectedId) openFunctionTester(selectedId);
    else {
      // 先应用到画布再测试
      const newId = addFunction({
        name, displayName, description, functionType,
        targetObjectTypeId: (functionType === 'object' || functionType === 'object_set') ? targetObjectTypeId : undefined,
        targetActionId: functionType === 'action_validation' ? targetActionId : undefined,
        parameters,
        returnType: returnType as PropertyType | 'void' | 'object_set',
        body,
        language,
        enabled: true,
        cacheStrategy,
        cacheTTL: cacheStrategy === 'ttl' ? cacheTTL : undefined,
      });
      openFunctionTester(newId);
    }
  };

  const needObjectTarget = functionType === 'object' || functionType === 'object_set';
  const needActionTarget = functionType === 'action_validation';

  return (
    <>
      <PanelHeader title={mode === 'create' ? '创建函数' : '编辑函数'} onClose={onClose} />

      <div className="flex-1 overflow-y-auto p-6 space-y-5">
        {/* 基本信息 */}
        <div className="space-y-4">
          <div>
            <label className="input-label">函数名称 *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value.replace(/\s/g, '_').toLowerCase())}
              className="input-field font-mono"
              placeholder="function_name"
            />
          </div>
          <div>
            <label className="input-label">显示名称 *</label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="input-field"
              placeholder="函数显示名称"
            />
          </div>
          <div>
            <label className="input-label">描述</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="input-field resize-none h-16"
              placeholder="描述这个函数的用途..."
            />
          </div>
        </div>

        {/* 函数类型 */}
        <div className="space-y-2">
          <label className="input-label">函数类型 *</label>
          <div className="grid grid-cols-2 gap-2">
            {functionTypes.map(ft => (
              <button
                key={ft.id}
                onClick={() => setFunctionType(ft.id)}
                className={`p-3 rounded-lg border text-left transition-all ${
                  functionType === ft.id
                    ? `border-${ft.color}-500/50 bg-${ft.color}-500/10`
                    : 'border-surface-700 bg-surface-800/50 hover:bg-surface-700/50'
                }`}
                style={functionType === ft.id ? {
                  borderColor: ft.color === 'cyan' ? '#06b6d480' : ft.color === 'violet' ? '#8b5cf680' : ft.color === 'red' ? '#ef444480' : '#10b98180',
                  backgroundColor: ft.color === 'cyan' ? '#06b6d41a' : ft.color === 'violet' ? '#8b5cf61a' : ft.color === 'red' ? '#ef44441a' : '#10b9811a',
                } : {}}
              >
                <div className={`text-sm font-medium ${
                  functionType === ft.id
                    ? ft.color === 'cyan' ? 'text-cyan-400' : ft.color === 'violet' ? 'text-violet-400' : ft.color === 'red' ? 'text-red-400' : 'text-emerald-400'
                    : 'text-surface-200'
                }`}>{ft.label}</div>
                <div className="text-[11px] text-surface-500 mt-0.5 leading-tight">{ft.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* 绑定目标 */}
        {needObjectTarget && (
          <div>
            <label className="input-label">绑定对象实体 *</label>
            <select
              value={targetObjectTypeId}
              onChange={(e) => setTargetObjectTypeId(e.target.value)}
              className="select-field"
            >
              <option value="">选择对象实体</option>
              {ontology?.objectTypes.map(ot => (
                <option key={ot.id} value={ot.id}>{ot.displayName}</option>
              ))}
            </select>
          </div>
        )}
        {needActionTarget && (
          <div>
            <label className="input-label">绑定动作 *</label>
            <select
              value={targetActionId}
              onChange={(e) => setTargetActionId(e.target.value)}
              className="select-field"
            >
              <option value="">选择动作</option>
              {ontology?.actions.map(a => (
                <option key={a.id} value={a.id}>{a.displayName}</option>
              ))}
            </select>
          </div>
        )}

        {/* 返回类型 */}
        <div>
          <label className="input-label">返回类型</label>
          <select
            value={returnType}
            onChange={(e) => setReturnType(e.target.value as PropertyType | 'void')}
            className="select-field"
          >
            <option value="void">void (无返回值)</option>
            {returnTypes.map(t => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>

        {/* 参数 */}
        <FunctionParameterEditor parameters={parameters} onChange={setParameters} />

        {/* 执行语言 */}
        <div className="space-y-2">
          <label className="input-label">执行语言</label>
          <div className="flex gap-2">
            <button
              onClick={() => setLanguage('expression')}
              className={`flex-1 px-3 py-2 rounded-lg border text-sm text-left transition-all ${
                language === 'expression'
                  ? 'border-cyan-500/60 bg-cyan-500/10 text-cyan-300'
                  : 'border-surface-700 text-surface-400 hover:border-surface-500'
              }`}
            >
              <div className="font-medium">expression（推荐）</div>
              <div className="text-[11px] opacity-80">后端权威执行：派生属性自动重算、动作校验、哨兵联动都依赖它</div>
            </button>
            <button
              onClick={() => setLanguage('typescript')}
              className={`flex-1 px-3 py-2 rounded-lg border text-sm text-left transition-all ${
                language === 'typescript'
                  ? 'border-amber-500/60 bg-amber-500/10 text-amber-300'
                  : 'border-surface-700 text-surface-400 hover:border-surface-500'
              }`}
            >
              <div className="font-medium">typescript</div>
              <div className="text-[11px] opacity-80">仅前端模拟执行；后端（保存重算/真实动作）不会运行它</div>
            </button>
          </div>
        </div>

        {/* 函数体 */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="input-label mb-0">函数体 ({language === 'expression' ? '单个表达式' : 'TypeScript'})</label>
            <div className="text-[11px] text-surface-500">安全沙箱执行</div>
          </div>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            className="w-full h-56 bg-surface-950 border border-surface-700 rounded-lg px-3 py-2 font-mono text-xs text-surface-200 resize-none focus:outline-none focus:border-cyan-500/50 leading-relaxed"
            spellCheck={false}
            placeholder={language === 'expression' ? '单个表达式，如 (object.price or 0) * 1.13' : '在此编写 TypeScript 代码...'}
          />
          <div className="flex items-start gap-2 p-2 bg-surface-800/50 rounded-lg text-[11px] text-surface-500 leading-relaxed">
            <BeakerIcon className="w-4 h-4 flex-shrink-0 mt-0.5 text-cyan-500/70" />
            {language === 'expression' ? (
              <div>
                <div><b>可用变量：</b> <code className="text-cyan-400">object</code>（当前对象属性）、<code className="text-cyan-400">params</code>（参数）、<code className="text-cyan-400">objects</code>（对象集合，集合函数）、<code className="text-cyan-400">utils</code>（sum/avg/count/min/max/round/contains…）</div>
                <div><b>写法：</b> 一个表达式即返回值，如 <code>object.score &gt; 80</code>、<code>utils.sum([o.amount for o in objects])</code>；支持 <code>and/or/not</code> 与三元 <code>x if cond else y</code>。</div>
              </div>
            ) : (
              <div>
                <div><b>可用变量：</b> <code className="text-cyan-400">object</code>（当前对象）、<code className="text-cyan-400">params</code>（参数）、<code className="text-cyan-400">objectSet</code>（对象集合，集合函数）、<code className="text-cyan-400">context</code>（上下文，含 allInstances）</div>
                <div><b>返回：</b> 使用 <code>return value;</code> 返回结果。校验函数返回 <code>{'{ valid: boolean, message?: string }'}</code>。<span className="text-amber-400">注意：TS 函数只在前端模拟运行。</span></div>
              </div>
            )}
          </div>
        </div>

        {/* 缓存策略 */}
        <div className="space-y-2">
          <label className="input-label">缓存策略</label>
          <div className="flex gap-2">
            {(['none', 'ttl'] as FunctionCacheStrategy[]).map(s => (
              <button
                key={s}
                onClick={() => setCacheStrategy(s)}
                className={`flex-1 px-3 py-2 rounded-lg border text-sm transition-all ${
                  cacheStrategy === s
                    ? 'border-cyan-500/50 bg-cyan-500/10 text-cyan-400'
                    : 'border-surface-700 bg-surface-800/50 text-surface-400 hover:bg-surface-700/50'
                }`}
              >
                {s === 'none' ? '不缓存' : `TTL 缓存`}
              </button>
            ))}
          </div>
          {cacheStrategy === 'ttl' && (
            <div className="flex items-center gap-2">
              <input
                type="number"
                value={cacheTTL}
                onChange={(e) => setCacheTTL(parseInt(e.target.value) || 60)}
                className="input-field text-sm"
                min={1}
              />
              <span className="text-xs text-surface-500">秒</span>
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between px-6 py-4 border-t border-surface-700">
        <div className="flex gap-2">
          {mode === 'edit' ? (
            <button onClick={handleDelete} className="btn-danger">
              <TrashIcon className="w-4 h-4" /> 删除
            </button>
          ) : <div />}
          {mode === 'edit' && (
            <button onClick={handleTest} className="btn-secondary text-emerald-400 hover:text-emerald-300">
              <PlayIcon className="w-4 h-4" /> 测试
            </button>
          )}
        </div>
        <button onClick={handleSave} className="btn-primary" disabled={!name || !displayName}>
          应用到画布
        </button>
      </div>
    </>
  );
}

function PanelFooter({
  onSave,
  onDelete,
  saveDisabled
}: {
  onSave: () => void;
  onDelete?: () => void;
  saveDisabled?: boolean;
}) {
  return (
    <div className="flex items-center justify-between px-6 py-4 border-t border-surface-700">
      {onDelete ? (
        <button onClick={onDelete} className="btn-danger">
          <TrashIcon className="w-4 h-4" />
          删除
        </button>
      ) : (
        <div />
      )}
      <button
        onClick={onSave}
        className="btn-primary"
        disabled={saveDisabled}
      >
        应用到画布
      </button>
    </div>
  );
}
