import type { ReactNode } from 'react';
import {
  BoltIcon,
  CodeBracketIcon,
  CubeIcon,
  EyeIcon,
  LinkIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';
import { useOntologyStore } from '../store/ontologyStore';
import type { Property } from '../types/ontology';

type DefinitionType = 'objectType' | 'linkType' | 'action' | 'function';

interface ReadonlyDefinitionPanelProps {
  type: DefinitionType;
  selectedId: string | null;
  onClose: () => void;
}

const cardinalityLabels: Record<string, string> = {
  'one-to-one': '一对一 (1:1)',
  'one-to-many': '一对多 (1:N)',
  'many-to-one': '多对一 (N:1)',
  'many-to-many': '多对多 (N:N)',
};

const functionTypeLabels: Record<string, string> = {
  object: '对象函数',
  object_set: '对象集合函数',
  action_validation: '动作校验函数',
};

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-medium uppercase tracking-wider text-surface-500">{title}</h3>
      <div className="overflow-hidden rounded-xl border border-surface-700 bg-surface-800/45">
        {children}
      </div>
    </section>
  );
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[7rem_minmax(0,1fr)] gap-3 border-b border-surface-700/70 px-4 py-3 text-sm last:border-b-0">
      <span className="text-surface-500">{label}</span>
      <span className="min-w-0 break-words text-surface-200">{children || '—'}</span>
    </div>
  );
}

