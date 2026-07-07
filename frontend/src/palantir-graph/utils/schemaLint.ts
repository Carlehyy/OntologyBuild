/**
 * Schema Lint — 本体模型静态校验
 *
 * 在编辑期把建模问题暴露出来，而不是等运行时才炸：
 *   - 重名（对象/关系/动作/函数、同类型内属性重名 —— 后端保存时会静默去重，这里前置提醒）
 *   - 主键缺失 / 主键指向不存在的属性
 *   - 悬挂引用（关系端点、动作绑定、函数绑定、computed 属性的函数）
 *   - 基数非法值
 *
 * 纯函数，无副作用；Header 的问题徽标与问题列表都基于它。
 */
import type { Ontology, ObjectType } from '../types/ontology';

export type LintSeverity = 'error' | 'warning';

export type LintTargetKind = 'objectType' | 'linkType' | 'action' | 'function';

export interface LintIssue {
  id: string;
  severity: LintSeverity;
  message: string;
  targetKind: LintTargetKind;
  targetId: string;
  targetLabel: string;
}

const VALID_CARDINALITIES = new Set(['one-to-one', 'one-to-many', 'many-to-one', 'many-to-many']);

function findDuplicates<T>(items: T[], key: (t: T) => string): Map<string, T[]> {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const k = key(item);
    if (!k) continue;
    const arr = groups.get(k) || [];
    arr.push(item);
    groups.set(k, arr);
  }
  for (const [k, arr] of groups) {
    if (arr.length < 2) groups.delete(k);
  }
  return groups;
}

function primaryKeyMatches(ot: ObjectType): boolean {
  if (!ot.primaryKey) return false;
  return ot.properties.some((p) => p.id === ot.primaryKey || p.name === ot.primaryKey);
}

