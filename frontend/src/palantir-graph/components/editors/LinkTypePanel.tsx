import { useEffect, useState } from 'react';
import { useOntologyStore } from '../../store/ontologyStore';
import type { Property } from '../../types/ontology';
import PropertyEditor from './PropertyEditor';
import { PanelFooter, PanelHeader } from './DefinitionPanelShell';

export default function LinkTypePanel({
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
