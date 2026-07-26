// Core Ontology Types - Inspired by Palantir Ontology

export type PropertyType =
  | 'string'
  | 'number'
  | 'boolean'
  | 'date'
  | 'datetime'
  | 'array'
  | 'object'
  | 'reference';

// 属性值来源：存储型(stored) / 计算型(computed)
export type PropertySource = 'stored' | 'computed';

// 属性与数据源字段的绑定/血缘信息。inferred=true 表示没有显式 mapping，
// 仅按属性名兜底推断来源字段。
export interface PropertyDataBinding {
  mappingId?: string;
  curatedDatasetId?: string;
  datasetName?: string;
  sourceColumn?: string;
  originalColumn?: string;
  pipelineId?: string;
  transformNodeId?: string;
  inferred?: boolean;
}

export interface Property {
  id: string;
  name: string;
  displayName: string;
  type: PropertyType;
  required: boolean;
  description?: string;
  defaultValue?: unknown;
  validation?: PropertyValidation;
  referenceType?: string;
  computed?: boolean;  // UI flag, true when source === 'computed' // For reference type, points to another ObjectType
  // 派生属性相关
  source?: PropertySource; // 默认 'stored'
  functionId?: string; // 当 source === 'computed' 时，绑定的 Object Function ID
  dataBinding?: PropertyDataBinding; // 数据源字段绑定/血缘信息
}

export interface PropertyValidation {
  min?: number;
  max?: number;
  pattern?: string;
  enum?: string[];
}

export interface ObjectType {
  id: string;
  name: string;
  displayName: string;
  description?: string;
  icon?: string;
  color?: string;
  primaryKey: string;
  properties: Property[];
  createdAt: string;
  updatedAt: string;
}

export interface LinkType {
  id: string;
  name: string;
  displayName: string;
  description?: string;
  sourceObjectTypeId: string;
  targetObjectTypeId: string;
  cardinality: 'one-to-one' | 'one-to-many' | 'many-to-one' | 'many-to-many';
  sourceRole?: string;
  targetRole?: string;
  properties?: Property[];
  createdAt: string;
  updatedAt: string;
}

