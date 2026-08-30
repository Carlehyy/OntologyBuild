"""噪声地板校准服务 — 同一批会话重复评分，度量 judge 分数方差。

自动投产的门禁是"分数差显著大于噪声"，否则达标判定等于抛硬币。
校准任务与评估任务共用全局串行闸门（_TASK_GATE，配额保护）与
judge 解析通道；方差按"每会话组内总体标准差，再对会话求均值"聚
合到维度级，overall_noise 取各维度最大值（保守口径）。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.assistant_evaluation import service as task_service
from app.assistant_evaluation.adapters import get_adapters
from app.assistant_evaluation.benchmark_service import items_of
from app.assistant_evaluation.dimensions import BASE_DIMENSION_KEYS, DIMENSIONS
from app.assistant_evaluation.engine import build_engine, openjudge_available
from app.assistant_evaluation.models import (
    AssistantEvalBenchmarkSet,
    AssistantEvalCalibration,
)
from app.assistant_evaluation.service import ServiceError
from app.assistant_evaluation.timeline import (
    EVENT_CALIBRATION_CREATED,
    EVENT_CALIBRATION_FAILED,
    EVENT_CALIBRATION_SUCCEEDED,
    ACTOR_ADMIN,
    ACTOR_SYSTEM,
    record_event,
)
from app.shared.database import SessionLocal

logger = logging.getLogger(__name__)

CAL_MAX_CONVERSATIONS = 10
CAL_MIN_REPEATS = 2
CAL_MAX_REPEATS = 5


def _std(values: list[float]) -> float:
    """总体标准差（0-100 归一化分口径）；单次采样方差恒为 0。"""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return (sum((v - mean) ** 2 for v in values) / n) ** 0.5


def _start_worker(calibration_id: str) -> None:
    thread = threading.Thread(target=_run_calibration, args=(calibration_id,), daemon=True,
                              name=f"assistant-eval-cal-{calibration_id[:8]}")
    thread.start()


def create_calibration(db: Session, *, assistant_key: str,
                       conversation_ids: list[str] | None, benchmark_set_id: str | None,
                       repeats: int, dimension_keys: list[str], model_config_id: str | None,
                       created_by: str | None) -> AssistantEvalCalibration:
    adapter = get_adapters().get(assistant_key)
    if not adapter:
        raise ServiceError(f"未知的助手类型：{assistant_key}")

    if benchmark_set_id:
        bench = (
            db.query(AssistantEvalBenchmarkSet)
            .filter(AssistantEvalBenchmarkSet.id == benchmark_set_id)
            .first()
        )
        if bench is None:
            raise ServiceError("基准集不存在。")
        if bench.assistant_key != assistant_key:
            raise ServiceError("所选基准集与助手类型不一致。")
        conversation_ids = [item.conversation_id for item in items_of(db, benchmark_set_id)]

    ids = list(dict.fromkeys(str(c) for c in (conversation_ids or []) if str(c)))
    if not ids:
        raise ServiceError("请提供会话列表或选择基准集。")
    if len(ids) > CAL_MAX_CONVERSATIONS:
        raise ServiceError(f"噪声校准每次最多 {CAL_MAX_CONVERSATIONS} 条会话，当前 {len(ids)} 条。")

    unknown = [k for k in dimension_keys if k not in DIMENSIONS]
    if unknown:
        raise ServiceError(f"未知评分维度：{', '.join(unknown)}")
    dimension_keys = list(dict.fromkeys(dimension_keys)) or list(BASE_DIMENSION_KEYS)
    repeats = max(CAL_MIN_REPEATS, min(int(repeats or CAL_MIN_REPEATS), CAL_MAX_REPEATS))

    llm_dims = [k for k in dimension_keys if DIMENSIONS[k].kind == "llm"]
    judge = task_service._resolve_judge_model(db, model_config_id) if llm_dims else None

    row = AssistantEvalCalibration(
        assistant_key=assistant_key,
        status="queued",
        params={
            "conversation_ids": ids,
            "dimension_keys": list(dimension_keys),
            "repeats": repeats,
            "engine": "openjudge" if openjudge_available() else "builtin",
            **({"benchmark_set_id": benchmark_set_id} if benchmark_set_id else {}),
        },
        judge_model_config_id=(str(judge.id) if judge is not None else None),
        judge_model_name=(getattr(judge, "name", "") or "") if judge is not None
        else "（仅代码型维度，无需 judge 模型）",
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    record_event(db, event_type=EVENT_CALIBRATION_CREATED, assistant_key=assistant_key,
                 actor=ACTOR_ADMIN, actor_user_id=created_by,
                 ref_type="calibration", ref_id=row.id,
                 detail={"conversation_count": len(ids), "repeats": repeats,
                         "dimension_keys": list(dimension_keys),
                         **({"benchmark_set_id": benchmark_set_id} if benchmark_set_id else {})})
    db.commit()
    db.refresh(row)

    _start_worker(row.id)
    return row


def _run_calibration(calibration_id: str) -> None:
    """校准执行入口（守护线程上下文）：与评估任务共用全局闸门串行。"""
    with task_service._TASK_GATE:
        asyncio.run(_run_calibration_async(calibration_id))


async def _run_calibration_async(calibration_id: str) -> None:
    started = time.monotonic()
    owns = SessionLocal()
    try:
        row = (
            owns.query(AssistantEvalCalibration)
            .filter(AssistantEvalCalibration.id == calibration_id)
            .first()
        )
        if row is None:
            return
        row.status = "running"
        owns.commit()

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

        adapter = get_adapters()[row.assistant_key]
        params = row.params or {}
        conv_ids = list(params.get("conversation_ids") or [])
        base_dims = list(params.get("dimension_keys") or BASE_DIMENSION_KEYS)
        repeats = int(params.get("repeats") or CAL_MIN_REPEATS)
        dims_map = dict(DIMENSIONS)

        # dim -> 按会话分组的归一化分（每组 repeats 个值）
        per_dim_groups: dict[str, list[list[float]]] = {}
        scored_conversations = 0
        for conv_id in conv_ids:
            trace = adapter.load_trace(owns, conv_id)
            if trace is None:
                continue
            dim_keys = task_service._effective_dims(base_dims, trace)
            if not dim_keys:
                continue
            group_scores: dict[str, list[float]] = {}
            for _ in range(repeats):
                raw = await task_service._evaluate_async(engine, dim_keys, trace, None)
                scores, _reasons = task_service._assemble_scores(dim_keys, dims_map, raw)
                for key, value in scores.items():
                    group_scores.setdefault(key, []).append(float(value))
            if not group_scores:
                continue
            scored_conversations += 1
            for key, values in group_scores.items():
                per_dim_groups.setdefault(key, []).append(values)

        if scored_conversations == 0:
            raise ServiceError("没有会话产出评分，无法校准噪声地板。")

        per_dim = {
            key: {
                "noise": round(sum(_std(g) for g in groups) / len(groups), 1),
                "conversations": len(groups),
                "samples": sum(len(g) for g in groups),
            }
            for key, groups in per_dim_groups.items()
        }
        overall_noise = max((stat["noise"] for stat in per_dim.values()), default=0.0)
        row.status = "success"
        row.result = {
            "repeats": repeats,
            "per_dim": per_dim,
            "overall_noise": round(overall_noise, 1),
            "scored_conversations": scored_conversations,
        }
        row.finished_at = datetime.now(timezone.utc)
        row.duration_ms = int((time.monotonic() - started) * 1000)
        record_event(db=owns, event_type=EVENT_CALIBRATION_SUCCEEDED,
                     assistant_key=row.assistant_key, actor=ACTOR_SYSTEM,
                     ref_type="calibration", ref_id=row.id,
                     detail={"overall_noise": row.result["overall_noise"],
                             "repeats": repeats, "per_dim": per_dim})
        owns.commit()
    except Exception as exc:
        logger.exception("噪声校准执行失败：%s", calibration_id)
        owns.rollback()
        row = (
            owns.query(AssistantEvalCalibration)
            .filter(AssistantEvalCalibration.id == calibration_id)
            .first()
        )
        if row is not None:
            row.status = "error"
            row.error = str(exc)[:2000]
            row.finished_at = datetime.now(timezone.utc)
            row.duration_ms = int((time.monotonic() - started) * 1000)
            record_event(db=owns, event_type=EVENT_CALIBRATION_FAILED,
                         assistant_key=row.assistant_key, actor=ACTOR_SYSTEM,
                         ref_type="calibration", ref_id=row.id,
                         detail={"error": str(exc)[:500]})
            owns.commit()
    finally:
        owns.close()


def list_calibrations(db: Session, assistant_key: str | None = None,
                      limit: int = 20) -> list[AssistantEvalCalibration]:
    query = db.query(AssistantEvalCalibration)
    if assistant_key:
        query = query.filter(AssistantEvalCalibration.assistant_key == assistant_key)
    return query.order_by(AssistantEvalCalibration.created_at.desc()).limit(min(limit, 100)).all()


def get_calibration(db: Session, calibration_id: str) -> AssistantEvalCalibration | None:
    return (
        db.query(AssistantEvalCalibration)
        .filter(AssistantEvalCalibration.id == calibration_id)
        .first()
    )


def delete_calibration(db: Session, calibration_id: str) -> bool:
    row = get_calibration(db, calibration_id)
    if row is None:
        return False
    if row.status in {"queued", "running"}:
        raise ServiceError("校准任务正在执行中，暂不能删除。")
    db.delete(row)
    db.commit()
    return True