function PropertyRows({ properties, primaryKey }: { properties: Property[]; primaryKey?: string }) {
  if (properties.length === 0) {
    return <div className="px-4 py-5 text-sm text-surface-500">暂无属性</div>;
  }

  return (
    <div className="divide-y divide-surface-700/70">
      {properties.map((property) => {
        const isPrimary = property.id === primaryKey || property.name === primaryKey;
        return (
          <div key={property.id} className="px-4 py-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-sm font-medium text-surface-200">{property.displayName || property.name}</span>
                  {isPrimary && <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-300">主键</span>}
                  {property.required && <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-[10px] text-rose-300">必填</span>}
                  {property.source === 'computed' && <span className="rounded bg-cyan-500/15 px-1.5 py-0.5 text-[10px] text-cyan-300">计算属性</span>}
                </div>
                <div className="mt-0.5 font-mono text-xs text-surface-500">{property.name}</div>
              </div>
              <span className={`type-badge type-${property.type} shrink-0`}>{property.type}</span>
            </div>
            {property.description && <p className="mt-2 text-xs leading-5 text-surface-400">{property.description}</p>}
            {property.dataBinding?.sourceColumn && (
              <p className="mt-2 text-xs text-surface-500">
                数据来源：{property.dataBinding.datasetName || '数据集'} / {property.dataBinding.sourceColumn}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

function MissingDefinition({ onClose }: { onClose: () => void }) {
  return (
    <>
      <div className="flex items-center justify-between border-b border-surface-700 px-6 py-4">
        <h2 className="font-display text-lg font-semibold text-surface-100">查看定义</h2>
        <button aria-label="关闭详情" onClick={onClose} className="rounded-lg p-2 text-surface-400 transition-colors hover:bg-surface-700 hover:text-surface-200">
          <XMarkIcon className="h-5 w-5" />
        </button>
      </div>
      <div className="flex flex-1 flex-col items-center justify-center px-8 text-center">
        <EyeIcon className="mb-3 h-10 w-10 text-surface-600" />
        <p className="text-sm text-surface-400">没有找到该版本中的定义</p>
      </div>
    </>
  );
}

export default function ReadonlyDefinitionPanel({ type, selectedId, onClose }: ReadonlyDefinitionPanelProps) {
  const ontology = useOntologyStore((state) => state.ontology);
  if (!ontology || !selectedId) return <MissingDefinition onClose={onClose} />;

  const objectName = (id?: string) => ontology.objectTypes.find((item) => item.id === id)?.displayName || '未绑定';
  const actionName = (id?: string) => ontology.actions.find((item) => item.id === id)?.displayName || '未绑定';

  let icon: ReactNode;
  let title: string;
  let subtitle: string;
  let body: ReactNode;

  if (type === 'objectType') {
    const item = ontology.objectTypes.find((candidate) => candidate.id === selectedId);
    if (!item) return <MissingDefinition onClose={onClose} />;
    icon = <CubeIcon className="h-5 w-5 text-indigo-400" />;
    title = item.displayName;
    subtitle = item.name;
    const relatedActions = ontology.actions.filter((action) => action.objectTypeId === item.id);
    body = (
      <>
        <Section title="基本定义">
          <DetailRow label="描述">{item.description || '暂无描述'}</DetailRow>
          <DetailRow label="主键">{item.properties.find((property) => property.id === item.primaryKey || property.name === item.primaryKey)?.displayName || item.primaryKey}</DetailRow>
          <DetailRow label="关联动作">{relatedActions.length ? relatedActions.map((action) => action.displayName).join('、') : '无'}</DetailRow>
        </Section>
        <Section title={`属性 · ${item.properties.length}`}>
          <PropertyRows properties={item.properties} primaryKey={item.primaryKey} />
        </Section>
      </>
    );
  } else if (type === 'linkType') {
    const item = ontology.linkTypes.find((candidate) => candidate.id === selectedId);
    if (!item) return <MissingDefinition onClose={onClose} />;
    icon = <LinkIcon className="h-5 w-5 text-cyan-400" />;
    title = item.displayName;
    subtitle = item.name;
    body = (
      <>
        <Section title="关系定义">
          <DetailRow label="描述">{item.description || '暂无描述'}</DetailRow>
          <DetailRow label="连接对象">{objectName(item.sourceObjectTypeId)} → {objectName(item.targetObjectTypeId)}</DetailRow>
          <DetailRow label="关系基数">{cardinalityLabels[item.cardinality] || item.cardinality}</DetailRow>
          <DetailRow label="源 / 目标角色">{item.sourceRole || '—'} / {item.targetRole || '—'}</DetailRow>
        </Section>
        <Section title={`关系属性 · ${item.properties?.length || 0}`}>
          <PropertyRows properties={item.properties || []} />
        </Section>
      </>
    );
  } else if (type === 'action') {
    const item = ontology.actions.find((candidate) => candidate.id === selectedId);
    if (!item) return <MissingDefinition onClose={onClose} />;
    icon = <BoltIcon className="h-5 w-5 text-amber-400" />;
    title = item.displayName;
    subtitle = item.name;
    body = (
      <>
        <Section title="动作定义">
          <DetailRow label="描述">{item.description || '暂无描述'}</DetailRow>
          <DetailRow label="作用对象">{objectName(item.objectTypeId)}</DetailRow>
          <DetailRow label="人工审批">{item.requiresApproval ? '需要' : '不需要'}</DetailRow>
          <DetailRow label="校验函数">{item.validationFunctionId ? ontology.functions.find((fn) => fn.id === item.validationFunctionId)?.displayName || item.validationFunctionId : '无'}</DetailRow>
        </Section>
        <Section title={`输入参数 · ${item.parameters.length}`}>
          {item.parameters.length ? item.parameters.map((parameter) => (
            <div key={parameter.id} className="flex items-start justify-between gap-3 border-b border-surface-700/70 px-4 py-3 last:border-b-0">
              <div>
                <div className="text-sm text-surface-200">{parameter.displayName || parameter.name}{parameter.required && <span className="ml-1 text-rose-400">*</span>}</div>
                <div className="font-mono text-xs text-surface-500">{parameter.name}</div>
              </div>
              <span className={`type-badge type-${parameter.type}`}>{parameter.type}</span>
            </div>
          )) : <div className="px-4 py-5 text-sm text-surface-500">无需参数</div>}
        </Section>
        <Section title={`执行规则 · ${item.rules?.length || 0}`}>
          {item.rules?.length ? item.rules.map((rule) => (
            <div key={rule.id} className="border-b border-surface-700/70 px-4 py-3 last:border-b-0">
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-surface-200">{rule.name}</span>
                <span className={`text-xs ${rule.enabled ? 'text-emerald-400' : 'text-surface-500'}`}>{rule.enabled ? '已启用' : '未启用'}</span>
              </div>
              <div className="mt-1 font-mono text-xs text-surface-500">{rule.type}</div>
              {rule.description && <p className="mt-2 text-xs text-surface-400">{rule.description}</p>}
            </div>
          )) : <div className="px-4 py-5 text-sm text-surface-500">暂无执行规则</div>}
        </Section>
      </>
    );
  } else {
    const item = ontology.functions.find((candidate) => candidate.id === selectedId);
    if (!item) return <MissingDefinition onClose={onClose} />;
    icon = <CodeBracketIcon className="h-5 w-5 text-cyan-400" />;
    title = item.displayName;
    subtitle = `${item.name}()`;
    const target = item.targetObjectTypeId ? objectName(item.targetObjectTypeId) : actionName(item.targetActionId);
    body = (
      <>
        <Section title="函数定义">
          <DetailRow label="描述">{item.description || '暂无描述'}</DetailRow>
          <DetailRow label="函数类型">{functionTypeLabels[item.functionType] || item.functionType}</DetailRow>
          <DetailRow label="绑定目标">{target}</DetailRow>
          <DetailRow label="语言 / 返回">{item.language} / {item.returnType}</DetailRow>
          <DetailRow label="缓存策略">{item.cacheStrategy === 'ttl' ? `TTL ${item.cacheTTL || 0} 秒` : item.cacheStrategy || 'none'}</DetailRow>
          <DetailRow label="状态">{item.enabled ? '已启用' : '已停用'}</DetailRow>
        </Section>
        <Section title={`输入参数 · ${item.parameters.length}`}>
          {item.parameters.length ? item.parameters.map((parameter) => (
            <div key={parameter.id} className="flex items-start justify-between gap-3 border-b border-surface-700/70 px-4 py-3 last:border-b-0">
              <div>
                <div className="text-sm text-surface-200">{parameter.displayName || parameter.name}{parameter.required && <span className="ml-1 text-rose-400">*</span>}</div>
                <div className="font-mono text-xs text-surface-500">{parameter.name}</div>
              </div>
              <span className={`type-badge type-${parameter.type}`}>{parameter.type}</span>
            </div>
          )) : <div className="px-4 py-5 text-sm text-surface-500">无需参数</div>}
        </Section>
        <Section title="函数体">
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words px-4 py-3 font-mono text-xs leading-5 text-surface-300">{item.body || '暂无函数体'}</pre>
        </Section>
      </>
    );
  }

  return (
    <>
      <div className="flex items-start justify-between border-b border-surface-700 px-6 py-4">
        <div className="flex min-w-0 items-start gap-3">
          <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-800">{icon}</div>
          <div className="min-w-0">
            <div className="mb-1 flex items-center gap-2">
              <h2 className="truncate font-display text-lg font-semibold text-surface-100">{title}</h2>
              <span className="shrink-0 rounded border border-surface-600 bg-surface-800 px-1.5 py-0.5 text-[10px] text-surface-400">只读查看</span>
            </div>
            <p className="truncate font-mono text-xs text-surface-500">{subtitle}</p>
          </div>
        </div>
        <button aria-label="关闭详情" onClick={onClose} className="rounded-lg p-2 text-surface-400 transition-colors hover:bg-surface-700 hover:text-surface-200">
          <XMarkIcon className="h-5 w-5" />
        </button>
      </div>
      <div className="flex-1 space-y-5 overflow-y-auto p-6" data-testid="readonly-definition-detail">
        {body}
      </div>
      <div className="border-t border-surface-700 px-6 py-3 text-xs leading-5 text-surface-500">
        当前版本的定义不可修改；可继续在画布中移动节点、缩放与查看关系。
      </div>
    </>
  );
}
