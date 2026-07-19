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
  draft: '草稿可查看完整定义，但不承载实例或执行数据',
  trial: '可查看隔离试跑数据；正式动作、函数和治理操作不可执行',
  release: '可查看完整历史定义，但不读取当前正式运行数据',
  archived: '可查看完整归档定义，但不读取当前正式运行数据',
};

export function getGraphWorkspaceCapabilities(mode: OntologyWorkspaceMode): GraphWorkspaceCapabilities {
  const canEditSchema = mode === 'draft';
  const hasRuntimeData = mode === 'runtime';
  const hasBrowsableInstances = mode === 'runtime' || mode === 'trial';
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
    canBrowseInstances: hasBrowsableInstances,
    canViewRunHistory: hasRuntimeData || mode === 'trial',
    canManageAutonomy: hasRuntimeData,
    // 哨兵属于版本快照中的第五类模型定义；非运行态仍可打开查看，
    // 只有草稿态由面板允许编辑，运行态继续使用线上哨兵接口。
    canManageSentinels: true,
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
