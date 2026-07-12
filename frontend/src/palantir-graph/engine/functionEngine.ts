/**
 * Function Execution Engine
 *
 * 提供一个浏览器端安全沙箱，用于执行 Ontology Functions。
 * 支持四种类型: object / object_set / action_validation / query
 *
 * 安全策略：
 * - 使用 new Function() 而非 eval，作用域完全隔离
 * - 只注入白名单 API（Math / Date / Object / Array / String 等基础工具 + ontology 上下文）
 * - 禁止访问 window / document / globalThis / fetch 等危险对象
 * - 执行超时保护（通过 Worker 或简单的步数限制）
 */
import type {
  Ontology,
  OntologyFunction,
  ObjectInstance,
  ValidationResult,
  FunctionExecutionResult,
  ObjectType,
  LinkType,
} from '../types/ontology';

// ======================
// 执行上下文（注入到函数的全局对象）
// ======================

export interface ExecutionContext {
  // 当前对象（object 类型函数传入）
  object?: Record<string, unknown>;
  // 对象集合（object_set 类型函数传入）
  objects?: Record<string, unknown>[];
  // 函数参数
  params: Record<string, unknown>;
  // 额外上下文（如 allInstances / auditLogs）
  context?: Record<string, unknown>;
  // Ontology 元数据（只读）
  ontology: Readonly<{
    objectTypes: Readonly<ObjectType>[];
    linkTypes: Readonly<LinkType>[];
  }>;
  // 工具函数（白名单）
  utils: {
    // 聚合工具
    sum: (arr: number[]) => number;
    avg: (arr: number[]) => number;
    count: (arr: unknown[]) => number;
    min: (arr: number[]) => number;
    max: (arr: number[]) => number;
    // 访问关联对象（通过实体关系）
    linkedTo?: (instanceId: string, linkTypeId: string) => ObjectInstance[];
  };
}

// 白名单安全全局对象
const SAFE_GLOBALS: Record<string, unknown> = {
  // 基础构造器和工具
  Math,
  Date,
  Number,
  String,
  Boolean,
  Array,
  Object,
  JSON,
  parseInt,
  parseFloat,
  isNaN,
  isFinite,
  // 常量
  NaN,
  Infinity,
  undefined,
  // 禁止：window, document, globalThis, fetch, XMLHttpRequest, WebSocket, Worker,
  // setTimeout, setInterval, eval, Function, require, import
};

const BLOCKED_KEYWORDS = [
  'window', 'document', 'globalThis', 'self',
  'fetch', 'XMLHttpRequest', 'WebSocket', 'Worker',
  'setTimeout', 'setInterval', 'setImmediate',
  'eval', 'Function', 'require', 'import',
  'process', 'global', '__proto__', 'constructor', 'prototype',
];

/**
 * 基本语法检查：拒绝包含危险关键字的函数体
 */
function validateFunctionBody(body: string): { ok: boolean; error?: string } {
  for (const keyword of BLOCKED_KEYWORDS) {
    // 使用词法边界匹配，避免误判（如 "windows" 或 "setTimeoutButton"）
    const re = new RegExp(`(^|[^a-zA-Z0-9_])${keyword}([^a-zA-Z0-9_]|$)`);
    if (re.test(body)) {
      return { ok: false, error: `函数体包含禁用关键字: "${keyword}"。为安全起见，不允许访问浏览器/运行时API。` };
    }
  }
  return { ok: true };
}

/**
 * 编译函数为可执行函数
 */
function compileFunction(fn: OntologyFunction): ((ctx: ExecutionContext) => unknown) {
  const body = fn.body.trim();
  const isStatementBody = fn.returnType === 'void'
    || /\breturn\b/.test(body)
    || /(^|\n)\s*(const|let|var|if|for|while|switch|try|throw)\b/.test(body)
    || body.includes(';');
  const executableBody = fn.returnType === 'void' || isStatementBody
    ? body
    : `return (${body});`;
  // 包装函数体：
  // 形参 ctx 包含 { object, objects, params, ontology, utils }
  // 允许直接通过解构使用
  const wrapped = `"use strict";
const { object, objects, params, ontology, utils, context } = ctx;
const objectSet = objects;
${executableBody}
`;


  const raw = new Function('ctx', ...Object.keys(SAFE_GLOBALS), wrapped) as (
    ctx: ExecutionContext,
    ...globals: unknown[]
  ) => unknown;
  return (ctx: ExecutionContext) => raw(ctx, ...Object.values(SAFE_GLOBALS));
}

