"""本体约束下的隔离决策推演引擎。

职责分工：
  * 本体与运行数据提供锁定的世界快照；
  * 多个 LLM 角色从不同职责视角提出可能性、假设与风险；
  * 固定代码使用加权均值、离散度与保守分数比较方案；
  * 输出仅用于辅助决策，绝不执行动作或写回真实对象。

这里刻意不引入仿真框架或统计库。当前版本使用标准库和平台既有 LLM 桥接，
在保持可运行的同时，为以后按领域替换某个评分器保留清晰边界。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import statistics
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ontology_formal import LinkInstance, ObjectInstance
from app.ontologies.agent_runtime import llm_bridge
from app.ontologies.agent_runtime.boundary import AgentScope, ToolError
from app.ontologies.decision_simulation.models import DecisionSimulationRun
from app.ontologies.sentinels.models import SentinelFiring

logger = logging.getLogger(__name__)

PROFILE_SCAN_CAP = 2000
SAMPLE_PER_TYPE = 12
PROPERTY_CAP = 30
TOP_VALUE_CAP = 6
RECENT_SENTINEL_CAP = 20

PERSPECTIVE_ROLES = (
    {
        "id": "evidence_auditor",
        "name": "证据审计视角",
        "mission": "检查数据支持度、缺口、口径和把相关性误当因果的风险。",
    },
    {
        "id": "operations_owner",
        "name": "业务执行视角",
        "mission": "评估资源、流程、依赖、落地成本与执行中的连锁反应。",
    },
    {
        "id": "risk_challenger",
        "name": "风险挑战视角",
        "mission": "主动寻找失败模式、极端情景、不可逆后果与停止条件。",
    },
    {
        "id": "opportunity_advocate",
        "name": "机会与相关方视角",
        "mission": "寻找潜在收益、被忽略的相关方、替代路径和无悔行动。",
    },
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any, cap: int = 1200) -> str:
    return str(value or "").strip()[:cap]


def _json_object(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回 JSON 对象")
    candidate = text[start:end + 1]
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        # 模型偶尔会漏一个逗号或引号。项目已内置 json-repair；这里只修复
        # 语法后仍会经过下游白名单规范化，绝不让修复器绕过领域契约。
        from json_repair import loads as repair_json_loads
        value = repair_json_loads(candidate)
    if not isinstance(value, dict):
        raise ValueError("模型返回的不是 JSON 对象")
    return value


def _call_json(call_kwargs: dict, system: str, user: str) -> tuple[dict, dict]:
    response = llm_bridge.chat(
        call_kwargs,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        [],
    )
    return _json_object(response.get("content") or ""), response.get("usage") or {}


def _phase(run: DecisionSimulationRun, db: Session, phase: str, **extra: Any) -> None:
    diagnostics = dict(run.diagnostics or {})
    diagnostics.update({"phase": phase, **extra})
    run.diagnostics = diagnostics
    db.commit()
    db.refresh(run)


def _instance_query(db: Session, scope: AgentScope):
    query = db.query(ObjectInstance).filter(
        ObjectInstance.ontology_id == scope.ontology.id)
    if scope.release_id is not None:
        query = query.filter(ObjectInstance.ontology_release_id == scope.release_id)
    return query


def _link_query(db: Session, scope: AgentScope):
    query = db.query(LinkInstance).filter(
        LinkInstance.ontology_id == scope.ontology.id)
    if scope.release_id is not None:
        query = query.filter(LinkInstance.ontology_release_id == scope.release_id)
    return query


def _instance_label(object_type: Any, instance: ObjectInstance) -> str:
    props = instance.properties or {}
    primary_key = getattr(object_type, "primary_key", None)
    if primary_key and props.get(primary_key) not in (None, ""):
        return _text(props.get(primary_key), 160)
    for key in ("name", "title", "code", "number", "id"):
        if props.get(key) not in (None, ""):
            return _text(props.get(key), 160)
    return instance.external_id or instance.id


def _property_profile(rows: list[ObjectInstance], property_name: str) -> dict[str, Any]:
    values = []
    for row in rows:
        merged = {**(row.properties or {}), **(row.computed or {})}
        value = merged.get(property_name)
        if value is not None and not isinstance(value, (dict, list)):
            values.append(value)

    profile: dict[str, Any] = {
        "observed": len(values),
        "missing": max(0, len(rows) - len(values)),
    }
    numbers = [float(value) for value in values
               if isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(float(value))]
    if numbers and len(numbers) >= max(1, len(values) // 2):
        profile.update({
            "kind": "numeric",
            "min": round(min(numbers), 6),
            "max": round(max(numbers), 6),
            "mean": round(sum(numbers) / len(numbers), 6),
        })
    else:
        top = Counter(_text(value, 120) for value in values if _text(value, 120))
        profile.update({
            "kind": "categorical",
            "topValues": [{"value": value, "count": count}
                          for value, count in top.most_common(TOP_VALUE_CAP)],
        })
    return profile


def build_snapshot(db: Session, scope: AgentScope) -> dict[str, Any]:
    """在授权边界内固化一份紧凑、可校验的数据快照。"""
    captured_at = _now().isoformat()
    counts = scope.instance_counts()
    object_summaries: list[dict[str, Any]] = []
    total_profiled = 0
    total_sampled = 0

    for object_type in scope.object_types.values():
        rows = (_instance_query(db, scope)
                .filter(ObjectInstance.object_type_id == object_type.id)
                .order_by(ObjectInstance.updated_at.desc(), ObjectInstance.id.asc())
                .limit(PROFILE_SCAN_CAP + 1).all())
        partial = len(rows) > PROFILE_SCAN_CAP
        profiled = rows[:PROFILE_SCAN_CAP]
        samples = profiled[:SAMPLE_PER_TYPE]
        total_profiled += len(profiled)
        total_sampled += len(samples)
        definitions = [item for item in (object_type.properties or [])
                       if isinstance(item, dict) and item.get("name")][:PROPERTY_CAP]
        profiles = [{
            "name": item["name"],
            "label": item.get("displayName") or item.get("display_name") or item["name"],
            "type": item.get("type") or "unknown",
            **_property_profile(profiled, item["name"]),
        } for item in definitions]
        object_summaries.append({
            "id": object_type.id,
            "name": object_type.name,
            "label": object_type.display_name,
            "description": _text(object_type.description, 500),
            "instanceCount": counts.get(object_type.id, 0),
            "profiledCount": len(profiled),
            "profilePartial": partial,
            "properties": profiles,
            "samples": [{
                "id": row.id,
                "label": _instance_label(object_type, row),
                "properties": {
                    key: value for key, value in {
                        **(row.properties or {}), **(row.computed or {}),
                    }.items() if key in {item["name"] for item in definitions}
                    and not isinstance(value, (dict, list))
                },
                "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
            } for row in samples],
        })

    link_counts = dict(
        _link_query(db, scope)
        .with_entities(LinkInstance.link_type_id, func.count(LinkInstance.id))
        .group_by(LinkInstance.link_type_id).all()
    )
    link_summaries = []
    for link_type in scope.link_types.values():
        source = scope.object_types.get(link_type.source_object_type_id)
        target = scope.object_types.get(link_type.target_object_type_id)
        link_summaries.append({
            "id": link_type.id,
            "name": link_type.name,
            "label": link_type.display_name,
            "source": source.display_name if source else link_type.source_object_type_id,
            "target": target.display_name if target else link_type.target_object_type_id,
            "count": int(link_counts.get(link_type.id, 0)),
        })

    firing_query = db.query(SentinelFiring).filter(
        SentinelFiring.ontology_id == scope.ontology.id)
    if scope.release_id is not None:
        firing_query = firing_query.filter(
            SentinelFiring.ontology_release_id == scope.release_id)
    firings = (firing_query.order_by(SentinelFiring.created_at.desc())
               .limit(RECENT_SENTINEL_CAP).all())
    recent_firings = [{
        "id": firing.id,
        "sentinel": firing.sentinel_name or firing.sentinel_id,
        "status": firing.status,
        "triggerSource": firing.trigger_source,
        "matchCount": firing.match_count,
        "actionStatuses": [item.get("status") for item in (firing.action_results or [])
                           if isinstance(item, dict)],
        "createdAt": firing.created_at.isoformat() if firing.created_at else None,
    } for firing in firings]

    snapshot: dict[str, Any] = {
        "ontologyId": scope.ontology.id,
        "ontologyName": scope.ontology.name,
        "releaseId": scope.release_id,
        "capturedAt": captured_at,
        "isolation": "read_only_release_snapshot",
        "objectTypes": object_summaries,
        "linkTypes": link_summaries,
        "recentSentinelFirings": recent_firings,
        "coverage": {
            "instanceCount": sum(counts.values()),
            "profiledCount": total_profiled,
            "sampledCount": total_sampled,
            "objectTypeCount": len(object_summaries),
            "linkTypeCount": len(link_summaries),
            "sentinelFiringCount": len(recent_firings),
            "profileCapPerType": PROFILE_SCAN_CAP,
        },
    }
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    snapshot["checksum"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return snapshot


def _fallback_spec(question: str, alternatives: list[str], horizon: Optional[str]) -> dict:
    choices = alternatives or ["维持现状", "采取调整方案"]
    return {
        "title": _text(question, 80),
        "decision": question,
        "horizon": horizon or "由当前数据可支持的近期范围",
        "alternatives": [
            {"id": f"option_{index + 1}", "label": label, "description": ""}
            for index, label in enumerate(choices[:6])
        ],
        "objectives": [
            {"id": "outcome", "label": "目标达成", "weight": 0.4},
            {"id": "feasibility", "label": "实施可行性", "weight": 0.3},
            {"id": "risk", "label": "风险可控性", "weight": 0.3},
        ],
        "constraints": [],
        "uncertainties": ["未来外部条件与当前快照可能不同"],
        "dataQuestions": [],
    }


def _normalize_spec(raw: dict, question: str, alternatives: list[str],
                    horizon: Optional[str]) -> dict:
    fallback = _fallback_spec(question, alternatives, horizon)
    raw_alternatives = raw.get("alternatives") if isinstance(raw.get("alternatives"), list) else []
    choices = alternatives or [
        _text(item.get("label") if isinstance(item, dict) else item, 160)
        for item in raw_alternatives
    ]
    choices = list(dict.fromkeys(choice for choice in choices if choice))[:6]
    if len(choices) < 2:
        choices = [item["label"] for item in fallback["alternatives"]]

    raw_objectives = raw.get("objectives") if isinstance(raw.get("objectives"), list) else []
    objectives = []
    for index, item in enumerate(raw_objectives[:6]):
        if not isinstance(item, dict):
            continue
        label = _text(item.get("label") or item.get("name"), 100)
        if not label:
            continue
        try:
            weight = max(0.0, float(item.get("weight", 1)))
        except (TypeError, ValueError):
            weight = 1.0
        objectives.append({
            "id": _text(item.get("id"), 48) or f"objective_{index + 1}",
            "label": label,
            "weight": weight,
        })
    if not objectives:
        objectives = fallback["objectives"]
    total_weight = sum(item["weight"] for item in objectives) or 1.0
    for item in objectives:
        item["weight"] = round(item["weight"] / total_weight, 6)

    def text_list(key: str, cap: int) -> list[str]:
        values = raw.get(key) if isinstance(raw.get(key), list) else []
        return [_text(value, cap) for value in values[:10] if _text(value, cap)]

    return {
        "title": _text(raw.get("title"), 160) or fallback["title"],
        "decision": question,
        "horizon": horizon or _text(raw.get("horizon"), 200) or fallback["horizon"],
        "alternatives": [{
            "id": f"option_{index + 1}",
            "label": label,
            "description": _text(raw_alternatives[index].get("description"), 500)
            if index < len(raw_alternatives) and isinstance(raw_alternatives[index], dict)
            else "",
        } for index, label in enumerate(choices)],
        "objectives": objectives,
        "constraints": text_list("constraints", 400),
        "uncertainties": text_list("uncertainties", 400) or fallback["uncertainties"],
        "dataQuestions": text_list("dataQuestions", 400),
    }


def _planner(call_kwargs: dict, question: str, alternatives: list[str],
             horizon: Optional[str], snapshot: dict) -> tuple[dict, dict]:
    system = """你是决策推演的场景编译器。把用户问题整理成可比较的决策契约。
