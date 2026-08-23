import { useEffect } from 'react'
import { HashRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useAuthStore } from '@/stores/authStore'
import Layout from '@/components/Layout'
import LoginPage from '@/pages/login/LoginPage'
import { OverviewPage } from '@/features/overview'
import InboxPage from '@/pages/inbox/InboxPage'
import OntologyListPage from '@/pages/ontologies/list/OntologyListPage'
import OntologyDetailPage from '@/pages/ontologies/detail/OntologyDetailPage'
import MappingConfigurationPage from '@/pages/ontologies/mapping/MappingConfigurationPage'
import EntityDetailPage from '@/pages/ontologies/detail/entity/EntityDetailPage'
import LogicDetailPage from '@/pages/ontologies/detail/logic/LogicDetailPage'
import ActionDetailPage from '@/pages/ontologies/detail/action/ActionDetailPage'
import ModelsPage from '@/pages/models/ModelsPage'
import SettingsPage from '@/pages/settings/SettingsPage'
import PipelinesLayout from '@/pages/pipelines/PipelinesLayout'
import PipelineListPage from '@/pages/pipelines/PipelineListPage'
import PythonScriptPage from '@/pages/pipelines/script/PythonScriptPage'
import DataStewardPage from '@/pages/pipelines/steward/DataStewardPage'
import FileAssetDownloadPage from '@/pages/pipelines/FileAssetDownloadPage'
import ConnectionsTab from '@/pages/pipelines/connections/ConnectionsTab'
import DatasetsTab from '@/pages/pipelines/datasets/DatasetsTab'
import CuratedTab from '@/pages/pipelines/curated/CuratedTab'
import SyncTasksTab from '@/pages/pipelines/sync-tasks/SyncTasksTab'
import StructuredDataPage from '@/pages/data-management/structured/StructuredDataPage'
import AgentWorkbenchPage from '@/pages/agent/AgentWorkbenchPage'
import ReportStudioPage from '@/pages/agent/ReportStudioPage'
import EventRegistryPage from '@/pages/events/EventRegistryPage'
import ExplorationPage from '@/pages/explore/ExplorationPage'
import SuperAssistantPage from '@/pages/super-assistant/SuperAssistantPage'
import SkillCommunityPage from '@/pages/community/SkillCommunityPage'
import PluginCommunityPage from '@/pages/community/PluginCommunityPage'
import OntologyNetworkPage from '@/pages/ontology-model/OntologyNetworkPage'
import OntologyGraphPage from '@/pages/ontologies/graph/OntologyGraphPage'
import WorldModelModelsPage from '@/pages/world-model/WorldModelModelsPage'
import WorldModelServicesPage from '@/pages/world-model/WorldModelServicesPage'
import WorldModelCallsPage from '@/pages/world-model/WorldModelCallsPage'
import WorldModelDevelopPage from '@/pages/world-model/WorldModelDevelopPage'
import ApiHubPage from '@/pages/api-hub/ApiHubPage'
import SceneListPage from '@/pages/scenes/list/SceneListPage'
import SceneDetailPage from '@/pages/scenes/detail/SceneDetailPage'
import PublicManualDatasetPage from '@/pages/data-management/structured/PublicManualDatasetPage'
import { ToastProvider } from '@/components/ui/Toast'
import { AccessDeniedPage, NoAssignedPagesPage } from '@/pages/errors/AccessDeniedPage'
import { canAccessPath, defaultLandingPath, firstAccessiblePath } from '@/config/navigation'

const qc = new QueryClient({
  defaultOptions: { queries: { retry: 1 } }
})

let lastAuthorizedPath: string | null = null

function loginDestination(pathname: string, search: string): string {
  const returnTo = `${pathname}${search}`
  return `/login?returnTo=${encodeURIComponent(returnTo)}`
}

function AuthenticatedRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore(s => s.token)
  const user = useAuthStore(s => s.user)
  const location = useLocation()
  if (!token || !user) {
    return (
      <Navigate
        to={loginDestination(location.pathname, location.search)}
        replace
        state={{ returnTo: `${location.pathname}${location.search}` }}
      />
    )
  }
  return children
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore(s => s.token)
  const user = useAuthStore(s => s.user)
  const location = useLocation()
  const authorized = !!token && !!user && canAccessPath(user, location.pathname)
  useEffect(() => {
    if (authorized) lastAuthorizedPath = location.pathname
  }, [authorized, location.pathname])
  if (!token || !user) {
    return (
      <Navigate
        to={loginDestination(location.pathname, location.search)}
        replace
        state={{ returnTo: `${location.pathname}${location.search}` }}
      />
    )
  }
  if (!authorized) {
    const returnTo = lastAuthorizedPath && canAccessPath(user, lastAuthorizedPath)
      ? lastAuthorizedPath
      : firstAccessiblePath(user)
    return <Layout><AccessDeniedPage returnTo={returnTo} /></Layout>
  }
  return <Layout>{children}</Layout>
}

