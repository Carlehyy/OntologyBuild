"""哨兵触发解释（可解释推理）— 零 LLM 的确定性还原。

借鉴 Semantica 的 Explanation/ReasoningStep/Justification：把「这条哨兵为什么
触发」还原成结构化解释——条件表达式、逐元组求值结果、参与判定的属性值与出处、
状态语义与动作结果。全部材料来自三个既有事实源：

- ``SentinelFiring``：触发记录（matches/entered/left/status/error/action_results）；
- ``SentinelMatchState.match_detail``：命中时刻的元组快照（__snapshots__），
  对象删除或值变化后仍能确定性重放当时的属性值；
- 哨兵定义（动态哨兵取 live 行；发布内置哨兵取 firing 所属 release 的
  不可变快照）。

只读；不写库、不执行动作。求值复用与评估器同源的 ``safe_eval`` 白名单求值，
条件结果对「离开」元组可能为 False（这正是离开的原因），如实呈现。
"""
from __future__ import annotations

import ast
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.sentinel import Sentinel, SentinelFiring, SentinelMatchState
from app.models.ontology_version import OntologyVersion
from app.services.formal.safe_eval import safe_eval, SafeEvalError
from app.ontologies.versions.snapshot_contract import complete_snapshot
from app.shared.time_utils import utc_iso

logger = logging.getLogger(__name__)

# 与评估器 evaluator.py 中 _MATCH_SNAPSHOTS_KEY/_MATCH_EVENT_KEY 的线上
# 快照格式一致（写入方在评估器，读方只认 wire format）。
_SNAPSHOTS_KEY = "__snapshots__"
_EVENT_KEY = "__event__"

_MATCH_LIMIT = 3             # 单次解释最多展开的命中元组数
_VALUE_TRUNC = 200           # 属性值输出截断长度

_STATUS_MEANING = {
    "fired": "条件命中且新进入的对象已执行绑定动作（全部成功）。",
    "no_change": "仍有对象满足条件，但本轮没有新进入或离开的边沿，按触发模式不重复执行动作。",
    "no_match": "本轮没有任何对象满足哨兵条件。",
    "muted": "哨兵处于静默（muted）状态：仍做评估与记录，但不执行动作。",
    "skipped": "产生了进入/离开边沿，但哨兵没有绑定动作（no_actions）。",
    "pending": "动作已触发但处于待审批或执行中，尚未完成。",
    "error": "求值或动作执行出错（细节见 error 字段）。",
    "failed": "动作执行失败。",
}


