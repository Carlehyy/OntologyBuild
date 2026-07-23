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


def _runtime_payload(*, pipeline_id: str, workflow_id: str, invocation_id: str,
                     purpose: str, owner_id: str | None,
                     payload: dict | None = None) -> dict:
    """Add server-owned invocation/file context; callers cannot override it."""
    from app.data_channel.file_assets.service import gateway_context

    result = dict(payload or {})
    result["run_id"] = invocation_id
    result["file_gateway"] = gateway_context(
        pipeline_id=pipeline_id,
        workflow_id=workflow_id,
        invocation_id=invocation_id,
        purpose=purpose,
        owner_id=owner_id,
    )
    return result


def _scrub_runtime_file_context(value: Any, gateway_token: str) -> Any:
    """Keep server-owned gateway credentials out of previews and lake rows.

    Older workflows sometimes pass the full Webhook body through unchanged.
    Removing the well-known context key preserves their pre-existing columns.
    If a workflow copied/renamed the token, fail closed rather than persisting
    a live credential under an arbitrary user-controlled key.
    """
    if isinstance(value, dict):
        return {
            key: _scrub_runtime_file_context(child, gateway_token)
            for key, child in value.items()
            if key != "file_gateway"
        }
    if isinstance(value, list):
        return [_scrub_runtime_file_context(child, gateway_token) for child in value]
    if isinstance(value, str) and gateway_token and gateway_token in value:
        raise StewardError(
            "末端输出包含本次文件网关短时令牌。请删除该字段，只保留上传响应中的 file_ref。")
    return value


def _validate_file_refs(db: Session, rows: list[dict], *, pipeline_id: str,
                        invocation_id: str,
                        gateway_token: str) -> tuple[list[dict], list[str]]:
    from app.data_channel.file_assets.service import validate_and_canonicalize_refs

    safe_rows = _scrub_runtime_file_context(rows, gateway_token)
    return validate_and_canonicalize_refs(
        db, safe_rows, pipeline_id=pipeline_id, invocation_id=invocation_id)


def _finalize_preview_file_invocation(
    db: Session, *, pipeline_id: str, invocation_id: str,
    referenced_ids: list[str],
) -> None:
    """Pin a fully validated preview and remove its upload orphans atomically."""
    from app.data_channel.file_assets.service import reconcile_invocation

    reconcile_invocation(
        db,
        pipeline_id=pipeline_id,
        invocation_id=invocation_id,
        referenced_ids=referenced_ids,
        pin_referenced=True,
    )


def _recover_validated_share_tokens(
    db: Session, asset_ids: list[str],
) -> dict[str, str]:
    """Capture exact FileRef bearer tokens before a formal run rolls them back."""
    if not asset_ids:
        return {}
    from app.data_channel.file_assets.models import PipelineFileAsset
    from app.data_channel.file_assets.service import recover_share_token

    assets = db.query(PipelineFileAsset).filter(
        PipelineFileAsset.id.in_(asset_ids)).all()
    by_id = {asset.id: asset for asset in assets}
    tokens: dict[str, str] = {}
    for asset_id in asset_ids:
        token = recover_share_token(by_id[asset_id]) if asset_id in by_id else None
        if token is None:
            raise StewardError(
                f"file_ref {asset_id} 未能生成可信匿名分享令牌，请重新执行流水线")
        tokens[asset_id] = token
    return tokens


def _abandon_file_invocation(db: Session, *, pipeline_id: str,
                             invocation_id: str) -> None:
    from app.data_channel.file_assets.service import abandon_invocation

    try:
        db.rollback()
        abandon_invocation(db, pipeline_id=pipeline_id, invocation_id=invocation_id)
    except Exception:  # noqa: BLE001 -- cleanup is retried by expiry/outbox workers
        db.rollback()
        logger.exception("清理失败的 n8n 文件执行上下文失败")


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


