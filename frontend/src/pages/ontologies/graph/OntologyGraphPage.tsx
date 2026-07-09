import { useState, lazy, Suspense, useEffect } from 'react';
import { ReactFlowProvider } from '@xyflow/react';
import { useParams } from 'react-router-dom';

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
import SentinelPanel from '../../../palantir-graph/components/panels/SentinelPanel';
import { FloatingMenu } from '../../../palantir-graph/components/FloatingMenu';

const HelpGuide = lazy(() => import('../../../palantir-graph/components/panels/HelpGuide'));
const GraphDatabaseView = lazy(() => import('../../../palantir-graph/components/panels/GraphDatabaseView'));
const FunctionTester = lazy(() => import('../../../palantir-graph/components/panels/FunctionTester'));
const ActionRunner = lazy(() => import('../../../palantir-graph/components/panels/ActionRunner'));
import InstanceBrowser from '../../../palantir-graph/components/panels/InstanceBrowser';
const ApiDocs = lazy(() => import('../../../palantir-graph/components/panels/ApiDocs'));
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

  const [showHelp, setShowHelp] = useState(false);
  const [showGraphDB, setShowGraphDB] = useState(false);
  const [showFunctionTester, setShowFunctionTester] = useState(false);
  const [testFunctionId, setTestFunctionId] = useState<string>('');
  const [showActionRunner, setShowActionRunner] = useState(false);
  const [runActionId, setRunActionId] = useState<string>('');
  const [runInstanceId, setRunInstanceId] = useState<string>('');
  const [showInstanceBrowser, setShowInstanceBrowser] = useState(false);
  const [instanceBrowserTypeId, setInstanceBrowserTypeId] = useState<string>('');
  const [showApiDocs, setShowApiDocs] = useState(false);
  const [showActionPanel, setShowActionPanel] = useState(false);
  const [showFunctionPanel, setShowFunctionPanel] = useState(false);
  const [showLinkPanel, setShowLinkPanel] = useState(false);
  const [showSentinel, setShowSentinel] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const [showRunHistory, setShowRunHistory] = useState(false);
  const [showAutonomy, setShowAutonomy] = useState(false);

  const loadFromBackend = useOntologyStore((s) => s.loadFromBackend);
  const syncStatus = useOntologyStore((s) => s.syncStatus);
  const isDirty = useOntologyStore((s) => s.isDirty);

  // 有未保存改动时，拦截关闭/刷新页面（浏览器原生确认框）
  useEffect(() => {
    if (!isDirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [isDirty]);

  // 加载后端本体后再绑定脏标记监听，避免把后端加载结果误判为用户编辑
  useEffect(() => {
    if (!ontologyId) return;
    let off: (() => void) | undefined;
    let cancelled = false;

    void loadFromBackend(ontologyId).then(() => {
      if (cancelled) return;
      off = attachAutoSave();
    });

    return () => {
      cancelled = true;
      off?.();
    };
  }, [ontologyId, loadFromBackend]);

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
      if (mod && e.key.toLowerCase() === 's') {
        e.preventDefault();
        void useOntologyStore.getState().saveToBackend();
        return;
      }
      if (inEditable) return;

      if (mod && !e.shiftKey && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        useOntologyStore.getState().undo();
        return;
      }
      if ((mod && e.shiftKey && e.key.toLowerCase() === 'z') || (mod && e.key.toLowerCase() === 'y')) {
        e.preventDefault();
        useOntologyStore.getState().redo();
        return;
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
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
  }, []);

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

  return (
    <ReactFlowProvider>
      <div className="fixed inset-0 z-[9999] h-screen w-screen overflow-hidden bg-surface-950">
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
          onToggleActions={() => setShowActionPanel(v => !v)}
          onToggleFunctions={() => setShowFunctionPanel(v => !v)}
          showActions={showActionPanel}
          showFunctions={showFunctionPanel}
          onToggleLinks={() => setShowLinkPanel(v => !v)}
          showLinks={showLinkPanel}
        />
        <main className="h-full pt-16">
          <Canvas
            onBrowseInstances={(objectTypeId) => {
              setInstanceBrowserTypeId(objectTypeId);
              setShowInstanceBrowser(true);
            }}
          />
        </main>
        <Toolbar />
        <Panel />
        <ActionList onRunAction={openActionRun} isOpen={showActionPanel} onClose={() => setShowActionPanel(false)} />
        <FunctionList onTestFunction={openFunctionTest} isOpen={showFunctionPanel} onClose={() => setShowFunctionPanel(false)} />
        <LinkList isOpen={showLinkPanel} onClose={() => setShowLinkPanel(false)} />
        <SentinelPanel isOpen={showSentinel} onClose={() => setShowSentinel(false)} />

        {showSearch && <SearchPalette onClose={() => setShowSearch(false)} />}
        {deleteTarget && (
          <DeleteSelectedDialog target={deleteTarget} onClose={() => setDeleteTarget(null)} />
        )}

        <FloatingMenu
          onOpenHelp={() => setShowHelp(true)}
          onOpenFunctionTest={() => openFunctionTest()}
          onOpenActionRun={() => openActionRun()}
          onOpenApiDocs={() => setShowApiDocs(true)}
          onOpenSentinel={() => setShowSentinel(true)}
          onOpenInstances={() => {
            setInstanceBrowserTypeId('');
            setShowInstanceBrowser(true);
          }}
          onOpenRunHistory={() => setShowRunHistory(true)}
          onOpenAutonomy={() => setShowAutonomy(true)}
        />

        <Suspense fallback={<PanelLoader />}>
          {showHelp && <HelpGuide isOpen={showHelp} onClose={() => setShowHelp(false)} />}
          {showGraphDB && <GraphDatabaseView isOpen={showGraphDB} onClose={() => setShowGraphDB(false)} />}
          {showFunctionTester && (
            <FunctionTester
              isOpen={showFunctionTester}
              initialFunctionId={testFunctionId}
              onClose={() => setShowFunctionTester(false)}
            />
          )}
          {showActionRunner && (
            <ActionRunner
              isOpen={showActionRunner}
              initialActionId={runActionId}
              initialInstanceId={runInstanceId}
              onClose={() => setShowActionRunner(false)}
            />
          )}
          {showInstanceBrowser && (
            <InstanceBrowser
              isOpen={showInstanceBrowser}
              initialObjectTypeId={instanceBrowserTypeId || undefined}
              onClose={() => setShowInstanceBrowser(false)}
              onRunAction={openActionRun}
            />
          )}
          {showApiDocs && <ApiDocs isOpen={showApiDocs} onClose={() => setShowApiDocs(false)} />}
          {showRunHistory && <RunHistoryPanel isOpen={showRunHistory} onClose={() => setShowRunHistory(false)} />}
          {showAutonomy && <AutonomyPanel isOpen={showAutonomy} onClose={() => setShowAutonomy(false)} />}
        </Suspense>
      </div>
    </ReactFlowProvider>
  );
}