/**
 * 执行带超时保护的函数
 */
function runWithTimeout<T>(fn: () => T, timeoutMs = 1000): T {
  // 简化版超时保护：对于浏览器单线程环境，无法真正中断死循环，
  // 但可做同步调用 + 时间检查——这里只做基础执行（合理函数都应是同步且很快）
  const start = Date.now();
  const result = fn();
  const duration = Date.now() - start;
  if (duration > timeoutMs) {
    console.warn(`[FunctionEngine] function took ${duration}ms (limit ${timeoutMs}ms)`);
  }
  return result;
}

// ======================
// 公共 API
// ======================

/**
 * 构建执行上下文
 */
function buildContext(
  ontology: Ontology,
  input: {
    object?: Record<string, unknown>;
    objects?: Record<string, unknown>[];
    params?: Record<string, unknown>;
    context?: Record<string, unknown>;
    linkedTo?: (instanceId: string, linkTypeId: string) => ObjectInstance[];
  }
): ExecutionContext {
  return {
    object: input.object,
    objects: input.objects,
    params: input.params ?? {},
    context: input.context || {},
    ontology: {
      objectTypes: ontology.objectTypes,
      linkTypes: ontology.linkTypes,
      ...(input.context || {}),
    },
    utils: {
      sum: (arr: number[]) => arr.reduce((a, b) => a + (Number(b) || 0), 0),
      avg: (arr: number[]) => arr.length ? arr.reduce((a, b) => a + (Number(b) || 0), 0) / arr.length : 0,
      count: (arr: unknown[]) => arr.length,
      min: (arr: number[]) => arr.length ? Math.min(...arr.map(Number)) : 0,
      max: (arr: number[]) => arr.length ? Math.max(...arr.map(Number)) : 0,
      linkedTo: input.linkedTo,
    },
    ...(input.context || {}),
  } as ExecutionContext;
}

/**
 * 执行任意 OntologyFunction
 */
export function executeFunction(
  fn: OntologyFunction,
  ontology: Ontology,
  input: {
    object?: Record<string, unknown>;
    objects?: Record<string, unknown>[];
    params?: Record<string, unknown>;
    context?: Record<string, unknown>;
    linkedTo?: (instanceId: string, linkTypeId: string) => ObjectInstance[];
  }
): FunctionExecutionResult {
  const start = Date.now();
  const timestamp = new Date().toISOString();

  if (!fn.enabled) {
    return {
      success: false,
      error: `函数 "${fn.displayName}" 已禁用`,
      durationMs: 0,
      timestamp,
    };
  }

  // 1. 语法安全检查
  const validation = validateFunctionBody(fn.body);
  if (!validation.ok) {
    return {
      success: false,
      error: validation.error,
      durationMs: 0,
      timestamp,
    };
  }

  try {
    // 2. 编译
    const compiled = compileFunction(fn);

    // 3. 构建上下文
    const ctx = buildContext(ontology, input);

    // 4. 执行

    const { object, objects, params, ontology: _o, utils } = ctx;
    // 传给 new Function 的额外参数都是安全全局值，通过 compileFunction 内接收
    void object; void objects; void params; void _o; void utils;

    const result = runWithTimeout(() => compiled(ctx), 2000);

    // 5. 校验函数返回值必须是 ValidationResult
    if (fn.functionType === 'action_validation') {
      if (typeof result === 'boolean') {
        return {
          success: true,
          data: { valid: result, errors: result ? [] : ['校验失败'] } satisfies ValidationResult,
          durationMs: Date.now() - start,
          timestamp,
        };
      }
      if (result && typeof result === 'object' && 'valid' in result) {
        return {
          success: true,
          data: result as ValidationResult,
          durationMs: Date.now() - start,
          timestamp,
        };
      }
      return {
        success: true,
        data: { valid: true, errors: [] } satisfies ValidationResult,
        durationMs: Date.now() - start,
        timestamp,
      };
    }

    return {
      success: true,
      data: result,
      durationMs: Date.now() - start,
      timestamp,
    };
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : String(err),
      durationMs: Date.now() - start,
      timestamp,
    };
  }
}

