import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { v4 as uuidv4 } from 'uuid';
import type {
  Ontology,
  ObjectType,
  LinkType,
  Action,
  OntologyNode,
  OntologyEdge,
  OntologyFunction,
  ObjectInstance,
  LinkInstance,
  ActionExecutionLog,
  FunctionExecutionResult,
} from '../types/ontology';
import { applyLayout, type LayoutOptions, type LayoutAlgorithm, type LayoutDirection } from '../utils/layoutAlgorithms';
import {
  tradeErpOntology,
  tradeErpNodes,
  tradeErpEdges,
} from './tradeErpDemo';
import { executeAction, getDefaultParameters } from '../engine/actionEngine';
import { executeFunction } from '../engine/functionEngine';
import {
  loadFullOntology, saveFullOntology, isSaveConflict,
  patchOntologyDelta, instanceToPayload, type DeltaKind, type FullOntologyDTO,
} from '../api/formalApi';
import type { Sentinel } from '../../api/sentinelApi';

// Apply default layout (hierarchical, left-to-right)
const defaultLayoutNodes = applyLayout(tradeErpNodes, tradeErpEdges, {
  algorithm: 'dagre',
  direction: 'LR',
  nodeWidth: 280,
  nodeHeight: 120,
  nodeSpacing: 100,
  rankSpacing: 200,
});

// 选择类型
export type SelectionType = 'node' | 'edge' | 'action' | 'function' | 'instance' | null;

interface OntologyState {
  // Current ontology
  ontology: Ontology | null;

  // ============ 后端同步状态 ============
  // 当前绑定的后端本体 id（来自路由 :id）。为 null 时退化为纯本地（localStorage）模式。
  backendId: string | null;
  // 非空时编辑不可变发布版分出的完整草稿快照；运行数据与线上动作均不在此工作区。
  workspaceVersionId: string | null;
  // runtime 仅承载当前正式投影；只有 draft 工作区允许修改模式结构。
  workspaceMode: 'runtime' | 'draft' | 'trial' | 'release' | 'archived';
  // Version-scoped governance/read-model data. Never substitute production
  // records when a draft/trial/history workspace is open.
  workspaceTrialRun: FullOntologyDTO['trialRun'];
  workspaceSentinels: Sentinel[];
  // 加载/保存状态。conflict = 保存被 409 拒绝（其他会话已修改）
  syncStatus: 'idle' | 'loading' | 'saving' | 'saved' | 'error' | 'conflict';
  syncError: string | null;
  lastSavedAt: string | null;
  isDirty: boolean;
  // 乐观并发修订戳（GET /full 返回，保存时带回；保存成功后更新）
  revision: string | null;
  // 最近一次保存触发的哨兵评估摘要（保存响应携带）
  lastSentinelSummary: { evaluated: number; fired: number } | null;

  // Canvas state
  nodes: OntologyNode[];
  edges: OntologyEdge[];

  // Selection state
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  selectedActionId: string | null;
  selectedFunctionId: string | null;
  selectedInstanceId: string | null;

  // UI state
  isPanelOpen: boolean;
  panelMode: 'create' | 'edit' | null;
  panelType: 'objectType' | 'linkType' | 'action' | 'function' | 'instance' | null;

  // Action execution UI
  isActionExecutorOpen: boolean;
  executingActionId: string | null;
  executingTargetInstanceId: string | null;
  lastExecutionLog: ActionExecutionLog | null;

  // Function test UI
  isFunctionTesterOpen: boolean;
  testingFunctionId: string | null;
  lastFunctionResult: FunctionExecutionResult | null;

  // Instance browser
  isInstanceBrowserOpen: boolean;
  instanceBrowserObjectTypeId: string | null;

  // ============ 后端同步 actions ============
  loadFromBackend: (id: string, versionId?: string | null) => Promise<void>;
  /** force=true 跳过冲突检测强制覆盖（用户在冲突对话框里明确选择后才用） */
  saveToBackend: (opts?: { force?: boolean }) => Promise<void>;
  /** 放弃本地未保存改动，从后端重新加载（冲突处理 / 后端模式下的"重置"） */
  discardAndReload: () => Promise<void>;

  // ============ Core actions ============
  createOntology: (name: string, description?: string) => void;

  // Object Type actions
  addObjectType: (objectType: Omit<ObjectType, 'id' | 'createdAt' | 'updatedAt'>) => string;
  updateObjectType: (id: string, updates: Partial<ObjectType>) => void;
  deleteObjectType: (id: string) => void;

  // Link Type actions
  addLinkType: (linkType: Omit<LinkType, 'id' | 'createdAt' | 'updatedAt'>) => string;
  updateLinkType: (id: string, updates: Partial<LinkType>) => void;
  deleteLinkType: (id: string) => void;

  // Interface actions

  // Action actions
  addAction: (action: Omit<Action, 'id' | 'createdAt' | 'updatedAt'>) => string;
  updateAction: (id: string, updates: Partial<Action>) => void;
  deleteAction: (id: string) => void;

  // ============ Functions actions ============
  addFunction: (fn: Omit<OntologyFunction, 'id' | 'createdAt' | 'updatedAt'>) => string;
  updateFunction: (id: string, updates: Partial<OntologyFunction>) => void;
  deleteFunction: (id: string) => void;

  // ============ Instance actions ============
  addInstance: (instance: Omit<ObjectInstance, 'createdAt' | 'updatedAt'> & { id?: string }) => string;
  updateInstance: (id: string, updates: Partial<ObjectInstance>) => void;
  deleteInstance: (id: string) => void;

  addLinkInstance: (link: Omit<LinkInstance, 'id' | 'createdAt'>) => string;
  deleteLinkInstance: (id: string) => void;

  // ============ Runtime actions ============
  openActionExecutor: (actionId: string, targetInstanceId?: string) => void;
  closeActionExecutor: () => void;
  runAction: (parameters: Record<string, unknown>, dryRun?: boolean) => ActionExecutionLog;

  openFunctionTester: (functionId: string) => void;
  closeFunctionTester: () => void;
  testFunction: (functionId: string, params: Record<string, unknown>, objectPropsOrId?: Record<string, unknown> | string) => FunctionExecutionResult;

