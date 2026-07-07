"""
哨兵评估器 (Sentinel Evaluator) — 三入口共用的核心

对单个哨兵：解析跨对象绑定(别名 + 链接遍历 + 必要时笛卡尔) → 跨别名条件求值
→ 命中则对 primary 别名对象依次执行绑定的动作列表 → 落 SentinelFiring 日志。

断环：执行哨兵动作期间置上下文标记 in_sentinel_run，CDC 据此抑制由动作回写引发的
即时再触发(同线程内可见)；遗漏的后续条件由定期扫描兜底。
"""
from __future__ import annotations

import time
from contextvars import ContextVar
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.orm import Session


def _now():
    return datetime.now(timezone.utc)

from app.models.ontology_formal import ObjectInstance, LinkInstance
from app.models.sentinel import Sentinel, SentinelFiring, SentinelMatchState
from app.services.formal.action_engine import execute_action
from app.services.formal.safe_eval import safe_eval, SafeEvalError

# 执行哨兵动作期间为 True；CDC 用它抑制级联即时再触发(断环)。
in_sentinel_run: ContextVar[bool] = ContextVar("in_sentinel_run", default=False)

MAX_TUPLES = 1000  # 跨对象匹配元组上限，防组合爆炸


def _instances(db: Session, ontology_id: str, object_type_id: str):
    return db.query(ObjectInstance).filter(
        ObjectInstance.ontology_id == ontology_id,
        ObjectInstance.object_type_id == object_type_id,
    ).all()


def _passes(expr: str | None, alias: str, props: dict,
            errors: list[str] | None = None) -> bool:
    if not expr:
        return True
    try:
        return bool(safe_eval(expr, {alias: props, "obj": props}))
    except SafeEvalError as e:
        if errors is not None and len(errors) < 5:
            errors.append(f"绑定过滤「{expr}」求值失败: {e}")
        return False


def _traverse(db: Session, ontology_id: str, link_type_id: str,
              instance_id: str, forward: bool, target_type: str):
    q = db.query(LinkInstance).filter(
        LinkInstance.ontology_id == ontology_id,
        LinkInstance.link_type_id == link_type_id,
    )
    if forward:
        rows = q.filter(LinkInstance.source_object_id == instance_id).all()
        ids = [r.target_object_id for r in rows]
    else:
        rows = q.filter(LinkInstance.target_object_id == instance_id).all()
        ids = [r.source_object_id for r in rows]
    if not ids:
        return []
    return db.query(ObjectInstance).filter(
        ObjectInstance.id.in_(ids),
        ObjectInstance.object_type_id == target_type,
        ObjectInstance.ontology_id == ontology_id,
    ).all()


def _resolve_tuples(db: Session, ontology_id: str, sentinel: Sentinel,
                    errors: list[str] | None = None) -> list[dict]:
    """解析满足绑定 filter 与链接约束的对象元组列表。元组：{alias: ObjectInstance}"""
    bindings = sentinel.bindings or []
    if not bindings:
        return []
    links = sentinel.links or []

    b0 = bindings[0]
    tuples: list[dict] = []
    for inst in _instances(db, ontology_id, b0["objectTypeId"]):
        if _passes(b0.get("filter"), b0["alias"], inst.properties or {}, errors):
            tuples.append({b0["alias"]: inst})

    for b in bindings[1:]:
        alias, otype, filt = b["alias"], b["objectTypeId"], b.get("filter")
        bound_aliases = {a for t in tuples for a in t} if tuples else set()
        link = next((l for l in links
                     if (l.get("to") == alias and l.get("from") in bound_aliases)
                     or (l.get("from") == alias and l.get("to") in bound_aliases)), None)
        new_tuples: list[dict] = []
        if link:
            for t in tuples:
                if link.get("to") == alias and link.get("from") in t:
                    related = _traverse(db, ontology_id, link["linkTypeId"],
                                        t[link["from"]].id, forward=True, target_type=otype)
                elif link.get("from") == alias and link.get("to") in t:
                    related = _traverse(db, ontology_id, link["linkTypeId"],
                                        t[link["to"]].id, forward=False, target_type=otype)
                else:
                    related = []
                for r in related:
                    if _passes(filt, alias, r.properties or {}, errors):
                        nt = dict(t); nt[alias] = r; new_tuples.append(nt)
                        if len(new_tuples) >= MAX_TUPLES:
                            break
                if len(new_tuples) >= MAX_TUPLES:
                    break
        else:
            cands = [i for i in _instances(db, ontology_id, otype)
                     if _passes(filt, alias, i.properties or {}, errors)]
            for t in tuples:
                for r in cands:
                    nt = dict(t); nt[alias] = r; new_tuples.append(nt)
                    if len(new_tuples) >= MAX_TUPLES:
                        break
                if len(new_tuples) >= MAX_TUPLES:
                    break
        tuples = new_tuples
        if not tuples:
            break
    return tuples