def _extract_execution_rows(execution: dict, expected_output_node: str) -> tuple[list[dict], dict]:
    """严格读取发布契约指定输出节点的一次、单 main 分支 items。"""
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
    if meta["execution_status"] != "success":
        return [], meta
    if not expected_output_node:
        raise StewardError("已发布运行契约缺少 n8n 输出节点名称，平台已拒绝猜测其他节点输出。")
    if last_node != expected_output_node:
        raise StewardError(
            f"n8n 最后执行节点「{last_node or '无'}」与发布契约输出节点"
            f"「{expected_output_node}」不一致；拒绝猜测其他节点输出。")

    run_data = result_data.get("runData") or {}
    if not isinstance(run_data, dict):
        raise StewardError("n8n execution 的 resultData.runData 结构非法。")
    runs = run_data.get(expected_output_node)
    if not isinstance(runs, list) or len(runs) != 1:
        actual = len(runs) if isinstance(runs, list) else 0
        raise StewardError(
            f"发布契约输出节点「{expected_output_node}」必须且只能执行 1 次，实际 {actual} 次；"
            "循环/分批多次执行必须先在 n8n 内汇总为单一输出。")
    run = runs[0]
    if not isinstance(run, dict):
        raise StewardError(f"输出节点「{expected_output_node}」的 execution run 结构非法。")
    data = run.get("data")
    if not isinstance(data, dict):
        raise StewardError(f"输出节点「{expected_output_node}」缺少 execution data。")
    main = data.get("main")
    if not isinstance(main, list) or len(main) != 1:
        actual = len(main) if isinstance(main, list) else 0
        raise StewardError(
            f"输出节点「{expected_output_node}」必须且只能产生 1 个 main 分支，实际 {actual} 个。")
    items = main[0]
    if not isinstance(items, list):
        raise StewardError(f"输出节点「{expected_output_node}」的 main[0] 必须是 n8n item 数组。")
    binary_items = [
        item for item in items
        if isinstance(item, dict) and isinstance(item.get("binary"), dict)
        and bool(item.get("binary"))
    ]
    if binary_items:
        raise StewardError(
            f"输出节点「{expected_output_node}」仍携带 {len(binary_items)} 个 binary item。"
            "平台不会把 n8n 二进制直接写入行数据；请先上传到本次 Webhook 提供的 "
            "file_gateway，再让末节点只输出普通 JSON 与 file_ref。")
    return normalize_rows(items), meta


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
                        wait_seconds: int = _DEFAULT_WAIT, *,
                        expected_output_node: str) -> tuple[list[dict], dict]:
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

    rows, meta = _extract_execution_rows(execution, expected_output_node)
    meta["run_id"] = run_id
    if meta.get("execution_status") != "success":
        detail = meta.get("error") or f"status={meta.get('execution_status') or 'unknown'}"
        raise StewardError(f"n8n 执行未成功：{detail}")
    return rows, meta


def _resolve_n8n_context(db: Session, pl, client: N8nClient, *,
                         require_active: bool = True) -> tuple[N8nPipeline, str, str, str]:
    rec = service.record_for_pipeline(db, pl)
    if rec is None:
        raise StewardError("该 n8n 流水线缺少数据管家治理记录，无法运行。请停用并归档后新建流水线替代。")
    try:
        remote_workflow = client.get_workflow(rec.n8n_workflow_id)
    except Exception as exc:  # noqa: BLE001
        raise StewardError(f"读取 n8n 已发布工作流失败：{exc}") from exc
    release = service.resolve_published_runtime_release(
        pl, rec, remote_workflow, require_active=require_active)
    contract = release["managed_contract"]
    return (
        rec,
        rec.n8n_workflow_id,
        str(contract["webhook_path"]),
        str(contract["output_node_name"]),
    )


