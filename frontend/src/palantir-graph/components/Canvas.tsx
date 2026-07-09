import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
  BackgroundVariant,
  ConnectionMode,
  MarkerType,
  type EdgeChange,
  type NodeChange,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  PencilSquareIcon, TrashIcon, TableCellsIcon, CubeIcon, ArrowsPointingOutIcon,
} from '@heroicons/react/24/outline';

import { useOntologyStore } from '../store/ontologyStore';
import ObjectTypeNode from './nodes/ObjectTypeNode';
import MultiConnectionEdge from './edges/MultiConnectionEdge';
import ConnectLinkDialog from './ConnectLinkDialog';
import DeleteSelectedDialog, { type DeleteTarget } from './DeleteSelectedDialog';

const nodeTypes = {
  objectType: ObjectTypeNode,
};

const edgeTypes = {
  multi: MultiConnectionEdge,
};

const defaultEdgeOptions = {
  type: 'multi',
  animated: true,
  style: { stroke: '#06b6d4', strokeWidth: 2 },
  markerEnd: {
    type: MarkerType.ArrowClosed,
    color: '#06b6d4',
  },
};

interface CanvasProps {
  /** 右键菜单"浏览实例"入口（由页面渲染 InstanceBrowser） */
  onBrowseInstances?: (objectTypeId: string) => void;
  /** 嵌入智能助手等场景时只展示结构，不允许创建、连线、拖拽或删除 */
  readOnly?: boolean;
}

/** 相邻平行边顶点之间的间距(px) */
const PARALLEL_SPACING = 42;

/**
 * 同一对节点间的多条边错开偏移，避免平行边完全重叠。
 *
 * 交给自定义边 `multi`（MultiConnectionEdge）按 `data.__offset` 弯出不同弧度：
 * - 无向分组：A→B 与 B→A 归为同组一起散开；
 * - 偏移量以 0 为中心对称展开（1 条→0；2 条→±½;3 条→-1/0/1 …）；
 * - 反向边(source 非规范首节点)法线方向相反，翻转符号保证同组分列两侧不叠。
 */
function offsetParallelEdges(edges: Edge[]): Edge[] {
  const groups = new Map<string, Edge[]>();
  for (const e of edges) {
    const key = [e.source, e.target].sort().join('::');
    const arr = groups.get(key) || [];
    arr.push(e);
    groups.set(key, arr);
  }
  const out: Edge[] = [];
  for (const [key, arr] of groups.entries()) {
    const canonicalFirst = key.split('::')[0]; // = min(source, target)
    const n = arr.length;
    arr.forEach((e, i) => {
      const signed = (i - (n - 1) / 2) * PARALLEL_SPACING;
      const flip = e.source !== canonicalFirst;
      const offset = flip ? -signed : signed;
      out.push({
        ...e,
        type: 'multi',
        data: { ...(e.data as Record<string, unknown>), __offset: offset },
      } as Edge);
    });
  }
  return out;
}

