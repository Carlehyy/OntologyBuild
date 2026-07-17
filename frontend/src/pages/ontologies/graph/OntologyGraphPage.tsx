import { useState, lazy, Suspense, useEffect } from 'react';
import { ReactFlowProvider } from '@xyflow/react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ontologyVersionApi } from '@/api/v2/ontology-versions';

import '../../../palantir-graph/palantir-graph.css';

import Canvas from '../../../palantir-graph/components/Canvas';
import Header from '../../../palantir-graph/components/Header';
import { useOntologyStore, attachAutoSave, attachHistory } from '../../../palantir-graph/store/ontologyStore';
import Toolbar from '../../../palantir-graph/components/Toolbar';
import Panel from '../../../palantir-graph/components/Panel';
import SearchPalette from '../../../palantir-graph/components/SearchPalette';
import DeleteSelectedDialog, { type DeleteTarget } from '../../../palantir-graph/components/DeleteSelectedDialog';
import ActionList from '../../../palantir-graph/components/panels/ActionList';
import FunctionList from '../../../palantir-graph/components/panels/FunctionList';
import LinkList from '../../../palantir-graph/components/panels/LinkList';
import ObjectList from '../../../palantir-graph/components/panels/ObjectList';
import SentinelPanel from '../../../palantir-graph/components/panels/SentinelPanel';
import { FloatingMenu } from '../../../palantir-graph/components/FloatingMenu';

const HelpGuide = lazy(() => import('../../../palantir-graph/components/panels/HelpGuide'));
const GraphDatabaseView = lazy(() => import('../../../palantir-graph/components/panels/GraphDatabaseView'));
const FunctionTester = lazy(() => import('../../../palantir-graph/components/panels/FunctionTester'));
const ActionRunner = lazy(() => import('../../../palantir-graph/components/panels/ActionRunner'));
import InstanceBrowser from '../../../palantir-graph/components/panels/InstanceBrowser';
const RunHistoryPanel = lazy(() => import('../../../palantir-graph/components/panels/RunHistoryPanel'));
const AutonomyPanel = lazy(() => import('../../../palantir-graph/components/panels/AutonomyPanel'));

function PanelLoader() {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="flex flex-col items-center gap-4">
        <div className="w-10 h-10 border-4 border-violet-500 border-t-transparent rounded-full animate-spin" />
        <span className="text-gray-400 text-sm">加载中...</span>
      </div>
    </div>
  );
}