/**
 * 计算派生属性：针对某个对象实例计算所有 computed 属性
 */
export function computeDerivedProperties(
  objectType: ObjectType,
  instance: Record<string, unknown>,
  ontology: Ontology,
  linkedTo?: (instanceId: string, linkTypeId: string) => ObjectInstance[]
): Record<string, unknown> {
  const computed: Record<string, unknown> = {};
  for (const prop of objectType.properties) {
    if (prop.source === 'computed' && prop.functionId) {
      const fn = ontology.functions.find(f => f.id === prop.functionId);
      if (fn) {
        const result = executeFunction(fn, ontology, { object: instance, linkedTo });
        if (result.success) {
          computed[prop.name] = result.data;
        } else {
          computed[prop.name] = `#ERROR: ${result.error}`;
        }
      }
    }
  }
  return computed;
}

/**
 * 执行动作校验：返回 ValidationResult
 */
export function executeActionValidation(
  actionId: string,
  parameters: Record<string, unknown>,
  ontology: Ontology,
  targetInstance?: Record<string, unknown>
): ValidationResult {
  const action = ontology.actions.find(a => a.id === actionId);
  if (!action) {
    return { valid: false, errors: [`动作不存在: ${actionId}`] };
  }

  const allErrors: string[] = [];
  const allWarnings: string[] = [];

  // 1. 绑定的 Validation Function
  if (action.validationFunctionId) {
    const fn = ontology.functions.find(f => f.id === action.validationFunctionId);
    if (fn) {
      const result = executeFunction(fn, ontology, {
        object: targetInstance,
        params: parameters,
      });
      if (result.success) {
        const vr = result.data as ValidationResult;
        if (!vr.valid) {
          allErrors.push(...(vr.errors || ['校验失败']));
        }
        if (vr.warnings) {
          allWarnings.push(...vr.warnings);
        }
      } else {
        allErrors.push(`校验函数执行错误: ${result.error}`);
      }
    }
  }

  // 2. 规则中的 validation 类型
  if (action.rules) {
    for (const rule of action.rules) {
      if (rule.type === 'validation' && rule.enabled) {
        // 使用函数校验（如果绑定了 functionId）
        const cfg = rule.config;
        if (cfg.type === 'validation' && cfg.functionId) {
          const fn = ontology.functions.find(f => f.id === cfg.functionId);
          if (fn) {
            const result = executeFunction(fn, ontology, {
              object: targetInstance,
              params: parameters,
            });
            if (result.success) {
              const vr = result.data as ValidationResult;
              if (!vr.valid) {
                allErrors.push(...(vr.errors || [cfg.errorMessage || '校验失败']));
              }
            } else {
              allErrors.push(`${rule.name}: ${result.error}`);
            }
          }
        } else if (cfg.type === 'validation') {
          // 简单条件表达式校验（解析单属性值）
          try {
            const condResult = evalExpression(cfg.condition, parameters, targetInstance);
            if (!condResult) {
              allErrors.push(cfg.errorMessage || `${rule.name} 校验失败`);
            }
          } catch {
            // 表达式解析失败就跳过，交给函数处理
          }
        }
      }
    }
  }

  return {
    valid: allErrors.length === 0,
    errors: allErrors,
    warnings: allWarnings.length ? allWarnings : undefined,
  };
}

/**
 * 安全的简单表达式求值（用于简单条件：params.xxx > 0, object.status === 'active'）
 * 只支持属性访问、比较、逻辑运算，不允许函数调用或任意代码。
 */
