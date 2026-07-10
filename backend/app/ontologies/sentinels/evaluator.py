"""
哨兵评估器 (Sentinel Evaluator) — 三入口共用的核心

对单个哨兵：解析跨对象绑定(别名 + 链接遍历 + 必要时笛卡尔) → 跨别名条件求值
→ 命中则对 primary 别名对象依次执行绑定的动作列表 → 落 SentinelFiring 日志。

断环：执行哨兵动作期间置上下文标记 in_sentinel_run，CDC 据此抑制由动作回写引发的
即时再触发(同线程内可见)；遗漏的后续条件由定期扫描兜底。
"""
from __future__ import annotations

import logging
import hashlib
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _now():
    return datetime.now(timezone.utc)

from app.models.ontology_formal import ActionType, ObjectInstance, LinkInstance
from app.models.sentinel import Sentinel, SentinelFiring, SentinelMatchState
from app.services.formal.action_engine import execute_action
from app.services.formal.safe_eval import safe_eval, SafeEvalError

logger = logging.getLogger(__name__)

_ACTIVE_MATCH_STATUSES = {
    "completed", "processing_leave", "pending_leave", "failed_leave",
}

# 执行哨兵动作期间为 True；CDC 用它抑制级联即时再触发(断环)。
in_sentinel_run: ContextVar[bool] = ContextVar("in_sentinel_run", default=False)

MAX_TUPLES = 1000  # 跨对象匹配元组上限，防组合爆炸

_LOCKS_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[str, threading.RLock] = {}


def _local_lock(sentinel_id: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCAL_LOCKS.setdefault(sentinel_id, threading.RLock())


@contextmanager
def _sentinel_execution_lock(db: Session, sentinel_id: str):
    """Serialize one sentinel across threads and, on PostgreSQL, processes.

    PostgreSQL session advisory locks survive the commits performed by the
    action engine, unlike transaction locks.  The unique match-state claim is
    still the final backstop for other database dialects.
    """
    lock = _local_lock(sentinel_id)
    lock.acquire()
    advisory = False
    try:
        bind = db.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            db.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"),
                {"key": f"sentinel:{sentinel_id}"},
            )
            advisory = True
        yield
    finally:
        if advisory:
            try:
                db.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": f"sentinel:{sentinel_id}"},
                )
            except Exception:  # connection close also releases session locks
                logger.warning("释放哨兵 advisory lock 失败: %s", sentinel_id, exc_info=True)
        lock.release()


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


_PARAM_TEMPLATE = re.compile(
    r"^\{\{\s*(?P<alias>[A-Za-z_][A-Za-z0-9_]*|primary|target)\."
    r"(?P<property>[A-Za-z_][A-Za-z0-9_]*|id)\s*\}\}$"
)


def _binding_instance(tup: dict, alias: str | None, primary: str | None):
    resolved = primary if alias in (None, "", "primary", "target") else alias
    return tup.get(resolved) if resolved else None


def _resolve_parameter_binding(spec, tup: dict, primary: str | None):
    """Resolve a deliberately small, non-executable sentinel binding language."""
    if isinstance(spec, str):
        match = _PARAM_TEMPLATE.match(spec)
        if not match:
            return spec, None
        inst = _binding_instance(tup, match.group("alias"), primary)
        if inst is None:
            return None, f"参数绑定找不到别名 {match.group('alias')}"
        prop = match.group("property")
        if prop == "id":
            return inst.id, None
        props = inst.properties or {}
        if prop not in props:
            return None, f"参数绑定属性不存在: {prop}"
        return props[prop], None

    if not isinstance(spec, dict):
        return spec, None

    source = spec.get("sourceType", spec.get("source"))
    if isinstance(source, str):
        source = source.strip().lower().replace("-", "_")
    if source not in ("constant", "literal", "property", "match", "match_property",
                       "target_id", "primary_id"):
        # Plain dicts remain valid literal values for object parameters.
        return spec, None
    if source in ("constant", "literal"):
        if "value" in spec:
            return spec.get("value"), None
        return spec.get("sourceValue"), None
    inst = _binding_instance(tup, spec.get("alias"), primary)
    if inst is None:
        return None, f"参数绑定找不到别名 {spec.get('alias') or primary}"
    if source in ("target_id", "primary_id"):
        return inst.id, None
    prop = spec.get("property", spec.get("sourceValue"))
    if not prop:
        return None, "property/match 参数绑定缺少 property"
    if prop == "id":
        return inst.id, None
    props = inst.properties or {}
    if prop not in props:
        return None, f"参数绑定属性不存在: {prop}"
    return props[prop], None


