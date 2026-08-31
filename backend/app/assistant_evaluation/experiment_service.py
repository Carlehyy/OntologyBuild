"""双臂沙箱实验 — 草稿提案投产前的受控验证。

流程：基准集的每条会话提取「用户问题脚本」，在沙箱中分别以
「当前生产配置」（baseline 臂）与「草稿提案配置」（trial 臂）完整
回放（复用 agent_runtime 的试跑入口：草稿 profile 不落库、沙箱会话
对用户不可见），两臂新轨迹用同一套维度与 judge 模型评分对比。

门禁只认留出集（heldout）增量，阈值下界为 max(入参 threshold,
2×最近一次噪声校准 overall_noise)：训练集增量仅作参考，防止草稿
向基准过拟合；噪声倍数防止 judge 抖动被误读为优化。

沙箱会话评分后立即删除（连同 DecisionSimulationRun 审计行），完整
轨迹以快照形式存回实验条目——实验结果自包含，不依赖任何活会话。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.assistant_evaluation import service as task_service
from app.assistant_evaluation.adapters import get_adapters
from app.assistant_evaluation.benchmark_service import items_of
from app.assistant_evaluation.dimensions import BASE_DIMENSION_KEYS, DIMENSIONS
from app.assistant_evaluation.engine import build_engine, openjudge_available
from app.assistant_evaluation.models import (
    AssistantEvalBenchmarkSet,
    AssistantEvalCalibration,
    AssistantEvalExperiment,
    AssistantEvalExperimentItem,
    AssistantEvalProposal,
)
from app.assistant_evaluation.service import ServiceError
from app.assistant_evaluation.timeline import (
    EVENT_EXPERIMENT_CREATED,
    EVENT_EXPERIMENT_FAILED,
    EVENT_EXPERIMENT_SUCCEEDED,
    EVENT_PROPOSAL_CREATED,
    ACTOR_ADMIN,
    ACTOR_SYSTEM,
    record_event,
)
from app.shared.database import SessionLocal

logger = logging.getLogger(__name__)

PROPOSAL_TYPES = ("prompt_patch", "model_swap")
EXPERIMENT_ASSISTANT_KEY = "ontology_agent"
MAX_BENCHMARK_ITEMS_PER_EXPERIMENT = 10
DEFAULT_GATE_THRESHOLD = 5.0
ARMS = ("baseline", "trial")


# ---------------------------------------------------------------- 提案


def create_proposal(db: Session, *, ontology_id: str, type: str, title: str,
                    rationale: str, payload: dict, evidence: dict,
                    created_by: str | None,
                    actor: str = ACTOR_ADMIN) -> AssistantEvalProposal:
    from app.ontologies.projects.models import OntologyProject
    from app.ontologies.agent_runtime.models import AgentProfile

    if type not in PROPOSAL_TYPES:
        raise ServiceError(f"提案类型仅支持：{' / '.join(PROPOSAL_TYPES)}。")
    if db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first() is None:
        raise ServiceError("所选本体不存在。")

    profile = (
        db.query(AgentProfile).filter(AgentProfile.ontology_id == ontology_id).first()
    )
    payload = dict(payload or {})

    if type == "prompt_patch":
        new_prompt = payload.get("system_prompt_extra")
        if not isinstance(new_prompt, str) or not new_prompt.strip():
            raise ServiceError("prompt_patch 提案必须给出替换后的完整 system_prompt_extra。")
        payload = {
            "system_prompt_extra": new_prompt,
            "base_system_prompt_extra": (profile.system_prompt_extra or "")
            if profile is not None else "",
        }
    else:  # model_swap
        model_config_id = str(payload.get("model_config_id") or "").strip()
        if not model_config_id:
            raise ServiceError("model_swap 提案必须指定 model_config_id。")
        from app.model_configs.models import ModelConfig

        config = (
            db.query(ModelConfig).filter(ModelConfig.id == model_config_id).first()
        )
        if config is None:
            raise ServiceError("目标模型配置不存在。")
        payload = {
            "model_config_id": model_config_id,
            "model_name": getattr(config, "name", "") or "",
            "base_model_config_id": (profile.default_model_id or None)
            if profile is not None else None,
        }

    row = AssistantEvalProposal(
        ontology_id=ontology_id, assistant_key=EXPERIMENT_ASSISTANT_KEY,
        type=type, title=(title or "").strip() or f"{type} 提案",
        rationale=rationale or "", payload=payload,
        evidence=dict(evidence or {}), created_by=created_by,
    )
    db.add(row)
    db.flush()
    record_event(db, event_type=EVENT_PROPOSAL_CREATED,
                 assistant_key=EXPERIMENT_ASSISTANT_KEY,
                 actor=actor, actor_user_id=created_by if actor == ACTOR_ADMIN else None,
                 ref_type="proposal", ref_id=row.id,
                 detail={"ontology_id": ontology_id, "type": type,
                         "title": row.title, "payload": payload})
    db.commit()
    db.refresh(row)
    return row


def list_proposals(db: Session, ontology_id: str | None = None,
                   limit: int = 20) -> list[AssistantEvalProposal]:
    query = db.query(AssistantEvalProposal)
    if ontology_id:
        query = query.filter(AssistantEvalProposal.ontology_id == ontology_id)
    return query.order_by(AssistantEvalProposal.created_at.desc()).limit(min(limit, 100)).all()


def get_proposal(db: Session, proposal_id: str) -> AssistantEvalProposal | None:
    return (
        db.query(AssistantEvalProposal)
        .filter(AssistantEvalProposal.id == proposal_id)
        .first()
    )


def _draft_profile(base_profile, proposal: AssistantEvalProposal):
    """按提案构造只读草稿 profile（不落库）：边界字段原样继承，仅换目标杠杆。"""
    from app.ontologies.agent_runtime.models import AgentProfile

    payload = proposal.payload or {}
    return AgentProfile(
        ontology_id=base_profile.ontology_id,
        enabled=True,
        allowed_object_type_ids=base_profile.allowed_object_type_ids,
        allowed_link_type_ids=base_profile.allowed_link_type_ids,
        allowed_action_ids=base_profile.allowed_action_ids,
        allow_action_proposals=base_profile.allow_action_proposals,
        max_rows_per_query=base_profile.max_rows_per_query,
        max_steps=base_profile.max_steps,
        system_prompt_extra=(
            payload.get("system_prompt_extra")
            if proposal.type == "prompt_patch"
            else base_profile.system_prompt_extra
        ),
        default_model_id=(
            payload.get("model_config_id")
            if proposal.type == "model_swap"
            else base_profile.default_model_id
        ),
    )


# ---------------------------------------------------------------- 实验


def create_experiment(db: Session, *, proposal_id: str, benchmark_set_id: str,
                      dimension_keys: list[str], threshold: float,
                      model_config_id: str | None,
                      created_by: str | None,
                      purpose: str | None = None) -> AssistantEvalExperiment:
    proposal = get_proposal(db, proposal_id)
    if proposal is None:
        raise ServiceError("提案不存在。")
    if proposal.status not in {"draft", "validated"}:
        raise ServiceError("该提案已终结，不能再次验证。")

    bench = (
        db.query(AssistantEvalBenchmarkSet)
        .filter(AssistantEvalBenchmarkSet.id == benchmark_set_id)
        .first()
    )
    if bench is None:
        raise ServiceError("基准集不存在。")
    if bench.assistant_key != EXPERIMENT_ASSISTANT_KEY:
        raise ServiceError("沙箱回放当前仅支持本体助手。")
    if not bench.ontology_id or bench.ontology_id != proposal.ontology_id:
        raise ServiceError("基准集与提案不属于同一本体。")

    items = items_of(db, benchmark_set_id)
    if not items:
        raise ServiceError("基准集为空。")
    if len(items) > MAX_BENCHMARK_ITEMS_PER_EXPERIMENT:
        raise ServiceError(
            f"实验单臂最多回放 {MAX_BENCHMARK_ITEMS_PER_EXPERIMENT} 条会话，"
            f"当前基准集 {len(items)} 条，请先精简基准集。"
        )
    if not any(i.split == "heldout" for i in items):
        raise ServiceError("基准集缺少留出集（heldout）条目，无法执行门禁对比。")

    unknown = [k for k in dimension_keys if k not in DIMENSIONS]
    if unknown:
        raise ServiceError(f"未知评分维度：{', '.join(unknown)}")
    dimension_keys = list(dict.fromkeys(dimension_keys)) or list(BASE_DIMENSION_KEYS)

    llm_dims = [k for k in dimension_keys if DIMENSIONS[k].kind == "llm"]
    judge = task_service._resolve_judge_model(db, model_config_id) if llm_dims else None

    try:
        # 注意 0 是合法阈值（配合零噪声地板的确定性维度），不能当 falsy 回退
        threshold = float(threshold) if threshold is not None else DEFAULT_GATE_THRESHOLD
    except (TypeError, ValueError):
        raise ServiceError("门禁阈值必须是数字。")
    threshold = max(0.0, threshold)

    row = AssistantEvalExperiment(
        ontology_id=proposal.ontology_id,
        proposal_id=proposal.id,
        benchmark_set_id=benchmark_set_id,
        status="queued",
        params={
            "dimension_keys": list(dimension_keys),
            "threshold": threshold,
            "benchmark_set_id": benchmark_set_id,
            "engine": "openjudge" if openjudge_available() else "builtin",
            "splits": {i.conversation_id: i.split for i in items},
            **({"purpose": purpose} if purpose else {}),
        },
        judge_model_config_id=(str(judge.id) if judge is not None else None),
        judge_model_name=(getattr(judge, "name", "") or "") if judge is not None
        else "（仅代码型维度，无需 judge 模型）",
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    record_event(db, event_type=EVENT_EXPERIMENT_CREATED,
                 assistant_key=EXPERIMENT_ASSISTANT_KEY,
                 actor=ACTOR_ADMIN, actor_user_id=created_by,
                 ref_type="experiment", ref_id=row.id,
                 detail={"ontology_id": row.ontology_id,
                         "proposal_id": proposal.id,
                         "benchmark_set_id": benchmark_set_id,
                         "dimension_keys": list(dimension_keys),
                         "threshold": threshold})
    db.commit()
    db.refresh(row)

    _start_worker(row.id)
    return row


def _start_worker(experiment_id: str) -> None:
    thread = threading.Thread(target=_run_experiment, args=(experiment_id,), daemon=True,
                              name=f"assistant-eval-exp-{experiment_id[:8]}")
    thread.start()


def _run_experiment(experiment_id: str) -> None:
    """实验执行入口（守护线程上下文）：与评估/校准任务共用全局闸门串行。"""
    with task_service._TASK_GATE:
        asyncio.run(_run_experiment_async(experiment_id))


def _user_script(db: Session, conversation_id: str) -> list[str]:
    """从历史会话提取用户问题脚本（回放的输入序列）。"""
    from app.ontologies.agent_runtime.models import AgentMessage

    rows = (
        db.query(AgentMessage)
        .filter(AgentMessage.conversation_id == conversation_id,
                AgentMessage.role == "user")
        .order_by(AgentMessage.created_at.asc(), AgentMessage.id.asc())
        .all()
    )
    return [r.content.strip() for r in rows if (r.content or "").strip()]


def _replay_conversation(owns, ontology_id: str, actor, questions: list[str],
                         profile_override) -> tuple[str | None, str | None]:
    """在沙箱里回放一条问题脚本，返回 (沙箱会话 id, 错误消息)。"""
    from app.ontologies.agent_runtime.orchestrator import run_agent_turn

    conversation_id: str | None = None
    error: str | None = None
    for question in questions:
        for event in run_agent_turn(
            owns, ontology_id, actor, question,
            conversation_id=conversation_id,
            profile_override=profile_override, sandbox=True,
        ):
            event_type = event.get("type")
            if event_type == "meta":
                conversation_id = event.get("conversationId") or conversation_id
            elif event_type == "error":
                error = str(event.get("message") or "回合执行失败")
                break
        if error:
            break
    return conversation_id, error


def _cleanup_sandbox(owns, conversation_id: str | None) -> None:
    """删除沙箱会话及其消息、决策推演审计行（评分快照已先行取走）。"""
    from app.ontologies.agent_runtime.conversation_service import delete_conversation
    from app.ontologies.agent_runtime.models import AgentConversation

    if not conversation_id:
        return
    conv = (
        owns.query(AgentConversation)
        .filter(AgentConversation.id == conversation_id)
        .first()
    )
    if conv is not None:
        delete_conversation(owns, conv)  # 自带 commit，按既有事务顺序清理


def _arm_stats(items: list[AssistantEvalExperimentItem]) -> dict:
    scored = [i for i in items if i.scores]
    dims_map = DIMENSIONS
    per_dim: dict[str, dict] = {}
    for key in dims_map:
        values = [float(i.scores[key]) for i in scored if key in i.scores]
        if values:
            per_dim[key] = {
                "avg": round(sum(values) / len(values), 1),
                "min": min(values),
                "count": len(values),
            }
    overalls = [float(i.overall_score) for i in scored if i.overall_score is not None]
    return {
        "overall": round(sum(overalls) / len(overalls), 1) if overalls else None,
        "per_dim": per_dim,
        "scored": len(scored),
        "failed": len([i for i in items if (i.flags or {}).get("engine_error")]),
    }


def _latest_noise_floor(owns) -> float:
    """最近一次成功校准的 overall_noise（无校准记录时视为 0）。"""
    row = (
        owns.query(AssistantEvalCalibration)
        .filter(AssistantEvalCalibration.assistant_key == EXPERIMENT_ASSISTANT_KEY,
                AssistantEvalCalibration.status == "success")
        .order_by(AssistantEvalCalibration.created_at.desc())
        .first()
    )
    if row is None:
        return 0.0
    return float((row.result or {}).get("overall_noise") or 0.0)


async def _run_experiment_async(experiment_id: str) -> None:
    started = time.monotonic()
    owns = SessionLocal()
    try:
        row = (
            owns.query(AssistantEvalExperiment)
            .filter(AssistantEvalExperiment.id == experiment_id)
            .first()
        )
        if row is None:
            return
        row.status = "running"
        owns.commit()

        proposal = (
            owns.query(AssistantEvalProposal)
            .filter(AssistantEvalProposal.id == row.proposal_id)
            .first()
        )
        if proposal is None:
            raise ServiceError("提案已不存在。")

        from app.ontologies.agent_runtime.boundary import get_or_create_profile

        base_profile = get_or_create_profile(owns, row.ontology_id)

        engine = None
        if row.judge_model_config_id:
            from app.model_configs.models import ModelConfig

            config = (
                owns.query(ModelConfig)
                .filter(ModelConfig.id == row.judge_model_config_id)
                .first()
            )
            if config is None:
                raise ServiceError("judge 模型配置已不存在。")
            engine = build_engine(config)

        adapter = get_adapters()[EXPERIMENT_ASSISTANT_KEY]
        params = row.params or {}
        base_dims = list(params.get("dimension_keys") or BASE_DIMENSION_KEYS)
        threshold = float(params.get("threshold") or DEFAULT_GATE_THRESHOLD)
        dims_map = dict(DIMENSIONS)

        bench_items = items_of(owns, params.get("benchmark_set_id"))
        actor = SimpleNamespace(id=row.created_by or "assistant-eval")
        draft = _draft_profile(base_profile, proposal)
        sandbox_ids: list[str] = []

        for arm in ARMS:
            override = draft if arm == "trial" else None
            for item in bench_items:
                questions = _user_script(owns, item.conversation_id)
                exp_item = AssistantEvalExperimentItem(
                    experiment_id=row.id, arm=arm,
                    conversation_id=item.conversation_id,
                    conversation_title=item.conversation_title,
                    split=item.split,
                )
                if not questions:
                    exp_item.flags = {"engine_error": "基准会话缺少用户消息，无法回放"}
                else:
                    conversation_id, error = _replay_conversation(
                        owns, row.ontology_id, actor, questions, override)
                    if conversation_id:
                        sandbox_ids.append(conversation_id)
                    trace = adapter.load_trace(owns, conversation_id) \
                        if conversation_id else None
                    if error or trace is None:
                        exp_item.flags = {"engine_error": (error or "会话未产出可评分轨迹")[:500]}
                    else:
                        dim_keys = task_service._effective_dims(base_dims, trace)
                        try:
                            raw = await task_service._evaluate_async(
                                engine, dim_keys, trace, None)
                            scores, _reasons = task_service._assemble_scores(
                                dim_keys, dims_map, raw)
                            exp_item.scores = scores
                            exp_item.overall_score = task_service._weighted_overall(
                                dim_keys, scores, dims_map)
                            exp_item.transcript = {
                                "query": trace.query,
                                "response": trace.response,
                                "openai_messages": trace.openai_messages,
                                "actions": trace.actions,
                                "tool_error_count": trace.tool_error_count,
                            }
                        except Exception as exc:  # 单条失败不摧毁实验
                            logger.exception("实验条目评分失败：%s", item.conversation_id)
                            exp_item.flags = {"engine_error": str(exc)[:500]}
                    _cleanup_sandbox(owns, conversation_id)  # 快照已取，立即清理
                owns.add(exp_item)
                owns.commit()
            params["sandbox_conversation_ids"] = list(sandbox_ids)
            row.params = dict(params)
            owns.commit()

        all_items = (
            owns.query(AssistantEvalExperimentItem)
            .filter(AssistantEvalExperimentItem.experiment_id == row.id)
            .all()
        )
        arms = {arm: _arm_stats([i for i in all_items if i.arm == arm]) for arm in ARMS}

        by_split: dict[str, dict] = {}
        for split in ("train", "heldout"):
            split_items = [i for i in all_items if i.split == split]
            entry: dict = {}
            for arm in ARMS:
                stats = _arm_stats([i for i in split_items if i.arm == arm])
                entry[arm] = stats["overall"]
            entry["delta"] = (
                round(entry["trial"] - entry["baseline"], 1)
                if entry["trial"] is not None and entry["baseline"] is not None
                else None
            )
            by_split[split] = entry

        noise_floor = _latest_noise_floor(owns)
        effective_threshold = round(max(threshold, 2 * noise_floor), 1)
        heldout_delta = by_split.get("heldout", {}).get("delta")
        gate = {
            "passed": heldout_delta is not None and heldout_delta >= effective_threshold,
            "heldout_delta": heldout_delta,
            "threshold": threshold,
            "noise_floor": noise_floor,
            "effective_threshold": effective_threshold,
        }

        row.status = "success"
        row.result = {
            "baseline": arms["baseline"],
            "trial": arms["trial"],
            "by_split": by_split,
            "gate": gate,
        }
        row.finished_at = datetime.now(timezone.utc)
        row.duration_ms = int((time.monotonic() - started) * 1000)
        if gate["passed"]:
            proposal.status = "validated"
        record_event(db=owns, event_type=EVENT_EXPERIMENT_SUCCEEDED,
                     assistant_key=EXPERIMENT_ASSISTANT_KEY, actor=ACTOR_SYSTEM,
                     ref_type="experiment", ref_id=row.id,
                     detail={"ontology_id": row.ontology_id,
                             "proposal_id": proposal.id,
                             "gate": gate,
                             "baseline_overall": arms["baseline"]["overall"],
                             "trial_overall": arms["trial"]["overall"]})
        owns.commit()
    except Exception as exc:
        logger.exception("双臂实验执行失败：%s", experiment_id)
        owns.rollback()
        row = (
            owns.query(AssistantEvalExperiment)
            .filter(AssistantEvalExperiment.id == experiment_id)
            .first()
        )
        if row is not None:
            row.status = "error"
            row.error = str(exc)[:2000]
            row.finished_at = datetime.now(timezone.utc)
            row.duration_ms = int((time.monotonic() - started) * 1000)
            record_event(db=owns, event_type=EVENT_EXPERIMENT_FAILED,
                         assistant_key=EXPERIMENT_ASSISTANT_KEY, actor=ACTOR_SYSTEM,
                         ref_type="experiment", ref_id=row.id,
                         detail={"ontology_id": row.ontology_id, "error": str(exc)[:500]})
            owns.commit()
    finally:
        owns.close()


# ---------------------------------------------------------------- 查询 / 清理


def list_experiments(db: Session, ontology_id: str | None = None,
                     limit: int = 20) -> list[AssistantEvalExperiment]:
    query = db.query(AssistantEvalExperiment)
    if ontology_id:
        query = query.filter(AssistantEvalExperiment.ontology_id == ontology_id)
    return query.order_by(AssistantEvalExperiment.created_at.desc()).limit(min(limit, 100)).all()


def get_experiment(db: Session, experiment_id: str) -> AssistantEvalExperiment | None:
    return (
        db.query(AssistantEvalExperiment)
        .filter(AssistantEvalExperiment.id == experiment_id)
        .first()
    )


def experiment_items(db: Session, experiment_id: str,
                     arm: str | None = None) -> list[AssistantEvalExperimentItem]:
    query = db.query(AssistantEvalExperimentItem).filter(
        AssistantEvalExperimentItem.experiment_id == experiment_id)
    if arm:
        query = query.filter(AssistantEvalExperimentItem.arm == arm)
    return query.order_by(AssistantEvalExperimentItem.arm.asc(),
                          AssistantEvalExperimentItem.created_at.asc()).all()


def delete_experiment(db: Session, experiment_id: str) -> bool:
    row = get_experiment(db, experiment_id)
    if row is None:
        return False
    if row.status in {"queued", "running"}:
        raise ServiceError("实验正在执行中，暂不能删除。")
    # 兜底清理：异常中断可能残留沙箱会话（评分快照已在正常路径删除）
    params = row.params or {}
    for conversation_id in params.get("sandbox_conversation_ids") or []:
        try:
            _cleanup_sandbox(db, conversation_id)
        except Exception:  # noqa: BLE001 — 清理失败不阻断删除
            logger.exception("残留沙箱会话清理失败：%s", conversation_id)
    db.delete(row)
    db.commit()
    return True