export function lintOntology(ontology: Ontology | null): LintIssue[] {
  if (!ontology) return [];
  const issues: LintIssue[] = [];
  let seq = 0;
  const push = (severity: LintSeverity, targetKind: LintTargetKind, targetId: string, targetLabel: string, message: string) => {
    issues.push({ id: `lint_${seq++}`, severity, targetKind, targetId, targetLabel, message });
  };

  const objectTypeIds = new Set(ontology.objectTypes.map((o) => o.id));
  const functionById = new Map(ontology.functions.map((f) => [f.id, f]));
  const actionIds = new Set(ontology.actions.map((a) => a.id));
  const linkTypeIds = new Set(ontology.linkTypes.map((l) => l.id));

  // —— 重名 ——
  for (const [name, items] of findDuplicates(ontology.objectTypes, (o) => o.name)) {
    for (const o of items) push('error', 'objectType', o.id, o.displayName || o.name, `对象类型名称 "${name}" 重复（共 ${items.length} 个）`);
  }
  for (const [name, items] of findDuplicates(ontology.linkTypes, (l) => l.name)) {
    for (const l of items) push('error', 'linkType', l.id, l.displayName || l.name, `关系类型名称 "${name}" 重复（共 ${items.length} 个）`);
  }
  for (const [name, items] of findDuplicates(ontology.actions, (a) => a.name)) {
    for (const a of items) push('error', 'action', a.id, a.displayName || a.name, `动作名称 "${name}" 重复（共 ${items.length} 个）`);
  }
  for (const [name, items] of findDuplicates(ontology.functions, (f) => f.name)) {
    for (const f of items) push('error', 'function', f.id, f.displayName || f.name, `函数名称 "${name}" 重复（共 ${items.length} 个）`);
  }

  // —— 对象类型 ——
  for (const ot of ontology.objectTypes) {
    const label = ot.displayName || ot.name;
    if (ot.properties.length === 0) {
      push('warning', 'objectType', ot.id, label, '没有任何属性');
    } else if (!ot.primaryKey) {
      push('warning', 'objectType', ot.id, label, '未设置主键');
    } else if (!primaryKeyMatches(ot)) {
      push('error', 'objectType', ot.id, label, `主键 "${ot.primaryKey}" 不在属性列表中`);
    }
    for (const [pname, props] of findDuplicates(ot.properties, (p) => p.name)) {
      void props;
      push('error', 'objectType', ot.id, label, `属性名 "${pname}" 重复（保存时后端会静默去重，仅保留最后一个）`);
    }
    for (const p of ot.properties) {
      const isComputed = p.source === 'computed' || p.computed;
      if (!isComputed) continue;
      if (!p.functionId) {
        push('error', 'objectType', ot.id, label, `计算属性 "${p.name}" 未绑定函数`);
      } else {
        const fn = functionById.get(p.functionId);
        if (!fn) {
          push('error', 'objectType', ot.id, label, `计算属性 "${p.name}" 绑定的函数已不存在`);
        } else if (!fn.enabled) {
          push('warning', 'objectType', ot.id, label, `计算属性 "${p.name}" 绑定的函数 "${fn.displayName || fn.name}" 已禁用`);
        }
      }
    }
  }

  // —— 关系类型 ——
  for (const lt of ontology.linkTypes) {
    const label = lt.displayName || lt.name;
    if (!objectTypeIds.has(lt.sourceObjectTypeId)) {
      push('error', 'linkType', lt.id, label, '源对象类型已不存在（悬挂引用）');
    }
    if (!objectTypeIds.has(lt.targetObjectTypeId)) {
      push('error', 'linkType', lt.id, label, '目标对象类型已不存在（悬挂引用）');
    }
    if (!VALID_CARDINALITIES.has(lt.cardinality)) {
      push('error', 'linkType', lt.id, label, `基数 "${lt.cardinality}" 非法`);
    }
  }

  // —— 动作 ——
  for (const a of ontology.actions) {
    const label = a.displayName || a.name;
    if (a.objectTypeId && !objectTypeIds.has(a.objectTypeId)) {
      push('error', 'action', a.id, label, '绑定的对象类型已不存在');
    }
    if (a.validationFunctionId && !functionById.has(a.validationFunctionId)) {
      push('error', 'action', a.id, label, '绑定的校验函数已不存在');
    }
    for (const rule of a.rules || []) {
      if (!rule.enabled) continue;
      const cfg = rule.config as unknown as Record<string, unknown>;
      const ruleName = rule.name || rule.type;
      if (typeof cfg.targetObjectTypeId === 'string' && cfg.targetObjectTypeId && !objectTypeIds.has(cfg.targetObjectTypeId)) {
        push('warning', 'action', a.id, label, `规则 "${ruleName}" 引用的对象类型已不存在`);
      }
      if (typeof cfg.linkTypeId === 'string' && cfg.linkTypeId && !linkTypeIds.has(cfg.linkTypeId)) {
        push('warning', 'action', a.id, label, `规则 "${ruleName}" 引用的关系类型已不存在`);
      }
      if (typeof cfg.functionId === 'string' && cfg.functionId && !functionById.has(cfg.functionId)) {
        push('warning', 'action', a.id, label, `规则 "${ruleName}" 引用的函数已不存在`);
      }
    }
  }

  // —— 函数 ——
  for (const f of ontology.functions) {
    const label = f.displayName || f.name;
    if (f.targetObjectTypeId && !objectTypeIds.has(f.targetObjectTypeId)) {
      push('warning', 'function', f.id, label, '绑定的对象类型已不存在');
    }
    if (f.functionType === 'action_validation' && f.targetActionId && !actionIds.has(f.targetActionId)) {
      push('warning', 'function', f.id, label, '绑定的动作已不存在');
    }
    if (!f.body.trim() && f.enabled) {
      push('warning', 'function', f.id, label, '函数体为空');
    }
  }

  // error 优先展示
  return issues.sort((x, y) => (x.severity === y.severity ? 0 : x.severity === 'error' ? -1 : 1));
}

export function lintSummary(issues: LintIssue[]): { errors: number; warnings: number } {
  let errors = 0, warnings = 0;
  for (const i of issues) (i.severity === 'error' ? errors++ : warnings++);
  return { errors, warnings };
}
