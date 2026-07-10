"""
n8n 流水线运行器 — 平台调度 engine=n8n 影子流水线的执行路径

与画布流水线共用同一条入湖通道（_save_curated_outputs：curated 版本化、
write_opts 的 overwrite/append/upsert 合并、空输出保护），差别只在"行数据
从哪来"：
  画布引擎：数据集 → route A/B/C steps
  n8n 引擎：POST 生产 webhook → 轮询执行 → 取末节点输出 items

被两处调用：
  - pipeline_run_task 经 engine_registry 分发（真实运行，含任务池调度的
    write_opts；通用骨架见 pipelines/external_runner）
  - 流水线编辑向导第 2 步对未发布 n8n 的执行预览（collect_test_rows：临时激活
    → 触发 → 还原，不写资产湖；数据管家已不再有试跑入口）
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.settings.workflows.n8n_client import N8nClient
from app.data_channel.steward import service
from app.data_channel.steward.models import N8nPipeline
from app.data_channel.steward.service import StewardError

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0
_DEFAULT_WAIT = 120       # 等待 n8n 执行完成的默认秒数
_MAX_ROWS = 50000         # 单次运行入湖行数上限（防误配置撑爆 SQLite）


def normalize_rows(body: Any) -> list[dict]:
    """把 webhook 响应 / 执行输出规整为 list[dict] 行数据。"""
    if body is None:
        return []
    if isinstance(body, list):
        actual = len(body)
        if actual > _MAX_ROWS:
            raise StewardError(
                f"n8n 单次输出超过平台安全上限：上限 {_MAX_ROWS} 行，实际 {actual} 行。"
                "本次运行已失败且不会截断入湖；请在 n8n 中分页/分批输出。")
        rows = []
        for item in body:
            if isinstance(item, dict):
                # n8n item 形如 {"json": {...}}；直接是行对象的也接受
                rows.append(item.get("json") if isinstance(item.get("json"), dict) else item)
            else:
                rows.append({"value": item})
        return rows
    if isinstance(body, dict):
        return [body]
    return [{"value": body}]


def _extract_execution_rows(execution: dict) -> tuple[list[dict], dict]:
    """从执行详情取末节点输出 items 与元信息。"""
    meta = {
        "execution_id": execution.get("id"),
        "execution_status": execution.get("status"),
        "started_at": execution.get("startedAt"),
        "stopped_at": execution.get("stoppedAt"),
    }
    result_data = (execution.get("data") or {}).get("resultData") or {}
    err = result_data.get("error") or {}
    if err:
        node = err.get("node")
        node_name = node.get("name") if isinstance(node, dict) else node
        meta["error"] = f"{err.get('message', '')}" + (f"（节点 {node_name}）" if node_name else "")
    last_node = result_data.get("lastNodeExecuted")
    meta["last_node"] = last_node
    rows: list[dict] = []
    run_data = result_data.get("runData") or {}
    if last_node and last_node in run_data:
        try:
            items = ((run_data[last_node][-1].get("data") or {}).get("main") or [[]])[0] or []
            rows = normalize_rows(items)
        except StewardError:
            raise
        except Exception:  # noqa: BLE001 — 结构异常由调用方按空输出处理
            logger.warning("解析执行 %s 末节点输出失败", execution.get("id"), exc_info=True)
    return rows, meta


def _execution_has_run_id(execution: dict, run_id: str) -> bool:
    """Verify that execution data contains the exact webhook correlation id.

    n8n's Webhook node normally records the request under ``json.body`` in
    ``resultData.runData``.  We intentionally inspect the complete saved
    execution instead of assuming that the newest execution belongs to this
    request; concurrent manual/external runs otherwise cross-wire data.
    """
    expected = str(run_id)

    def contains(value: Any) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"run_id", "runId", "correlation_id", "correlationId"}:
                    if str(child) == expected:
                        return True
                if contains(child):
                    return True
        elif isinstance(value, list):
            return any(contains(child) for child in value)
        return False

    return contains((execution.get("data") or {}).get("resultData") or {})


def _response_execution_id(body: Any) -> str | None:
    """Read an optional exact execution id returned by a managed webhook."""
    if not isinstance(body, dict):
        return None
    for key in ("executionId", "execution_id"):
        value = body.get(key)
        if value is not None and str(value).strip():
            return str(value)
    meta = body.get("meta")
    if isinstance(meta, dict):
        return _response_execution_id(meta)
    return None


def trigger_and_collect(client: N8nClient, workflow_id: str, webhook_path: str,
                        payload: dict | None = None,
                        wait_seconds: int = _DEFAULT_WAIT) -> tuple[list[dict], dict]:
    """POST webhook → 等执行落库 → 返回 (行数据, 执行元信息)。

    行数据只取与本次 run_id 精确匹配的 execution 详情。不能验证关联时
    明确失败，不回退到「最新 execution」或未关联的 webhook 响应体。
    """
    request_payload = dict(payload or {})
    run_id = str(request_payload.get("run_id") or uuid.uuid4())
    request_payload["run_id"] = run_id
    before_ids = {str(e.get("id")) for e in client.list_executions(workflow_id=workflow_id, limit=20)}

    status_code, body = client.trigger_webhook(webhook_path, payload=request_payload,
                                               timeout_seconds=float(wait_seconds))
    if status_code == 404:
        raise StewardError(
            f"Webhook 触发返回 404：工作流可能未激活，或 webhook path「{webhook_path}」不正确。")
    if status_code >= 400:
        raise StewardError(f"Webhook 触发失败 (HTTP {status_code}): {str(body)[:300]}")

    deadline = time.time() + wait_seconds
    execution: dict | None = None
    response_execution_id = _response_execution_id(body)
    while time.time() < deadline:
        if response_execution_id:
            candidates = [{"id": response_execution_id}]
        else:
            candidates = [
                item for item in client.list_executions(workflow_id=workflow_id, limit=20)
                if str(item.get("id")) not in before_ids
            ]
        for candidate in candidates:
            execution_id = candidate.get("id")
            if execution_id is None:
                continue
            detail = client.get_execution(str(execution_id), include_data=True)
            if detail.get("status") not in ("success", "error", "crashed", "canceled"):
                continue
            if _execution_has_run_id(detail, run_id):
                execution = detail
                break
        if execution is not None:
            break
        time.sleep(_POLL_INTERVAL)

    if execution is None:
        raise StewardError(
            f"无法在 {wait_seconds} 秒内把 Webhook 请求与 n8n execution 精确关联"
            f"（run_id={run_id}）。为避免并发任务串线，本次运行已失败且不会采用最新执行或"
            "Webhook 响应体。请确认 n8n 保存成功/失败 execution 数据，且 Webhook 输入中的 "
            "run_id 未被清除。")

    rows, meta = _extract_execution_rows(execution)
    meta["run_id"] = run_id
    if meta.get("execution_status") != "success":
        detail = meta.get("error") or f"status={meta.get('execution_status') or 'unknown'}"
        raise StewardError(f"n8n 执行未成功：{detail}")
    return rows, meta


def _resolve_n8n_context(db: Session, pl) -> tuple[N8nPipeline, str, str]:
    n8n_def = (pl.definition or {}).get("n8n") or {}
    rec = service.record_for_pipeline(db, pl)
    if rec is None:
        raise StewardError("该 n8n 流水线缺少数据管家治理记录，无法运行。请删除后在数据管家重新新建该流水线。")
    if (pl.status or "") != "published":
        raise StewardError("该 n8n 流水线尚未发布，不能被调度运行。请先在流水线列表的编辑向导中完成发布。")
    webhook_path = n8n_def.get("webhook_path") or service.find_webhook_path(rec.workflow_snapshot)
    if not webhook_path:
        raise StewardError("该工作流没有 Webhook 触发器，平台无法主动调度（它只能由 n8n 内部定时自跑）。")
    return rec, rec.n8n_workflow_id, str(webhook_path)


def collect_n8n_rows(db: Session, pl, payload: dict | None = None) -> tuple[list[dict], dict]:
    """试运行取数（不写湖）：治理校验 → 触发生产 webhook → 规整为行数据。

    供列表页 dry-run 预览使用。注意：n8n 没有"只看不跑"的模式——试运行
    依然真实触发生产 workflow（这就是它的执行方式），只是产物先不入湖。
    """
    rec, workflow_id, webhook_path = _resolve_n8n_context(db, pl)
    client = service.get_n8n_client(db)
    service.require_published_revision(pl, client.get_workflow(workflow_id))
    wait_seconds = int(((pl.definition or {}).get("n8n") or {}).get("wait_seconds") or _DEFAULT_WAIT)
    from app.data_channel.datasets.lock import dataset_write_lock
    with dataset_write_lock(
        f"n8n::{workflow_id}", bind=db.get_bind(),
        wait_timeout=float(wait_seconds) + 10,
        stale_after=float(wait_seconds) + 60,
    ):
        rows, exec_meta = trigger_and_collect(
            client, workflow_id, webhook_path,
            payload=payload or {"source": "ontoprompt-dry-run"},
            wait_seconds=wait_seconds)
    if exec_meta.get("error"):
        raise StewardError(f"n8n 执行失败：{exec_meta['error']}")
    return rows, exec_meta


def run_n8n_pipeline(db: Session, pl, run, write_opts: dict | None = None) -> None:
    """pipeline_run_task 经 engine_registry 分发到此的 engine=n8n 运行入口。

    run 状态机 / 资产湖准入闸门 / 版本化入湖 / 统计记账全部由通用的
    run_external_pipeline 承担；这里只保留 n8n 特有的两件事：
    发布校验 + webhook 取数（collector）、发布时固化的期望列（契约）。
    运行成败不再回写 pipeline.status（发布态由编辑向导管理），失败详情由
    run.status/error_log 承载。新引擎照此文件抄作业即可。
    """
    from app.data_channel.pipelines.external_runner import run_external_pipeline

    def collector(db_: Session, pl_) -> tuple[list[dict], dict]:
        _rec, workflow_id, webhook_path = _resolve_n8n_context(db_, pl_)
        client = service.get_n8n_client(db_)
        service.require_published_revision(pl_, client.get_workflow(workflow_id))
        wait_seconds = int(((pl_.definition or {}).get("n8n") or {}).get("wait_seconds") or _DEFAULT_WAIT)
        from app.data_channel.datasets.lock import dataset_write_lock
        with dataset_write_lock(
            f"n8n::{workflow_id}", bind=db_.get_bind(),
            wait_timeout=float(wait_seconds) + 10,
            stale_after=float(wait_seconds) + 60,
        ):
            rows, exec_meta = trigger_and_collect(
                client, workflow_id, webhook_path,
                payload={"source": "ontoprompt", "run_id": run.id},
                wait_seconds=wait_seconds)
        if exec_meta.get("error"):
            raise StewardError(f"n8n 执行失败：{exec_meta['error']}")
        return rows, exec_meta

    contract_cols = ((pl.definition or {}).get("n8n") or {}).get("expected_columns") or None
    run_external_pipeline(db, pl, run, write_opts, engine_name="n8n",
                          collector=collector, contract_columns=contract_cols)


def collect_test_rows(db: Session, rec: N8nPipeline, payload: dict | None = None,
                      wait_seconds: int = 60) -> tuple[list[dict], dict]:
    """未发布 workflow 的取数通道：临时激活 → 触发 → 收集完整行 → 恢复原激活状态。

    不写资产湖。供流水线编辑向导对未发布 n8n 的执行预览使用——未发布 n8n 的
    生产 webhook 未注册，走 collect_n8n_rows 必 404。数据管家已无试跑入口。
    """
    client = service.get_n8n_client(db)
    workflow = client.get_workflow(rec.n8n_workflow_id)
    webhook_path = service.find_webhook_path(workflow)
    if not webhook_path:
        raise StewardError("该工作流没有 Webhook 触发器，无法由平台试跑。")

    was_active = bool(workflow.get("active"))
    if not was_active:
        try:
            client.activate_workflow(rec.n8n_workflow_id)
        except Exception as exc:  # noqa: BLE001
            raise StewardError(f"临时激活失败：{exc}") from exc
        # webhook 注册非即时，稍等再触发
        time.sleep(1.5)
    try:
        from app.data_channel.datasets.lock import dataset_write_lock
        with dataset_write_lock(
            f"n8n::{rec.n8n_workflow_id}", bind=db.get_bind(),
            wait_timeout=float(wait_seconds) + 10,
            stale_after=float(wait_seconds) + 60,
        ):
            rows, exec_meta = trigger_and_collect(
                client, rec.n8n_workflow_id, webhook_path,
                payload=payload or {"source": "ontoprompt-test"},
                wait_seconds=wait_seconds)
    finally:
        if not was_active:
            try:
                client.deactivate_workflow(rec.n8n_workflow_id)
            except Exception as exc:  # noqa: BLE001
                raise StewardError(
                    f"执行预览结束，但恢复 n8n 草稿的停用状态失败：{exc}。"
                    "为避免未发布工作流继续对外生效，请立即在 n8n 中停用后再继续。") from exc
    return rows, exec_meta


def persist_test_result(db: Session, rec: N8nPipeline, rows: list[dict], exec_meta: dict) -> None:
    """执行预览成功即持久化列样本：发布时固化为影子流水线的期望列契约。

    流水线编辑向导的执行预览走这里，与发布契约共用同一持久化口径。"""
    if exec_meta.get("error"):
        return
    from datetime import datetime, timezone
    columns: list[str] = []
    for row in rows[:50]:
        for k in row.keys():
            if k not in columns:
                columns.append(k)
    rec.last_test_result = {
        "rows": len(rows),
        "columns": columns,
        "sample": rows[:5],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    db.commit()