def _configured_action_parameters(sentinel: Sentinel, action_id: str, tup: dict,
                                  primary: str | None) -> tuple[dict, list[str]]:
    all_config = sentinel.action_parameters or {}
    if not isinstance(all_config, dict):
        return {}, ["哨兵 actionParameters 顶层必须是对象"]
    configured = all_config.get(action_id, {})
    if configured is None:
        configured = {}
    if not isinstance(configured, dict):
        return {}, [f"动作 {action_id} 的 actionParameters 必须是对象"]
    params: dict = {}
    errors: list[str] = []
    for name, spec in configured.items():
        value, error = _resolve_parameter_binding(spec, tup, primary)
        if error:
            errors.append(f"参数「{name}」: {error}")
        else:
            params[str(name)] = value
    return params, errors


def _action_idempotency_key(sentinel: Sentinel, state: SentinelMatchState,
                            match_key: str, edge: str, action_id: str) -> str:
    """Stable for one edge lifecycle, distinct after leave/re-enter or run_on_all.

    The digest includes the explicit business tuple requested by the runtime
    contract plus the durable claim/epoch.  The latter is what lets a real
    leave→re-enter transition execute again without losing crash-retry safety.
    """
    material = "\x00".join([
        sentinel.id, match_key, edge, action_id, state.id,
        str(state.execution_epoch or 0),
    ])
    return f"sentinel:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def _run_actions(db: Session, ontology_id: str, sentinel: Sentinel,
                 tup: dict, primary: str | None, edge: str,
                 match_key: str, state: SentinelMatchState,
                 results: list) -> tuple[bool, str]:
    """Run one edge fail-fast; only all-success may consume match state."""
    action_ids = list(sentinel.action_ids or [])
    if not action_ids:
        return True, "no_actions"
    invalid_action_ids = [a for a in action_ids if not isinstance(a, str) or not a]
    if invalid_action_ids:
        invalid_action_id = invalid_action_ids[0]
        results.append({
            "actionId": str(invalid_action_id), "targetInstanceId": None,
            "edge": edge, "matchKey": match_key, "status": "failed",
            "errorMessage": "actionIds 中存在非法动作 ID", "effects": [],
            "validationErrors": ["invalid_action_id"],
        })
        return False, "failed"
    if len(action_ids) != len(set(action_ids)):
        results.append({
            "actionId": "", "targetInstanceId": None, "edge": edge,
            "matchKey": match_key, "status": "failed",
            "errorMessage": "actionIds 不允许重复；重复动作会破坏幂等语义",
            "effects": [], "validationErrors": ["duplicate_action_id"],
        })
        return False, "failed"
    target = _binding_instance(tup, primary, primary)
    target_id = target.id if target is not None else None
    entries: list[tuple[str, ActionType, SimpleNamespace]] = []
    for aid in action_ids:
        params, binding_errors = _configured_action_parameters(
            sentinel, aid, tup, primary)
        if binding_errors:
            results.append({
                "actionId": aid, "targetInstanceId": target_id, "edge": edge,
                "matchKey": match_key,
                "status": "failed", "errorMessage": "; ".join(binding_errors),
                "effects": [], "validationErrors": binding_errors,
            })
            return False, "failed"
        action = db.query(ActionType).filter(
            ActionType.id == aid, ActionType.ontology_id == ontology_id,
        ).first()
        if action is None:
            results.append({
                "actionId": aid, "targetInstanceId": target_id, "edge": edge,
                "matchKey": match_key, "status": "failed",
                "errorMessage": "动作不存在", "effects": [],
                "validationErrors": ["missing_action"],
            })
            return False, "failed"
        body = SimpleNamespace(
            action_id=aid, parameters=params,
            target_instance_id=target_id, dry_run=False,
            idempotency_key=_action_idempotency_key(
                sentinel, state, match_key, edge, aid),
            sentinel_match_state_id=state.id,
        )
        entries.append((aid, action, body))

    def _execute(aid: str, body: SimpleNamespace):
        try:
            return execute_action(db, ontology_id, body)
        except Exception as exc:  # noqa: BLE001
            logger.exception("哨兵动作执行抛出未封装异常: %s", aid)
            return {
                "status": "failed", "errorMessage": f"动作执行异常: {exc}",
                "effects": [], "validationErrors": [],
            }

    def _append(aid: str, log: dict) -> str:
        status = str(log.get("status") or "failed")
        results.append({
            "actionId": aid, "targetInstanceId": target_id, "edge": edge,
            "matchKey": match_key,
            "status": status, "logId": log.get("id"),
            "idempotentReplay": bool(log.get("idempotentReplay")),
            "effects": log.get("effects", []),
            "errorMessage": log.get("errorMessage"),
            "validationErrors": log.get("validationErrors", []),
        })
        return status

    # HITL is a gate for the *whole* chain.  Do not commit earlier automatic
    # effects and only then discover that a later step is pending/rejected.
    # Approved gates resolve through their durable idempotency owner and let the
    # normal pass continue; the first unresolved gate stops before side effects.
    for aid, action, body in entries:
        if not action.requires_approval:
            continue
        gate_log = _execute(aid, body)
        gate_status = str(gate_log.get("status") or "failed")
        if gate_status != "success":
            _append(aid, gate_log)
            return False, "pending" if gate_status == "pending" else "failed"

    for aid, _action, body in entries:
        status = _append(aid, _execute(aid, body))
        if status != "success":
            return False, "pending" if status == "pending" else "failed"
    return True, "success"