  openInstanceBrowser: (objectTypeId: string) => void;
  closeInstanceBrowser: () => void;

  // ============ Helpers ============
  getInstancesForType: (objectTypeId: string) => ObjectInstance[];

  // ============ Canvas actions ============
  setNodes: (nodes: OntologyNode[]) => void;
  setEdges: (edges: OntologyEdge[]) => void;
  updateNodePosition: (id: string, position: { x: number; y: number }) => void;

  // ============ Selection actions ============
  setSelectedNode: (id: string | null) => void;
  setSelectedEdge: (id: string | null) => void;
  setSelectedAction: (id: string | null) => void;
  setSelectedFunction: (id: string | null) => void;
  setSelectedInstance: (id: string | null) => void;

  // ============ Panel actions ============
  openPanel: (mode: 'create' | 'edit', type: 'objectType' | 'linkType' | 'action' | 'function' | 'instance') => void;
  closePanel: () => void;

  // 画布右键"在此创建"时暂存的目标位置；addObjectType 用后即清
  pendingNodePosition: { x: number; y: number } | null;
  setPendingNodePosition: (p: { x: number; y: number } | null) => void;

  // ============ Import/Export ============
  exportOntology: () => string;
  importOntology: (json: string) => void;

  // ============ Undo / Redo ============
  canUndo: boolean;
  canRedo: boolean;
  undo: () => void;
  redo: () => void;

  // Reset
  reset: () => void;

  // Auto Layout
  autoLayout: (algorithm: LayoutAlgorithm, direction?: LayoutDirection) => void;
}

const now = () => new Date().toISOString();

// 保存成功后，"已保存"提示的复位定时器（手动保存模式下，saved 状态停留 3s 后回到 idle）
let _savedResetTimer: ReturnType<typeof setTimeout> | null = null;

// ============ Undo/Redo 历史栈（模块级，不进 persist） ============
interface HistorySnapshot {
  ontology: Ontology | null;
  nodes: OntologyNode[];
  edges: OntologyEdge[];
}
const HISTORY_LIMIT = 50;
let _histPast: HistorySnapshot[] = [];
let _histFuture: HistorySnapshot[] = [];
// undo/redo 自身触发的 set 不再记录历史
let _timeTravel = false;
// 后端加载会整体替换状态，期间不记录且清空历史
let _histSuspended = false;
// 保存成功后回填服务端计算结果（computed）时置位：
// 该变更不标记 dirty、不进撤销历史（它不是用户编辑）
let _syncApplying = false;

// ============ 增量保存的变更追踪（模块级，不进 persist） ============
// 记录自上次成功保存以来"改了哪些实体、删了哪些实体"。
// full=true 表示无法可靠追踪（首次进入 / undo/redo / 导入 / 重置），下次保存退回全量。
function _newKindSets(): Record<DeltaKind, Set<string>> {
  return {
    objectTypes: new Set(), linkTypes: new Set(), actions: new Set(),
    functions: new Set(), instances: new Set(), linkInstances: new Set(),
  };
}
const _delta = {
  full: true,
  upserts: _newKindSets(),
  deletes: _newKindSets(),
};
function _resetDelta(full: boolean) {
  _delta.full = full;
  _delta.upserts = _newKindSets();
  _delta.deletes = _newKindSets();
}
function _markChanged(kind: DeltaKind, id: string | undefined | null) {
  if (!id) return;
  _delta.upserts[kind].add(id);
  _delta.deletes[kind].delete(id);
}
function _markDeleted(kind: DeltaKind, id: string | undefined | null) {
  if (!id) return;
  _delta.deletes[kind].add(id);
  _delta.upserts[kind].delete(id);
}
function _markFull() {
  _delta.full = true;
}
function _deltaCount(): number {
  let n = 0;
  for (const k of Object.keys(_delta.upserts) as DeltaKind[]) {
    n += _delta.upserts[k].size + _delta.deletes[k].size;
  }
  return n;
}

function _migrateOntology(raw: Ontology | null | undefined): Ontology | null {
  if (!raw) return raw as Ontology | null;
  const rawAny = raw as unknown as Record<string, unknown>;
  const execLogs = (rawAny.executionLogs as ActionExecutionLog[]) || (rawAny.auditLogs as ActionExecutionLog[]) || [];
  return {
    ...raw,
    functions: raw.functions || [],
    instances: (Array.isArray(raw.instances) ? raw.instances : []) as ObjectInstance[],
    linkInstances: raw.linkInstances || [],
    executionLogs: execLogs,
  };
}

