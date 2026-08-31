"""值守循环 — 无人值守的自优化飞轮执行引擎（NATS 消费侧调用）。

每轮：投产後看守（劣化即自动回退）→ 采样评估新会话 → 新坏例并入基准集
→ LLM 生成 prompt_patch 提案 → 双臂沙箱实验 → 门禁通过且周预算未耗尽
→ 自动投产。连续失败 3 轮熔断（suspended）并 inbox 告警，等待人工介入。

防重入跨进程可见：开跑前查 DB 中该助手是否已有 queued/running 的评估
任务或实验（JetStream Msg-Id 去重 + DB 状态检查双保险）。
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.assistant_evaluation import apply_service
from app.assistant_evaluation import benchmark_service
from app.assistant_evaluation import experiment_service
from app.assistant_evaluation import service as task_service
from app.assistant_evaluation.benchmark_service import MAX_ITEMS_PER_SET
from app.assistant_evaluation.dimensions import BASE_DIMENSION_KEYS, DIMENSIONS
from app.assistant_evaluation.experiment_service import (
    MAX_BENCHMARK_ITEMS_PER_EXPERIMENT,
)
from app.assistant_evaluation.models import (
    AssistantEvalAutopilotConfig,
    AssistantEvalBenchmarkSet,
    AssistantEvalExperiment,
    AssistantEvalProfileVersion,
    AssistantEvalTask,
)
from app.assistant_evaluation.service import ServiceError
from app.assistant_evaluation.timeline import (
    EVENT_CYCLE_FAILED,
    EVENT_CYCLE_SKIPPED,
    EVENT_CYCLE_STARTED,
    EVENT_CYCLE_SUCCEEDED,
    ACTOR_ADMIN,
    ACTOR_AUTOPILOT,
    ACTOR_SYSTEM,
    record_event,
)
from app.assistant_evaluation.notify import notify_admins
from app.shared.database import SessionLocal

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 3
CYCLE_WAIT_TIMEOUT_SECONDS = 1800
PROPOSAL_BADCASE_LIMIT = 5
_RUN_AT_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

ASSISTANT_KEY = "ontology_agent"


# ---------------------------------------------------------------- 配置


def get_config(db: Session, ontology_id: str) -> AssistantEvalAutopilotConfig | None:
    return (
        db.query(AssistantEvalAutopilotConfig)
        .filter(AssistantEvalAutopilotConfig.ontology_id == ontology_id)
        .first()
    )


def save_config(db: Session, *, ontology_id: str, enabled: bool, run_at: str,
                benchmark_set_id: str | None, dimension_keys: list[str],
                model_config_id: str | None, threshold: float,
                max_applies_per_week: int, sample_days: int,
                actor_user_id: str | None) -> AssistantEvalAutopilotConfig:
    if not _RUN_AT_PATTERN.match((run_at or "").strip()):
        raise ServiceError("触发时间格式必须为 HH:MM（00:00-23:59）。")
    unknown = [k for k in dimension_keys if k not in DIMENSIONS]
    if unknown:
        raise ServiceError(f"未知评分维度：{', '.join(unknown)}")
    if benchmark_set_id:
        bench = (
            db.query(AssistantEvalBenchmarkSet)
            .filter(AssistantEvalBenchmarkSet.id == benchmark_set_id)
            .first()
        )
        if bench is None:
            raise ServiceError("基准集不存在。")
        if bench.assistant_key != ASSISTANT_KEY or bench.ontology_id != ontology_id:
            raise ServiceError("基准集必须属于该本体的本体助手。")
        if not any(i.split == "heldout" for i in benchmark_service.items_of(db, bench.id)):
            raise ServiceError("基准集缺少留出集条目，值守循环无法执行门禁。")
    if enabled and not benchmark_set_id:
        raise ServiceError("开启值守前必须绑定一个基准集（含留出集条目）。")

    row = get_config(db, ontology_id)
    if row is None:
        row = AssistantEvalAutopilotConfig(
            ontology_id=ontology_id, created_by=actor_user_id)
        db.add(row)
    row.enabled = bool(enabled)
    row.run_at = (run_at or "03:00").strip()
    row.benchmark_set_id = benchmark_set_id or None
    row.dimension_keys = list(dict.fromkeys(dimension_keys)) or list(BASE_DIMENSION_KEYS)
    row.model_config_id = model_config_id or None
    try:
        row.threshold = max(0.0, float(threshold) if threshold is not None else 5.0)
        row.max_applies_per_week = max(1, min(int(max_applies_per_week or 3), 7))
        row.sample_days = max(1, min(int(sample_days or 14), 90))
    except (TypeError, ValueError):
        raise ServiceError("阈值 / 预算 / 采样窗口必须是数字。")
    # 人工保存即视为介入：解除熔断并清零失败计数
    row.suspended = False
    row.suspend_reason = ""
    row.consecutive_failures = 0
    db.commit()
    db.refresh(row)
    return row


def parse_run_at(run_at: str) -> tuple[int, int]:
    match = _RUN_AT_PATTERN.match((run_at or "").strip())
    if not match:
        return 3, 0
    return int(match.group(1)), int(match.group(2))


def is_due(config: AssistantEvalAutopilotConfig, now_local) -> bool:
    """今日时段已到且尚未派发（last_dispatched_at 未覆盖今日时段）。"""
    if not config.enabled or config.suspended:
        return False
    hour, minute = parse_run_at(config.run_at)
    slot = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now_local < slot:
        return False
    slot_utc = slot.astimezone(timezone.utc)
    last = config.last_dispatched_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return last < slot_utc


# ---------------------------------------------------------------- 循环


def _wait_terminal(db: Session, model, row_id: str, timeout: int = CYCLE_WAIT_TIMEOUT_SECONDS):
    """轮询任务/实验终态（子任务在各自线程/闸门内执行，跨进程可见）。"""
    deadline = time.monotonic() + timeout
    row = None
    while time.monotonic() < deadline:
        db.expire_all()
        row = db.query(model).filter(model.id == row_id).first()
        if row is None or row.status in {"success", "error"}:
            return row
        time.sleep(2)
    return row


def _has_inflight_work(db: Session) -> bool:
    """跨进程防重入：该助手存在排队/执行中的评估任务或双臂实验。"""
    busy_tasks = (
        db.query(AssistantEvalTask)
        .filter(AssistantEvalTask.assistant_key == ASSISTANT_KEY,
                AssistantEvalTask.status.in_(("queued", "running")))
        .count()
    )
    busy_experiments = (
        db.query(AssistantEvalExperiment)
        .filter(AssistantEvalExperiment.status.in_(("queued", "running")))
        .count()
    )
    return bool(busy_tasks or busy_experiments)


def _generate_prompt_patch(db: Session, config: AssistantEvalAutopilotConfig,
                           task: AssistantEvalTask) -> dict:
    """LLM 基于坏例生成 system_prompt_extra 改进稿（复用 judge 网关通道）。"""
    from app.assistant_evaluation import engine as eng
    from app.model_configs.selector import llm_call_kwargs

    judge = task_service._resolve_judge_model(db, config.model_config_id)
    kwargs = llm_call_kwargs(judge)

    items = [
        item for item in task_service.task_items(db, task.id)
        if item.conversation_id in set(
            (task.summary or {}).get("badcase_conversation_ids") or [])
    ][:PROPOSAL_BADCASE_LIMIT]
    badcases = [{
        "title": item.conversation_title,
        "root_cause": item.root_cause,
        "low_dims": (item.flags or {}).get("low_dims") or [],
        "reasons": {k: (v or {}).get("reason", "")
                    for k, v in (item.reasons or {}).items()
                    if k in ((item.flags or {}).get("low_dims") or [])},
    } for item in items]

    from app.ontologies.agent_runtime.boundary import get_or_create_profile

    profile = get_or_create_profile(db, config.ontology_id)
    system = (
        "你是本体助手的提示词优化器。基于评估坏例，为本体的 system_prompt_extra "
        "产出一个改进后的完整版本：保持原有约束，针对坏例根因补充明确规则。"
        '只输出 JSON：{"rationale": "一句话说明改了什么、为什么", '
        '"system_prompt_extra": "改进后的完整提示词补充段"}'
    )
    user = json.dumps({
        "current_system_prompt_extra": profile.system_prompt_extra or "",
        "dimension_stats": (task.summary or {}).get("dimensions") or {},
        "insights": (task.summary or {}).get("insights") or {},
        "badcases": badcases,
    }, ensure_ascii=False, default=str)

    content = eng._gateway_judge(kwargs, system, user)
    parsed = eng._parse_judge_json(content)
    if not parsed or not str(parsed.get("system_prompt_extra") or "").strip():
        raise ServiceError("提案生成失败：LLM 未给出有效的 system_prompt_extra。")
    return {
        "rationale": str(parsed.get("rationale") or "基于坏例的提示词改进"),
        "system_prompt_extra": str(parsed["system_prompt_extra"]).strip(),
    }


def _finish(db: Session, config: AssistantEvalAutopilotConfig, status: str,
            detail: dict) -> dict:
    config.last_cycle_at = datetime.now(timezone.utc)
    config.last_cycle_status = status
    event_type = {
        "error": EVENT_CYCLE_FAILED,
        "skipped_busy": EVENT_CYCLE_SKIPPED, "skipped_no_badcase": EVENT_CYCLE_SKIPPED,
        "skipped_budget": EVENT_CYCLE_SKIPPED, "skipped_disabled": EVENT_CYCLE_SKIPPED,
    }.get(status, EVENT_CYCLE_SUCCEEDED)
    record_event(db, event_type=event_type, assistant_key=ASSISTANT_KEY,
                 actor=ACTOR_AUTOPILOT, ref_type="autopilot_config", ref_id=config.id,
                 detail={"ontology_id": config.ontology_id, "status": status, **detail})
    db.commit()
    return {"status": status, **detail}


def run_cycle(config_id: str) -> dict:
    """值守一轮（NATS 消费线程上下文）：任何步骤异常计入连续失败并熔断。"""
    db = SessionLocal()
    try:
        config = (
            db.query(AssistantEvalAutopilotConfig)
            .filter(AssistantEvalAutopilotConfig.id == config_id)
            .first()
        )
        if config is None:
            return {"status": "missing"}
        if not config.enabled:
            return _finish(db, config, "skipped_disabled", {"reason": "开关已关闭"})
        if config.suspended:
            return _finish(db, config, "skipped_disabled",
                           {"reason": f"已熔断：{config.suspend_reason}"})
        record_event(db, event_type=EVENT_CYCLE_STARTED, assistant_key=ASSISTANT_KEY,
                     actor=ACTOR_AUTOPILOT, ref_type="autopilot_config",
                     ref_id=config.id, detail={"ontology_id": config.ontology_id})
        db.commit()

        if _has_inflight_work(db):
            return _finish(db, config, "skipped_busy",
                           {"reason": "存在执行中的评估任务或实验"})

        # ① 投产後看守：劣化即自动回退，本轮到此为止
        engine = None
        if config.model_config_id:
            from app.model_configs.models import ModelConfig
            from app.assistant_evaluation.engine import build_engine

            judge_config = (
                db.query(ModelConfig)
                .filter(ModelConfig.id == config.model_config_id)
                .first()
            )
            if judge_config is not None:
                engine = build_engine(judge_config)
        watch = apply_service.watch_latest(
            db, config.ontology_id,
            dimension_keys=config.dimension_keys or None, engine=engine)
        if watch == "rolled_back":
            config.consecutive_failures = 0
            return _finish(db, config, "rolled_back",
                           {"reason": "投产后劣化，已自动回退上一版本"})

        # ② 采样评估近期新会话（留完整任务记录，全程可审计）
        task = task_service.create_task(
            db, assistant_key=ASSISTANT_KEY, conversation_ids=None,
            sample_size=10, sample_days=config.sample_days,
            dimension_keys=list(config.dimension_keys or BASE_DIMENSION_KEYS),
            model_config_id=config.model_config_id, rubric_id=None,
            created_by=config.created_by, purpose="autopilot")
        task = _wait_terminal(db, AssistantEvalTask, task.id)
        if task is None or task.status != "success":
            raise ServiceError(
                f"采样评估失败：{(task.error if task is not None else '任务丢失') or '未知错误'}")
        summary = task.summary or {}
        badcases = list(summary.get("badcase_conversation_ids") or [])
        if not badcases:
            config.consecutive_failures = 0
            return _finish(db, config, "skipped_no_badcase",
                           {"task_id": task.id, "reason": "采样窗口内无坏例"})

        # ③ 新坏例并入基准集（保持基准新鲜；超出实验回放上限则本轮不扩）
        existing = {i.conversation_id
                    for i in benchmark_service.items_of(db, config.benchmark_set_id)}
        fresh = [cid for cid in badcases if cid not in existing]
        if fresh:
            # 采样任务混采多本体（助手级评估），基准集按本体隔离：
            # 只归并属于值守本体的坏例，其余静默跳过（2026-08-30 真实 E2E 缺陷）
            ownership = benchmark_service.conversation_ontology_map(db, fresh)
            fresh = [cid for cid in fresh
                     if ownership.get(cid) == config.ontology_id]
        folded = 0
        if fresh and len(existing) + len(fresh) <= min(
                MAX_ITEMS_PER_SET, MAX_BENCHMARK_ITEMS_PER_EXPERIMENT):
            benchmark_service.add_items(
                db, config.benchmark_set_id,
                entries=[{"conversation_id": cid, "origin": "badcase"} for cid in fresh],
                actor_user_id=None, actor=ACTOR_SYSTEM)
            folded = len(fresh)

        # ④ LLM 基于坏例生成 prompt_patch 提案
        patch = _generate_prompt_patch(db, config, task)
        proposal = experiment_service.create_proposal(
            db, ontology_id=config.ontology_id, type="prompt_patch",
            title=f"值守自动提案 · {datetime.now(timezone.utc):%m-%d}",
            rationale=patch["rationale"],
            payload={"system_prompt_extra": patch["system_prompt_extra"]},
            evidence={"task_id": task.id, "badcase_conversation_ids": badcases[:10],
                      "insights": summary.get("insights") or {}},
            created_by=config.created_by, actor=ACTOR_AUTOPILOT)

        # ⑤ 双臂沙箱实验（留出集门禁 + 噪声地板）
        experiment = experiment_service.create_experiment(
            db, proposal_id=proposal.id, benchmark_set_id=config.benchmark_set_id,
            dimension_keys=list(config.dimension_keys or BASE_DIMENSION_KEYS),
            threshold=config.threshold, model_config_id=config.model_config_id,
            created_by=config.created_by, purpose="autopilot")
        experiment = _wait_terminal(db, AssistantEvalExperiment, experiment.id)
        if experiment is None or experiment.status != "success":
            raise ServiceError(
                f"双臂实验失败：{(experiment.error if experiment is not None else '任务丢失') or '未知错误'}")
        gate = (experiment.result or {}).get("gate") or {}

        # ⑥ 门禁通过 → 周预算检查 → 自动投产
        if not gate.get("passed"):
            config.consecutive_failures = 0
            return _finish(db, config, "success", {
                "task_id": task.id, "proposal_id": proposal.id,
                "experiment_id": experiment.id, "applied": False,
                "reason": f"门禁未通过（heldout delta {gate.get('heldout_delta')} "
                          f"< 阈值 {gate.get('effective_threshold')}）",
                "badcases_folded": folded,
            })
        if apply_service.applies_last_week(db, config.ontology_id) >= config.max_applies_per_week:
            notify_admins(db, kind="notice",
                          title="值守循环预算耗尽，本轮不投产",
                          summary=f"本体 {config.ontology_id[:8]} 近 7 天自动投产已达 "
                                  f"{config.max_applies_per_week} 次上限，"
                                  f"通过门禁的提案暂缓（{proposal.id[:8]}）。",
                          correlation_key=f"budget:{config.id}",
                          safe_context={"ontology_id": config.ontology_id,
                                        "proposal_id": proposal.id},
                          weekly_dedupe=True)
            config.consecutive_failures = 0
            return _finish(db, config, "skipped_budget", {
                "task_id": task.id, "proposal_id": proposal.id, "applied": False,
                "reason": f"近 7 天自动投产已达 {config.max_applies_per_week} 次上限"})

        version = apply_service.apply_proposal(
            db, proposal_id=proposal.id, trigger="autopilot", actor_user_id=None)
        config.consecutive_failures = 0
        return _finish(db, config, "success", {
            "task_id": task.id, "proposal_id": proposal.id,
            "experiment_id": experiment.id, "version": version.version,
            "applied": True, "badcases_folded": folded})
    except Exception as exc:
        logger.exception("值守循环执行失败：%s", config_id)
        try:
            db.rollback()
            config = (
                db.query(AssistantEvalAutopilotConfig)
                .filter(AssistantEvalAutopilotConfig.id == config_id)
                .first()
            )
            if config is not None:
                config.consecutive_failures = (config.consecutive_failures or 0) + 1
                detail = {"error": str(exc)[:500]}
                if config.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    config.suspended = True
                    config.suspend_reason = f"连续失败 {config.consecutive_failures} 轮：{str(exc)[:200]}"
                    notify_admins(db, kind="alert",
                                  title="助手值守循环已熔断，需人工介入",
                                  summary=f"本体 {config.ontology_id[:8]} 的值守循环"
                                          f"连续失败 {config.consecutive_failures} 轮，已自动暂停。"
                                          f"最后错误：{str(exc)[:200]}",
                                  correlation_key=f"suspend:{config.id}",
                                  safe_context={"ontology_id": config.ontology_id})
                    detail["suspended"] = True
                return _finish(db, config, "error", detail)
        except Exception:  # noqa: BLE001 — 收尾失败不掩盖原始异常
            logger.exception("值守循环收尾失败：%s", config_id)
        return {"status": "error", "error": str(exc)[:500]}
    finally:
        db.close()