def _claim_match_state(db: Session, ontology_id: str, sentinel: Sentinel,
                       key: str, tup: dict, now: datetime,
                       *, new_cycle: bool = False) -> SentinelMatchState | None:
    """Claim/recover an enter edge; uniqueness arbitrates concurrent workers."""
    existing = db.query(SentinelMatchState).filter(
        SentinelMatchState.sentinel_id == sentinel.id,
        SentinelMatchState.match_key == key,
    ).first()
    if existing is not None:
        if new_cycle and existing.runtime_status == "completed":
            existing.execution_epoch = int(existing.execution_epoch or 0) + 1
        existing.runtime_status = "processing_enter"
        existing.match_detail = {a: inst.id for a, inst in tup.items()}
        existing.last_seen_at = now
        db.commit()
        db.refresh(existing)
        return existing

    state = SentinelMatchState(
        ontology_id=ontology_id, sentinel_id=sentinel.id, match_key=key,
        match_detail={a: inst.id for a, inst in tup.items()},
        runtime_status="processing_enter", execution_epoch=0,
        first_seen_at=now, last_seen_at=now)
    db.add(state)
    try:
        db.commit()
        db.refresh(state)
        return state
    except IntegrityError:
        db.rollback()
        winner = db.query(SentinelMatchState).filter(
            SentinelMatchState.sentinel_id == sentinel.id,
            SentinelMatchState.match_key == key,
        ).first()
        return winner


def _record_edge_outcome(db: Session, state: SentinelMatchState,
                         edge: str, outcome: str) -> None:
    if edge == "leave" and outcome in ("success", "no_actions"):
        db.delete(state)
        return
    if edge == "enter" and outcome in ("success", "no_actions"):
        state.runtime_status = "completed"
        return
    state.runtime_status = f"{'pending' if outcome == 'pending' else 'failed'}_{edge}"


def _tuple_from_detail(db: Session, ontology_id: str, detail: dict) -> dict:
    ids = [value for value in (detail or {}).values() if value]
    if not ids:
        return {}
    objects = {obj.id: obj for obj in db.query(ObjectInstance).filter(
        ObjectInstance.ontology_id == ontology_id,
        ObjectInstance.id.in_(ids)).all()}
    return {alias: objects[iid] for alias, iid in (detail or {}).items() if iid in objects}


def _claim_edge(state: SentinelMatchState) -> str:
    return "leave" if (state.runtime_status or "").endswith("_leave") else "enter"


