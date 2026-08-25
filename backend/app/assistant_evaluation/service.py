"""评估任务服务 — 任务创建 / 后台执行 / 汇总 / 报告导出。

执行模型：任务落库后由守护线程异步执行（与平台 runtime/reflection 的线程
模式一致），前端轮询任务状态。单条会话的部分维度失败不影响整体报告。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.assistant_evaluation.adapters import get_adapters
from app.assistant_evaluation.dimensions import (
    BASE_DIMENSION_KEYS,
    DIMENSIONS,
    normalize,
    root_cause_of,
)
from app.assistant_evaluation.engine import build_engine, openjudge_available
from app.assistant_evaluation.models import AssistantEvalItem, AssistantEvalTask
from app.shared.database import SessionLocal

logger = logging.getLogger(__name__)

MAX_CONVERSATIONS_PER_TASK = 50


class ServiceError(ValueError):
    """对用户可读的业务错误。"""


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def create_task(db: Session, *, assistant_key: str, conversation_ids: list[str] | None,
                sample_size: int, sample_days: int, dimension_keys: list[str],
                model_config_id: str | None, created_by: str | None) -> AssistantEvalTask:
    adapter = get_adapters().get(assistant_key)
    if not adapter:
        raise ServiceError(f"未知的助手类型：{assistant_key}")

    mode = "manual"
    if not conversation_ids:
        mode = "sample"
        sample_size = max(1, min(int(sample_size or 10), 30))
        sample_days = max(1, min(int(sample_days or 30), 180))
        since = _as_utc(datetime.now(timezone.utc)) - timedelta(days=sample_days)
        _, refs = adapter.list_conversations(db, limit=200, offset=0)
        recent = [r for r in refs if r.created_at and _as_utc(r.created_at) >= since]
        if not recent:
            raise ServiceError(f"「{adapter.label}」在最近 {sample_days} 天内没有可评估的会话。")
        conversation_ids = [r.id for r in recent[:sample_size]]
    if not conversation_ids:
        raise ServiceError("请至少选择一条要评估的会话。")
    if len(conversation_ids) > MAX_CONVERSATIONS_PER_TASK:
        raise ServiceError(f"单次评估最多 {MAX_CONVERSATIONS_PER_TASK} 条会话，当前 {len(conversation_ids)} 条。")

    unknown = [k for k in dimension_keys if k not in DIMENSIONS]
    if unknown:
        raise ServiceError(f"未知评分维度：{', '.join(unknown)}")
    dimension_keys = list(dict.fromkeys(dimension_keys)) or list(BASE_DIMENSION_KEYS)
    llm_dims = [k for k in dimension_keys if DIMENSIONS[k].kind == "llm"]
    judge = _resolve_judge_model(db, model_config_id) if llm_dims else None

    task = AssistantEvalTask(
        assistant_key=assistant_key,
        title=f"{adapter.label} · {len(conversation_ids)} 条会话",
        status="queued",
        params={
            "mode": mode,
            "dimension_keys": dimension_keys,
            "conversation_ids": list(conversation_ids)[:MAX_CONVERSATIONS_PER_TASK],
            "engine": "openjudge" if openjudge_available() else "builtin",
        },
        judge_model_config_id=(str(judge.id) if judge is not None else None),
        judge_model_name=(getattr(judge, "name", "") or "") if judge is not None else "（仅代码型维度，无需 judge 模型）",
        conversation_count=len(conversation_ids),
        created_by=created_by,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    thread = threading.Thread(target=_run_task, args=(task.id,), daemon=True,
                              name=f"assistant-eval-{task.id[:8]}")
    thread.start()
    return task


def _resolve_judge_model(db: Session, model_config_id: str | None):
    from app.model_configs.models import ModelConfig
    from app.model_configs.selector import select_llm_model_config

    selected = select_llm_model_config(db, model_id=model_config_id or None)
    if selected is None:
        raise ServiceError("没有可用的 LLM 模型配置：请先到「模型配置」添加并启用一个 OpenAI 兼容模型作为 judge 模型。")
    return selected


def _run_task(task_id: str) -> None:
    started = time.monotonic()
    owns = SessionLocal()
    try:
        task = owns.query(AssistantEvalTask).filter(AssistantEvalTask.id == task_id).first()
        if task is None:
            return
        task.status = "running"
        owns.commit()

        engine = None
        if task.judge_model_config_id:
            from app.model_configs.models import ModelConfig

            config = (
                owns.query(ModelConfig)
                .filter(ModelConfig.id == task.judge_model_config_id)
                .first()
            )
            if config is None:
                raise ServiceError("judge 模型配置已不存在。")
            engine = build_engine(config)

        adapter = get_adapters()[task.assistant_key]
        conv_ids = list((task.params or {}).get("conversation_ids") or [])
        base_dims = list((task.params or {}).get("dimension_keys") or BASE_DIMENSION_KEYS)

        items: list[AssistantEvalItem] = []
        for index, conv_id in enumerate(conv_ids):
            trace = adapter.load_trace(owns, conv_id)
            item = AssistantEvalItem(
                task_id=task.id, conversation_id=conv_id,
                conversation_title=adapter.get_title(owns, conv_id) or conv_id[:8],
            )
            if trace is None:
                item.flags = {"engine_error": "会话内容为空或缺少完整的问答轮次"}
            else:
                dim_keys = _effective_dims(base_dims, trace)
                try:
                    raw_results = _evaluate_sync(engine, dim_keys, trace)
                    scores, reasons = {}, {}
                    for key in dim_keys:
                        dim = DIMENSIONS[key]
                        payload = raw_results.get(key) or {}
                        raw = payload.get("raw")
                        if raw is None:
                            reasons[key] = {"score": None, "reason": payload.get("reason") or "该维度未产出分数"}
                            continue
                        scores[key] = normalize(dim, raw)
                        reason_text = str(payload.get("reason") or "").strip()
                        reasons[key] = {"score": scores[key],
                                        "reason": f"{dim.label} {raw}/{dim.scale[1]}：{reason_text}".strip("：")}
                    flags = {
                        "loop_detected": bool(scores.get("action_loop") is not None and scores["action_loop"] < 80),
                        "tool_error_count": trace.tool_error_count,
                        "low_dims": [k for k, v in scores.items() if v < 60],
                    }
                    overall = _weighted_overall(dim_keys, scores)
                    item.scores = scores
                    item.reasons = reasons
                    item.flags = flags
                    item.overall_score = overall
                    item.root_cause = root_cause_of(scores, flags)
                except Exception as exc:  # 单会话失败不影响其余会话
                    logger.exception("评估单条会话失败：%s", conv_id)
                    item.flags = {"engine_error": str(exc)[:500]}
            owns.add(item)
            task.completed_conversations = index + 1
            owns.commit()
            items.append(item)

        task.status = "success"
        task.summary = _build_summary(items, engine_name=(engine.name if engine else "code-only"))
        task.finished_at = datetime.now(timezone.utc)
        task.duration_ms = int((time.monotonic() - started) * 1000)
        owns.commit()
    except Exception as exc:
        logger.exception("评估任务执行失败：%s", task_id)
        owns.rollback()
        task = owns.query(AssistantEvalTask).filter(AssistantEvalTask.id == task_id).first()
        if task is not None:
            task.status = "error"
            task.error = str(exc)[:2000]
            task.finished_at = datetime.now(timezone.utc)
            task.duration_ms = int((time.monotonic() - started) * 1000)
            owns.commit()
    finally:
        owns.close()


def _effective_dims(base_dims: list[str], trace) -> list[str]:
    """按会话实际数据裁剪维度：无工具步骤的会话跳过轨迹类维度。"""
    has_actions = len(trace.actions) >= 1
    result: list[str] = []
    for key in base_dims:
        if key not in DIMENSIONS:
            continue
        if key in {"trajectory", "action_loop"} and not has_actions:
            continue
        if key == "action_loop" and len(trace.actions) < 2:
            continue
        result.append(key)
    return result


def _evaluate_sync(engine, dim_keys: list[str], trace) -> dict:
    if engine is None:
        # 纯代码型维度的任务没有 judge 模型，直接本地确定性计算
        engine = _CodeOnlyEngine()
    return asyncio.run(engine.evaluate(dim_keys, trace))


class _CodeOnlyEngine:
    name = "code-only"

    async def evaluate(self, dim_keys: list[str], trace) -> dict:  # noqa: ANN001
        from app.assistant_evaluation.engine import detect_action_loop

        results = {}
        for key in dim_keys:
            results[key] = {"raw": detect_action_loop(trace.actions), "reason": ""}
        return results


def _weighted_overall(dim_keys: list[str], scores: dict[str, float]) -> float | None:
    weighted, total_weight = 0.0, 0.0
    for key in dim_keys:
        value = scores.get(key)
        if value is None:
            continue
        weight = DIMENSIONS[key].weight
        weighted += value * weight
        total_weight += weight
    return round(weighted / total_weight, 1) if total_weight else None


def _build_summary(items: list[AssistantEvalItem], engine_name: str) -> dict:
    scored = [i for i in items if i.scores]
    dimensions: dict[str, dict] = {}
    for key in DIMENSIONS:
        values = [float(i.scores[key]) for i in scored if key in i.scores]
        if values:
            dimensions[key] = {
                "label": DIMENSIONS[key].label,
                "avg": round(sum(values) / len(values), 1),
                "min": min(values),
                "max": max(values),
                "count": len(values),
            }
    overalls = [float(i.overall_score) for i in scored if i.overall_score is not None]
    badcases = sorted(
        [i for i in scored if (i.overall_score or 0) < 60],
        key=lambda x: x.overall_score or 0,
    )
    # failed 只统计真正执行出错（评分异常/引擎报错）的会话；
    # 无工具步骤导致维度不适用、内容缺完整问答轮次的记为 skipped。
    errored = [i for i in items if (i.flags or {}).get("engine_error")]
    skipped = len(items) - len(scored) - len(errored)
    return {
        "overall": round(sum(overalls) / len(overalls), 1) if overalls else None,
        "dimensions": dimensions,
        "badcase_conversation_ids": [i.conversation_id for i in badcases][:20],
        "evaluated": len(scored),
        "failed": len(errored),
        "skipped": skipped,
        "llm_calls": sum(
            1
            for i in items
            for k in (i.reasons or {})
            if DIMENSIONS.get(k) and DIMENSIONS[k].kind == "llm"
        ),
        "engine": engine_name,
    }


# ---------------------------------------------------------------- 查询 / 导出


def list_tasks(db: Session, assistant_key: str | None, limit: int = 20):
    query = db.query(AssistantEvalTask)
    if assistant_key:
        query = query.filter(AssistantEvalTask.assistant_key == assistant_key)
    return query.order_by(AssistantEvalTask.created_at.desc()).limit(min(limit, 100)).all()


def get_task(db: Session, task_id: str) -> AssistantEvalTask | None:
    return db.query(AssistantEvalTask).filter(AssistantEvalTask.id == task_id).first()


def task_items(db: Session, task_id: str) -> list[AssistantEvalItem]:
    return (
        db.query(AssistantEvalItem)
        .filter(AssistantEvalItem.task_id == task_id)
        .order_by(AssistantEvalItem.overall_score.asc().nullslast(), AssistantEvalItem.created_at.asc())
        .all()
    )


def export_markdown(task: AssistantEvalTask, items: list[AssistantEvalItem]) -> str:
    lines: list[str] = [
        f"# 助手质量报告 · {task.title}",
        "",
        f"- 任务状态：{task.status}",
        f"- 评估引擎：{(task.summary or {}).get('engine') or task.params.get('engine', '')}"
        f" · judge 模型：{task.judge_model_name or '-'}",
        f"- 综合得分：**{(task.summary or {}).get('overall') if task.summary else '-'}** / 100",
        f"- 会话数：{task.completed_conversations}/{task.conversation_count}"
        f" · 产出评分 {(task.summary or {}).get('evaluated', 0)}"
        f" · 执行失败 {(task.summary or {}).get('failed', 0)}"
        f" · 跳过（维度不适用）{(task.summary or {}).get('skipped', 0)}",
        "",
        "## 维度得分",
        "",
        "| 维度 | 平均分 | 最低 | 最高 | 样本数 |",
        "|---|---|---|---|---|",
    ]
    summary = task.summary or {}
    for key, stat in (summary.get("dimensions") or {}).items():
        lines.append(f"| {stat['label']} | {stat['avg']} | {stat['min']} | {stat['max']} | {stat['count']} |")
    lines += ["", "## 会话明细", ""]
    for item in items:
        lines.append(f"### {item.conversation_title or item.conversation_id[:8]}")
        lines.append("")
        overall = f"{item.overall_score}" if item.overall_score is not None else "未产出"
        lines.append(f"- 总分：{overall} · 根因：{item.root_cause or '-'}")
        flags = item.flags or {}
        note = []
        if flags.get("loop_detected"):
            note.append("检测到动作循环")
        if flags.get("tool_error_count"):
            note.append(f"{flags['tool_error_count']} 次工具调用失败")
        if note:
            lines.append(f"- 标记：{'、'.join(note)}")
        for key, reason in (item.reasons or {}).items():
            score = reason.get("score")
            text = reason.get("reason") or ""
            lines.append(f"- {DIMENSIONS[key].label if key in DIMENSIONS else key}：{'' if score is None else score}分 {text}")
        lines.append("")
    return "\n".join(lines)


def delete_task(db: Session, task_id: str) -> bool:
    task = get_task(db, task_id)
    if task is None:
        return False
    if task.status in {"queued", "running"}:
        raise ServiceError("任务正在执行中，暂不能删除。")
    db.delete(task)
    db.commit()
    return True
