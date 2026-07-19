/**
 * Action Execution Engine
 *
 * 实现动作的校验、模拟执行、真实执行逻辑。
 * 支持：参数校验 → 校验函数 → 事务性规则执行 → 审计日志
 */
import type {
  Ontology,
  Action,
  ActionExecutionLog,
  ActionExecutionStatus,
  ActionEffect,
  ObjectInstance,
  ObjectType,
  ActionRule,
  PropertyMapping,
  WebhookConfig,
} from '../types/ontology';
import { executeFunction, executeActionValidation } from './functionEngine';

const now = () => new Date().toISOString();
const genId = () => `exec_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
const genInstanceId = () => `inst_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

export interface ActionExecuteInput {
  actionId: string;
  parameters: Record<string, unknown>;
  /** 要操作的目标实例 ID（对 update 类动作） */
  targetInstanceId?: string;
  /** 是否为模拟（dry-run），不修改实际数据 */
  dryRun?: boolean;
}

export interface ActionExecuteResult {
  success: boolean;
  log: ActionExecutionLog;
  // 若产生了新实例/修改/链接变化，返回新状态（真实执行时由 store 应用到画布）
  newInstances?: ObjectInstance[];
  updatedInstances?: ObjectInstance[];
  newLinks?: { linkTypeId: string; sourceId: string; targetId: string }[];
  deletedLinkIds?: string[];
  notifications?: string[];
}

/**
 * 获取属性映射的实际值
 */
function resolveMappingValue(
  mapping: PropertyMapping,
  params: Record<string, unknown>,
  sourceInstance: Record<string, unknown> | undefined,
  ontology: Ontology,
  _createdInstances: Map<string, ObjectInstance> // 用于 created_object 引用
): unknown {
  switch (mapping.sourceType) {
    case 'parameter':
      return params[mapping.sourceValue];
    case 'source_property':
    case 'property':
      return sourceInstance?.[mapping.sourceValue];
    case 'constant':
      try {
        return JSON.parse(mapping.sourceValue);
      } catch {
        return mapping.sourceValue;
      }
    case 'expression': {
      // 使用函数引擎评估表达式
      const fn = ontology.functions.find(f => f.id === mapping.functionId);
      if (fn) {
        const r = executeFunction(fn, ontology, { object: sourceInstance, params });
        return r.success ? r.data : undefined;
      }
      return undefined;
    }
    case 'function': {
      const fn2 = ontology.functions.find(f => f.id === mapping.functionId);
      if (fn2) {
        const r = executeFunction(fn2, ontology, { object: sourceInstance, params });
        return r.success ? r.data : undefined;
      }
      return undefined;
    }
  }
}

/**
 * 根据规则查找目标对象类型
 */
function getObjectType(ontology: Ontology, id: string): ObjectType | undefined {
  return ontology.objectTypes.find(ot => ot.id === id);
}

/**
 * 执行单个规则，返回产生的 effect 和新实例/更新
 */