只使用给定本体快照，不补造业务事实；没有证据的内容列入 uncertainties 或 dataQuestions。
返回单个 JSON 对象，不要 Markdown。字段：title, horizon, alternatives（2-6项，每项 label/description），
objectives（2-6项，每项 id/label/weight，weight 总和不要求你精确归一），constraints（字符串数组），
uncertainties（字符串数组），dataQuestions（字符串数组）。不要给未来事件分配概率。"""
    prompt = json.dumps({
        "question": question,
        "fixedAlternatives": alternatives,
        "fixedHorizon": horizon,
        "snapshotSummary": {
            "ontologyName": snapshot.get("ontologyName"),
            "capturedAt": snapshot.get("capturedAt"),
            "coverage": snapshot.get("coverage"),
            "objectTypes": [{
                "label": item.get("label"),
                "instanceCount": item.get("instanceCount"),
                "properties": [prop.get("label") for prop in item.get("properties", [])],
            } for item in snapshot.get("objectTypes", [])],
            "linkTypes": snapshot.get("linkTypes", []),
            "recentSentinelFirings": snapshot.get("recentSentinelFirings", []),
        },
    }, ensure_ascii=False, default=str)
    return _call_json(call_kwargs, system, prompt)


def _normalize_perspective(raw: dict, role: dict, spec: dict) -> dict:
    option_ids = {item["id"] for item in spec["alternatives"]}
    objective_ids = {item["id"] for item in spec["objectives"]}
    raw_options = raw.get("optionAssessments") if isinstance(raw.get("optionAssessments"), list) else []
    by_id = {item.get("optionId"): item for item in raw_options if isinstance(item, dict)}
    assessments = []
    for option in spec["alternatives"]:
        item = by_id.get(option["id"]) or {}
        raw_scores = item.get("scores") if isinstance(item.get("scores"), dict) else {}
        scores = {}
        for objective_id in objective_ids:
            try:
                score = float(raw_scores.get(objective_id, 50))
            except (TypeError, ValueError):
                score = 50.0
            scores[objective_id] = round(max(0.0, min(100.0, score)), 2)
        evidence_refs = [_text(value, 160) for value in (item.get("evidenceRefs") or [])[:12]
                         if _text(value, 160)]
        assumptions = [_text(value, 300) for value in (item.get("assumptions") or [])[:8]
                       if _text(value, 300)]
        risks = [_text(value, 300) for value in (item.get("risks") or [])[:8]
                 if _text(value, 300)]
        assessments.append({
            "optionId": option["id"],
            "scores": scores,
            "rationale": _text(item.get("rationale"), 1000),
            "evidenceRefs": evidence_refs,
            "assumptions": assumptions,
            "risks": risks,
        })
    raw_scenarios = raw.get("scenarioOutlooks") if isinstance(raw.get("scenarioOutlooks"), list) else []
    scenarios = []
    for item in raw_scenarios[:5]:
        if not isinstance(item, dict):
            continue
        impacts = item.get("impacts") if isinstance(item.get("impacts"), dict) else {}
        scenarios.append({
            "name": _text(item.get("name"), 100) or "未命名情景",
            "trigger": _text(item.get("trigger"), 400),
            "impacts": {key: _text(value, 500) for key, value in impacts.items()
                        if key in option_ids},
            "earlySignals": [_text(value, 240) for value in (item.get("earlySignals") or [])[:8]
                             if _text(value, 240)],
        })
    evidence_count = sum(len(item["evidenceRefs"]) for item in assessments)
    assumption_count = sum(len(item["assumptions"]) for item in assessments)
    coverage = evidence_count / max(1, evidence_count + assumption_count)
    return {
        "id": role["id"],
        "name": role["name"],
        "mission": role["mission"],
        "stance": _text(raw.get("stance"), 500),
        "keyFindings": [_text(value, 500) for value in (raw.get("keyFindings") or [])[:8]
                        if _text(value, 500)],
        "challenges": [_text(value, 500) for value in (raw.get("challenges") or [])[:8]
                       if _text(value, 500)],
        "optionAssessments": assessments,
        "scenarioOutlooks": scenarios,
        "evidenceCoverage": round(coverage, 3),
    }


def _run_perspective(call_kwargs: dict, role: dict, spec: dict,
                     snapshot: dict) -> tuple[dict, dict]:
    system = f"""你是独立的“{role['name']}”Agent。你的职责：{role['mission']}