def _trunc(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _VALUE_TRUNC:
        return value[:_VALUE_TRUNC] + "…"
    return value


def _merged(snapshot: dict) -> dict:
    return {
        **dict(snapshot.get("properties") or {}),
        **dict(snapshot.get("computed") or {}),
    }


def _condition_reads(expr: str, scope: dict[str, dict]) -> list[dict]:
    """抽取条件直接读取的 alias.property（含值）——证据清单，不是求值。"""
    reads: list[dict] = []
    seen: set[tuple[str, str]] = set()
    try:
        tree = ast.parse((expr or "").strip().rstrip(";"), mode="eval")
    except SyntaxError:
        return reads
    for node in ast.walk(tree):
        alias = prop = None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            alias, prop = node.value.id, node.attr
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            alias, prop = node.value.id, node.slice.value
        if alias is None or alias not in scope:
            continue
        if (alias, prop) in seen:
            continue
        seen.add((alias, prop))
        reads.append({
            "alias": alias,
            "property": prop,
            "value": _trunc(scope[alias].get(prop)),
        })
    return reads


def _eval(expr: str | None, scope: dict[str, dict]) -> dict:
    """对条件/绑定过滤做确定性求值，返回结果与错误（不吞错）。"""
    if not expr:
        return {"expression": expr, "result": True, "error": None}
    try:
        return {
            "expression": expr,
            "result": bool(safe_eval(expr, scope)),
            "error": None,
        }
    except SafeEvalError as exc:
        return {"expression": expr, "result": False, "error": str(exc)}


def _match_key_for(tup: dict[str, Any], primary: str | None) -> str:
    """与评估器 _match_key 同构的命中键（单对象=primary id，跨对象=元组签名）。

    输入 tup 来自 firing.matches：{alias: instanceId}，值为字符串。
    """
    if len(tup) <= 1 and primary and primary in tup:
        return str(tup[primary] or "")
    return "|".join(
        f"{a}={tup[a] or ''}" for a in sorted(tup))


def _binding_definition(
        bindings: list, alias: str) -> Optional[dict]:
    for item in bindings or []:
        if not isinstance(item, dict):
            continue
        if item.get("alias") == alias or item.get("name") == alias:
            return item
    return None


def resolve_sentinel_definition(
        db: Session, *, ontology_id: str, sentinel_id: str,
        firing: SentinelFiring,
) -> dict | None:
    """哨兵定义解析：动态哨兵取 live 行；发布内置哨兵取 firing 所属 release 快照。"""
    live = db.query(Sentinel).filter(
        Sentinel.ontology_id == ontology_id,
        Sentinel.id == sentinel_id,
    ).first()
    if live is not None and str(getattr(live, "origin", "") or "") == (
            "assistant_dynamic"):
        return {
            "id": live.id,
            "name": live.name,
            "displayName": live.display_name,
            "origin": str(getattr(live, "origin", "") or "release_builtin"),
            "condition": live.condition,
            "conditionLogic": live.condition_logic,
            "triggerMode": live.trigger_mode,
            "bindings": list(live.bindings or []),
            "links": list(live.links or []),
            "primaryAlias": live.primary_alias,
            "actionIds": list(live.action_ids or []),
        }
    if firing.ontology_release_id:
        version = db.query(OntologyVersion).filter(
            OntologyVersion.id == firing.ontology_release_id,
        ).first()
        if version is not None and isinstance(version.snapshot_formal, dict):
            for raw in complete_snapshot(version.snapshot_formal)["sentinels"]:
                if str(raw.get("id") or "") == sentinel_id:
                    return {
                        "id": str(raw.get("id") or ""),
                        "name": str(raw.get("name") or ""),
                        "displayName": str(
                            raw.get("displayName") or raw.get("name") or ""),
                        "origin": "release_builtin",
                        "condition": raw.get("condition"),
                        "conditionLogic": raw.get("conditionLogic") or "and",
                        "triggerMode": raw.get("triggerMode") or "on_enter",
                        "bindings": list(raw.get("bindings") or []),
                        "links": list(raw.get("links") or []),
                        "primaryAlias": raw.get("primaryAlias"),
                        "actionIds": list(raw.get("actionIds") or []),
                    }
    return None


def explain_sentinel_firing(
        db: Session,
        *,
        ontology_id: str,
        definition: dict,
        firing: SentinelFiring,
        match_limit: int = _MATCH_LIMIT,
) -> dict:
    """把一次触发还原成结构化解释（供助手叙述，零 LLM）。"""
    limit = max(1, min(int(match_limit or _MATCH_LIMIT), 10))
    primary = definition.get("primaryAlias")
    bindings = definition.get("bindings") or []

    # 命中元组（firing.matches 为 [{alias: instanceId}, ...]）
    tuples = [
        {str(alias): str(instance_id) for alias, instance_id in item.items()}
        for item in (firing.matches or [])
        if isinstance(item, dict)
    ]
    # 命中快照（按 match_key 对齐；旧数据无快照时如实标记）
    states = db.query(SentinelMatchState).filter(
        SentinelMatchState.sentinel_id == firing.sentinel_id,
    ).all()
    states_by_key = {str(s.match_key): s for s in states}

    matched_tuples: list[dict] = []
    for tup in tuples[:limit]:
        key = _match_key_for(tup, primary)
        state = states_by_key.get(key)
        snapshots = (state.match_detail or {}).get(_SNAPSHOTS_KEY) \
            if state is not None else None
        aliases: list[dict] = []
        scope: dict[str, dict] = {}
        snapshot_missing = False
        for alias, instance_id in tup.items():
            snap = None
            if isinstance(snapshots, dict):
                raw = snapshots.get(alias)
                if isinstance(raw, dict) and raw.get("id"):
                    snap = raw
            if snap is None:
                snapshot_missing = True
                aliases.append({
                    "alias": alias,
                    "instanceId": instance_id,
                    "externalId": None,
                    "snapshotAvailable": False,
                })
                continue
            values = _merged(snap)
            scope[alias] = values
            aliases.append({
                "alias": alias,
                "instanceId": str(snap.get("id") or instance_id),
                "objectTypeId": snap.get("objectTypeId"),
                "externalId": snap.get("externalId"),
                "snapshotAvailable": True,
            })
        condition = None
        if not snapshot_missing:
            condition = _eval(definition.get("condition"), scope)
            condition["reads"] = _condition_reads(
                definition.get("condition") or "", scope)
        binding_checks = []
        if not snapshot_missing:
            for binding in bindings:
                alias = binding.get("alias")
                if not alias or alias not in scope:
                    continue
                # 与评估器 _passes 同语义：绑定过滤只在该绑定自己的
                # 对象视图上求值（alias 与 obj 都指向它）
                check_scope = {alias: scope[alias], "obj": scope[alias]}
                check = _eval(binding.get("filter"), check_scope)
                check["reads"] = _condition_reads(
                    binding.get("filter") or "", check_scope)
                binding_checks.append({"alias": alias, **check})
        # 该元组在 firing 中的边沿与动作结果
        edge = "enter"
        event = None
        if state is not None:
            event = (state.match_detail or {}).get(_EVENT_KEY) or {}
            if isinstance(event, dict) and event.get("edge"):
                edge = str(event["edge"])
        actions = [
            item for item in (firing.action_results or [])
            if item.get("targetInstanceId") in tup.values()
            or item.get("instanceId") in tup.values()
            or item.get("alias") in tup
        ]
        matched_tuples.append({
            "matchKey": key,
            "edge": edge,
            "occurredAt": (event or {}).get("occurredAt") if event else None,
            "aliases": aliases,
            "condition": condition,
            "bindingFilters": binding_checks,
            "actions": [
                {
                    "actionId": a.get("actionId"),
                    "status": a.get("status"),
                    "errorMessage": a.get("errorMessage"),
                }
                for a in actions
            ],
            "snapshotMissing": snapshot_missing,
        })

    return {
        "sentinel": {
            "id": definition.get("id"),
            "name": definition.get("name"),
            "displayName": definition.get("displayName"),
            "origin": definition.get("origin"),
            "triggerMode": definition.get("triggerMode"),
            "condition": definition.get("condition"),
            "conditionLogic": definition.get("conditionLogic"),
            "primaryAlias": primary,
            "bindings": [
                {
                    "alias": b.get("alias"),
                    "objectTypeId": b.get("objectTypeId"),
                    "filter": b.get("filter"),
                }
                for b in bindings if isinstance(b, dict)
            ],
            "links": definition.get("links") or [],
            "actionIds": definition.get("actionIds") or [],
        },
        "firing": {
            "id": firing.id,
            "sentinelId": firing.sentinel_id,
            "triggerSource": firing.trigger_source,
            "status": firing.status,
            "error": firing.error,
            "ontologyReleaseId": firing.ontology_release_id,
            "createdAt": utc_iso(firing.created_at),
            "matchCount": firing.match_count,
            "entered": len(firing.entered or []),
            "left": len(firing.left or []),
            "actionResults": [
                {
                    "actionId": a.get("actionId"),
                    "targetInstanceId": a.get("targetInstanceId"),
                    "edge": a.get("edge"),
                    "status": a.get("status"),
                    "errorMessage": a.get("errorMessage"),
                }
                for a in (firing.action_results or [])
            ],
        },
        "statusMeaning": _STATUS_MEANING.get(
            firing.status,
            f"状态 {firing.status}（未知语义，按原始记录呈现）"),
        "matchedTuples": matched_tuples,
        "explanationLimits": {
            "matchLimit": limit,
            "totalMatches": len(tuples),
            "snapshotMissingTuples": sum(
                1 for t in matched_tuples if t.get("snapshotMissing")),
        },
    }