export default function Canvas({ onBrowseInstances, readOnly = false }: CanvasProps = {}) {
  const {
    ontology,
    nodes: storeNodes,
    edges: storeEdges,
    selectedNodeId,
    selectedEdgeId,
    setSelectedNode,
    setSelectedEdge,
    updateNodePosition,
    openPanel,
    setPendingNodePosition,
    autoLayout,
  } = useOntologyStore();
  const { screenToFlowPosition, fitView } = useReactFlow();

  const selectedStoreNodes = useMemo<Node[]>(
    () => (storeNodes as unknown as Node[]).map((node): Node => ({
      ...node,
      selected: node.id === selectedNodeId,
    })),
    [storeNodes, selectedNodeId]
  );
  const sourceStoreEdges = useMemo<Edge[]>(() => {
    const direct = storeEdges as Edge[];
    if (direct.length > 0 || !ontology) return direct;
    return ontology.linkTypes.map((lt): Edge => ({
      id: lt.id,
      source: lt.sourceObjectTypeId,
      target: lt.targetObjectTypeId,
      type: 'multi',
      data: lt as unknown as Record<string, unknown>,
      label: lt.displayName,
    }));
  }, [ontology, storeEdges]);
  const selectedStoreEdges = useMemo<Edge[]>(
    () => offsetParallelEdges(sourceStoreEdges.map((edge): Edge => ({
      ...edge,
      selected: edge.id === selectedEdgeId,
    }))),
    [sourceStoreEdges, selectedEdgeId]
  );

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(selectedStoreNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(selectedStoreEdges);

  const renderNodes = useMemo(
    () => readOnly
      ? nodes.map((node) => ({
          ...node,
          width: node.width ?? 280,
          height: node.height ?? 140,
          measured: node.measured ?? { width: node.width ?? 280, height: node.height ?? 140 },
          style: { ...(node.style || {}), visibility: 'visible' as const },
          data: { ...(node.data as Record<string, unknown>), __readOnly: true },
        }))
      : nodes,
    [nodes, readOnly]
  );
  const renderEdges = readOnly ? selectedStoreEdges : edges;

  // Sync with store when store changes
  useEffect(() => {
    setNodes(selectedStoreNodes);
  }, [selectedStoreNodes, setNodes]);

  useEffect(() => {
    setEdges(selectedStoreEdges);
  }, [selectedStoreEdges, setEdges]);

  useEffect(() => {
    if (selectedStoreNodes.length === 0) return;
    const timer = window.setTimeout(() => {
      void fitView({ padding: 0.25, maxZoom: readOnly ? 0.72 : 0.8, duration: 220 });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [fitView, readOnly, selectedStoreEdges, selectedStoreNodes]);

  // 右键上下文菜单
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; kind: 'node' | 'edge' | 'pane'; id?: string } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);

  // 待确认的连线：拖线松手后先弹卡片让用户命名/选基数，确认才真正创建 LinkType。
  // 不再静默创建，也不再往画布加"只有视觉、不进 store"的边。
  const [pendingConn, setPendingConn] = useState<{ sourceId: string; targetId: string } | null>(null);

  const onConnect = useCallback(
    (connection: Connection) => {
      if (readOnly) return;
      if (!connection.source || !connection.target) return;
      const sourceNode = storeNodes.find((n) => n.id === connection.source);
      const targetNode = storeNodes.find((n) => n.id === connection.target);
      if (sourceNode?.type === 'objectType' && targetNode?.type === 'objectType') {
        setPendingConn({ sourceId: connection.source, targetId: connection.target });
      }
    },
    [storeNodes, readOnly]
  );

  const onNodeDragStop = useCallback(
    (_event: unknown, node: Node) => {
      if (readOnly) return;
      updateNodePosition(node.id, node.position);
    },
    [updateNodePosition, readOnly]
  );

  const onNodeClick = useCallback(
    (_event: unknown, node: Node) => {
      setSelectedNode(node.id);
    },
    [setSelectedNode]
  );

  const onEdgeClick = useCallback(
    (_: React.MouseEvent, edge: Edge) => {
      setSelectedEdge(edge.id);
    },
    [setSelectedEdge]
  );

  const onEdgeDoubleClick = useCallback(
    (_: React.MouseEvent, edge: Edge) => {
      setSelectedEdge(edge.id);
      if (readOnly) return;
      openPanel('edit', 'linkType');
    },
    [setSelectedEdge, openPanel, readOnly]
  );

  const onPaneClick = useCallback(() => {
    setSelectedNode(null);
    setSelectedEdge(null);
    setCtxMenu(null);
  }, [setSelectedNode, setSelectedEdge]);

  const onNodeContextMenu = useCallback((e: React.MouseEvent, node: Node) => {
    e.preventDefault();
    setSelectedNode(node.id);
    if (readOnly) return;
    setCtxMenu({ x: e.clientX, y: e.clientY, kind: 'node', id: node.id });
  }, [setSelectedNode, readOnly]);

  const onEdgeContextMenu = useCallback((e: React.MouseEvent, edge: Edge) => {
    e.preventDefault();
    setSelectedEdge(edge.id);
    if (readOnly) return;
    setCtxMenu({ x: e.clientX, y: e.clientY, kind: 'edge', id: edge.id });
  }, [setSelectedEdge, readOnly]);

  const onPaneContextMenu = useCallback((e: React.MouseEvent | MouseEvent) => {
    e.preventDefault();
    if (readOnly) return;
    setCtxMenu({ x: e.clientX, y: e.clientY, kind: 'pane' });
  }, [readOnly]);

  const handleNodesChange = useCallback((changes: NodeChange<Node>[]) => {
    onNodesChange(readOnly ? changes.filter((change) => change.type === 'select') : changes);
  }, [onNodesChange, readOnly]);

  const handleEdgesChange = useCallback((changes: EdgeChange<Edge>[]) => {
    onEdgesChange(readOnly ? changes.filter((change) => change.type === 'select') : changes);
  }, [onEdgesChange, readOnly]);

  // 右键菜单条目
  const menuItems = useMemo(() => {
    if (readOnly) return [];
    if (!ctxMenu) return [];
    const close = () => setCtxMenu(null);
    if (ctxMenu.kind === 'node' && ctxMenu.id) {
      const id = ctxMenu.id;
      return [
        {
          icon: PencilSquareIcon, label: '编辑对象实体',
          onClick: () => { setSelectedNode(id); openPanel('edit', 'objectType'); close(); },
        },
        {
          icon: TableCellsIcon, label: '浏览实例数据',
          onClick: () => { onBrowseInstances?.(id); close(); },
        },
        {
          icon: TrashIcon, label: '删除…', danger: true,
          onClick: () => { setDeleteTarget({ kind: 'objectType', id }); close(); },
        },
      ];
    }
    if (ctxMenu.kind === 'edge' && ctxMenu.id) {
      const id = ctxMenu.id;
      return [
        {
          icon: PencilSquareIcon, label: '编辑实体关系',
          onClick: () => { setSelectedEdge(id); openPanel('edit', 'linkType'); close(); },
        },
        {
          icon: TrashIcon, label: '删除…', danger: true,
          onClick: () => { setDeleteTarget({ kind: 'linkType', id }); close(); },
        },
      ];
    }
    return [
      {
        icon: CubeIcon, label: '在此创建对象实体',
        onClick: () => {
          const pos = screenToFlowPosition({ x: ctxMenu.x, y: ctxMenu.y });
          setPendingNodePosition(pos);
          openPanel('create', 'objectType');
          close();
        },
      },
      {
        icon: ArrowsPointingOutIcon, label: '自动布局',
        onClick: () => { autoLayout('dagre', 'LR'); close(); },
      },
    ];
  }, [ctxMenu, readOnly, setSelectedNode, setSelectedEdge, openPanel, onBrowseInstances, screenToFlowPosition, setPendingNodePosition, autoLayout]);

  return (
    <div className="w-full h-full canvas-bg">
      <ReactFlow
        nodes={renderNodes}
        edges={renderEdges}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onConnect={onConnect}
        onNodeDragStop={onNodeDragStop}
        onNodeClick={onNodeClick}
        onEdgeClick={onEdgeClick}
        onEdgeDoubleClick={onEdgeDoubleClick}
        onPaneClick={onPaneClick}
        onNodeContextMenu={onNodeContextMenu}
        onEdgeContextMenu={onEdgeContextMenu}
        onPaneContextMenu={onPaneContextMenu}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        defaultEdgeOptions={defaultEdgeOptions}
        connectionMode={ConnectionMode.Loose}
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        deleteKeyCode={null}
        fitView
        fitViewOptions={{ padding: 0.2, maxZoom: 0.8 }}
        minZoom={0.1}
        maxZoom={2}
        snapToGrid
        snapGrid={[12, 12]}
        proOptions={{ hideAttribution: true }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={24}
          size={1}
          color="rgba(99, 102, 241, 0.15)"
        />
        <Controls 
          className="!bottom-6 !left-6"
          showInteractive={false}
        />
        {!readOnly && (
          <MiniMap
            className="!bottom-6 !right-6"
            nodeColor={(node) => {
              if (node.type === 'objectType') return '#6366f1';
              return '#64748b';
            }}
            maskColor="rgba(10, 14, 23, 0.8)"
          />
        )}
      </ReactFlow>

      {/* 空态引导：还没有任何对象类型时给出下一步指引 */}
      {storeNodes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="pointer-events-auto text-center max-w-sm px-8 py-10 rounded-2xl bg-surface-900/70 border border-surface-700 backdrop-blur">
            <CubeIcon className="w-10 h-10 text-indigo-400/70 mx-auto mb-3" />
            <h3 className="text-surface-100 font-medium mb-1.5">
              {readOnly ? '暂无模型结构可展示' : '从第一个对象实体开始'}
            </h3>
            <p className="text-xs text-surface-400 leading-relaxed mb-4">
              {readOnly
                ? '选择一个已经建模的本体后，这里会展示对象实体、关系与运行能力的结构视图。'
                : '对象实体是本体的骨架。先声明业务里"有什么"（如 订单、客户），再拖线建立它们之间的关系。'}
            </p>
            {!readOnly && (
              <>
                <button
                  onClick={() => openPanel('create', 'objectType')}
                  className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium transition-colors"
                >
                  ＋ 创建对象实体
                </button>
                <p className="text-[11px] text-surface-500 mt-3">也可以右键画布，或按 Ctrl+K 搜索</p>
              </>
            )}
          </div>
        </div>
      )}

      {/* 右键上下文菜单 */}
      {ctxMenu && menuItems.length > 0 && (
        <>
          <div className="fixed inset-0 z-[104]" onClick={() => setCtxMenu(null)} onContextMenu={(e) => { e.preventDefault(); setCtxMenu(null); }} />
          <div
            className="fixed z-[105] min-w-[180px] bg-surface-800 border border-surface-600 rounded-xl shadow-2xl p-1.5 animate-fade-in"
            style={{
              left: Math.min(ctxMenu.x, window.innerWidth - 200),
              top: Math.min(ctxMenu.y, window.innerHeight - menuItems.length * 40 - 16),
            }}
          >
            {menuItems.map((item) => (
              <button
                key={item.label}
                onClick={item.onClick}
                className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-left text-sm transition-colors ${
                  'danger' in item && item.danger
                    ? 'text-red-300 hover:bg-red-900/40'
                    : 'text-surface-200 hover:bg-surface-700'
                }`}
              >
                <item.icon className="w-4 h-4 shrink-0" />
                {item.label}
              </button>
            ))}
          </div>
        </>
      )}

      {pendingConn && (
        <ConnectLinkDialog
          sourceId={pendingConn.sourceId}
          targetId={pendingConn.targetId}
          onClose={() => setPendingConn(null)}
        />
      )}

      {deleteTarget && (
        <DeleteSelectedDialog target={deleteTarget} onClose={() => setDeleteTarget(null)} />
      )}
    </div>
  );
}