function executeRule(
  rule: ActionRule,
  action: Action,
  params: Record<string, unknown>,
  sourceInstance: ObjectInstance | undefined,
  ontology: Ontology,
  createdInstances: Map<string, ObjectInstance>
): {
  effect?: ActionEffect;
  newInstance?: ObjectInstance;
  updatedInstance?: ObjectInstance;
  newLink?: { linkTypeId: string; sourceId: string; targetId: string };
  deletedLinkIds?: string[];
  notification?: { message: string };
  error?: string;
} {
  if (!rule.enabled) return {};

  const cfg = rule.config;

  switch (rule.type) {
    case 'create_object': {
      if (cfg.type !== 'create_object') break;
      const ot = getObjectType(ontology, cfg.targetObjectTypeId);
      if (!ot) return { error: `目标对象类型不存在: ${cfg.targetObjectTypeId}` };

      const props: Record<string, unknown> = {};
      // 填充主键（自动生成）
      const pkProp = ot.properties.find(p => p.id === ot.primaryKey);
      const pkValue = genInstanceId();
      if (pkProp) props[pkProp.name] = pkValue;

      // 应用属性映射
      for (const m of cfg.propertyMappings) {
        props[m.targetProperty] = resolveMappingValue(m, params, sourceInstance?.properties, ontology, createdInstances);
      }

      const newInstance: ObjectInstance = {
        id: pkValue,
        objectTypeId: cfg.targetObjectTypeId,
        properties: props,
        createdAt: now(),
        updatedAt: now(),
      };

      // 计算派生属性
      const otForCompute = ot;
      for (const p of otForCompute.properties) {
        if (p.source === 'computed' && p.functionId) {
          const fn = ontology.functions.find(f => f.id === p.functionId);
          if (fn) {
            const r = executeFunction(fn, ontology, { object: props });
            if (r.success) props[p.name] = r.data;
          }
        }
      }

      return {
        newInstance,
        effect: {
          type: 'create_object',
          description: `创建新的 ${ot.displayName} (${pkValue})`,
          targetObjectTypeId: cfg.targetObjectTypeId,
          targetInstanceId: pkValue,
        },
      };
    }

    case 'update_property': {
      if (cfg.type !== 'update_property' || !sourceInstance) {
        return { error: 'update_property 规则需要目标实例' };
      }
      const ot = getObjectType(ontology, action.objectTypeId);
      if (!ot) return { error: `对象类型不存在: ${action.objectTypeId}` };

      // 解析新值
      let newValue: unknown;
      if (cfg.valueSource === 'parameter') {
        newValue = params[cfg.value];
      } else if (cfg.valueSource === 'constant') {
        try { newValue = JSON.parse(cfg.value); } catch { newValue = cfg.value; }
      } else if (cfg.valueSource === 'function' && cfg.functionId) {
        const fn = ontology.functions.find(f => f.id === cfg.functionId);
        if (fn) {
          const r = executeFunction(fn, ontology, { object: sourceInstance.properties, params });
          newValue = r.success ? r.data : undefined;
        }
      } else if (cfg.valueSource === 'expression') {
        try {
          // 简单表达式：直接在沙箱里算

          const fn = new Function('object', 'params', `"use strict"; return (${cfg.value});`);
          newValue = fn(sourceInstance.properties, params);
        } catch (e) {
          return { error: `表达式计算失败: ${e instanceof Error ? e.message : String(e)}` };
        }
      }

      const oldValue = sourceInstance.properties[cfg.targetProperty];
      const updatedInstance: ObjectInstance = {
        ...sourceInstance,
        properties: { ...sourceInstance.properties, [cfg.targetProperty]: newValue },
        updatedAt: now(),
      };

      return {
        updatedInstance,
        effect: {
          type: 'update_property',
          description: `更新 ${cfg.targetProperty}: ${JSON.stringify(oldValue)} → ${JSON.stringify(newValue)}`,
          targetObjectTypeId: action.objectTypeId,
          targetInstanceId: sourceInstance.id,
          property: cfg.targetProperty,
          oldValue,
          newValue,
        },
      };
    }

    case 'create_link': {
      if (cfg.type !== 'create_link') break;
      if (!sourceInstance) return { error: 'create_link 规则需要源实例' };

      let targetId: string | undefined;
      switch (cfg.targetSource) {
        case 'parameter':
          targetId = String(params[cfg.targetValue] ?? '');
          break;
        case 'created_object':
          // 引用前面规则创建的对象
          // cfg.targetValue 是 targetObjectTypeId，找到第一个创建的该类型实例
          for (const inst of createdInstances.values()) {
            if (inst.objectTypeId === cfg.targetValue) { targetId = inst.id; break; }
          }
          break;
        case 'source':
          targetId = sourceInstance.id;
          break;
        case 'expression':
          targetId = String(params[cfg.targetValue] ?? '');
          break;
      }

      if (!targetId) return { error: `create_link 无法解析目标对象: ${cfg.targetValue}` };

      // 查找 linkType
      const lt = ontology.linkTypes.find(l => l.id === cfg.linkTypeId);
      if (!lt) return { error: `实体关系不存在: ${cfg.linkTypeId}` };

      return {
        newLink: { linkTypeId: cfg.linkTypeId, sourceId: sourceInstance.id, targetId },
        effect: {
          type: 'create_link',
          description: `创建实体关系 ${lt.displayName}: ${sourceInstance.id} → ${targetId}`,
          linkTypeId: cfg.linkTypeId,
          targetInstanceId: targetId,
        },
      };
    }

    case 'delete_link': {
      if (cfg.type !== 'delete_link') break;
      if (!sourceInstance) return { error: 'delete_link 规则需要目标实例' };
      // 真正解析要删的链接：该链接类型下、以当前实例为源的全部链接实例
      const doomed = ontology.linkInstances
        .filter(li => li.linkTypeId === cfg.linkTypeId && li.sourceObjectId === sourceInstance.id)
        .map(li => li.id);
      const lt = ontology.linkTypes.find(l => l.id === cfg.linkTypeId);
      return {
        deletedLinkIds: doomed,
        effect: {
          type: 'delete_link',
          description: `删除链接 ${lt?.displayName || cfg.linkTypeId} × ${doomed.length}`,
          linkTypeId: cfg.linkTypeId,
        },
      };
    }

    case 'notification': {
      if (cfg.type !== 'notification') break;
      // 解析模板：简单替换 {{params.xxx}} 和 {{object.xxx}}
      let msg = cfg.messageTemplate;
      msg = msg.replace(/\{\{params\.(\w+)\}\}/g, (_, k) => String(params[k] ?? ''));
      if (sourceInstance) {
        msg = msg.replace(/\{\{object\.(\w+)\}\}/g, (_, k) => String(sourceInstance.properties[k] ?? ''));
      }
      return {
        notification: { message: msg },
        effect: {
          type: 'notification',
          description: `发送通知(${cfg.channel}): ${msg}`,
        },
      };
    }

    case 'webhook': {
      // 浏览器的 dry-run 只预览效果；真实执行会由后端安全投递器完成。
      const whCfg = cfg as WebhookConfig;
      return {
        effect: {
          type: 'webhook',
          description: `[模拟] 调用 Webhook: ${whCfg.method} ${whCfg.url}`,
        },
      };
    }

    case 'validation': {
      // validation 已在校验阶段处理，执行阶段跳过
      return {};
    }
  }

  return {};
}