export const useOntologyStore = create<OntologyState>()(
  persist(
    (set, get) => ({
      ontology: _migrateOntology(tradeErpOntology),
      backendId: null,
      workspaceVersionId: null,
      workspaceMode: 'runtime',
      workspaceTrialRun: null,
      workspaceSentinels: [],
      syncStatus: 'idle',
      syncError: null,
      lastSavedAt: null,
      isDirty: false,
      revision: null,
      lastSentinelSummary: null,
      nodes: defaultLayoutNodes,
      edges: tradeErpEdges,
      selectedNodeId: null,
      selectedEdgeId: null,
      selectedActionId: null,
      selectedFunctionId: null,
      selectedInstanceId: null,
      isPanelOpen: false,
      panelMode: null,
      panelType: null,
      isActionExecutorOpen: false,
      executingActionId: null,
      executingTargetInstanceId: null,
      lastExecutionLog: null,
      isFunctionTesterOpen: false,
      testingFunctionId: null,
      lastFunctionResult: null,
      isInstanceBrowserOpen: false,
      instanceBrowserObjectTypeId: null,
      pendingNodePosition: null,

      // ============ 后端同步 ============
      loadFromBackend: async (id, versionId = null) => {
        _histSuspended = true;
        set({
          backendId: id,
          workspaceVersionId: versionId,
          workspaceMode: versionId ? 'archived' : 'runtime',
          workspaceTrialRun: null,
          workspaceSentinels: [],
          syncStatus: 'loading',
          syncError: null,
        });
        try {
          const full = await loadFullOntology(id, versionId);
          const objectTypes: ObjectType[] = full.objectTypes.map((o) => {
            const { positionX, positionY, ...rest } = o as ObjectType & { positionX?: number; positionY?: number };
            void positionX; void positionY;
            return rest as ObjectType;
          });
          const ontology: Ontology = {
            id: full.id,
            name: full.name,
            description: full.description,
            version: full.version || '1.0.0',
            objectTypes,
            linkTypes: full.linkTypes,
            actions: full.actions,
            functions: full.functions,
            instances: full.instances,
            linkInstances: full.linkInstances,
            executionLogs: full.executionLogs,
            createdAt: now(),
            updatedAt: now(),
          };

          // 画布节点：对象类型 + 接口；坐标优先用后端保存的 positionX/Y，缺失则布局
          const rawNodes: OntologyNode[] = [
            ...full.objectTypes.map((o): OntologyNode => ({
              id: o.id,
              type: 'objectType',
              position: { x: (o as any).positionX || 0, y: (o as any).positionY || 0 },
              data: objectTypes.find((x) => x.id === o.id)!,
            })),
          ];
          const edges: OntologyEdge[] = full.linkTypes.map((lt): OntologyEdge => ({
            id: lt.id,
            source: lt.sourceObjectTypeId,
            target: lt.targetObjectTypeId,
            type: 'link',
            data: lt,
            label: lt.displayName,
          }));

          // 若所有对象类型坐标都为 0（从未保存过布局），自动布局
          const needsLayout = full.objectTypes.length > 0 &&
            full.objectTypes.every((o) => !(o as any).positionX && !(o as any).positionY);
          const nodes = needsLayout
            ? applyLayout(rawNodes, edges, {
                algorithm: 'dagre', direction: 'LR',
                nodeWidth: 280, nodeHeight: 120, nodeSpacing: 100, rankSpacing: 200,
              })
            : rawNodes;

          set({
            ontology, nodes, edges,
            workspaceMode: full.workspaceMode || (versionId ? 'archived' : 'runtime'),
            workspaceTrialRun: full.trialRun ?? null,
            workspaceSentinels: full.sentinels || [],
            syncStatus: 'idle', syncError: null, isDirty: false,
            lastSavedAt: now(),
            revision: full.revision ?? null,
            selectedNodeId: null, selectedEdgeId: null, selectedActionId: null,
            selectedFunctionId: null, selectedInstanceId: null,
          });
          // 全新基线：变更追踪归零，后续编辑走增量保存
          _resetDelta(false);
        } catch (e: any) {
          console.error('loadFromBackend failed', e);
          // 加载失败时清空画布数据，避免 persist 的旧缓存冒充后端真实数据误导用户。
          // 仅保留错误状态供 Header 的「重试」按钮展示。
          set({
            ontology: null,
            nodes: [],
            edges: [],
            selectedNodeId: null, selectedEdgeId: null,
            selectedActionId: null, selectedFunctionId: null, selectedInstanceId: null,
            workspaceMode: versionId ? 'archived' : 'runtime',
            workspaceTrialRun: null,
            workspaceSentinels: [],
            syncStatus: 'error', syncError: e?.detail || e?.message || '加载失败',
          });
          _resetDelta(true);
        } finally {
          // 重新加载后旧历史全部失效
          _histPast = [];
          _histFuture = [];
          _histSuspended = false;
          set({ canUndo: false, canRedo: false });
        }
      },

      saveToBackend: async (opts) => {
        const { backendId, workspaceVersionId, ontology, nodes, revision } = get();
        if (!backendId || !ontology) return;
        // 取消上一次"已保存"复位任务，避免与本次保存状态产生竞态
        if (_savedResetTimer) { clearTimeout(_savedResetTimer); _savedResetTimer = null; }
        set({ syncStatus: 'saving', syncError: null });
        try {
          const positions: Record<string, { x: number; y: number }> = {};
          for (const n of nodes) {
            if (n.type === 'objectType') positions[n.id] = n.position;
          }
          // 常规保存携带加载基准 revision（冲突检测）；force 时置空跳过检测
          const base = opts?.force ? null : revision;

          let savedRevision: string | null = null;
          let savedSentinel: { evaluated: number; fired: number } | null = null;
          let savedInstances: ObjectInstance[] = [];

          // 增量保存：变更可追踪且非强制覆盖时只发 delta（负载 O(变更)）。
          // undo/redo/导入/首次进入等无法追踪的场景自动退回全量。
          const useDelta = !workspaceVersionId && !opts?.force && !_delta.full && _deltaCount() > 0;
          if (useDelta) {
            const pick = <T extends { id: string }>(items: T[], kind: DeltaKind) =>
              items.filter((x) => _delta.upserts[kind].has(x.id));
            const res = await patchOntologyDelta(backendId, {
              name: ontology.name,
              description: ontology.description,
              version: ontology.version,
              baseRevision: base,
              upserts: {
                objectTypes: pick(ontology.objectTypes, 'objectTypes').map((ot) => ({
                  ...ot,
                  positionX: positions[ot.id]?.x ?? 0,
                  positionY: positions[ot.id]?.y ?? 0,
                })),
                linkTypes: pick(ontology.linkTypes, 'linkTypes'),
                actions: pick(ontology.actions, 'actions'),
                functions: pick(ontology.functions, 'functions'),
                // 出处字段原样带回（source/externalId），不得清洗采集器溯源
                instances: pick(ontology.instances, 'instances').map(instanceToPayload),
                linkInstances: pick(ontology.linkInstances, 'linkInstances'),
              },
              deletes: {
                objectTypes: [..._delta.deletes.objectTypes],
                linkTypes: [..._delta.deletes.linkTypes],
                actions: [..._delta.deletes.actions],
                functions: [..._delta.deletes.functions],
                instances: [..._delta.deletes.instances],
                linkInstances: [..._delta.deletes.linkInstances],
              },
            });
            savedRevision = res.revision ?? null;
            savedSentinel = res.sentinelSummary ?? null;
            savedInstances = res.instances || [];
          } else {
            const saved = await saveFullOntology(
              backendId, ontology, positions, base, workspaceVersionId);
            savedRevision = saved.revision ?? null;
            savedSentinel = saved.sentinelSummary ?? null;
            savedInstances = saved.instances || [];
          }

          // 服务端在保存时会自动重算派生属性 → 把最新 computed 回填到本地投影
          const savedComputed = new Map(
            savedInstances.map((i) => [i.id, i._computed || {}])
          );
          _syncApplying = true;
          set((state) => {
            let nextOntology = state.ontology;
            if (nextOntology && savedComputed.size > 0) {
              nextOntology = {
                ...nextOntology,
                instances: nextOntology.instances.map((inst) => {
                  const c = savedComputed.get(inst.id);
                  return c && Object.keys(c).length > 0
                    ? { ...inst, _computed: { ...inst._computed, ...c } }
                    : inst;
                }),
              };
            }
            return {
              ontology: nextOntology,
              syncStatus: 'saved' as const, isDirty: false, lastSavedAt: now(),
              revision: savedRevision,
              lastSentinelSummary: savedSentinel,
            };
          });
          _syncApplying = false;
          // 本轮变更已全部落库，追踪归零，之后继续增量
          _resetDelta(false);
          // 手动保存模式下：saved 状态停留 3s 后复位到 idle，避免"已保存"提示长期占用视野
          _savedResetTimer = setTimeout(() => {
            _savedResetTimer = null;
            // 仅在仍处于 saved 态、且期间没有产生新改动时复位
            const cur = get();
            if (cur.syncStatus === 'saved' && !cur.isDirty) {
              set({ syncStatus: 'idle' });
            }
          }, 3000);
        } catch (e: any) {
          console.error('saveToBackend failed', e);
          if (isSaveConflict(e)) {
            // 409：其他会话已修改。不覆盖，交给用户选择"重新加载 / 强制覆盖"
            set({ syncStatus: 'conflict', syncError: e.detail.message });
            return;
          }
          if (e?.detail?.code === 'validation_failed') {
            // 422：后端强制校验拒绝。顶部 Lint 徽标里有同样的问题清单可逐条修复
            const errs: { message: string }[] = e.detail.errors || [];
            const head = errs.slice(0, 3).map((x) => x.message).join('；');
            set({
              syncStatus: 'error',
              syncError: `${e.detail.message}${head ? `：${head}` : ''}${errs.length > 3 ? ` …等 ${errs.length} 项` : ''}`,
            });
            return;
          }
          const detail = e?.detail;
          const msg = typeof detail === 'string' ? detail : (detail?.message || e?.message || '保存失败');
          set({ syncStatus: 'error', syncError: msg });
        }
      },

      discardAndReload: async () => {
        const { backendId, workspaceVersionId, loadFromBackend } = get();
        if (!backendId) return;
        await loadFromBackend(backendId, workspaceVersionId);
      },

      createOntology: (name, description) => {
        _markFull();
        const newOntology: Ontology = {
          id: uuidv4(),
          name,
          description,
          version: '1.0.0',
          objectTypes: [],
          linkTypes: [],
          actions: [],
          functions: [],
          instances: [],
          linkInstances: [],
          executionLogs: [],
          createdAt: now(),
          updatedAt: now(),
        };
        set({
          ontology: newOntology,
          nodes: [],
          edges: [],
          selectedNodeId: null, selectedEdgeId: null, selectedActionId: null,
          selectedFunctionId: null, selectedInstanceId: null,
        });
      },

      // ============ ObjectType ============
      addObjectType: (objectType) => {
        const id = uuidv4();
        const newObjectType: ObjectType = { ...objectType, id, createdAt: now(), updatedAt: now() };
        set((state) => {
          if (!state.ontology) return state;
          const newNode: OntologyNode = {
            id, type: 'objectType',
            // 右键"在此创建"时使用暂存位置，否则随机落点
            position: state.pendingNodePosition
              || { x: Math.random() * 400 + 100, y: Math.random() * 300 + 100 },
            data: newObjectType,
          };
          return {
            ontology: {
              ...state.ontology,
              objectTypes: [...state.ontology.objectTypes, newObjectType],
              updatedAt: now(),
            },
            nodes: [...state.nodes, newNode],
            pendingNodePosition: null,
          };
        });
        _markChanged('objectTypes', id);
        return id;
      },

      updateObjectType: (id, updates) => {
        _markChanged('objectTypes', id);
        set((state) => {
          if (!state.ontology) return state;
          const updatedObjectTypes = state.ontology.objectTypes.map((ot) =>
            ot.id === id ? { ...ot, ...updates, updatedAt: now() } : ot
          );
          const updatedNodes = state.nodes.map((node) =>
            node.id === id && node.type === 'objectType'
              ? { ...node, data: { ...node.data, ...updates, updatedAt: now() } }
              : node
          );
          return {
            ontology: { ...state.ontology, objectTypes: updatedObjectTypes, updatedAt: now() },
            nodes: updatedNodes,
          };
        });
      },

      deleteObjectType: (id) => {
        set((state) => {
          if (!state.ontology) return state;
          // 级联范围：连带删除的关系类型 + 连带删除的实例，
          // 二者关联的链接实例也必须清掉，否则孤儿链接实例会在保存时写回后端
          const removedLinkTypeIds = new Set(
            state.ontology.linkTypes
              .filter((lt) => lt.sourceObjectTypeId === id || lt.targetObjectTypeId === id)
              .map((lt) => lt.id)
          );
          const removedInstanceIds = new Set(
            state.ontology.instances.filter((i) => i.objectTypeId === id).map((i) => i.id)
          );
          // 增量追踪：级联删除展开为显式删除集合
          _markDeleted('objectTypes', id);
          removedLinkTypeIds.forEach((x) => _markDeleted('linkTypes', x));
          removedInstanceIds.forEach((x) => _markDeleted('instances', x));
          state.ontology.functions.filter((f) => f.targetObjectTypeId === id)
            .forEach((f) => _markDeleted('functions', f.id));
          state.ontology.actions.filter((a) => a.objectTypeId === id)
            .forEach((a) => _markDeleted('actions', a.id));
          state.ontology.linkInstances
            .filter((li) => removedLinkTypeIds.has(li.linkTypeId)
              || removedInstanceIds.has(li.sourceObjectId)
              || removedInstanceIds.has(li.targetObjectId))
            .forEach((li) => _markDeleted('linkInstances', li.id));
          return {
            ontology: {
              ...state.ontology,
              objectTypes: state.ontology.objectTypes.filter((ot) => ot.id !== id),
              linkTypes: state.ontology.linkTypes.filter((lt) => !removedLinkTypeIds.has(lt.id)),
              // 清理相关函数（绑定到该对象实体的 object/object_set 函数）、动作、实例
              functions: state.ontology.functions.filter(
                (f) => f.targetObjectTypeId !== id
              ),
              actions: state.ontology.actions.filter((a) => a.objectTypeId !== id),
              instances: state.ontology.instances.filter((i) => i.objectTypeId !== id),
              linkInstances: state.ontology.linkInstances.filter(
                (li) => !removedLinkTypeIds.has(li.linkTypeId)
                  && !removedInstanceIds.has(li.sourceObjectId)
                  && !removedInstanceIds.has(li.targetObjectId)
              ),
              updatedAt: now(),
            },
            nodes: state.nodes.filter((node) => node.id !== id),
            edges: state.edges.filter((edge) => edge.source !== id && edge.target !== id),
            selectedNodeId: state.selectedNodeId === id ? null : state.selectedNodeId,
          };
        });
      },

      // ============ LinkType ============
      addLinkType: (linkType) => {
        const id = uuidv4();
        const newLinkType: LinkType = { ...linkType, id, createdAt: now(), updatedAt: now() };
        set((state) => {
          if (!state.ontology) return state;
          const newEdge: OntologyEdge = {
            id, source: linkType.sourceObjectTypeId, target: linkType.targetObjectTypeId,
            type: 'link', data: newLinkType, label: linkType.displayName,
          };
          return {
            ontology: { ...state.ontology, linkTypes: [...state.ontology.linkTypes, newLinkType], updatedAt: now() },
            edges: [...state.edges, newEdge],
          };
        });
        _markChanged('linkTypes', id);
        return id;
      },

      updateLinkType: (id, updates) => {
        _markChanged('linkTypes', id);
        set((state) => {
          if (!state.ontology) return state;
          const updatedLinkTypes = state.ontology.linkTypes.map((lt) =>
            lt.id === id ? { ...lt, ...updates, updatedAt: now() } : lt
          );
          const updatedEdges = state.edges.map((edge) =>
            edge.id === id
              ? { ...edge, data: edge.data ? { ...edge.data, ...updates, updatedAt: now() } : undefined, label: updates.displayName || edge.label }
              : edge
          );
          return {
            ontology: { ...state.ontology, linkTypes: updatedLinkTypes, updatedAt: now() },
            edges: updatedEdges,
          };
        });
      },

      deleteLinkType: (id) => {
        set((state) => {
          if (!state.ontology) return state;
          _markDeleted('linkTypes', id);
          state.ontology.linkInstances.filter((li) => li.linkTypeId === id)
            .forEach((li) => _markDeleted('linkInstances', li.id));
          return {
            ontology: {
              ...state.ontology,
              linkTypes: state.ontology.linkTypes.filter((lt) => lt.id !== id),
              // 该关系类型下的链接实例一并删除，避免孤儿数据写回后端
              linkInstances: state.ontology.linkInstances.filter((li) => li.linkTypeId !== id),
              updatedAt: now(),
            },
            edges: state.edges.filter((edge) => edge.id !== id),
            selectedEdgeId: state.selectedEdgeId === id ? null : state.selectedEdgeId,
          };
        });
      },


      // ============ Action ============
      addAction: (action) => {
        const id = uuidv4();
        const newAction: Action = { ...action, id, createdAt: now(), updatedAt: now() };
        set((state) => {
          if (!state.ontology) return state;
          return {
            ontology: { ...state.ontology, actions: [...state.ontology.actions, newAction], updatedAt: now() },
          };
        });
        _markChanged('actions', id);
        return id;
      },

      updateAction: (id, updates) => {
        _markChanged('actions', id);
        set((state) => {
          if (!state.ontology) return state;
          return {
            ontology: {
              ...state.ontology,
              actions: state.ontology.actions.map((a) => a.id === id ? { ...a, ...updates, updatedAt: now() } : a),
              updatedAt: now(),
            },
          };
        });
      },

      deleteAction: (id) => {
        set((state) => {
          if (!state.ontology) return state;
          _markDeleted('actions', id);
          state.ontology.functions.filter((f) => f.targetActionId === id)
            .forEach((f) => _markDeleted('functions', f.id));
          return {
            ontology: {
              ...state.ontology,
              actions: state.ontology.actions.filter((a) => a.id !== id),
              functions: state.ontology.functions.filter(f => f.targetActionId !== id),
              updatedAt: now(),
            },
          };
        });
      },

      // ============ Function ============
      addFunction: (fn) => {
        const id = uuidv4();
        const newFn: OntologyFunction = { ...fn, id, createdAt: now(), updatedAt: now() };
        set((state) => {
          if (!state.ontology) return state;
          return {
            ontology: { ...state.ontology, functions: [...state.ontology.functions, newFn], updatedAt: now() },
          };
        });
        _markChanged('functions', id);
        return id;
      },

      updateFunction: (id, updates) => {
        _markChanged('functions', id);
        set((state) => {
          if (!state.ontology) return state;
          return {
            ontology: {
              ...state.ontology,
              functions: state.ontology.functions.map((f) => f.id === id ? { ...f, ...updates, updatedAt: now() } : f),
              updatedAt: now(),
            },
          };
        });
      },

      deleteFunction: (id) => {
        set((state) => {
          if (!state.ontology) return state;
          _markDeleted('functions', id);
          // 增量追踪：引用被清理的对象类型 / 动作也发生了变化
          state.ontology.objectTypes
            .filter(ot => ot.properties.some(p => p.source === 'computed' && p.functionId === id))
            .forEach(ot => _markChanged('objectTypes', ot.id));
          state.ontology.actions
            .filter(a => a.validationFunctionId === id
              || (a.rules || []).some(r =>
                (r.config.type === 'update_property' && r.config.valueSource === 'function' && r.config.functionId === id)
                || (r.config.type === 'create_object' && r.config.propertyMappings.some(m => m.functionId === id))))
            .forEach(a => _markChanged('actions', a.id));
          // 移除属性对该函数的引用
          const updatedObjectTypes = state.ontology.objectTypes.map(ot => ({
            ...ot,
            properties: ot.properties.map(p =>
              p.source === 'computed' && p.functionId === id
                ? { ...p, source: 'stored' as const, functionId: undefined, defaultValue: undefined }
                : p
            ),
          }));
          // 移除 Action 规则对该函数的引用
          const updatedActions = state.ontology.actions.map(a => ({
            ...a,
            rules: (a.rules || []).map(r => {
              if ((r.config.type === 'update_property' && r.config.valueSource === 'function' && r.config.functionId === id)
                  || (r.config.type === 'create_object' && r.config.propertyMappings.some(m => m.functionId === id))) {
                return { ...r, enabled: false };
              }
              return r;
            }),
            validationFunctionId: a.validationFunctionId === id ? undefined : a.validationFunctionId,
          }));
          const updatedNodes = state.nodes.map(node => {
            if (node.type !== 'objectType') return node;
            const ot = updatedObjectTypes.find(o => o.id === node.id);
            return ot ? { ...node, data: ot } : node;
          });
          return {
            ontology: {
              ...state.ontology,
              functions: state.ontology.functions.filter((f) => f.id !== id),
              objectTypes: updatedObjectTypes,
              actions: updatedActions,
              updatedAt: now(),
            },
            nodes: updatedNodes,
          };
        });
      },

      // ============ Instances ============
      addInstance: (instance) => {
        const id = instance.id || `inst_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
        const newInstance: ObjectInstance = { ...instance, id, createdAt: now(), updatedAt: now() };
        set((state) => {
          if (!state.ontology) return state;
          return {
            ontology: { ...state.ontology, instances: [...state.ontology.instances, newInstance], updatedAt: now() },
          };
        });
        _markChanged('instances', id);
        return id;
      },

      updateInstance: (id, updates) => {
        _markChanged('instances', id);
        set((state) => {
          if (!state.ontology) return state;
          return {
            ontology: {
              ...state.ontology,
              instances: state.ontology.instances.map((i) => i.id === id ? { ...i, ...updates, updatedAt: now() } : i),
              updatedAt: now(),
            },
          };
        });
      },

      deleteInstance: (id) => {
        set((state) => {
          if (!state.ontology) return state;
          _markDeleted('instances', id);
          state.ontology.linkInstances
            .filter((l) => l.sourceObjectId === id || l.targetObjectId === id)
            .forEach((l) => _markDeleted('linkInstances', l.id));
          return {
            ontology: {
              ...state.ontology,
              instances: state.ontology.instances.filter((i) => i.id !== id),
              linkInstances: state.ontology.linkInstances.filter((l) => l.sourceObjectId !== id && l.targetObjectId !== id),
              executionLogs: state.ontology.executionLogs.filter((l) => l.objectInstanceId !== id),
              updatedAt: now(),
            },
            selectedInstanceId: state.selectedInstanceId === id ? null : state.selectedInstanceId,
          };
        });
      },

      addLinkInstance: (link) => {
        const id = `li_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
        const newLink: LinkInstance = { ...link, id, createdAt: now() };
        set((state) => {
          if (!state.ontology) return state;
          return {
            ontology: { ...state.ontology, linkInstances: [...state.ontology.linkInstances, newLink], updatedAt: now() },
          };
        });
        _markChanged('linkInstances', id);
        return id;
      },

      deleteLinkInstance: (id) => {
        _markDeleted('linkInstances', id);
        set((state) => {
          if (!state.ontology) return state;
          return {
            ontology: { ...state.ontology, linkInstances: state.ontology.linkInstances.filter((l) => l.id !== id), updatedAt: now() },
          };
        });
      },

      // ============ Runtime: Action Execution ============
      openActionExecutor: (actionId, targetInstanceId) => {
        set({
          isActionExecutorOpen: true,
          executingActionId: actionId,
          executingTargetInstanceId: targetInstanceId || null,
          lastExecutionLog: null,
        });
      },

      closeActionExecutor: () => {
        set({ isActionExecutorOpen: false, executingActionId: null, executingTargetInstanceId: null, lastExecutionLog: null });
      },

      runAction: (parameters, dryRun = false) => {
        const state = get();
        if (!state.ontology || !state.executingActionId) {
          const failedLog: ActionExecutionLog = {
            id: `exec_${Date.now()}`, actionId: '', actionName: '',
            objectTypeId: '', parameters, status: 'failed',
            errorMessage: 'No action selected', executedAt: now(), durationMs: 0,
          };
          set({ lastExecutionLog: failedLog });
          return failedLog;
        }
        const result = executeAction(state.ontology, {
          actionId: state.executingActionId,
          parameters,
          targetInstanceId: state.executingTargetInstanceId || undefined,
          dryRun,
        });

        if (!dryRun && result.success) {
          // 应用变更到 ontology（实例 + 链接实例，本地引擎真实执行不再丢建链/删链）
          result.newInstances?.forEach((i) => _markChanged('instances', i.id));
          result.updatedInstances?.forEach((i) => _markChanged('instances', i.id));
          set((s) => {
            if (!s.ontology) return s;
            let instances = [...s.ontology.instances];
            if (result.newInstances?.length) {
              instances = [...instances, ...result.newInstances];
            }
            if (result.updatedInstances?.length) {
              instances = instances.map(inst => {
                const upd = result.updatedInstances!.find(u => u.id === inst.id);
                return upd || inst;
              });
            }
            let linkInstances = [...s.ontology.linkInstances];
            if (result.deletedLinkIds?.length) {
              const gone = new Set(result.deletedLinkIds);
              linkInstances.forEach((li) => { if (gone.has(li.id)) _markDeleted('linkInstances', li.id); });
              linkInstances = linkInstances.filter((li) => !gone.has(li.id));
            }
            if (result.newLinks?.length) {
              for (const nl of result.newLinks) {
                const li: LinkInstance = {
                  id: uuidv4(), linkTypeId: nl.linkTypeId,
                  sourceObjectId: nl.sourceId, targetObjectId: nl.targetId,
                  createdAt: now(),
                };
                linkInstances.push(li);
                _markChanged('linkInstances', li.id);
              }
            }
            const auditLogs = [...s.ontology.executionLogs, result.log];
            return {
              ontology: { ...s.ontology, instances, linkInstances, executionLogs: auditLogs, updatedAt: now() },
              lastExecutionLog: result.log,
            };
          });
        } else {
          set({ lastExecutionLog: result.log });
        }

        return result.log;
      },

      // ============ Runtime: Function Testing ============
      openFunctionTester: (functionId) => {
        set({ isFunctionTesterOpen: true, testingFunctionId: functionId, lastFunctionResult: null });
      },

      closeFunctionTester: () => {
        set({ isFunctionTesterOpen: false, testingFunctionId: null, lastFunctionResult: null });
      },

      testFunction: (functionId: string, params: Record<string, unknown>, objectPropsOrId?: Record<string, unknown> | string) => {
        const state = get();
        if (!state.ontology || !functionId) {
          const err: FunctionExecutionResult = { success: false, error: 'No function selected', durationMs: 0, timestamp: now(), result: undefined as unknown };
          set({ lastFunctionResult: err });
          return err;
        }
        const fn = state.ontology.functions.find(f => f.id === functionId);
        if (!fn) {
          const err: FunctionExecutionResult = { success: false, error: 'Function not found', durationMs: 0, timestamp: now(), result: undefined as unknown };
          set({ lastFunctionResult: err });
          return err;
        }        let objectProps: Record<string, unknown> | undefined;
        if (typeof objectPropsOrId === 'string') {
          const inst = state.ontology.instances.find((i: ObjectInstance) => i.id === objectPropsOrId);
          objectProps = inst?.properties;
        } else {
          objectProps = objectPropsOrId;
        }
        const ctx: Record<string, unknown> = {};
        if (fn.functionType === 'object_set' || fn.functionType === 'action_validation') {
          ctx.allInstances = state.ontology.instances;
          ctx.auditLogs = state.ontology.executionLogs;
        }
        const execResult = executeFunction(fn, state.ontology, {
          object: objectProps,
          params,
          objects: fn.functionType === 'object_set' && fn.targetObjectTypeId
            ? state.ontology.instances.filter((i: ObjectInstance) => i.objectTypeId === fn.targetObjectTypeId).map((i: ObjectInstance) => i.properties)
            : undefined,
          context: ctx,
        });
        const result: FunctionExecutionResult = {
          ...execResult,
          result: execResult.result ?? execResult.data,
        };
        set({ lastFunctionResult: result });
        return result;
      },

      // ============ Instance Browser ============
      openInstanceBrowser: (objectTypeId) => {
        set({ isInstanceBrowserOpen: true, instanceBrowserObjectTypeId: objectTypeId });
      },

      closeInstanceBrowser: () => {
        set({ isInstanceBrowserOpen: false, instanceBrowserObjectTypeId: null });
      },

      // ============ Canvas ============
      setNodes: (nodes) => set({ nodes }),
      setEdges: (edges) => set({ edges }),
      updateNodePosition: (id, position) => {
        // 画布坐标是独立展示元数据，由 Canvas 的布局接口自动保存；
        // 不计入模型 delta，也不触发本体结构的未保存状态。
        set((state) => ({
          nodes: state.nodes.map((node) => node.id === id ? { ...node, position } : node),
        }));
      },

      // ============ Selection ============
      setSelectedNode: (id) => set({ selectedNodeId: id, selectedEdgeId: null, selectedActionId: null, selectedFunctionId: null, selectedInstanceId: null }),
      setSelectedEdge: (id) => set({ selectedEdgeId: id, selectedNodeId: null, selectedActionId: null, selectedFunctionId: null, selectedInstanceId: null }),
      setSelectedAction: (id) => set({ selectedActionId: id, selectedNodeId: null, selectedEdgeId: null, selectedFunctionId: null, selectedInstanceId: null }),
      setSelectedFunction: (id) => set({ selectedFunctionId: id, selectedNodeId: null, selectedEdgeId: null, selectedActionId: null, selectedInstanceId: null }),
      setSelectedInstance: (id) => set({ selectedInstanceId: id, selectedNodeId: null, selectedEdgeId: null, selectedActionId: null, selectedFunctionId: null }),

      // ============ Panel ============
      openPanel: (mode, type) => set({ isPanelOpen: true, panelMode: mode, panelType: type }),
      closePanel: () => set({ isPanelOpen: false, panelMode: null, panelType: null }),
      setPendingNodePosition: (p) => set({ pendingNodePosition: p }),

      // ============ Undo / Redo ============
      canUndo: false,
      canRedo: false,

      undo: () => {
        if (!_histPast.length) return;
        // 撤销后的状态无法可靠映射为 delta，退回全量保存
        _markFull();
        const s = get();
        const prev = _histPast.pop()!;
        _histFuture.push({ ontology: s.ontology, nodes: s.nodes, edges: s.edges });
        if (_histFuture.length > HISTORY_LIMIT) _histFuture.shift();
        _timeTravel = true;
        set({
          ontology: prev.ontology, nodes: prev.nodes, edges: prev.edges,
          canUndo: _histPast.length > 0, canRedo: true,
          // 选中的元素可能在回退后不存在，直接清空选择最安全
          selectedNodeId: null, selectedEdgeId: null, selectedActionId: null,
          selectedFunctionId: null, selectedInstanceId: null,
        });
        _timeTravel = false;
      },

      redo: () => {
        if (!_histFuture.length) return;
        _markFull();
        const s = get();
        const next = _histFuture.pop()!;
        _histPast.push({ ontology: s.ontology, nodes: s.nodes, edges: s.edges });
        if (_histPast.length > HISTORY_LIMIT) _histPast.shift();
        _timeTravel = true;
        set({
          ontology: next.ontology, nodes: next.nodes, edges: next.edges,
          canUndo: true, canRedo: _histFuture.length > 0,
          selectedNodeId: null, selectedEdgeId: null, selectedActionId: null,
          selectedFunctionId: null, selectedInstanceId: null,
        });
        _timeTravel = false;
      },

      // ============ Import/Export ============
      exportOntology: () => {
        const state = get();
        return JSON.stringify({ ontology: state.ontology, nodes: state.nodes, edges: state.edges }, null, 2);
      },

      importOntology: (json) => {
        try {
          _markFull();
          const data = JSON.parse(json);
          const ontology = _migrateOntology(data.ontology);
          set({
            ontology,
            nodes: data.nodes || [],
            edges: data.edges || [],
            selectedNodeId: null, selectedEdgeId: null, selectedActionId: null,
            selectedFunctionId: null, selectedInstanceId: null,
          });
        } catch (error) {
          console.error('Failed to import ontology:', error);
        }
      },

      // ============ Reset ============
      reset: () => {
        _markFull();
        set({
          ontology: _migrateOntology(tradeErpOntology),
          nodes: defaultLayoutNodes,
          edges: tradeErpEdges,
          selectedNodeId: null, selectedEdgeId: null, selectedActionId: null,
          selectedFunctionId: null, selectedInstanceId: null,
          isPanelOpen: false, panelMode: null, panelType: null,
          isActionExecutorOpen: false, executingActionId: null, executingTargetInstanceId: null, lastExecutionLog: null,
          isFunctionTesterOpen: false, testingFunctionId: null, lastFunctionResult: null,
          isInstanceBrowserOpen: false, instanceBrowserObjectTypeId: null,
          workspaceTrialRun: null, workspaceSentinels: [],
        });
      },

      // ============ Helpers ============
      getInstancesForType: (objectTypeId: string) => {
        const s = get();
        if (!s.ontology) return [];
        return s.ontology.instances.filter(i => i.objectTypeId === objectTypeId);
      },

      // ============ Auto Layout ============
      autoLayout: (algorithm, direction = 'TB') => {
        const state = get();
        const options: LayoutOptions = {
          algorithm, direction, nodeWidth: 280, nodeHeight: 120, nodeSpacing: 100, rankSpacing: 180,
        };
        const newNodes = applyLayout(state.nodes, state.edges, options);
        set({ nodes: newNodes });
      },
    }),
    {
      name: 'ontology-storage',
      version: 11,
      // 只持久化本地建模数据；后端同步态与瞬时 UI 态不入库
      partialize: (state) => ({
        ontology: state.ontology,
        nodes: state.nodes,
        edges: state.edges,
      }),
      migrate: (persistedState: unknown, version: number) => {
        if (version < 10) {
          return {
            ontology: _migrateOntology(tradeErpOntology),
            nodes: defaultLayoutNodes,
            edges: tradeErpEdges,
            selectedNodeId: null, selectedEdgeId: null, selectedActionId: null,
            selectedFunctionId: null, selectedInstanceId: null,
            isPanelOpen: false, panelMode: null, panelType: null,
            isActionExecutorOpen: false, executingActionId: null, executingTargetInstanceId: null, lastExecutionLog: null,
            isFunctionTesterOpen: false, testingFunctionId: null, lastFunctionResult: null,
            isInstanceBrowserOpen: false, instanceBrowserObjectTypeId: null,
          };
        }
        const s = persistedState as OntologyState;
        return { ...s, ontology: _migrateOntology(s.ontology) };
      },
    }
  )
);

