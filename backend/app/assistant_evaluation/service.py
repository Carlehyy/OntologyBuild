"""评估任务服务 — 任务创建 / 后台执行 / 汇总 / 报告导出 / rubric 管理 / 恢复。

执行模型：任务落库后由守护线程异步执行（与平台 runtime/reflection 的线程
模式一致），前端轮询任务状态。任务内会话并发执行（TASK_CONCURRENCY=3），
单条会话的部分维度失败不影响整体报告。进程重启后 queued 任务自动重排、
running 任务标记中断（bootstrap 启动钩子调用 recover_interrupted_tasks）。
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
    RUBRIC_DIM_KEY,
    normalize,
    rubric_dimension,
    structured_root_cause,
)
from app.assistant_evaluation.engine import (
    build_engine,
    generate_rubrics,
    openjudge_available,
)
from app.assistant_evaluation.models import (
    AssistantEvalItem,
    AssistantEvalRubric,
    AssistantEvalTask,
)
from app.assistant_evaluation.timeline import (
    EVENT_TASK_CREATED,
    EVENT_TASK_FAILED,
    EVENT_TASK_SUCCEEDED,
    ACTOR_ADMIN,
    ACTOR_SYSTEM,
    record_event,
)
from app.shared.database import SessionLocal

logger = logging.getLogger(__name__)

MAX_CONVERSATIONS_PER_TASK = 50
# P0-4（MYW-79 生产实测）：任务间并行 × 会话并发曾把 judge 配额打爆（429，
# 44 条会话只评成 9 条）——任务间经 _TASK_GATE 串行，会话并发降为 2。
TASK_CONCURRENCY = 2
_TASK_GATE = threading.Lock()


class ServiceError(ValueError):
    """对用户可读的业务错误。"""


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _start_worker(task_id: str) -> None:
    thread = threading.Thread(target=_run_task, args=(task_id,), daemon=True,
                              name=f"assistant-eval-{task_id[:8]}")
    thread.start()


def create_task(db: Session, *, assistant_key: str, conversation_ids: list[str] | None,
                sample_size: int, sample_days: int, dimension_keys: list[str],
                model_config_id: str | None, rubric_id: str | None,
                created_by: str | None) -> AssistantEvalTask:
    adapter = get_adapters().get(assistant_key)
    if not adapter:
        raise ServiceError(f"未知的助手类型：{assistant_key}")

    mode = "manual"
    if not conversation_ids:
        mode = "sample"
        sample_size = max(1, min(int(sample_size or 10), 30))
        sample_days = max(1, min(int(sample_days or 30), 180))
        since = _as_utc(datetime.now(timezone.utc)) - timedelta(days=sample_days)
        # SQL 层窗口过滤，直接取窗口内最近 sample_size 条（消除"仅扫最近200条"偏差）
        _, refs = adapter.list_conversations(db, limit=sample_size, offset=0, since=since)
        conversation_ids = [r.id for r in refs]
        if not conversation_ids:
            raise ServiceError(f"「{adapter.label}」在最近 {sample_days} 天内没有可评估的会话。")
    if len(conversation_ids) > MAX_CONVERSATIONS_PER_TASK:
        raise ServiceError(f"单次评估最多 {MAX_CONVERSATIONS_PER_TASK} 条会话，当前 {len(conversation_ids)} 条。")

    unknown = [k for k in dimension_keys if k not in DIMENSIONS and k != RUBRIC_DIM_KEY]
    if unknown:
        raise ServiceError(f"未知评分维度：{', '.join(unknown)}")
    dimension_keys = list(dict.fromkeys(dimension_keys)) or list(BASE_DIMENSION_KEYS)

    rubric = None
    if rubric_id:
        rubric_row = (
            db.query(AssistantEvalRubric)
            .filter(AssistantEvalRubric.id == rubric_id)
            .first()
        )
        if rubric_row is None:
            raise ServiceError("所选评分标准不存在或已被删除。")
        rubric = {"name": rubric_row.name, "rubrics": rubric_row.rubrics,
                  "min_score": rubric_row.min_score, "max_score": rubric_row.max_score}
        if RUBRIC_DIM_KEY not in dimension_keys:
            dimension_keys = list(dimension_keys) + [RUBRIC_DIM_KEY]

    llm_dims = [k for k in dimension_keys
                if DIMENSIONS.get(k) and DIMENSIONS[k].kind == "llm"]
    needs_judge = bool(llm_dims) or rubric is not None
    judge = _resolve_judge_model(db, model_config_id) if needs_judge else None

    task = AssistantEvalTask(
        assistant_key=assistant_key,
        title=f"{adapter.label} · {len(conversation_ids)} 条会话",
        status="queued",
        params={
            "mode": mode,
            "dimension_keys": list(dimension_keys),
            "conversation_ids": list(conversation_ids),
            "engine": "openjudge" if openjudge_available() else "builtin",
            **({"rubric": rubric} if rubric else {}),
        },
        judge_model_config_id=(str(judge.id) if judge is not None else None),
        judge_model_name=(getattr(judge, "name", "") or "") if judge is not None
        else "（仅代码型维度，无需 judge 模型）",
        conversation_count=len(conversation_ids),
        created_by=created_by,
    )
    db.add(task)
    db.flush()
    record_event(db, event_type=EVENT_TASK_CREATED, assistant_key=assistant_key,
                 actor=ACTOR_ADMIN, actor_user_id=created_by,
                 ref_type="task", ref_id=task.id,
                 detail={"title": task.title, "mode": mode,
                         "conversation_count": len(conversation_ids),
                         "judge_model_name": task.judge_model_name})
    db.commit()
    db.refresh(task)

    _start_worker(task.id)
    return task


def _resolve_judge_model(db: Session, model_config_id: str | None):
    from app.model_configs.models import ModelConfig
    from app.model_configs.selector import select_llm_model_config

    selected = select_llm_model_config(db, model_id=model_config_id or None)
    if selected is None:
        raise ServiceError("没有可用的 LLM 模型配置：请先到「模型配置」添加并启用一个 OpenAI 兼容模型作为 judge 模型。")
    return selected


def _run_task(task_id: str) -> None:
    """任务执行入口（守护线程上下文）：全局闸门内串行执行，内联事件循环调度。"""
    with _TASK_GATE:
        asyncio.run(_run_task_async(task_id))


async def _run_task_async(task_id: str) -> None:
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
        params = task.params or {}
        conv_ids = list(params.get("conversation_ids") or [])
        base_dims = list(params.get("dimension_keys") or BASE_DIMENSION_KEYS)
        rubric = params.get("rubric")

        dims_map = dict(DIMENSIONS)
        if rubric:
            dims_map[RUBRIC_DIM_KEY] = rubric_dimension(
                rubric["name"], rubric["min_score"], rubric["max_score"]
            )

        sem = asyncio.Semaphore(TASK_CONCURRENCY)

        async def _process(conv_id: str):
            async with sem:
                # 数据库操作均为同步代码，事件循环内串行执行，会话共享安全
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
                        raw_results = await _evaluate_async(engine, dim_keys, trace, rubric)
                        scores, reasons = _assemble_scores(dim_keys, dims_map, raw_results)
                        flags = {
                            "loop_detected": bool(scores.get("action_loop") is not None
                                                  and scores["action_loop"] < 80),
                            "tool_error_count": trace.tool_error_count,
                            "low_dims": [k for k, v in scores.items() if v < 60],
                        }
                        item.scores = scores
                        item.reasons = reasons
                        item.flags = flags
                        item.overall_score = _weighted_overall(dim_keys, scores, dims_map)
                        attribution = structured_root_cause(scores, flags)
                        item.root_cause = attribution["summary"]
                        item.attribution = attribution
                    except Exception as exc:  # 单会话失败不影响其余会话
                        logger.exception("评估单条会话失败：%s", conv_id)
                        item.flags = {"engine_error": str(exc)[:500]}
                owns.add(item)
                task.completed_conversations = (task.completed_conversations or 0) + 1
                owns.commit()
                return item

        items = await asyncio.gather(*[_process(c) for c in conv_ids])

        task.status = "success"
        task.summary = _build_summary(items, engine_name=(engine.name if engine else "code-only"),
                                      rubric=rubric)
        task.finished_at = datetime.now(timezone.utc)
        task.duration_ms = int((time.monotonic() - started) * 1000)
        summary = task.summary or {}
        record_event(db=owns, event_type=EVENT_TASK_SUCCEEDED,
                     assistant_key=task.assistant_key, actor=ACTOR_SYSTEM,
                     ref_type="task", ref_id=task.id,
                     detail={"title": task.title, "overall": summary.get("overall"),
                             "evaluated": summary.get("evaluated"),
                             "failed": summary.get("failed"),
                             "badcases": len(summary.get("badcase_conversation_ids") or []),
                             "insights": summary.get("insights") or {}})
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
            record_event(db=owns, event_type=EVENT_TASK_FAILED,
                         assistant_key=task.assistant_key, actor=ACTOR_SYSTEM,
                         ref_type="task", ref_id=task.id,
                         detail={"title": task.title, "error": str(exc)[:500]})
            owns.commit()
    finally:
        owns.close()


def _effective_dims(base_dims: list[str], trace) -> list[str]:
    """按会话实际数据裁剪维度：无工具步骤的会话跳过轨迹类维度。"""
    has_actions = len(trace.actions) >= 1
    result: list[str] = []
    for key in base_dims:
        if key not in DIMENSIONS and key != RUBRIC_DIM_KEY:
            continue
        if key in {"trajectory", "tool_call_success", "action_loop"} and not has_actions:
            continue
        if key == "action_loop" and len(trace.actions) < 2:
            continue
        result.append(key)
    return result


async def _evaluate_async(engine, dim_keys: list[str], trace, rubric: dict | None) -> dict:
    if engine is None:
        # 纯代码型维度的任务没有 judge 模型，直接本地确定性计算
        engine = _CodeOnlyEngine()
    return await engine.evaluate(dim_keys, trace, rubric=rubric)


class _CodeOnlyEngine:
    name = "code-only"

    async def evaluate(self, dim_keys: list[str], trace, rubric: dict | None = None) -> dict:
        from app.assistant_evaluation import engine as eng

        results: dict = {}
        for key in dim_keys:
            if key == "action_loop":
                results[key] = {"raw": eng.detect_action_loop(trace.actions), "reason": ""}
            elif key == "response_repetition":
                results[key] = {"raw": eng.detect_ngram_repetition(trace.response),
                                "reason": ""}
        return results


def _assemble_scores(dim_keys: list[str], dims_map: dict, raw_results: dict):
    """把引擎原始分归一化并组装 scores/reasons（含 rubric 维度）。"""
    scores: dict = {}
    reasons: dict = {}
    for key in dim_keys:
        dim = dims_map.get(key)
        if dim is None:
            continue
        payload = raw_results.get(key) or {}
        raw = payload.get("raw")
        if raw is None:
            reasons[key] = {"score": None,
                            "reason": payload.get("reason") or "该维度未产出分数"}
            continue
        scores[key] = normalize(dim, raw)
        reason_text = str(payload.get("reason") or "").strip()
        reasons[key] = {"score": scores[key],
                        "reason": f"{dim.label} {raw}/{dim.scale[1]}：{reason_text}".strip("：")}
    return scores, reasons


def _weighted_overall(dim_keys: list[str], scores: dict, dims_map: dict) -> float | None:
    weighted, total_weight = 0.0, 0.0
    for key in dim_keys:
        value = scores.get(key)
        if value is None:
            continue
        weight = dims_map[key].weight
        weighted += value * weight
        total_weight += weight
    return round(weighted / total_weight, 1) if total_weight else None


def _build_insights(scored: list[AssistantEvalItem]) -> dict:
    """汇总结构化归因：类别分布 + 建议杠杆排序（M2 提案生成的直接输入）。"""
    by_category: dict[str, dict] = {}
    lever_counts: dict[str, int] = {}
    for item in scored:
        attribution = item.attribution or {}
        category = attribution.get("category")
        if not category or category == "good":
            continue
        slot = by_category.setdefault(
            category, {"count": 0, "conversation_ids": [], "levers": []})
        slot["count"] += 1
        if len(slot["conversation_ids"]) < 20:
            slot["conversation_ids"].append(item.conversation_id)
        levers = list(attribution.get("levers") or [])
        slot["levers"] = sorted(set(slot["levers"]) | set(levers))
        for lever in levers:
            lever_counts[lever] = lever_counts.get(lever, 0) + 1
    suggested = [lever for lever, _ in
                 sorted(lever_counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"by_category": by_category, "suggested_levers": suggested}


def _build_summary(items: list[AssistantEvalItem], engine_name: str,
                   rubric: dict | None = None) -> dict:
    scored = [i for i in items if i.scores]
    dims_map = dict(DIMENSIONS)
    if rubric:
        dims_map[RUBRIC_DIM_KEY] = rubric_dimension(
            rubric["name"], rubric["min_score"], rubric["max_score"]
        )
    dimensions: dict[str, dict] = {}
    stat_keys = list(DIMENSIONS.keys()) + ([RUBRIC_DIM_KEY] if rubric else [])
    for key in stat_keys:
        values = [float(i.scores[key]) for i in scored if key in i.scores]
        if values:
            dimensions[key] = {
                "label": dims_map[key].label,
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
            if dims_map.get(k) and dims_map[k].kind == "llm"
        ),
        "engine": engine_name,
        "insights": _build_insights(scored),
    }


# ---------------------------------------------------------------- 查询 / 导出 / rubric / 恢复


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


def trend(db: Session, assistant_key: str, limit: int = 12) -> list[AssistantEvalTask]:
    """同助手历次成功任务（时间升序，供前端趋势图与上次对比）。"""
    return (
        db.query(AssistantEvalTask)
        .filter(AssistantEvalTask.assistant_key == assistant_key,
                AssistantEvalTask.status == "success")
        .order_by(AssistantEvalTask.created_at.asc())
        .limit(min(max(1, int(limit or 12)), 24))
        .all()
    )


def load_item_trace(db: Session, task: AssistantEvalTask,
                    item: AssistantEvalItem) -> dict | None:
    """会话归一化轨迹（下钻用，只读）：query/response/OpenAI 消息/工具动作。"""
    adapter = get_adapters().get(task.assistant_key)
    if adapter is None:
        return None
    trace = adapter.load_trace(db, item.conversation_id)
    if trace is None:
        return None
    return {
        "conversation_id": item.conversation_id,
        "conversation_title": item.conversation_title,
        "query": trace.query,
        "response": trace.response,
        "openai_messages": trace.openai_messages,
        "actions": trace.actions,
        "tool_error_count": trace.tool_error_count,
    }


def _dims_map_for(task: AssistantEvalTask) -> dict:
    dims_map = dict(DIMENSIONS)
    rubric = (task.params or {}).get("rubric")
    if rubric:
        dims_map[RUBRIC_DIM_KEY] = rubric_dimension(
            rubric["name"], rubric["min_score"], rubric["max_score"]
        )
    return dims_map


def export_markdown(task: AssistantEvalTask, items: list[AssistantEvalItem]) -> str:
    params = task.params or {}
    rubric = params.get("rubric")
    dims_map = _dims_map_for(task)
    lines: list[str] = [
        f"# 助手质量报告 · {task.title}",
        "",
        f"- 任务状态：{task.status}",
        f"- 评估引擎：{(task.summary or {}).get('engine') or params.get('engine', '')}"
        f" · judge 模型：{task.judge_model_name or '-'}",
    ]
    if rubric:
        lines.append(f"- 自定义评分标准：{rubric.get('name') or '-'}"
                     f"（{rubric.get('min_score')}-{rubric.get('max_score')} 分制）")
    lines += [
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
            label = dims_map[key].label if key in dims_map else key
            lines.append(f"- {label}：{'' if score is None else score}分 {text}")
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


# ---------------------------------------------------------------- rubric 管理


def create_rubric(db: Session, *, name: str, task_description: str,
                  sample_queries: list | None, min_score: float, max_score: float,
                  model_config_id: str | None, created_by: str | None) -> AssistantEvalRubric:
    name = (name or "").strip()
    task_description = (task_description or "").strip()
    if not name or not task_description:
        raise ServiceError("评分标准名称与任务描述不能为空。")
    try:
        min_score = float(min_score)
        max_score = float(max_score)
    except (TypeError, ValueError):
        raise ServiceError("分值区间必须是数字。")
    if min_score < 0 or max_score <= min_score:
        raise ServiceError("分值区间不合法：需满足 0 ≤ 最低分 < 最高分。")
    judge = _resolve_judge_model(db, model_config_id)
    rubrics_text = generate_rubrics(judge, name, task_description,
                                    list(sample_queries or []), min_score, max_score)
    row = AssistantEvalRubric(
        name=name,
        task_description=task_description,
        rubrics=rubrics_text,
        min_score=min_score,
        max_score=max_score,
        judge_model_config_id=str(judge.id),
        judge_model_name=getattr(judge, "name", "") or "",
        created_by=created_by,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_rubrics(db: Session) -> list[AssistantEvalRubric]:
    return db.query(AssistantEvalRubric).order_by(AssistantEvalRubric.created_at.desc()).all()


def delete_rubric(db: Session, rubric_id: str) -> bool:
    row = db.query(AssistantEvalRubric).filter(AssistantEvalRubric.id == rubric_id).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def recover_interrupted_tasks() -> dict:
    """启动恢复：queued 任务重新调度，running 任务标记中断（进程重启语义）。"""
    db = SessionLocal()
    try:
        queued = db.query(AssistantEvalTask).filter(AssistantEvalTask.status == "queued").all()
        running = db.query(AssistantEvalTask).filter(AssistantEvalTask.status == "running").all()
        for task in running:
            task.status = "error"
            task.error = "服务重启中断了评估任务，请重新发起。"
            task.finished_at = datetime.now(timezone.utc)
        db.commit()
        for task in queued:
            _start_worker(task.id)
        return {"requeued": len(queued), "interrupted": len(running)}
    finally:
        db.close()
