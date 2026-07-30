import { useEffect, useState } from 'react';
import { useOntologyStore } from '../../store/ontologyStore';
import type { ActionParameter, ActionRule } from '../../types/ontology';
import ParameterEditor from './ParameterEditor';
import RuleEditor from './RuleEditor';
import { PanelFooter, PanelHeader } from './DefinitionPanelShell';

// Action Panel
export default function ActionPanel({
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