export default function OntologyGraphPage() {
  const { id: ontologyId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const versionId = searchParams.get('versionId');

  const [showHelp, setShowHelp] = useState(false);
  const [showGraphDB, setShowGraphDB] = useState(false);
  const [showFunctionTester, setShowFunctionTester] = useState(false);
  const [testFunctionId, setTestFunctionId] = useState<string>('');
  const [showActionRunner, setShowActionRunner] = useState(false);
  const [runActionId, setRunActionId] = useState<string>('');
  const [runInstanceId, setRunInstanceId] = useState<string>('');
  const [showInstanceBrowser, setShowInstanceBrowser] = useState(false);
  const [instanceBrowserTypeId, setInstanceBrowserTypeId] = useState<string>('');
  const [showActionPanel, setShowActionPanel] = useState(false);
  const [showFunctionPanel, setShowFunctionPanel] = useState(false);
  const [showLinkPanel, setShowLinkPanel] = useState(false);
  const [showObjectPanel, setShowObjectPanel] = useState(false);
  const [showSentinel, setShowSentinel] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const [showRunHistory, setShowRunHistory] = useState(false);
  const [showAutonomy, setShowAutonomy] = useState(false);

  const loadFromBackend = useOntologyStore((s) => s.loadFromBackend);
  const syncStatus = useOntologyStore((s) => s.syncStatus);
  const isDirty = useOntologyStore((s) => s.isDirty);
  const workspaceMode = useOntologyStore((s) => s.workspaceMode);
  const ontology = useOntologyStore((s) => s.ontology);
  const readOnly = workspaceMode !== 'draft';
  const [creatingDraft, setCreatingDraft] = useState(false);
  const [draftError, setDraftError] = useState('');

  // 有未保存改动时，拦截关闭/刷新页面（浏览器原生确认框）
  useEffect(() => {
    if (readOnly || !isDirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [isDirty, readOnly]);

  // 加载后端本体后再绑定脏标记监听，避免把后端加载结果误判为用户编辑
  useEffect(() => {
    if (!ontologyId) return;
    let off: (() => void) | undefined;
    let cancelled = false;

    void loadFromBackend(ontologyId, versionId).then(() => {
      if (cancelled) return;
      if (useOntologyStore.getState().workspaceMode === 'draft') {
        off = attachAutoSave();
      }
    });

    return () => {
      cancelled = true;
      off?.();
    };
  }, [ontologyId, versionId, loadFromBackend]);

  // 撤销/重做历史记录（与后端加载无关，进入编辑器即绑定）
  useEffect(() => attachHistory(), []);

  // 快捷键：Ctrl+K 搜索 / Ctrl+Z 撤销 / Ctrl+Shift+Z·Ctrl+Y 重做 / Ctrl+S 保存
  // Delete 删除选中（带影响预览）/ Esc 关闭右侧面板
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const inEditable = !!el && (
        el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable
      );
      const mod = e.metaKey || e.ctrlKey;

      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setShowSearch((v) => !v);
        return;
      }
      if (mod && e.key.toLowerCase() === 's' && !readOnly) {
        e.preventDefault();
        void useOntologyStore.getState().saveToBackend();
        return;
      }
      if (inEditable) return;

      if (!readOnly && mod && !e.shiftKey && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        useOntologyStore.getState().undo();
        return;
      }
      if (!readOnly && ((mod && e.shiftKey && e.key.toLowerCase() === 'z') || (mod && e.key.toLowerCase() === 'y'))) {
        e.preventDefault();
        useOntologyStore.getState().redo();
        return;
      }
      if (!readOnly && (e.key === 'Delete' || e.key === 'Backspace')) {
        const s = useOntologyStore.getState();
        if (s.selectedNodeId) {
          e.preventDefault();
          setDeleteTarget({ kind: 'objectType', id: s.selectedNodeId });
        } else if (s.selectedEdgeId) {
          e.preventDefault();
          setDeleteTarget({ kind: 'linkType', id: s.selectedEdgeId });
        }
        return;
      }
      if (e.key === 'Escape') {
        const s = useOntologyStore.getState();
        if (s.isPanelOpen) s.closePanel();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [readOnly]);

  // 为 body 添加作用域类，确保 Palantir 样式只在本页面生效
  useEffect(() => {
    const originalClass = document.body.className;
    const originalOverflow = document.body.style.overflow;
    document.body.classList.add('palantir-graph-root');
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.className = originalClass;
      document.body.style.overflow = originalOverflow;
    };
  }, []);

  const openFunctionTest = (fnId?: string) => {
    setTestFunctionId(fnId || '');
    setShowFunctionTester(true);
  };
  const openActionRun = (actionId?: string, instanceId?: string) => {
    setRunActionId(actionId || '');
    setRunInstanceId(instanceId || '');
    setShowActionRunner(true);
  };

  const beginCurrentReleaseEdit = async () => {
    if (!ontologyId || creatingDraft) return;
    setDraftError('');
    setCreatingDraft(true);
    try {
      const tree = await ontologyVersionApi.tree(ontologyId);
      const draft = await ontologyVersionApi.createDraft(
        ontologyId,
        tree.current_release_id,
        {
          versionLabel: '模型修改',
          description: `基于当前发布版 ${tree.current_release_version} 创建`,
        },
      );
      navigate(`/ontologies/${ontologyId}/graph?versionId=${draft.id}`);
    } catch (error: any) {
      const detail = error?.response?.data?.detail ?? error?.detail;
      setDraftError(typeof detail === 'string' ? detail : detail?.message || error?.message || '创建修改分支失败');
    } finally {
      setCreatingDraft(false);
    }
  };

  const stage = workspaceMode === 'draft'
    ? { tone: 'sky', text: `草稿 ${ontology?.version || ''} · 可编辑并查看全部模型定义，不产生 Fact、不执行本体网络` }
    : workspaceMode === 'trial'
      ? { tone: 'amber', text: `试跑态 ${ontology?.version || ''} · 可查看定义并保存画布布局，真实数据仅写入隔离空间` }
      : workspaceMode === 'release'
        ? { tone: 'slate', text: `历史发布 ${ontology?.version || ''} · 可查看定义并保存画布布局，不承载当前运行数据` }
        : workspaceMode === 'archived'
          ? { tone: 'slate', text: `已归档分支 ${ontology?.version || ''} · 可查看定义并保存画布布局` }
          : { tone: 'emerald', text: `当前发布 ${ontology?.version || ''} · 可查看定义并保存画布布局，正式数据与本体网络持续运行` };

  const stageClass = {
    sky: 'border-sky-500/40 bg-sky-950/90 text-sky-200',
    amber: 'border-amber-500/40 bg-amber-950/90 text-amber-200',
    slate: 'border-slate-500/40 bg-slate-950/90 text-slate-200',
    emerald: 'border-emerald-500/40 bg-emerald-950/90 text-emerald-200',
  }[stage.tone];

  return (
    <ReactFlowProvider>
      <div className="fixed inset-0 z-[9999] h-screen w-screen overflow-hidden bg-surface-950">
        <div className={`fixed left-1/2 top-[4.4rem] z-50 flex max-w-[calc(100vw-2rem)] -translate-x-1/2 items-center gap-2 rounded-lg border px-3 py-1.5 text-xs shadow-lg ${stageClass}`}>
          <span className="truncate">{stage.text}</span>
          {workspaceMode === 'runtime' ? (
            <button
              className="shrink-0 font-medium underline disabled:cursor-wait disabled:opacity-60"
              disabled={creatingDraft}
              onClick={() => void beginCurrentReleaseEdit()}
            >
              {creatingDraft ? '正在创建草稿' : '基于此版本开始修改'}
            </button>
          ) : (
            <button className="shrink-0 font-medium underline" onClick={() => navigate(`/ontologies/${ontologyId}?tab=versions`)}>返回版本演进</button>
          )}
        </div>
        {draftError && (
          <div role="alert" className="fixed left-1/2 top-[6.8rem] z-50 -translate-x-1/2 rounded-lg border border-red-500/40 bg-red-950/95 px-3 py-2 text-xs text-red-200 shadow-lg">
            {draftError}
          </div>
        )}
        {/* 加载本体时的轻量提示（保存状态已整合到 Header 的保存按钮） */}
        {syncStatus === 'loading' && (
          <div className="fixed top-3 left-1/2 -translate-x-1/2 z-50">
            <span className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-surface-800/80 backdrop-blur border border-surface-700 text-xs text-surface-300">
              <span className="w-3 h-3 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
              加载本体…
            </span>
          </div>
        )}

        <Header
          readOnly={readOnly}
          stageLabel={workspaceMode === 'runtime' ? '当前发布' : workspaceMode === 'draft' ? '草稿编辑' : workspaceMode === 'trial' ? '试跑快照' : '历史快照'}
          onToggleActions={() => setShowActionPanel(v => !v)}
          onToggleFunctions={() => setShowFunctionPanel(v => !v)}
          showActions={showActionPanel}
          showFunctions={showFunctionPanel}
          onToggleLinks={() => setShowLinkPanel(v => !v)}
          showLinks={showLinkPanel}
          onToggleObjects={() => setShowObjectPanel(v => !v)}
          showObjects={showObjectPanel}
        />
        <main className="h-full pt-16">
          <Canvas
            readOnly={readOnly}
            layoutScope={`${ontologyId || 'ontology'}:${versionId || 'runtime'}`}
            onBrowseInstances={(objectTypeId) => {
              setInstanceBrowserTypeId(objectTypeId);
              setShowInstanceBrowser(true);
            }}
          />
        </main>
        {!readOnly && <Toolbar />}
        <Panel readOnly={readOnly} />
        <ActionList
          readOnly={readOnly}
          onRunAction={workspaceMode === 'runtime' ? openActionRun : undefined}
          isOpen={showActionPanel}
          onClose={() => setShowActionPanel(false)}
        />
        <FunctionList
          readOnly={readOnly}
          onTestFunction={workspaceMode === 'runtime' ? openFunctionTest : undefined}
          isOpen={showFunctionPanel}
          onClose={() => setShowFunctionPanel(false)}
        />
        <LinkList readOnly={readOnly} isOpen={showLinkPanel} onClose={() => setShowLinkPanel(false)} />
        <ObjectList readOnly={readOnly} isOpen={showObjectPanel} onClose={() => setShowObjectPanel(false)} />
        <SentinelPanel isOpen={showSentinel} onClose={() => setShowSentinel(false)} />

        {!readOnly && showSearch && <SearchPalette onClose={() => setShowSearch(false)} />}
        {!readOnly && deleteTarget && (
          <DeleteSelectedDialog target={deleteTarget} onClose={() => setDeleteTarget(null)} />
        )}

        {workspaceMode === 'runtime' && <FloatingMenu
          onOpenHelp={() => setShowHelp(true)}
          onOpenFunctionTest={() => openFunctionTest()}
          onOpenActionRun={() => openActionRun()}
          onOpenSentinel={() => setShowSentinel(true)}
          onOpenInstances={() => {
            setInstanceBrowserTypeId('');
            setShowInstanceBrowser(true);
          }}
          onOpenRunHistory={() => setShowRunHistory(true)}
          onOpenAutonomy={() => setShowAutonomy(true)}
        />}

        <Suspense fallback={<PanelLoader />}>
          {showHelp && <HelpGuide isOpen={showHelp} onClose={() => setShowHelp(false)} />}
          {showGraphDB && <GraphDatabaseView isOpen={showGraphDB} onClose={() => setShowGraphDB(false)} />}
          {workspaceMode === 'runtime' && showFunctionTester && (
            <FunctionTester
              isOpen={showFunctionTester}
              initialFunctionId={testFunctionId}
              onClose={() => setShowFunctionTester(false)}
            />
          )}
          {workspaceMode === 'runtime' && showActionRunner && (
            <ActionRunner
              isOpen={showActionRunner}
              initialActionId={runActionId}
              initialInstanceId={runInstanceId}
              onClose={() => setShowActionRunner(false)}
            />
          )}
          {workspaceMode === 'runtime' && showInstanceBrowser && (
            <InstanceBrowser
              isOpen={showInstanceBrowser}
              initialObjectTypeId={instanceBrowserTypeId || undefined}
              onClose={() => setShowInstanceBrowser(false)}
              onRunAction={openActionRun}
            />
          )}
          {workspaceMode === 'runtime' && showRunHistory && <RunHistoryPanel isOpen={showRunHistory} onClose={() => setShowRunHistory(false)} />}
          {workspaceMode === 'runtime' && showAutonomy && <AutonomyPanel isOpen={showAutonomy} onClose={() => setShowAutonomy(false)} />}
        </Suspense>
      </div>
    </ReactFlowProvider>
  );
}
