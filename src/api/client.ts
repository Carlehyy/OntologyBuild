/**
 * API Client for the Ontology-Graph-AI Framework backend.
 * All API calls go through this centralized client.
 */

// API base URL - configurable for different deployment environments
const API_BASE = (window as any).__API_BASE__ || import.meta.env.VITE_API_BASE || "http://localhost:8000/api/v1";

async function fetchJson<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json() as Promise<T>;
}

// ── Domains ──
export const listDomains = () => fetchJson<Domain[]>("/ontology/domains");
export const getDomain = (id: string) => fetchJson<Domain>(`/ontology/domains/${id}`);
export const createDomain = (data: { name: string; description?: string }) =>
  fetchJson<Domain>("/ontology/domains", { method: "POST", body: JSON.stringify(data) });

// ── Object Types ──
export const listObjectTypes = (domainId: string) =>
  fetchJson<ObjectType[]>(`/ontology/domains/${domainId}/object-types`);
export const createObjectType = (domainId: string, data: Record<string, any>) =>
  fetchJson<ObjectType>(`/ontology/domains/${domainId}/object-types`, { method: "POST", body: JSON.stringify(data) });
export const updateObjectType = (id: string, data: Record<string, any>) =>
  fetchJson<ObjectType>(`/ontology/object-types/${id}`, { method: "PUT", body: JSON.stringify(data) });
export const deleteObjectType = (id: string) =>
  fetchJson<{ success: boolean }>(`/ontology/object-types/${id}`, { method: "DELETE" });

// ── Relation Types ──
export const listRelationTypes = (domainId: string) =>
  fetchJson<RelationType[]>(`/ontology/domains/${domainId}/relation-types`);
export const createRelationType = (domainId: string, data: Record<string, any>) =>
  fetchJson<RelationType>(`/ontology/domains/${domainId}/relation-types`, { method: "POST", body: JSON.stringify(data) });
export const deleteRelationType = (id: string) =>
  fetchJson<{ success: boolean }>(`/ontology/relation-types/${id}`, { method: "DELETE" });

// ── Rules ──
export const listRules = (domainId: string) =>
  fetchJson<Rule[]>(`/rules/domain/${domainId}`);
export const createRule = (domainId: string, data: Record<string, any>) =>
  fetchJson<Rule>(`/rules/domain/${domainId}`, { method: "POST", body: JSON.stringify(data) });
export const updateRule = (id: string, data: Record<string, any>) =>
  fetchJson<Rule>(`/rules/${id}`, { method: "PUT", body: JSON.stringify(data) });
export const toggleRule = (id: string) =>
  fetchJson<{ is_active: boolean }>(`/rules/${id}/toggle-active`, { method: "POST" });
export const publishRule = (id: string) =>
  fetchJson<{ success: boolean }>(`/rules/${id}/publish`, { method: "POST" });
export const deleteRule = (id: string) =>
  fetchJson<{ success: boolean }>(`/rules/${id}`, { method: "DELETE" });

// ── Graph ──
export const getGraphVisualization = (domainId: string) =>
  fetchJson<GraphData>(`/graph/domain/${domainId}/visualization`);
export const getGraphStats = (domainId: string) =>
  fetchJson<{ entity_count: number; relation_count: number; type_breakdown: any[] }>(`/graph/domain/${domainId}/stats`);
export const searchGraph = (domainId: string, query: string) =>
  fetchJson<{ results: any[] }>(`/graph/search?domain_id=${domainId}&q=${encodeURIComponent(query)}`);
export const listEntities = (domainId: string, objectTypeId?: string) => {
  const params = new URLSearchParams();
  if (objectTypeId) params.append("object_type_id", objectTypeId);
  return fetchJson<Entity[]>(`/graph/domain/${domainId}/entities?${params}`);
};
export const createEntity = (domainId: string, data: Record<string, any>) =>
  fetchJson<Entity>(`/graph/domain/${domainId}/entities`, { method: "POST", body: JSON.stringify(data) });
export const listRelations = (domainId: string) =>
  fetchJson<Relation[]>(`/graph/domain/${domainId}/relations`);

// ── Documents ──
export const listDocuments = (domainId: string) =>
  fetchJson<Document[]>(`/extraction/domain/${domainId}/documents`);
export const uploadDocument = async (domainId: string, file: File) => {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${API_BASE}/extraction/domain/${domainId}/documents`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
    throw new Error(error.detail || `Upload failed`);
  }
  return response.json() as Promise<Document>;
};
export const runExtraction = (documentId: string) =>
  fetchJson<{ success: boolean; message: string }>(`/extraction/documents/${documentId}/extract`, { method: "POST" });
export const getExtractionResults = (documentId: string) =>
  fetchJson<ExtractionResult[]>(`/extraction/documents/${documentId}/results`);
export const reviewExtraction = (data: { result_id: string; action: string; modifications?: Record<string, any>; comment?: string }) =>
  fetchJson<{ success: boolean }>(`/extraction/results/review`, { method: "POST", body: JSON.stringify(data) });

// ── Inference ──
export const runInference = (data: { domain_id: string; query: string; use_rules?: boolean; use_llm?: boolean }) =>
  fetchJson<{ query: string; answer: string | null; rule_hits: any[]; referenced_entities: string[]; confidence: number; reasoning: string | null }>(`/inference/query`, { method: "POST", body: JSON.stringify(data) });

// ── Feedback ──
export const listFeedback = (domainId: string) =>
  fetchJson<FeedbackRecord[]>(`/feedback/domain/${domainId}`);
export const getFeedbackStats = (domainId: string) =>
  fetchJson<Record<string, any>>(`/feedback/stats/${domainId}`);

// ── Admin ──
export const getDashboardStats = () =>
  fetchJson<DashboardStats>("/admin/dashboard");
export const getSystemConfig = () =>
  fetchJson<SystemConfig>("/admin/config");
export const listUsers = () =>
  fetchJson<User[]>("/admin/users");
export const listAuditLogs = () =>
  fetchJson<AuditLog[]>("/admin/audit-logs");

// ── Rule Executions ──
export const listRuleExecutions = (ruleId: string) =>
  fetchJson<any[]>(`/rules/${ruleId}/executions`);

// ── Seed ──
export const seedDatabase = () =>
  fetchJson<Record<string, any>>("/seed", { method: "POST" });

// Import types for reference
import type { Domain, ObjectType, RelationType, Rule, Entity, Relation, Document, ExtractionResult, FeedbackRecord, GraphData, DashboardStats, User, SystemConfig, AuditLog } from "@/types";
