import { useOntologyStore } from '../store/ontologyStore';
import ActionPanel from './editors/ActionPanel';
import FunctionPanel from './editors/FunctionPanel';
import LinkTypePanel from './editors/LinkTypePanel';
import ObjectTypePanel from './editors/ObjectTypePanel';
import ReadonlyDefinitionPanel from './ReadonlyDefinitionPanel';

export default function Panel({ readOnly = false }: { readOnly?: boolean }) {
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

  if (readOnly && panelType !== 'instance') {
    const selectedId = panelType === 'objectType'
      ? selectedNodeId
      : panelType === 'linkType'
        ? selectedEdgeId
        : panelType === 'action'
          ? selectedActionId
          : selectedFunctionId;
    return (
      <div className="fixed right-0 top-0 bottom-0 w-[420px] z-[80] panel-enter">
        <div className="h-full glass border-l border-surface-700 flex flex-col">
          <ReadonlyDefinitionPanel type={panelType} selectedId={selectedId} onClose={closePanel} />
        </div>
      </div>
    );
  }

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