export { getDefaultParameters };

/**
 * 绑定撤销/重做历史记录：监听 ontology / nodes / edges 引用变化，
 * 变化前的快照入 past 栈，并清空 future 栈（新分支）。
 * undo/redo 自身与后端加载不记录。返回取消订阅函数。
 */
export function attachHistory(): () => void {
  const s0 = useOntologyStore.getState();
  let prev: HistorySnapshot = { ontology: s0.ontology, nodes: s0.nodes, edges: s0.edges };
  const unsub = useOntologyStore.subscribe((state) => {
    const changed = state.ontology !== prev.ontology
      || state.nodes !== prev.nodes
      || state.edges !== prev.edges;
    if (!changed) return;
    const snapshot: HistorySnapshot = { ontology: state.ontology, nodes: state.nodes, edges: state.edges };
    if (_histSuspended || _timeTravel || _syncApplying || state.syncStatus === 'loading') {
      prev = snapshot;
      return;
    }
    _histPast.push(prev);
    if (_histPast.length > HISTORY_LIMIT) _histPast.shift();
    _histFuture = [];
    prev = snapshot;
    const cur = useOntologyStore.getState();
    if (!cur.canUndo || cur.canRedo) {
      useOntologyStore.setState({ canUndo: true, canRedo: false });
    }
  });
  return unsub;
}

