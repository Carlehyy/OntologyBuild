// ── Domain ──
export interface Domain {
  id: string;
  name: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

// ── Object Type ──
export interface PropertyType {
  id: string;
  object_type_id: string;
  name: string;
  description: string | null;
  data_type: string;
  enum_values: string[] | null;
  unit: string | null;
  is_required: boolean;
  is_unique: boolean;
  default_value: string | null;
  display_order: number;
  created_at: string;
}

export interface ObjectType {
  id: string;
  domain_id: string;
  name: string;
  description: string | null;
  color: string;
  icon: string;
  is_active: boolean;
  version: number;
  properties: PropertyType[];
  created_at: string;
  updated_at: string;
}

// ── Relation Type ──
export interface RelationType {
  id: string;
  domain_id: string;
  name: string;
  description: string | null;
  source_type_id: string;
  target_type_id: string;
  source_type_name: string | null;
  target_type_name: string | null;
  is_directed: boolean;
  cardinality: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

// ── Rule ──
export interface Rule {
  id: string;
  domain_id: string;
  name: string;
  description: string | null;
  condition: Record<string, any>;
  action_type: string;
  action_config: Record<string, any> | null;
  priority: number;
  is_active: boolean;
  is_draft: boolean;
  version: number;
  hit_count: number;
  false_positive_count: number;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

// ── Entity ──
export interface Entity {
  id: string;
  domain_id: string;
  object_type_id: string;
  object_type_name: string | null;
  name: string;
  properties: Record<string, any>;
  confidence: number;
  is_verified: boolean;
  source_document_id: string | null;
  created_at: string;
  updated_at: string;
}

// ── Relation Instance ──
export interface Relation {
  id: string;
  domain_id: string;
  relation_type_id: string;
  relation_type_name: string | null;
  source_id: string;
  source_name: string | null;
  target_id: string;
  target_name: string | null;
  properties: Record<string, any>;
  confidence: number;
  is_verified: boolean;
  created_at: string;
}

// ── Document ──
export interface Document {
  id: string;
  domain_id: string;
  filename: string;
  original_filename: string;
  file_size: number | null;
  mime_type: string | null;
  content_text: string | null;
  content_summary: string | null;
  status: string;
  extracted_entities_count: number;
  extracted_relations_count: number;
  reviewed_by: string | null;
  uploaded_by: string | null;
  created_at: string;
}

// ── Extraction Result ──
export interface ExtractionResult {
  id: string;
  document_id: string;
  result_type: string;
  candidate_object_type_id: string | null;
  candidate_object_type_name: string | null;
  candidate_name: string | null;
  candidate_properties: Record<string, any> | null;
  candidate_relation_type_id: string | null;
  candidate_relation_type_name: string | null;
  candidate_source_name: string | null;
  candidate_target_name: string | null;
  confidence: number | null;
  llm_reasoning: string | null;
  status: string;
  review_action: string | null;
  review_comment: string | null;
  created_at: string;
}

// ── Feedback ──
export interface FeedbackRecord {
  id: string;
  domain_id: string;
  feedback_type: string;
  target_id: string;
  target_type: string;
  verdict: string;
  correction_data: Record<string, any> | null;
  context: Record<string, any> | null;
  user_id: string | null;
  notes: string | null;
  created_at: string;
}

// ── Graph ──
export interface GraphNode {
  id: string;
  label: string;
  type: string;
  type_id: string;
  properties: Record<string, any>;
  color: string;
  confidence: number;
  is_verified: boolean;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  relation_type_id: string;
  properties: Record<string, any>;
  confidence: number;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// ── Dashboard ──
export interface DomainStats {
  domain_id: string;
  domain_name: string;
  object_types_count: number;
  relation_types_count: number;
  rules_count: number;
  rules_active_count: number;
  entities_count: number;
  relations_count: number;
  documents_count: number;
  documents_pending_review: number;
  feedback_count: number;
  proposals_pending: number;
}

export interface DashboardStats {
  total_domains: number;
  total_entities: number;
  total_relations: number;
  total_documents: number;
  pending_reviews: number;
  recent_feedback: FeedbackRecord[];
  domain_stats: DomainStats[];
}

// ── User ──
export interface User {
  id: string;
  username: string;
  email: string;
  display_name: string | null;
  role: string;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

// ── System Config ──
export interface SystemConfig {
  app_name: string;
  app_version: string;
  llm_provider: string;
  llm_available: boolean;
  llm_model: string;
  features: Record<string, boolean>;
}

// ── Audit Log ──
export interface AuditLog {
  id: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  user_id: string | null;
  details: Record<string, any> | null;
  created_at: string;
}