你只能使用输入中的冻结本体快照。要明确区分 snapshot evidence 与 assumption；不能把多个 Agent 的
意见数当成概率，不能声称已建立因果。请挑战其他角色可能忽略的地方。
返回单个 JSON 对象，不要 Markdown，字段：stance, keyFindings（数组）, challenges（数组）,
optionAssessments（每个方案一项：optionId, scores{{objectiveId:0-100}}, rationale,
evidenceRefs（引用如“对象类型/属性/哨兵触发”）, assumptions（数组）, risks（数组）），
scenarioOutlooks（2-4项：name, trigger, impacts{{optionId:说明}}, earlySignals）。"""
    prompt = json.dumps({
        "decisionSpec": spec,
        "snapshot": snapshot,
    }, ensure_ascii=False, default=str)
    raw, usage = _call_json(call_kwargs, system, prompt)
    return _normalize_perspective(raw, role, spec), usage


def evaluate(spec: dict, perspectives: list[dict]) -> dict[str, Any]:
    """用固定公式比较方案；本函数不调用 LLM。"""
    weights = {item["id"]: float(item["weight"]) for item in spec["objectives"]}
    options = []
    for alternative in spec["alternatives"]:
        utilities: list[float] = []
        objective_values: dict[str, list[float]] = {key: [] for key in weights}
        for perspective in perspectives:
            assessment = next((item for item in perspective["optionAssessments"]
                               if item["optionId"] == alternative["id"]), None)
            if not assessment:
                continue
            utility = 0.0
            for objective_id, weight in weights.items():
                score = float(assessment["scores"].get(objective_id, 50))
                objective_values[objective_id].append(score)
                utility += score * weight
            utilities.append(utility)
        mean = statistics.fmean(utilities) if utilities else 0.0
        spread = statistics.pstdev(utilities) if len(utilities) > 1 else 0.0
        disagreement = (max(utilities) - min(utilities)) if utilities else 0.0
        robust = max(0.0, mean - 0.35 * spread)
        options.append({
            "optionId": alternative["id"],
            "label": alternative["label"],
            "meanScore": round(mean, 2),
            "robustScore": round(robust, 2),
            "minScore": round(min(utilities), 2) if utilities else 0.0,
            "maxScore": round(max(utilities), 2) if utilities else 0.0,
            "disagreement": round(disagreement, 2),
            "perspectiveCount": len(utilities),
            "objectiveScores": {
                objective_id: round(statistics.fmean(values), 2) if values else 0.0
                for objective_id, values in objective_values.items()
            },
        })
    options.sort(key=lambda item: (-item["robustScore"], -item["meanScore"], item["label"]))
    for index, item in enumerate(options):
        item["rank"] = index + 1
    max_disagreement = max((item["disagreement"] for item in options), default=0.0)
    disagreement_level = "high" if max_disagreement >= 30 else "medium" if max_disagreement >= 15 else "low"
    evidence_coverage = statistics.fmean(
        [float(item.get("evidenceCoverage") or 0) for item in perspectives]
    ) if perspectives else 0.0
    return {
        "method": {
            "name": "weighted_mean_minus_dispersion",
            "formula": "robustScore = weightedMean - 0.35 × populationStdDev",
            "scoreRange": [0, 100],
            "probability": False,
        },
        "options": options,
        "objectives": spec["objectives"],
        "disagreementLevel": disagreement_level,
        "maxDisagreement": round(max_disagreement, 2),
        "evidenceCoverage": round(evidence_coverage, 3),
    }


def _fallback_recommendation(spec: dict, evaluation: dict, perspectives: list[dict]) -> dict:
    winner = evaluation["options"][0]
    signals: list[str] = []
    risks: list[str] = []
    for perspective in perspectives:
        for scenario in perspective.get("scenarioOutlooks", []):
            signals.extend(scenario.get("earlySignals", []))
        assessment = next((item for item in perspective.get("optionAssessments", [])
                           if item.get("optionId") == winner["optionId"]), None)
        if assessment:
            risks.extend(assessment.get("risks", []))
    coverage = float(evaluation.get("evidenceCoverage") or 0)
    disagreement = float(evaluation.get("maxDisagreement") or 0)
    confidence_band = (
        "strong" if len(perspectives) >= 3 and coverage >= 0.6 and disagreement < 15
        else "moderate" if len(perspectives) >= 2 and disagreement < 30
        else "weak"
    )
    return {
        "recommendedOptionId": winner["optionId"],
        "recommendedOption": winner["label"],
        "robustScore": winner["robustScore"],
        "summary": f"按当前快照与既定权重，{winner['label']}的保守综合分最高。",
        "rationale": [
            f"加权均分 {winner['meanScore']}，计入视角离散度后的保守分 {winner['robustScore']}。",
            f"最大视角分歧 {winner['disagreement']}；该值表示意见离散，不是发生概率。",
        ],
        "tradeoffs": list(dict.fromkeys(risks))[:6],
        "noRegretActions": ["先验证关键数据缺口，再以小范围、可回滚方式执行。"],
        "earlySignals": list(dict.fromkeys(signals))[:8],
        "stopConditions": ["关键约束被突破或早期指标持续恶化时暂停并重新推演。"],
        "confidenceBand": confidence_band,
        "nature": "exploratory_decision_support",
        "disclaimer": "这是基于当前发布版快照的辅助决策结果，不是未来概率、因果证明或自动执行指令。",
    }


def _synthesize(call_kwargs: dict, spec: dict, evaluation: dict,
                perspectives: list[dict]) -> tuple[dict, dict]:
    winner = evaluation["options"][0]
    system = """你是决策委员会的记录员，不重新打分，也不能改变代码已经确定的推荐方案。