def collect_n8n_rows(db: Session, pl, payload: dict | None = None) -> tuple[list[dict], dict]:
    """试运行取数（不写湖）：治理校验 → 触发生产 webhook → 规整为行数据。

    供列表页 dry-run 预览使用。注意：n8n 没有"只看不跑"的模式——试运行
    依然真实触发生产 workflow（这就是它的执行方式），只是产物先不入湖。
    """
    rec = service.record_for_pipeline(db, pl)
    if rec is None:
        raise StewardError("该 n8n 流水线缺少数据管家治理记录，无法运行。请停用并归档后新建流水线替代。")
    client = service.get_n8n_client(db)
    workflow_id = rec.n8n_workflow_id
    invocation_id = str(uuid.uuid4())
    wait_seconds = int(((pl.definition or {}).get("n8n") or {}).get("wait_seconds") or _DEFAULT_WAIT)
    from app.data_channel.datasets.lock import dataset_write_lock
    try:
        with dataset_write_lock(
            f"n8n::{workflow_id}", bind=db.get_bind(),
            wait_timeout=float(wait_seconds) + 10,
            stale_after=float(wait_seconds) + 60,
        ):
            _rec, workflow_id, webhook_path, output_node_name = _resolve_n8n_context(
                db, pl, client, require_active=False)
            before = client.get_workflow(workflow_id)
            was_active = bool(before.get("active"))
            if not was_active:
                try:
                    activated = service.set_remote_active_for_preview(
                        rec, client, enabled=True)
                    service.resolve_published_runtime_release(
                        pl, rec, activated, require_active=True)
                    time.sleep(1.5)
                except Exception as exc:  # noqa: BLE001
                    raise StewardError(
                        f"为执行预览临时启用已发布工作流失败：{exc}") from exc
            try:
                runtime_payload = _runtime_payload(
                    pipeline_id=pl.id,
                    workflow_id=workflow_id,
                    invocation_id=invocation_id,
                    purpose="preview",
                    owner_id=rec.created_by,
                    payload=payload or {"source": "ontoprompt-dry-run"},
                )
                rows, exec_meta = trigger_and_collect(
                    client, workflow_id, webhook_path,
                    payload=runtime_payload,
                    wait_seconds=wait_seconds,
                    expected_output_node=output_node_name)
                rows, asset_ids = _validate_file_refs(
                    db, rows, pipeline_id=pl.id, invocation_id=invocation_id,
                    gateway_token=runtime_payload["file_gateway"]["token"])
                exec_meta["file_invocation_id"] = invocation_id
                exec_meta["file_asset_ids"] = asset_ids
            finally:
                if not was_active:
                    try:
                        service.set_remote_active_for_preview(
                            rec, client, enabled=False)
                    except Exception as exc:  # noqa: BLE001
                        raise StewardError(
                            f"执行预览结束，但恢复已发布流水线的停用状态失败：{exc}。"
                            "请立即在 n8n 中停用并核对平台启用开关。") from exc
            if exec_meta.get("error"):
                raise StewardError(f"n8n 执行失败：{exec_meta['error']}")
            _finalize_preview_file_invocation(
                db,
                pipeline_id=pl.id,
                invocation_id=invocation_id,
                referenced_ids=asset_ids,
            )
    except Exception:
        _abandon_file_invocation(
            db, pipeline_id=pl.id, invocation_id=invocation_id)
        raise
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

    pending_share_tokens: dict[str, str] = {}

    def collector(db_: Session, pl_) -> tuple[list[dict], dict]:
        nonlocal pending_share_tokens
        client = service.get_n8n_client(db_)
        rec = service.record_for_pipeline(db_, pl_)
        if rec is None:
            raise StewardError("该 n8n 流水线缺少数据管家治理记录，无法运行。")
        workflow_id = rec.n8n_workflow_id
        wait_seconds = int(((pl_.definition or {}).get("n8n") or {}).get("wait_seconds") or _DEFAULT_WAIT)
        from app.data_channel.datasets.lock import dataset_write_lock
        with dataset_write_lock(
            f"n8n::{workflow_id}", bind=db_.get_bind(),
            wait_timeout=float(wait_seconds) + 10,
            stale_after=float(wait_seconds) + 60,
        ):
            _rec, workflow_id, webhook_path, output_node_name = _resolve_n8n_context(
                db_, pl_, client, require_active=True)
            runtime_payload = _runtime_payload(
                pipeline_id=pl_.id,
                workflow_id=workflow_id,
                invocation_id=run.id,
                purpose="run",
                owner_id=rec.created_by,
                payload={"source": "ontoprompt"},
            )
            rows, exec_meta = trigger_and_collect(
                client, workflow_id, webhook_path,
                payload=runtime_payload,
                wait_seconds=wait_seconds,
                expected_output_node=output_node_name)
            rows, asset_ids = _validate_file_refs(
                db_, rows, pipeline_id=pl_.id, invocation_id=run.id,
                gateway_token=runtime_payload["file_gateway"]["token"])
            pending_share_tokens = _recover_validated_share_tokens(db_, asset_ids)
            # Dataset version creation commits internally.  Keep anonymous
            # tokens out of the database until commit_invocation finalizes the
            # successful run; the exact plaintext tokens remain only in this
            # in-process closure so persisted FileRef URLs stay valid.
            db_.rollback()
            exec_meta["file_invocation_id"] = run.id
            exec_meta["file_asset_ids"] = asset_ids
        if exec_meta.get("error"):
            raise StewardError(f"n8n 执行失败：{exec_meta['error']}")
        return rows, exec_meta

    contract_cols = ((pl.definition or {}).get("n8n") or {}).get("expected_columns") or None
    def finalize_files(db_, pl_, _run, _outputs, exec_meta, dataset_version_id):
        from app.data_channel.file_assets.service import commit_invocation

        commit_invocation(
            db_,
            pipeline_id=pl_.id,
            invocation_id=run.id,
            referenced_ids=exec_meta.get("file_asset_ids") or [],
            dataset_version_id=dataset_version_id,
            share_tokens=pending_share_tokens,
        )

    run_external_pipeline(
        db, pl, run, write_opts, engine_name="n8n",
        collector=collector, contract_columns=contract_cols,
        output_finalizer=finalize_files,
    )
    if run.status != "success":
        _abandon_file_invocation(db, pipeline_id=pl.id, invocation_id=run.id)
    else:
        from app.data_channel.datasets.service import drain_storage_deletion_outbox
        drain_storage_deletion_outbox(db)