def _holds(expr: str | None, tup: dict, errors: list[str] | None = None) -> bool:
    """条件求值。求值失败视为不命中（fail-closed），但错误必须被记录并
    展示到触发日志——写错的条件不能表现为"永远静默 no_match"。"""
    if not expr:
        return True
    scope = {alias: (inst.properties or {}) for alias, inst in tup.items()}
    try:
        return bool(safe_eval(expr, scope))
    except SafeEvalError as e:
        if errors is not None and len(errors) < 5:
            errors.append(f"条件「{expr}」求值失败: {e}")
        return False


def _match_key(tup: dict, primary: str | None) -> str:
    """命中键:跨对象时为整个元组的稳定签名,单对象时即 primary 实例 id。"""
    if len(tup) <= 1 and primary and primary in tup:
        return tup[primary].id
    return "|".join(f"{a}={tup[a].id}" for a in sorted(tup))


def _run_actions(db: Session, ontology_id: str, sentinel: Sentinel,
                 target_id: str | None, edge: str, results: list) -> None:
    for aid in (sentinel.action_ids or []):
        body = SimpleNamespace(action_id=aid, parameters={},
                               target_instance_id=target_id, dry_run=False)
        log = execute_action(db, ontology_id, body)
        results.append({
            "actionId": aid, "targetInstanceId": target_id, "edge": edge,
            "status": log.get("status"), "logId": log.get("id"),
            "effects": log.get("effects", []),
            "errorMessage": log.get("errorMessage"),
            "validationErrors": log.get("validationErrors", []),
        })


def evaluate_sentinel(db: Session, ontology_id: str, sentinel: Sentinel,
                      source: str) -> SentinelFiring:
    """边沿触发评估(参考 Foundry Automate):算当前命中集 → 与上次做差 →
    仅对"进入"(可选"离开")执行动作 → 更新命中状态。source ∈ manual|change|schedule

    整体异常防护：单个哨兵怎么坏（配置损坏/表达式炸/动作抛错）都不能让
    手动触发 500、扫描 tick 崩溃或 CDC 线程静默吞错——统一落 status=error 日志。"""
    start = time.time()
    try:
        return _evaluate_inner(db, ontology_id, sentinel, source, start)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        firing = SentinelFiring(
            ontology_id=ontology_id, sentinel_id=sentinel.id,
            sentinel_name=sentinel.display_name, trigger_source=source,
            matches=[], match_count=0, entered=[], left=[],
            action_results=[], status="error", error=str(e),
            duration_ms=int((time.time() - start) * 1000))
        db.add(firing); db.commit(); db.refresh(firing)
        return firing