def reject_sentinel_match_claim(db: Session, ontology_id: str,
                                state_id: str) -> dict:
    """Release the edge claim owned by a rejected HITL action."""
    state = db.query(SentinelMatchState).filter(
        SentinelMatchState.id == state_id,
        SentinelMatchState.ontology_id == ontology_id,
    ).first()
    if state is None:
        return {"status": "not_found"}
    edge = _claim_edge(state)
    match_key = state.match_key
    with _sentinel_execution_lock(db, state.sentinel_id):
        if edge == "leave":
            # Leave was not completed; retain the previously consumed enter so
            # the absent match can be proposed again with a fresh action key.
            state.runtime_status = "completed"
        else:
            db.delete(state)
        db.commit()
    return {"status": "released", "edge": edge, "matchKey": match_key}


def fail_sentinel_match_claim(db: Session, ontology_id: str,
                              state_id: str) -> dict:
    """Keep a failed edge recoverable without counting it as consumed."""
    state = db.query(SentinelMatchState).filter(
        SentinelMatchState.id == state_id,
        SentinelMatchState.ontology_id == ontology_id,
    ).first()
    if state is None:
        return {"status": "not_found"}
    edge = _claim_edge(state)
    state.runtime_status = f"failed_{edge}"
    db.commit()
    return {"status": "failed", "edge": edge, "matchKey": state.match_key}


def resume_sentinel_match_claim(db: Session, ontology_id: str,
                                state_id: str) -> dict:
    """Resume the whole action chain after one pending step is approved.

    Earlier successful steps replay their durable idempotency logs; the approved
    step resolves through its linked success log; only the remaining steps run.
    This avoids both duplicate side effects and silently truncating the chain.
    """
    state = db.query(SentinelMatchState).filter(
        SentinelMatchState.id == state_id,
        SentinelMatchState.ontology_id == ontology_id,
    ).first()
    if state is None:
        return {"status": "not_found"}
    sentinel = db.query(Sentinel).filter(
        Sentinel.id == state.sentinel_id,
        Sentinel.ontology_id == ontology_id,
    ).first()
    if sentinel is None:
        return {"status": "sentinel_not_found"}

    with _sentinel_execution_lock(db, sentinel.id):
        db.refresh(state)
        edge = _claim_edge(state)
        match_key = state.match_key
        match_detail = dict(state.match_detail or {})
        state.runtime_status = f"processing_{edge}"
        db.commit()
        db.refresh(state)
        tup = _tuple_from_detail(db, ontology_id, state.match_detail or {})
        primary = sentinel.primary_alias or (
            sentinel.bindings[0].get("alias") if sentinel.bindings else None)
        results: list[dict] = []
        token = in_sentinel_run.set(True)
        try:
            _ok, outcome = _run_actions(
                db, ontology_id, sentinel, tup, primary, edge,
                match_key, state, results)
        finally:
            in_sentinel_run.reset(token)
        _record_edge_outcome(db, state, edge, outcome)

        if outcome == "pending":
            firing_status = "pending"
        elif outcome == "failed":
            firing_status = "error"
        elif outcome == "no_actions":
            firing_status = "skipped"
        else:
            firing_status = "fired"
        firing = SentinelFiring(
            ontology_id=ontology_id, sentinel_id=sentinel.id,
            sentinel_name=sentinel.display_name, trigger_source="approval",
            matches=[match_detail], match_count=1,
            entered=[match_key] if edge == "enter" else [],
            left=[match_key] if edge == "leave" else [],
            action_results=results, status=firing_status,
            error="; ".join(
                str(item.get("errorMessage")) for item in results
                if item.get("status") == "failed" and item.get("errorMessage")
            ) or None,
            duration_ms=0,
        )
        db.add(firing)
        db.commit()
        db.refresh(firing)
        return {
            "status": firing_status, "edge": edge,
            "matchKey": match_key, "actionResults": results,
            "firingId": firing.id,
        }