def collect_test_rows(db: Session, rec: N8nPipeline, payload: dict | None = None,
                      wait_seconds: int = 60, *,
                      require_publish_evidence: bool = True) -> tuple[list[dict], dict]:
    """未发布 workflow 的取数通道：临时激活 → 触发 → 收集完整行 → 恢复原激活状态。

    不写资产湖。供流水线编辑向导和数据管家对未发布 n8n 的执行预览使用——未发布
    n8n 的生产 webhook 未注册，走 collect_n8n_rows 必 404。

    编辑向导默认要求完整 revision，形成后续发布凭证；数据管家的普通执行只需要
    精确关联本次 execution 并确认编排快照未并发变化，不应因为 n8n 版本未返回
    activeVersionId 而拒绝展示已经成功产生的输出。
    """
    client = service.get_n8n_client(db)
    if not rec.pipeline_id:
        raise StewardError("受管 n8n 工作流缺少平台影子流水线，无法签发文件上传上下文。")
    invocation_id = str(uuid.uuid4())
    from app.data_channel.datasets.lock import dataset_write_lock
    # 锁必须覆盖 GET active → 临时 activate → 触发 → 恢复 deactivate → 最终 GET。
    # 只锁 trigger 会让并发 publish 在中途启用成功，随后本预览按旧 was_active
    # 把正式 workflow 错误停用，形成平台 enabled / 远端 inactive 的裂脑。
    try:
        with dataset_write_lock(
            f"n8n::{rec.n8n_workflow_id}", bind=db.get_bind(),
            wait_timeout=float(wait_seconds) + 10,
            stale_after=float(wait_seconds) + 60,
        ):
            workflow = client.get_workflow(rec.n8n_workflow_id)
            managed_contract = service.validate_managed_workflow_contract(workflow)
            initial_snapshot_hash = service.canonical_json_hash(
                N8nClient.sanitize_workflow(workflow))
            webhook_path = managed_contract["webhook_path"]
            output_node_name = managed_contract["output_node_name"]

            was_active = bool(workflow.get("active"))
            if not was_active:
                try:
                    client.activate_workflow(rec.n8n_workflow_id)
                except Exception as exc:  # noqa: BLE001
                    raise StewardError(f"临时激活失败：{exc}") from exc
                # webhook 注册非即时，稍等再触发
                time.sleep(1.5)
            try:
                runtime_payload = _runtime_payload(
                    pipeline_id=rec.pipeline_id,
                    workflow_id=rec.n8n_workflow_id,
                    invocation_id=invocation_id,
                    purpose="preview",
                    owner_id=rec.created_by,
                    payload=payload or {"source": "ontoprompt-test"},
                )
                rows, exec_meta = trigger_and_collect(
                    client, rec.n8n_workflow_id, webhook_path,
                    payload=runtime_payload,
                    wait_seconds=wait_seconds,
                    expected_output_node=output_node_name)
                rows, asset_ids = _validate_file_refs(
                    db, rows, pipeline_id=rec.pipeline_id,
                    invocation_id=invocation_id,
                    gateway_token=runtime_payload["file_gateway"]["token"])
                exec_meta["file_invocation_id"] = invocation_id
                exec_meta["file_asset_ids"] = asset_ids
            finally:
                if not was_active:
                    try:
                        client.deactivate_workflow(rec.n8n_workflow_id)
                    except Exception as exc:  # noqa: BLE001
                        raise StewardError(
                            f"执行预览结束，但恢复 n8n 草稿的停用状态失败：{exc}。"
                            "为避免未发布工作流继续对外生效，请立即在 n8n 中停用后再继续。") from exc
            if exec_meta.get("error"):
                raise StewardError(f"n8n 执行失败：{exec_meta['error']}")
            try:
                final_workflow = client.get_workflow(rec.n8n_workflow_id)
            except Exception as exc:  # noqa: BLE001
                raise StewardError(f"执行预览后无法读取 n8n 工作流状态：{exc}") from exc
            final_snapshot = N8nClient.sanitize_workflow(final_workflow)
            final_snapshot_hash = service.canonical_json_hash(final_snapshot)
            if final_snapshot_hash != initial_snapshot_hash:
                raise StewardError(
                    "n8n 工作流在执行预览期间发生编排变化，无法确认本次输出对应同一编排。"
                    "请确认无人同时编辑后重新执行预览。")
            workflow_evidence = None
            try:
                # 即使调用方只要求查看预览，也尽可能生成后续发布所需的证据，
                # 让标准 n8n 环境保持原有“一次预览即可继续发布”的流程。
                workflow_evidence = service.workflow_validation_evidence(
                    final_workflow, context="执行预览后")
            except StewardError as exc:
                if require_publish_evidence:
                    raise
                # 某些 n8n 公共 API 版本不返回 activeVersionId。数据已经成功执行
                # 时仍返回预览，但明确标记发布凭证不可用，由字段校验步骤继续阻断发布。
                exec_meta["publish_evidence_error"] = str(exc)
            _finalize_preview_file_invocation(
                db,
                pipeline_id=rec.pipeline_id,
                invocation_id=invocation_id,
                referenced_ids=asset_ids,
            )
    except Exception:
        _abandon_file_invocation(
            db, pipeline_id=rec.pipeline_id, invocation_id=invocation_id)
        raise
    rec.workflow_snapshot = final_snapshot
    exec_meta["workflow_snapshot_hash"] = final_snapshot_hash
    if workflow_evidence is not None:
        exec_meta["workflow_evidence"] = workflow_evidence
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
        "output_checksum": service.canonical_json_hash(rows),
        "workflow_evidence": exec_meta.get("workflow_evidence"),
        # 预览成功但 n8n 公共 API 缺少版本字段时保留具体原因；发布端据此
        # 返回可操作的错误，而不是退化成笼统的“没有发布凭证”。
        "publish_evidence_error": exec_meta.get("publish_evidence_error"),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    db.commit()