export interface Action {
  id: string;
  name: string;
  displayName: string;
  description?: string;
  objectTypeId: string;
  parameters: ActionParameter[];
  rules?: ActionRule[];
  // 绑定的校验函数 ID（Action Validation Function）
  validationFunctionId?: string;
  /** HITL 审批闸门：真实执行先挂起为 pending，待人批准/拒绝（决策记为事实） */
  requiresApproval?: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface ActionParameter {
  id: string;
  name: string;
  displayName?: string;
  type: PropertyType;
  required: boolean;
  description?: string;
  defaultValue?: unknown;
  // 后端兼容标量数组与 {label,value} 数组，也接受 enum/allowedValues 别名。
  options?: Array<{ label: string; value: unknown } | string | number | boolean | null>;
  enum?: Array<{ label: string; value: unknown } | string | number | boolean | null>;
  allowedValues?: Array<{ label: string; value: unknown } | string | number | boolean | null>;
}

// ======================
// Functions (一等公民)
// ======================

export type FunctionType =
  | 'object'             // Object Function - 针对单个对象计算派生属性/摘要
  | 'object_set'         // Object Set Function - 面向对象集合批量计算/聚合
  | 'action_validation'; // Action Validation Function - Action 提交前的业务规则校验

export type FunctionLanguage = 'typescript' | 'expression';

export type CacheStrategy = 'none' | 'ttl' | 'materialized';

export interface FunctionParameter {
  id: string;
  name: string;
  displayName?: string;
  type: PropertyType | 'object' | 'object_set';
  required: boolean;
  description?: string;
  defaultValue?: unknown;
}

export interface OntologyFunction {
  id: string;
  name: string;
  displayName: string;
  description?: string;
  functionType: FunctionType;
  language: FunctionLanguage;
  // 绑定的对象类型（object / object_set 类型）
  targetObjectTypeId?: string;
  // 绑定的动作（action_validation 类型）
  targetActionId?: string;
  // 输入参数（query / action_validation 常用；object 函数隐含 params.object + params.properties）
  parameters: FunctionParameter[];
  // 返回类型
  returnType: PropertyType | 'object' | 'object_set' | 'void' | 'validation_result';
  // 函数体
  body: string;
  // 缓存策略
  cacheStrategy?: CacheStrategy;
  cacheTTL?: number; // 秒
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
}

// ======================
// Object Instances (运行时数据)
// ======================

export interface ObjectInstance {
  id: string;           // 实例 ID = primaryKey 的值
  objectTypeId: string;
  properties: Record<string, unknown>;
  _computed?: Record<string, unknown>;  // 派生属性计算结果
  _links?: Record<string, string[]>;    // 关联的 link instance IDs
  /** 数据出处（manual | collector | import | action）。保存时原样带回，
   *  避免编辑器保存把采集器实例的溯源清洗成 manual */
  source?: string;
  /** 外部源唯一 id（采集去重键），只读透传 */
  externalId?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface LinkInstance {
  id: string;
  linkTypeId: string;
  sourceObjectId: string;
  targetObjectId: string;
  properties?: Record<string, unknown>;
  createdAt: string;
}

// ======================
// Action Execution (动作执行记录)
// ======================

export type ActionExecutionStatus =
  | 'pending'      // 等待 HITL 审批
  | 'validating' | 'validated' | 'executing' | 'success' | 'failed'
  | 'approved'     // 已批准（relatedLogId 指向真正的执行日志）
  | 'rejected'     // 已拒绝（拒绝也是事实，可回放分析）
  | 'rolled_back';

export interface ActionEffect {
  type: 'create_object' | 'update_property' | 'create_link' | 'delete_link' | 'notification' | 'webhook';
  description: string;
  // 具体影响
  targetObjectTypeId?: string;
  targetInstanceId?: string;
  property?: string;
  oldValue?: unknown;
  newValue?: unknown;
  linkTypeId?: string;
}

export interface ActionExecutionLog {
  id: string;
  actionId: string;
  actionName: string;
  objectTypeId: string;
  objectInstanceId?: string;
  parameters: Record<string, unknown>;
  status: ActionExecutionStatus;
  validationErrors?: string[];
  effects?: ActionEffect[];
  errorMessage?: string;
  executedAt: string;
  durationMs?: number;
  dryRun?: boolean;
  errors?: string[];
  validations?: { rule: string; passed: boolean; error?: string }[];
  // —— 溯源 + HITL 审批（后端权威日志携带） ——
  actorId?: string | null;
  decidedBy?: string | null;
  decidedAt?: string | null;
  decisionReason?: string | null;
  relatedLogId?: string | null;
  ontologyVersion?: string | null;
  ontologyReleaseId?: string | null;
  idempotencyKey?: string | null;
  sentinelMatchStateId?: string | null;
  targetSnapshot?: {
    id: string;
    objectTypeId: string;
    properties: Record<string, unknown>;
    computed?: Record<string, unknown>;
  } | null;
  pendingApproval?: boolean;
}

// ======================
// Action Rules (原规则类型)
// ======================

export type ActionRuleType =
  | 'create_object'      // 创建新对象
  | 'update_property'    // 更新属性
  | 'create_link'        // 创建实体关系
  | 'delete_link'        // 删除链接
  | 'validation'         // 验证规则
  | 'webhook'            // 调用外部API
  | 'notification';      // 发送通知

export interface ActionRule {
  id: string;
  type: ActionRuleType;
  name: string;
  description?: string;
  enabled: boolean;
  order: number;
  config: ActionRuleConfig;
}

export type ActionRuleConfig =
  | CreateObjectConfig
  | UpdatePropertyConfig
  | CreateLinkConfig
  | DeleteLinkConfig
  | ValidationConfig
  | WebhookConfig
  | NotificationConfig
  | FunctionRuleConfig;

export interface CreateObjectConfig {
  type: 'create_object';
  targetObjectTypeId: string;
  propertyMappings: PropertyMapping[];
}

export interface UpdatePropertyConfig {
  type: 'update_property';
  targetProperty: string;
  valueSource: 'parameter' | 'expression' | 'constant' | 'function';
  value: string;
  functionId?: string;
}

export interface CreateLinkConfig {
  type: 'create_link';
  linkTypeId: string;
  targetSource: 'parameter' | 'created_object' | 'expression' | 'source';
  targetValue: string;
}

export interface DeleteLinkConfig {
  type: 'delete_link';
  linkTypeId: string;
  condition?: string;
}

export interface ValidationConfig {
  type: 'validation';
  condition: string;
  errorMessage: string;
  functionId?: string; // 可选：绑定函数实现复杂校验
}

export interface WebhookConfig {
  type: 'webhook';
  url: string;
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  headers?: Record<string, string>;
  bodyTemplate?: string;
}

export interface NotificationConfig {
  type: 'notification';
  channel: 'email' | 'sms' | 'push' | 'internal' | 'in_app' | 'in-app';
  /** link = 沿链接跳转取关联对象的属性（如 订单→归属→商家.email），后端已支持 */
  recipientSource: 'parameter' | 'property' | 'constant' | 'link';
  recipient: string;
  messageTemplate: string;
  /** recipientSource=link 时：沿哪条链接类型跳转 */
  linkTypeId?: string;
  /** recipientSource=link 时：读取关联对象的哪个属性（默认 email） */
  recipientProperty?: string;
}

export interface FunctionRuleConfig {
  type: 'function';
  functionId: string; // 执行一个 Function 作为规则
  parameterMappings: PropertyMapping[];
}

export interface PropertyMapping {
  targetProperty: string;
  sourceType: 'parameter' | 'source_property' | 'property' | 'expression' | 'constant' | 'function';
  sourceValue: string;
  functionId?: string;
}

// ======================
// Ontology 根
// ======================

export interface Ontology {
  id: string;
  name: string;
  description?: string;
  version: string;
  objectTypes: ObjectType[];
  linkTypes: LinkType[];
  actions: Action[];
  functions: OntologyFunction[]; // 一等公民
  // 运行时数据（实例）
  instances: ObjectInstance[];
  linkInstances: LinkInstance[];
  // 执行日志
  executionLogs: ActionExecutionLog[];
  createdAt: string;
  updatedAt: string;
}

// ======================
// Canvas Types
// ======================

export interface ObjectTypeNode {
  id: string;
  type: 'objectType';
  position: { x: number; y: number };
  data: ObjectType;
}

export interface FunctionNode {
  id: string;
  type: 'function';
  position: { x: number; y: number };
  data: OntologyFunction;
}

export type OntologyNode = ObjectTypeNode | FunctionNode;

export interface OntologyEdge {
  id: string;
  source: string;
  target: string;
  type: 'link' | 'implements' | 'function_binding' | 'derived_property';
  data?: LinkType | { functionId: string; label?: string };
  animated?: boolean;
  label?: string;
  style?: React.CSSProperties;
}

// ======================
// 函数执行结果
// ======================

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings?: string[];
}

export interface FunctionExecutionResult<T = unknown> {
  success: boolean;
  result?: T;
  data?: T;
  error?: string;
  durationMs: number;
  timestamp: string;
  logs?: string[];
}
