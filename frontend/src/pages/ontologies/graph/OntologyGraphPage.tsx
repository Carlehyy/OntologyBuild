import { useNavigate, useParams, useSearchParams } from 'react-router-dom';

import GraphWorkspace from '../../../palantir-graph/GraphWorkspace';
import { MappingWorkspace } from '../mapping/MappingConfigurationPage';

/**
 * 图谱编辑器路由页（薄壳）：只负责读取路由参数并把工作区组装委托给
 * GraphWorkspace；`?view=mapping` 时整页切换为数据映射工作台。
 */
export default function OntologyGraphPage() {
  const { id: ontologyId = '' } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const versionId = searchParams.get('versionId');
  const mappingView = searchParams.get('view') === 'mapping';

  if (mappingView) {
    const modelStructurePath = versionId
      ? `/ontologies/${ontologyId}/graph?versionId=${encodeURIComponent(versionId)}`
      : `/ontologies/${ontologyId}/graph`;
    return (
      <div className="fixed inset-0 z-[9999] h-screen w-screen overflow-hidden">
        <MappingWorkspace
          ontologyId={ontologyId}
          versionId={versionId}
          focus={searchParams.get('focus')}
          onOpenModelStructure={() => navigate(modelStructurePath)}
        />
      </div>
    );
  }

  if (!ontologyId) return null;

  return (
    <GraphWorkspace
      ontologyId={ontologyId}
      versionId={versionId}
      onOpenMapping={() => {
        const params = new URLSearchParams();
        if (versionId) params.set('versionId', versionId);
        params.set('view', 'mapping');
        navigate(`/ontologies/${ontologyId}/graph?${params.toString()}`);
      }}
      onBackToVersions={() => navigate(`/ontologies/${ontologyId}?tab=versions`)}
    />
  );
}
