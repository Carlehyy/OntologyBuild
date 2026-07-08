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
  - steward router 的 test-run（临时激活 → 试跑 → 还原，不写资产湖）
"""
from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from app.settings.workflows.n8n_client import N8nClient
from app.data_channel.steward import service
from app.data_channel.steward.models import N8nPipeline, STATUS_APPROVED
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
        rows = []
        for item in body:
            if isinstance(item, dict):
                # n8n item 形如 {"json": {...}}；直接是行对象的也接受
                rows.append(item.get("json") if isinstance(item.get("json"), dict) else item)
            else:
                rows.append({"value": item})
        return rows[:_MAX_ROWS]
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
        except Exception:  # noqa: BLE001 — 结构异常时退回 webhook 响应体
            logger.warning("解析执行 %s 末节点输出失败", execution.get("id"), exc_info=True)
    return rows, meta


def trigger_and_collect(client: N8nClient, workflow_id: str, webhook_path: str,
                        payload: dict | None = None,
                        wait_seconds: int = _DEFAULT_WAIT) -> tuple[list[dict], dict]:
    """POST webhook → 等执行落库 → 返回 (行数据, 执行元信息)。

    行数据优先取执行详情的末节点完整 items（webhook responseData 默认只回
    首条，直接用响应体会漏行）；执行详情拿不到时才退回响应体。
    """
    before_ids = {str(e.get("id")) for e in client.list_executions(workflow_id=workflow_id, limit=20)}

    status_code, body = client.trigger_webhook(webhook_path, payload=payload or {},
                                               timeout_seconds=float(wait_seconds))
    if status_code == 404:
        raise StewardError(
            f"Webhook 触发返回 404：工作流可能未激活，或 webhook path「{webhook_path}」不正确。")
    if status_code >= 400:
        raise StewardError(f"Webhook 触发失败 (HTTP {status_code}): {str(body)[:300]}")

    deadline = time.time() + wait_seconds
    execution: dict | None = None
    while time.time() < deadline:
        candidates = client.list_executions(workflow_id=workflow_id, limit=20)
        fresh = [e for e in candidates if str(e.get("id")) not in before_ids]
        if fresh:
            newest = fresh[0]  # n8n 按时间倒序返回
            if newest.get("status") in ("success", "error", "crashed", "canceled"):
                execution = client.get_execution(str(newest["id"]), include_data=True)
                break
        time.sleep(_POLL_INTERVAL)

    if execution is None:
        # 执行未落库（saveData 关闭 / 尚未结束）：退回 webhook 响应体
        rows = normalize_rows(body)
        return rows, {"execution_id": None, "execution_status": "unknown",
                      "note": "未在等待窗口内取到执行详情，行数据来自 webhook 响应体。"}

    rows, meta = _extract_execution_rows(execution)
    if meta.get("execution_status") == "success" and not rows:
        rows = normalize_rows(body)
        if rows:
            meta["note"] = "执行详情无末节点数据，行数据来自 webhook 响应体。"
    return rows, meta


def _resolve_n8n_context(db: Session, pl) -> tuple[N8nPipeline, str, str]:
    n8n_def = (pl.definition or {}).get("n8n") or {}
    steward_id = n8n_def.get("steward_id")
    rec = db.query(N8nPipeline).filter(N8nPipeline.id == steward_id).first() if steward_id else None
    if rec is None:
        raise StewardError("该 n8n 流水线缺少数据管家治理记录，无法运行。请到数据管家页面重新审批注册。")
    if rec.status != STATUS_APPROVED:
        raise StewardError(f"该 n8n 流水线当前状态为 {rec.status}（未批准），不能运行。请先在数据管家中完成审批。")
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
    wait_seconds = int(((pl.definition or {}).get("n8n") or {}).get("wait_seconds") or _DEFAULT_WAIT)
    rows, exec_meta = trigger_and_collect(client, workflow_id, webhook_path,
                                          payload=payload or {"source": "ontoprompt-dry-run"},
                                          wait_seconds=wait_seconds)
    if exec_meta.get("error"):
        raise StewardError(f"n8n 执行失败：{exec_meta['error']}")
    return rows, exec_meta


def run_n8n_pipeline(db: Session, pl, run, write_opts: dict | None = None) -> None:
    """pipeline_run_task 经 engine_registry 分发到此的 engine=n8n 运行入口。

    run 状态机 / 资产湖准入闸门 / 版本化入湖 / 统计记账全部由通用的
    run_external_pipeline 承担；这里只保留 n8n 特有的两件事：
    治理校验 + webhook 取数（collector）、审批时固化的期望列（契约）。
    运行成败不再回写 pipeline.status（发布态由审批流管理），失败详情由
    run.status/error_log 承载。新引擎照此文件抄作业即可。
    """
    from app.data_channel.pipelines.external_runner import run_external_pipeline

    def collector(db_: Session, pl_) -> tuple[list[dict], dict]:
        _rec, workflow_id, webhook_path = _resolve_n8n_context(db_, pl_)
        client = service.get_n8n_client(db_)
        wait_seconds = int(((pl_.definition or {}).get("n8n") or {}).get("wait_seconds") or _DEFAULT_WAIT)
        rows, exec_meta = trigger_and_collect(client, workflow_id, webhook_path,
                                              payload={"source": "ontoprompt", "run_id": run.id},
                                              wait_seconds=wait_seconds)
        if exec_meta.get("error"):
            raise StewardError(f"n8n 执行失败：{exec_meta['error']}")
        return rows, exec_meta

    contract_cols = ((pl.definition or {}).get("n8n") or {}).get("expected_columns") or None
    run_external_pipeline(db, pl, run, write_opts, engine_name="n8n",
                          collector=collector, contract_columns=contract_cols)


def test_run_workflow(db: Session, rec: N8nPipeline, payload: dict | None = None,
                      wait_seconds: int = 60) -> dict:
    """审批前试跑：临时激活 → 触发 → 取样 → 恢复原激活状态。不写资产湖。"""
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
        rows, exec_meta = trigger_and_collect(client, rec.n8n_workflow_id, webhook_path,
                                              payload=payload or {"source": "ontoprompt-test"},
                                              wait_seconds=wait_seconds)
    finally:
        if not was_active:
            try:
                client.deactivate_workflow(rec.n8n_workflow_id)
            except Exception:  # noqa: BLE001
                logger.warning("试跑后停用 workflow %s 失败", rec.n8n_workflow_id, exc_info=True)

    columns: list[str] = []
    for row in rows[:50]:
        for k in row.keys():
            if k not in columns:
                columns.append(k)
    return {
        "rows": len(rows),
        "columns": columns,
        "sample": rows[:10],
        "execution": exec_meta,
        "error": exec_meta.get("error"),
    }
