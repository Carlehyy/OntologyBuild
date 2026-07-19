import type { FullOntologyDTO } from './api/formalApi';

export type OntologyWorkspaceMode = NonNullable<FullOntologyDTO['workspaceMode']>;
export type OntologyDataScope = 'draft' | 'trial' | 'runtime' | 'historical';

/**
 * 图谱编辑器的能力必须分别表达结构、布局和运行数据权限。
 * `workspaceMode` 决定数据作用域与操作能力，不决定功能入口是否存在。
 */
export interface GraphWorkspaceCapabilities {
  mode: OntologyWorkspaceMode;
  dataScope: OntologyDataScope;
  canEditSchema: boolean;
  canEditLayout: true;
  canSearch: true;
  canExport: true;
  canImport: boolean;
  canBrowseInstances: boolean;
  canViewRunHistory: boolean;
  canManageAutonomy: boolean;
  canManageSentinels: boolean;
  canRunActions: boolean;
  canTestFunctions: boolean;
  canViewGraphDatabase: true;
  schemaDisabledReason?: string;
  runtimeDisabledReason?: string;
}

const SCHEMA_DISABLED_REASON: Record<Exclude<OntologyWorkspaceMode, 'draft'>, string> = {
  runtime: '当前发布版本的模型结构不可直接修改，请先创建草稿',
  trial: '试跑快照已冻结结构，请创建新分支后修改',
  release: '历史发布版本不可修改，请基于该版本创建草稿',
  archived: '归档分支不可修改，请创建新分支后继续',
};

const RUNTIME_DISABLED_REASON: Record<Exclude<OntologyWorkspaceMode, 'runtime'>, string> = {
  draft: '草稿不承载运行数据，完成试跑和发布后可用',
  trial: '当前运行面板尚未接入试跑隔离数据，为避免访问正式数据已禁用',
  release: '历史发布快照不承载当前运行数据',
  archived: '归档分支不承载运行数据',
};

export function getGraphWorkspaceCapabilities(mode: OntologyWorkspaceMode): GraphWorkspaceCapabilities {
  const canEditSchema = mode === 'draft';
  const hasRuntimeData = mode === 'runtime';
  const dataScope: OntologyDataScope = mode === 'runtime'
    ? 'runtime'
    : mode === 'trial'
      ? 'trial'
      : mode === 'draft'
        ? 'draft'
        : 'historical';

  return {
    mode,
    dataScope,
    canEditSchema,
    canEditLayout: true,
    canSearch: true,
    canExport: true,
    canImport: canEditSchema,
    canBrowseInstances: hasRuntimeData,
    canViewRunHistory: hasRuntimeData,
    canManageAutonomy: hasRuntimeData,
    canManageSentinels: hasRuntimeData,
    canRunActions: hasRuntimeData,
    canTestFunctions: hasRuntimeData,
    canViewGraphDatabase: true,
    schemaDisabledReason: canEditSchema
      ? undefined
      : SCHEMA_DISABLED_REASON[mode as Exclude<OntologyWorkspaceMode, 'draft'>],
    runtimeDisabledReason: hasRuntimeData
      ? undefined
      : RUNTIME_DISABLED_REASON[mode as Exclude<OntologyWorkspaceMode, 'runtime'>],
  };
}
