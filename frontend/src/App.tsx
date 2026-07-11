import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useAuthStore } from '@/stores/authStore'
import Layout from '@/components/Layout'
import LoginPage from '@/pages/login/LoginPage'
import OverviewPage from '@/pages/overview/OverviewPage'
import OntologyListPage from '@/pages/ontologies/list/OntologyListPage'
import OntologyDetailPage from '@/pages/ontologies/detail/OntologyDetailPage'
import MappingConfigurationPage from '@/pages/ontologies/mapping/MappingConfigurationPage'
import EntityDetailPage from '@/pages/ontologies/detail/entity/EntityDetailPage'
import LogicDetailPage from '@/pages/ontologies/detail/logic/LogicDetailPage'
import ActionDetailPage from '@/pages/ontologies/detail/action/ActionDetailPage'
import ModelsPage from '@/pages/models/ModelsPage'
import SettingsPage from '@/pages/settings/SettingsPage'
import OpenInterfacesPage from '@/pages/settings/OpenInterfacesPage'
import SkillCenterPage from '@/pages/settings/SkillCenterPage'
import PipelinesLayout from '@/pages/pipelines/PipelinesLayout'
import PipelineListPage from '@/pages/pipelines/PipelineListPage'
import PipelineBuilderPage from '@/pages/pipelines/builder/PipelineBuilderPage'
import DataStewardPage from '@/pages/pipelines/steward/DataStewardPage'
import ConnectionsTab from '@/pages/pipelines/connections/ConnectionsTab'
import DatasetsTab from '@/pages/pipelines/datasets/DatasetsTab'
import TransformsTab from '@/pages/pipelines/transforms/TransformsTab'
import CuratedTab from '@/pages/pipelines/curated/CuratedTab'
import SyncTasksTab from '@/pages/pipelines/sync-tasks/SyncTasksTab'
import StructuredDataPage from '@/pages/data-management/structured/StructuredDataPage'
import AgentWorkbenchPage from '@/pages/agent/AgentWorkbenchPage'
import EventRegistryPage from '@/pages/events/EventRegistryPage'
import ExplorationPage from '@/pages/explore/ExplorationPage'
import OntologyGraphPage from '@/pages/ontologies/graph/OntologyGraphPage'
import ApiHubPage from '@/pages/api-hub/ApiHubPage'
import PublicManualDatasetPage from '@/pages/data-management/structured/PublicManualDatasetPage'

const qc = new QueryClient({
  defaultOptions: { queries: { retry: 1 } }
})

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore(s => s.token)
  return token ? <Layout>{children}</Layout> : <Navigate to="/login" replace />
}

function UnknownRouteRedirect() {
  const token = useAuthStore(s => s.token)
  return <Navigate to={token ? '/overview' : '/login'} replace />
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <HashRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/share/manual/:token" element={<PublicManualDatasetPage />} />
          <Route path="/" element={<Navigate to="/overview" replace />} />
          <Route path="/overview" element={<ProtectedRoute><OverviewPage /></ProtectedRoute>} />

          {/* ── 数据管理 ── */}
          <Route path="/data" element={<Navigate to="/data/pipelines" replace />} />
          <Route path="/data/structured" element={<ProtectedRoute><StructuredDataPage /></ProtectedRoute>} />
          <Route path="/data/pipelines" element={<ProtectedRoute><PipelinesLayout /></ProtectedRoute>}>
            <Route index element={<PipelineListPage />} />
            <Route path="connections" element={<ConnectionsTab />} />
            <Route path="sync-tasks" element={<SyncTasksTab />} />
            <Route path="datasets" element={<DatasetsTab />} />
            <Route path="transforms" element={<TransformsTab />} />
            <Route path="curated" element={<CuratedTab />} />
          </Route>
          {/* 数据管家（对话式 n8n 流水线）— 静态段优先于 :pipelineId 匹配 */}
          <Route path="/data/pipelines/steward" element={<ProtectedRoute><DataStewardPage /></ProtectedRoute>} />
          <Route path="/data/pipelines/:pipelineId" element={<ProtectedRoute><PipelineBuilderPage /></ProtectedRoute>} />

          {/* Legacy redirect — keep old /pipelines URLs working */}
          <Route path="/pipelines" element={<Navigate to="/data/pipelines" replace />} />
          <Route path="/pipelines/*" element={<Navigate to="/data/pipelines" replace />} />

          {/* ── 业务探索（对话式业务建模 → 需求文档 → 本体草稿） ── */}
          <Route path="/explore" element={<ProtectedRoute><ExplorationPage /></ProtectedRoute>} />

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
          <Route path="/agent" element={<ProtectedRoute><AgentWorkbenchPage /></ProtectedRoute>} />
          <Route path="/events" element={<ProtectedRoute><EventRegistryPage /></ProtectedRoute>} />
          <Route path="/rag" element={<Navigate to="/agent" replace />} />
          <Route path="/settings" element={<Navigate to="/settings/extraction" replace />} />
          <Route path="/settings/open-interfaces" element={<ProtectedRoute><OpenInterfacesPage /></ProtectedRoute>} />
          <Route path="/settings/skills" element={<ProtectedRoute><SkillCenterPage /></ProtectedRoute>} />
          <Route path="/settings/:tab" element={<ProtectedRoute><SettingsPage /></ProtectedRoute>} />
          <Route path="*" element={<UnknownRouteRedirect />} />
        </Routes>
      </HashRouter>
    </QueryClientProvider>
  )
}