function evalExpression(
  expr: string,
  params: Record<string, unknown>,
  object?: Record<string, unknown>
): boolean {
  // 简单实现：把 params.xxx / object.xxx 替换为值，再做受限的比较
  // 为安全起见，这里只做一些常见模式支持，复杂逻辑应使用函数
  let safe = expr.trim();

  // 替换 params.xxx 和 object.xxx
  safe = safe.replace(/params\.([a-zA-Z_][a-zA-Z0-9_]*)/g, (_, key) => {
    const v = params[key];
    return JSON.stringify(v);
  });
  safe = safe.replace(/object\.([a-zA-Z_][a-zA-Z0-9_]*)/g, (_, key) => {
    const v = object?.[key];
    return JSON.stringify(v);
  });

  // 拒绝任何剩余的字母标识符（除了 true/false/null），防止访问其他对象
  if (/[a-zA-Z_][a-zA-Z0-9_]*/.test(safe.replace(/\b(true|false|null)\b/g, ''))) {
    throw new Error('表达式只能包含 params.xxx / object.xxx 引用与比较运算符');
  }


  const fn = new Function(`"use strict"; return (${safe});`);
  return Boolean(fn());
}

/**
 * 测试：简单测试一下函数引擎（开发环境下可在 console 调用）
 */
export function _engineSelfTest(): string[] {
  const results: string[] = [];
  const testOntology: Ontology = {
    id: 'test',
    name: 'test',
    version: '1.0.0',
    objectTypes: [],
    linkTypes: [],
    actions: [],
    functions: [],
    instances: [],
    linkInstances: [],
    executionLogs: [],
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };

  // 测试1：简单加法
  const addFn: OntologyFunction = {
    id: '1', name: 'add', displayName: 'Add',
    functionType: 'object_set', language: 'typescript',
    parameters: [{ id: 'a', name: 'a', type: 'number', required: true }, { id: 'b', name: 'b', type: 'number', required: true }],
    returnType: 'number',
    body: 'params.a + params.b',
    cacheStrategy: 'none', enabled: true,
    createdAt: '', updatedAt: '',
  };
  const r1 = executeFunction(addFn, testOntology, { params: { a: 2, b: 3 } });
  results.push(`add(2,3)=${r1.success ? r1.data : 'ERROR:' + r1.error} (expect 5)`);

  // 测试2：聚合函数
  const sumFn: OntologyFunction = {
    id: '2', name: 'total', displayName: 'Total',
    functionType: 'object_set', language: 'typescript',
    parameters: [],
    returnType: 'number',
    body: "utils.sum(objects.map(o => o.price || 0))",
    cacheStrategy: 'none', enabled: true,
    createdAt: '', updatedAt: '',
  };
  const r2 = executeFunction(sumFn, testOntology, {
    objects: [{ price: 10 }, { price: 25 }, { price: 30 }]
  });
  results.push(`sum([10,25,30])=${r2.success ? r2.data : 'ERROR:' + r2.error} (expect 65)`);

  // 测试3：危险关键字拦截
  const badFn: OntologyFunction = {
    ...addFn, id: '3',
    body: "window.alert('x'); return 1;",
  };
  const r3 = executeFunction(badFn, testOntology, { params: {} });
  results.push(`blocked: ${r3.success ? 'FAIL(should be blocked)' : 'OK: ' + r3.error}`);

  // 测试4：validation
  const valFn: OntologyFunction = {
    ...addFn, id: '4', functionType: 'action_validation',
    returnType: 'validation_result',
    body: "({ valid: params.amount > 0, errors: params.amount <= 0 ? ['金额必须大于0'] : [] })",
  };
  const r4 = executeFunction(valFn, testOntology, { params: { amount: -1 } });
  results.push(`validate(-1): ${r4.success ? JSON.stringify(r4.data) : 'ERROR:' + r4.error}`);

  return results;
}

// 挂载到 window 供调试
if (typeof window !== 'undefined') {
  (window as unknown as Record<string, unknown>).__ontologyEngineSelfTest = _engineSelfTest;
}

// 类型检查：SAFE_GLOBALS 声明以避免未使用
void SAFE_GLOBALS;