def evaluate_sentinel(db: Session, ontology_id: str, sentinel: Sentinel,
                      source: str) -> SentinelFiring:
    """边沿触发评估(参考 Foundry Automate):算当前命中集 → 与上次做差 →
    仅对"进入"(可选"离开")执行动作 → 更新命中状态。source ∈ manual|change|schedule

    整体异常防护：单个哨兵怎么坏（配置损坏/表达式炸/动作抛错）都不能让
    手动触发 500、扫描 tick 崩溃或 CDC 线程静默吞错——统一落 status=error 日志。"""
    start = time.time()
    try:
        with _sentinel_execution_lock(db, sentinel.id):
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
    prior_by_key = {r.match_key: r for r in prior_rows}
    # Only a completed enter (or an unfinished leave of a previously completed
    # enter) belongs to the consumed match set.  Enter failures/pending claims
    # stay recoverable but do not suppress the edge.
    prior_keys = {
        r.match_key for r in prior_rows
        if (r.runtime_status or "completed") in _ACTIVE_MATCH_STATUSES
    }
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
    edge_outcomes: list[str] = []
    now = _now()

    if not sentinel.muted:
        token = in_sentinel_run.set(True)    # 断环:动作回写不即时级联
        try:
            for k in enter_targets:
                tup = current.get(k)
                if not tup:
                    continue
                newly_entered = k in entered
                state = _claim_match_state(
                    db, ontology_id, sentinel, k, tup, now,
                    new_cycle=(mode == "run_on_all" and not newly_entered),
                )
                if state is None:
                    # Another evaluator has already claimed this edge.
                    continue
                ok, outcome = _run_actions(
                    db, ontology_id, sentinel, tup, primary, "enter",
                    k, state, action_results)
                edge_outcomes.append(outcome)
                _record_edge_outcome(db, state, "enter", outcome)
            for k in leave_targets:
                state = prior_by_key.get(k)
                if state is None:
                    continue
                state.runtime_status = "processing_leave"
                db.commit()
                db.refresh(state)
                detail = state.match_detail or {}
                tup = _tuple_from_detail(db, ontology_id, detail or {})
                ok, outcome = _run_actions(
                    db, ontology_id, sentinel, tup, primary, "leave",
                    k, state, action_results)
                edge_outcomes.append(outcome)
                _record_edge_outcome(db, state, "leave", outcome)
        finally:
            in_sentinel_run.reset(token)

        # 4) 更新命中状态。failed/pending claim 虽保留用于恢复与幂等，
        #    但 runtime_status 不属于 consumed set，因此不会静默吸收 on_enter。
        #    muted(影子模式)不更新——match_state 语义是"已执行过动作的命中"；
        #    否则解除静默后存量命中已被吸收，on_enter 永不触发。
        for r in prior_rows:
            if r.match_key in left:
                if mode != "on_enter_leave":
                    db.delete(r)
            elif r.match_key in cur_keys:
                r.last_seen_at = now
            elif (r.runtime_status or "").endswith("_enter") \
                    and r.runtime_status != "pending_enter":
                # The condition disappeared before an enter retry succeeded;
                # end that lifecycle so a future re-entry receives a fresh key.
                db.delete(r)

    # 4.5) 缺席事实：查询结果为空/非空的状态翻转冻结进事实流（Negation-as-Failure 溯源）。
    #      表达式全在报错时跳过——那是 error 不是"确认为空"，不能伪造缺席证据。
    if not (eval_errors and not matched and tuples):
        nested = None
        try:
            nested = db.begin_nested()
            from app.ontologies.formal_modeling.facts import record_absence_fact
            record_absence_fact(
                db, ontology_id=ontology_id, subject_id=sentinel.id,
                empty=(len(current) == 0), scanned=len(tuples),
                source=f"sentinel://{sentinel.name or sentinel.id}@{source}",
                detail={"condition": sentinel.condition or "",
                        "sentinelName": sentinel.display_name})
            nested.commit()
        except Exception:  # noqa: BLE001 — 取证失败不能影响评估主流程
            if nested is not None and nested.is_active:
                nested.rollback()

    # 5) 记录触发日志
    action_failed = "failed" in edge_outcomes
    action_pending = "pending" in edge_outcomes
    only_no_actions = bool(edge_outcomes) and all(o == "no_actions" for o in edge_outcomes)
    if eval_errors and not matched and tuples:
        status = "error"       # 有候选但条件全在报错 → 明确暴露,而非伪装成 no_match
    elif sentinel.muted:
        status = "muted"
    elif action_failed:
        status = "error"
    elif action_pending:
        status = "pending"
    elif only_no_actions:
        status = "skipped"
    elif action_results and all(r.get("status") == "success" for r in action_results):
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
        error=("; ".join([
            *eval_errors,
            *[str(r.get("errorMessage")) for r in action_results
              if r.get("status") == "failed" and r.get("errorMessage")],
        ]) or None),
        duration_ms=int((time.time() - start) * 1000))
    db.add(firing); db.commit(); db.refresh(firing)
    return firing