function HomeRedirect() {
  const token = useAuthStore(s => s.token)
  const user = useAuthStore(s => s.user)
  return <Navigate to={token && user ? defaultLandingPath(user) : '/login'} replace />
}

function UnknownRouteRedirect() {
  const token = useAuthStore(s => s.token)
  const user = useAuthStore(s => s.user)
  return <Navigate to={token && user ? defaultLandingPath(user) : '/login'} replace />
}

/** 旧世界模型路径（/ontologies/world-model/*）升级为一级路由后的兜底重定向，保留子路径与 query */
function LegacyWorldModelRedirect() {
  const location = useLocation()
  const rest = location.pathname.replace(/^\/ontologies\/world-model/, '')
  return <Navigate to={`/world-model${rest}${location.search}`} replace />
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <HashRouter>
          <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/share/manual/:token" element={<PublicManualDatasetPage />} />
          <Route
            path="/file-assets/:assetId/download"
            element={<AuthenticatedRoute><FileAssetDownloadPage /></AuthenticatedRoute>}
          />
          <Route path="/" element={<HomeRedirect />} />
          <Route path="/no-access" element={<ProtectedRoute><NoAssignedPagesPage /></ProtectedRoute>} />
          <Route path="/overview" element={<ProtectedRoute><OverviewPage /></ProtectedRoute>} />
          <Route path="/inbox" element={<ProtectedRoute><InboxPage /></ProtectedRoute>} />

          {/* ── 数据管理 ── */}
          <Route path="/data" element={<Navigate to="/data/pipelines" replace />} />
          <Route path="/data/structured" element={<ProtectedRoute><StructuredDataPage /></ProtectedRoute>} />
          <Route path="/data/pipelines" element={<ProtectedRoute><PipelinesLayout /></ProtectedRoute>}>
            <Route index element={<PipelineListPage />} />
            <Route path="connections" element={<ConnectionsTab />} />
            <Route path="sync-tasks" element={<SyncTasksTab />} />
            <Route path="datasets" element={<DatasetsTab />} />
            <Route path="curated" element={<CuratedTab />} />
          </Route>
          {/* 数据管家（对话式 n8n 流水线）— 静态段优先于 :pipelineId 匹配 */}
          <Route path="/data/pipelines/steward" element={<ProtectedRoute><DataStewardPage /></ProtectedRoute>} />
          {/* Python 脚本流水线编辑页 — 静态段优先于 :pipelineId 匹配 */}
          <Route path="/data/pipelines/script/:pipelineId" element={<ProtectedRoute><PythonScriptPage /></ProtectedRoute>} />
          {/* 画布编排已下线：旧 builder 深链重定向回流水线列表（静态段天然优先于 :pipelineId） */}
          <Route path="/data/pipelines/:pipelineId" element={<Navigate to="/data/pipelines" replace />} />

          {/* Legacy redirect — keep old /pipelines URLs working */}
          <Route path="/pipelines" element={<Navigate to="/data/pipelines" replace />} />
          <Route path="/pipelines/*" element={<Navigate to="/data/pipelines" replace />} />

          {/* ── 本体建模（对话式业务建模 → 需求文档 → 本体草稿） ── */}
          <Route path="/explore" element={<ProtectedRoute><ExplorationPage /></ProtectedRoute>} />

          {/* 本体模型（一级导航域）— 本体建模/本体管理为子菜单，本体网络待建设 */}
          <Route path="/ontology-model" element={<Navigate to="/explore" replace />} />
          <Route path="/ontology-model/network" element={<ProtectedRoute><OntologyNetworkPage /></ProtectedRoute>} />

          {/* 世界模型（演化层）— 一级导航域；推演模型/推演服务/调用记录为独立页面；旧 /ontologies/world-model/* 路径保留重定向 */}
          <Route path="/world-model" element={<Navigate to="/world-model/models" replace />} />
          <Route path="/world-model/models" element={<ProtectedRoute><WorldModelModelsPage /></ProtectedRoute>} />
          <Route path="/world-model/models/:modelId/develop" element={<ProtectedRoute><WorldModelDevelopPage /></ProtectedRoute>} />
          <Route path="/world-model/services" element={<ProtectedRoute><WorldModelServicesPage /></ProtectedRoute>} />
          <Route path="/world-model/calls" element={<ProtectedRoute><WorldModelCallsPage /></ProtectedRoute>} />
          <Route path="/ontologies/world-model" element={<LegacyWorldModelRedirect />} />
          <Route path="/ontologies/world-model/*" element={<LegacyWorldModelRedirect />} />
          <Route path="/ontologies" element={<ProtectedRoute><OntologyListPage /></ProtectedRoute>} />
          <Route path="/ontologies/new" element={<ProtectedRoute><OntologyListPage defaultCreateOpen /></ProtectedRoute>} />
          <Route path="/ontologies/:id" element={<ProtectedRoute><OntologyDetailPage /></ProtectedRoute>} />
          <Route path="/ontologies/:id/mapping-config" element={<ProtectedRoute><MappingConfigurationPage /></ProtectedRoute>} />
          <Route path="/ontologies/:id/graph" element={<ProtectedRoute><OntologyGraphPage /></ProtectedRoute>} />
          <Route path="/ontologies/:id/entities/:eid" element={<ProtectedRoute><EntityDetailPage /></ProtectedRoute>} />
          <Route path="/ontologies/:id/logic/:lid" element={<ProtectedRoute><LogicDetailPage /></ProtectedRoute>} />
          <Route path="/ontologies/:id/actions/:aid" element={<ProtectedRoute><ActionDetailPage /></ProtectedRoute>} />
          <Route path="/models" element={<ProtectedRoute><ModelsPage /></ProtectedRoute>} />
          <Route path="/api-hub" element={<Navigate to="/api-hub/interfaces" replace />} />
          <Route path="/api-hub/:tab" element={<ProtectedRoute><ApiHubPage /></ProtectedRoute>} />
          <Route path="/community" element={<Navigate to="/community/skills" replace />} />
          <Route path="/community/skills" element={<ProtectedRoute><SkillCommunityPage /></ProtectedRoute>} />
          <Route path="/community/plugins" element={<ProtectedRoute><PluginCommunityPage /></ProtectedRoute>} />
          <Route path="/agent/reports" element={<ProtectedRoute><ReportStudioPage /></ProtectedRoute>} />
          <Route path="/agent/reports/:templateId" element={<ProtectedRoute><ReportStudioPage /></ProtectedRoute>} />
          <Route path="/agent" element={<ProtectedRoute><AgentWorkbenchPage /></ProtectedRoute>} />
          <Route path="/super-assistant" element={<ProtectedRoute><SuperAssistantPage /></ProtectedRoute>} />
          <Route path="/events" element={<ProtectedRoute><EventRegistryPage /></ProtectedRoute>} />
          <Route path="/scenes" element={<ProtectedRoute><SceneListPage /></ProtectedRoute>} />
          <Route path="/scenes/:id" element={<ProtectedRoute><SceneDetailPage /></ProtectedRoute>} />
          <Route path="/rag" element={<Navigate to="/agent" replace />} />
          <Route path="/settings" element={<Navigate to="/settings/users" replace />} />
          <Route path="/settings/skills" element={<Navigate to="/settings/users" replace />} />
          <Route path="/settings/extraction" element={<Navigate to="/settings/users" replace />} />
          <Route path="/settings/rules" element={<Navigate to="/settings/users" replace />} />
          <Route path="/settings/prompts" element={<Navigate to="/settings/users" replace />} />
          <Route path="/settings/open-interfaces" element={<Navigate to="/settings/users" replace />} />
          <Route path="/settings/minio" element={<Navigate to="/settings/users" replace />} />
          <Route path="/settings/workflows" element={<Navigate to="/settings/users" replace />} />
          <Route path="/settings/agents" element={<Navigate to="/settings/users" replace />} />
          <Route path="/settings/:tab" element={<ProtectedRoute><SettingsPage /></ProtectedRoute>} />
          <Route path="*" element={<UnknownRouteRedirect />} />
          </Routes>
        </HashRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}