/**
 * 绑定模型脏标记：只监听 ontology 引用变化，标记 isDirty=true（右上角保存按钮高亮）。
 * nodes 中的画布坐标属于独立展示元数据，由布局接口自动保存，不应污染模型脏状态。
 * 注意：自手动保存改造后，此函数不再自动触发保存，仅用于追踪"有未保存改动"。
 * 仅在 backendId 存在时生效。返回取消订阅函数。
 *
 * 用法：必须在 loadFromBackend(id) 成功完成后再调用，避免把后端加载结果误判为用户编辑。
 */
export function attachAutoSave(): () => void {
  let prevOntology = useOntologyStore.getState().ontology;
  const unsub = useOntologyStore.subscribe((state) => {
    const { ontology, backendId, syncStatus } = state;
    if (!backendId) return;
    if (syncStatus === 'loading' || _histSuspended || _syncApplying) {
      // 加载 / 保存回填 computed 过程中刷新基准，避免把它们当成用户改动
      prevOntology = ontology;
      return;
    }
    if (ontology !== prevOntology) {
      prevOntology = ontology;
      // 手动保存模式：只标记 dirty，由用户点击右上角"保存"按钮触发 saveToBackend
      useOntologyStore.setState({ isDirty: true });
    }
  });
  return unsub;
}