把分歧、权衡、无悔行动、早期信号和停止条件写成可执行但审慎的建议。返回单个 JSON 对象，
不要 Markdown。字段：summary, rationale（数组）, tradeoffs（数组）, noRegretActions（数组）,
earlySignals（数组）, stopConditions（数组）。不要把分数或 Agent 数量解释为概率。"""
    prompt = json.dumps({
        "decisionSpec": spec,
        "fixedRecommendedOption": winner,
        "evaluation": evaluation,
        "perspectives": perspectives,
    }, ensure_ascii=False, default=str)
    raw, usage = _call_json(call_kwargs, system, prompt)
    return raw, usage


def _merge_recommendation(base: dict, raw: dict) -> dict:
    merged = deepcopy(base)
    summary = _text(raw.get("summary"), 1200)
    if summary:
        merged["summary"] = summary
    for key, cap in (
        ("rationale", 500), ("tradeoffs", 500), ("noRegretActions", 500),
        ("earlySignals", 300), ("stopConditions", 400),
    ):
        values = raw.get(key) if isinstance(raw.get(key), list) else []
        cleaned = [_text(value, cap) for value in values[:10] if _text(value, cap)]
        if cleaned:
            merged[key] = cleaned
    return merged


def _usage_add(total: dict[str, int], usage: dict) -> None:
    for key in ("inputTokens", "outputTokens"):
        try:
            total[key] += int(usage.get(key) or 0)
        except (TypeError, ValueError):
            pass


def execute(
    db: Session,
    scope: AgentScope,
    *,
    question: str,
    alternatives: Optional[list[str]],
    horizon: Optional[str],
    conversation_id: Optional[str],
    created_by: str,
    call_kwargs: dict,
    model_config_id: Optional[str] = None,
) -> DecisionSimulationRun:
    """运行完整推演。失败也保留可审计记录，并把错误交给调用方。"""
    question = _text(question, 4000)
    if len(question) < 8:
        raise ToolError("决策推演问题至少需要 8 个字，请说明要比较什么或决定什么")
    choices = [_text(value, 160) for value in (alternatives or []) if _text(value, 160)][:6]
    if choices and len(set(choices)) < 2:
        raise ToolError("显式提供方案时至少需要两个不同方案")
    choices = list(dict.fromkeys(choices))
    if not created_by:
        raise ToolError("决策推演需要可审计的当前用户")

    run = DecisionSimulationRun(
        ontology_id=scope.ontology.id,
        ontology_release_id=scope.release_id,
        conversation_id=conversation_id,
        created_by=created_by,
        model_config_id=model_config_id or call_kwargs.get("model_config_id"),
        model_name=call_kwargs.get("model"),
        title=_text(question, 160),
        question=question,
        status="running",
        diagnostics={
            "phase": "snapshot",
            "engineVersion": "decision-simulation-v1",
            "isolation": "no_production_writes",
            "perspectiveTotal": len(PERSPECTIVE_ROLES),
            "perspectiveCompleted": 0,
            "warnings": [],
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    usage_total = {"inputTokens": 0, "outputTokens": 0}

    try:
        run.snapshot = build_snapshot(db, scope)
        db.commit()
        _phase(run, db, "planning")

        try:
            raw_spec, usage = _planner(
                call_kwargs, question, choices, _text(horizon, 200) or None, run.snapshot)
            _usage_add(usage_total, usage)
            spec = _normalize_spec(raw_spec, question, choices, _text(horizon, 200) or None)
        except Exception as exc:  # 规划失败可安全退化为固定契约
            logger.warning("决策推演场景编译失败，使用固定契约: %s", exc)
            spec = _fallback_spec(question, choices, _text(horizon, 200) or None)
            warnings = list((run.diagnostics or {}).get("warnings") or [])
            warnings.append(f"场景编译器返回不可用，已使用固定契约：{_text(exc, 240)}")
            run.diagnostics = {**(run.diagnostics or {}), "warnings": warnings}
        run.specification = spec
        run.title = spec["title"]
        db.commit()

        perspectives: list[dict] = []
        failures: list[dict] = []
        for index, role in enumerate(PERSPECTIVE_ROLES):
            _phase(
                run, db, "perspectives",
                perspectiveCurrent=role["name"],
                perspectiveCompleted=index,
            )
            try:
                perspective, usage = _run_perspective(
                    call_kwargs, role, spec, run.snapshot)
                _usage_add(usage_total, usage)
                perspectives.append(perspective)
                run.perspectives = deepcopy(perspectives)
                db.commit()
            except Exception as exc:  # 单个角色失败不摧毁其它视角
                logger.warning("决策推演视角 %s 失败: %s", role["id"], exc)
                failures.append({"perspectiveId": role["id"], "message": _text(exc, 300)})

        if len(perspectives) < 2:
            raise ToolError("有效推演视角不足两个，请检查模型配置后重试")

        evaluation = evaluate(spec, perspectives)
        run.evaluation = evaluation
        base_recommendation = _fallback_recommendation(spec, evaluation, perspectives)
        run.recommendation = base_recommendation
        run.diagnostics = {
            **(run.diagnostics or {}),
            "perspectiveCompleted": len(perspectives),
            "perspectiveFailures": failures,
            "usage": usage_total,
        }
        db.commit()
        _phase(run, db, "synthesis")

        try:
            raw_recommendation, usage = _synthesize(
                call_kwargs, spec, evaluation, perspectives)
            _usage_add(usage_total, usage)
            run.recommendation = _merge_recommendation(
                base_recommendation, raw_recommendation)
        except Exception as exc:
            logger.warning("决策推演综合说明失败，使用确定性说明: %s", exc)
            warnings = list((run.diagnostics or {}).get("warnings") or [])
            warnings.append(f"综合说明不可用，已使用确定性摘要：{_text(exc, 240)}")
            run.diagnostics = {**(run.diagnostics or {}), "warnings": warnings}

        run.status = "succeeded"
        run.completed_at = _now()
        run.diagnostics = {
            **(run.diagnostics or {}),
            "phase": "complete",
            "perspectiveCurrent": None,
            "perspectiveCompleted": len(perspectives),
            "usage": usage_total,
        }
        db.commit()
        db.refresh(run)
        return run
    except Exception as exc:
        db.rollback()
        failed = db.query(DecisionSimulationRun).filter(
            DecisionSimulationRun.id == run.id).first()
        if failed:
            failed.status = "failed"
            failed.error_message = _text(exc, 2000)
            failed.completed_at = _now()
            failed.diagnostics = {
                **(failed.diagnostics or {}),
                "phase": "failed",
                "usage": usage_total,
            }
            db.commit()
        if isinstance(exc, ToolError):
            raise
        raise ToolError(f"决策推演失败：{exc}") from exc


def compact_tool_result(run: DecisionSimulationRun) -> dict[str, Any]:
    recommendation = run.recommendation or {}
    return {
        "kind": "decision_simulation",
        "runId": run.id,
        "status": run.status,
        "title": run.title,
        "question": run.question,
        "releaseId": run.ontology_release_id,
        "snapshotChecksum": (run.snapshot or {}).get("checksum"),
        "dataCutoff": (run.snapshot or {}).get("capturedAt"),
        "perspectiveCount": len(run.perspectives or []),
        "recommendedOption": recommendation.get("recommendedOption"),
        "robustScore": recommendation.get("robustScore"),
        "disagreementLevel": (run.evaluation or {}).get("disagreementLevel"),
        "note": "推演已保存到独立运行记录；未修改任何真实对象、事实、哨兵或动作。",
    }