def _evaluate_inner(db: Session, ontology_id: str, sentinel: Sentinel,
                    source: str, start: float) -> SentinelFiring:
    primary = sentinel.primary_alias or (sentinel.bindings[0]["alias"]
                                         if sentinel.bindings else None)
    mode = sentinel.trigger_mode or "on_enter"
    eval_errors: list[str] = []   # 表达式求值错误——fail-closed 但必须可见

    # 1) 当前命中集
    tuples = _resolve_tuples(db, ontology_id, sentinel, eval_errors)
    matched = [t for t in tuples if _holds(sentinel.condition, t, eval_errors)]
    # 命中键 → 元组(同键去重,保留首个)
    current: dict[str, dict] = {}
    for t in matched:
        k = _match_key(t, primary)
        current.setdefault(k, t)

    # 2) 上次命中集
    prior_rows = db.query(SentinelMatchState).filter(
        SentinelMatchState.sentinel_id == sentinel.id).all()
    prior_keys = {r.match_key for r in prior_rows}
    cur_keys = set(current.keys())

    entered = cur_keys - prior_keys          # 新进入
    left = prior_keys - cur_keys             # 离开

    # 3) 决定本次要执行动作的命中(按触发模式)
    if mode == "run_on_all":
        enter_targets = cur_keys             # 电平:每轮对全部当前命中执行
    else:
        enter_targets = entered              # 边沿:仅新进入
    leave_targets = left if mode == "on_enter_leave" else set()

    action_results: list[dict] = []
    now = _now()

    if not sentinel.muted:
        token = in_sentinel_run.set(True)    # 断环:动作回写不即时级联
        try:
            for k in enter_targets:
                tup = current.get(k)
                tid = tup.get(primary).id if (tup and tup.get(primary)) else None
                _run_actions(db, ontology_id, sentinel, tid, "enter", action_results)
            for k in leave_targets:
                detail = next((r.match_detail for r in prior_rows if r.match_key == k), {})
                tid = detail.get(primary) if detail else None
                _run_actions(db, ontology_id, sentinel, tid, "leave", action_results)
        finally:
            in_sentinel_run.reset(token)

        # 4) 更新命中状态:删除离开的,upsert 当前的。
        #    muted(影子模式)不更新——match_state 语义是"已执行过动作的命中"；
        #    否则解除静默后存量命中已被吸收，on_enter 永不触发。
        for r in prior_rows:
            if r.match_key in left:
                db.delete(r)
            elif r.match_key in cur_keys:
                r.last_seen_at = now
        for k in (cur_keys - prior_keys):
            tup = current[k]
            db.add(SentinelMatchState(
                ontology_id=ontology_id, sentinel_id=sentinel.id, match_key=k,
                match_detail={a: inst.id for a, inst in tup.items()},
                first_seen_at=now, last_seen_at=now))

    # 4.5) 缺席事实：查询结果为空/非空的状态翻转冻结进事实流（Negation-as-Failure 溯源）。
    #      表达式全在报错时跳过——那是 error 不是"确认为空"，不能伪造缺席证据。
    if not (eval_errors and not matched and tuples):
        try:
            from app.ontologies.formal_modeling.facts import record_absence_fact
            record_absence_fact(
                db, ontology_id=ontology_id, subject_id=sentinel.id,
                empty=(len(current) == 0), scanned=len(tuples),
                source=f"sentinel://{sentinel.name or sentinel.id}@{source}",
                detail={"condition": sentinel.condition or "",
                        "sentinelName": sentinel.display_name})
        except Exception:  # noqa: BLE001 — 取证失败不能影响评估主流程
            db.rollback()

    # 5) 记录触发日志
    if eval_errors and not matched and tuples:
        status = "error"       # 有候选但条件全在报错 → 明确暴露,而非伪装成 no_match
    elif sentinel.muted:
        status = "muted"
    elif enter_targets or leave_targets:
        status = "fired"
    elif matched:
        status = "no_change"   # 仍有命中但无新进入/离开 → 不重复触发
    else:
        status = "no_match"

    firing = SentinelFiring(
        ontology_id=ontology_id, sentinel_id=sentinel.id,
        sentinel_name=sentinel.display_name, trigger_source=source,
        matches=[{a: inst.id for a, inst in t.items()} for t in current.values()],
        match_count=len(current),
        entered=sorted(entered), left=sorted(left),
        action_results=action_results, status=status,
        error="; ".join(eval_errors) if eval_errors else None,
        duration_ms=int((time.time() - start) * 1000))
    db.add(firing); db.commit(); db.refresh(firing)
    return firing