/**
 * 执行动作（支持 dry-run 模拟）
 */
export function executeAction(
  ontology: Ontology,
  input: ActionExecuteInput
): ActionExecuteResult {
  const { actionId, parameters, targetInstanceId, dryRun } = input;
  const start = Date.now();
  const logId = genId();

  const action = ontology.actions.find(a => a.id === actionId);
  if (!action) {
    return {
      success: false,
      log: {
        id: logId, actionId, actionName: '(unknown)',
        objectTypeId: '', parameters, status: 'failed',
        errorMessage: `动作不存在: ${actionId}`,
        executedAt: now(), durationMs: Date.now() - start,
      },
    };
  }

  // 1. 找到目标实例
  const targetInstance = targetInstanceId
    ? ontology.instances.find(i => i.id === targetInstanceId && i.objectTypeId === action.objectTypeId)
    : undefined;

  // 2. 校验
  const validation = executeActionValidation(actionId, parameters, ontology, targetInstance?.properties);

  if (!validation.valid) {
    return {
      success: false,
      log: {
        id: logId, actionId, actionName: action.displayName,
        objectTypeId: action.objectTypeId, objectInstanceId: targetInstanceId,
        parameters, status: 'failed',
        validationErrors: validation.errors,
        effects: [],
        executedAt: now(), durationMs: Date.now() - start,
      },
    };
  }

  // 3. 按顺序执行规则
  const effects: ActionEffect[] = [];
  const allNewInstances: ObjectInstance[] = [];
  const allUpdatedInstances: ObjectInstance[] = [];
  const newLinks: { linkTypeId: string; sourceId: string; targetId: string }[] = [];
  const deletedLinkIds: string[] = [];
  const notifications: string[] = [];
  const createdInstances = new Map<string, ObjectInstance>();
  let currentInstance = targetInstance;

  const rules = [...(action.rules || [])].sort((a, b) => a.order - b.order);
  for (const rule of rules) {
    const result = executeRule(rule, action, parameters, currentInstance, ontology, createdInstances);
    if (result.error) {
      return {
        success: false,
        log: {
          id: logId, actionId, actionName: action.displayName,
          objectTypeId: action.objectTypeId, objectInstanceId: targetInstanceId,
          parameters, status: 'failed',
          effects, errorMessage: `规则 "${rule.name}" 执行失败: ${result.error}`,
          executedAt: now(), durationMs: Date.now() - start,
        },
      };
    }
    // effect 只在 executeRule 内生成一次，此处不再重复 push（曾导致 create_link 双计数）
    if (result.effect) effects.push(result.effect);
    if (result.newInstance) {
      allNewInstances.push(result.newInstance);
      createdInstances.set(result.newInstance.objectTypeId, result.newInstance);
    }
    if (result.updatedInstance) {
      allUpdatedInstances.push(result.updatedInstance);
      currentInstance = result.updatedInstance; // 后续规则基于更新后的实例
    }
    if (result.newLink) newLinks.push(result.newLink);
    if (result.deletedLinkIds?.length) deletedLinkIds.push(...result.deletedLinkIds);
    if (result.notification) notifications.push(result.notification.message);
  }

  const status: ActionExecutionStatus = dryRun ? 'validated' : 'success';

  const log: ActionExecutionLog = {
    id: logId,
    actionId,
    actionName: action.displayName,
    objectTypeId: action.objectTypeId,
    objectInstanceId: targetInstanceId,
    parameters,
    status,
    validationErrors: validation.warnings?.length ? [] : undefined,
    effects,
    executedAt: now(),
    durationMs: Date.now() - start,
  };

  // dry-run 不修改数据
  if (dryRun) {
    return { success: true, log };
  }

  return {
    success: true,
    log,
    newInstances: allNewInstances,
    updatedInstances: allUpdatedInstances,
    newLinks,
    deletedLinkIds,
    notifications,
  };
}

/**
 * 预填参数默认值
 */
export function getDefaultParameters(action: Action): Record<string, unknown> {
  const defaults: Record<string, unknown> = {};
  for (const p of action.parameters) {
    if (p.defaultValue !== undefined) {
      defaults[p.name] = p.defaultValue;
    } else if (p.type === 'boolean') {
      defaults[p.name] = false;
    } else if (p.type === 'number') {
      defaults[p.name] = 0;
    } else {
      defaults[p.name] = '';
    }
  }
  return defaults;
}
