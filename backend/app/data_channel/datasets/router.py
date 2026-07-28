"""v2 Dataset API"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.deps import get_current_user, require_admin
from app.services.v2.dataset_service import DatasetService

router = APIRouter(dependencies=[Depends(get_current_user)])
logger = logging.getLogger(__name__)

MANUAL_FIELD_CONTRACT_VERSION = 2
MANUAL_FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _dispatch_dataset_import_task(
    task,
    job_id: str,
    background_tasks: BackgroundTasks,
    *,
    operation: str,
) -> dict:
    """Dispatch one import task without making optional Celery a hard dependency."""
    from app.config import settings
    from app.data_channel.datasets.import_jobs import update_status

    if not settings.dataset_import_use_celery:
        background_tasks.add_task(task.run, job_id)
        return update_status(job_id, execution_mode="local")

    try:
        task.delay(job_id)
        execution_mode = "celery"
    except Exception as exc:  # noqa: BLE001 - policy decides fallback behavior
        if settings.require_external_dependencies:
            update_status(
                job_id,
                status="failed",
                execution_mode="celery",
                progress=5,
                phase="后台任务投递失败",
                error="Redis/Celery 后台任务服务不可用",
            )
            logger.error(
                "Celery 无法投递数据集%s任务 %s；生产强制依赖模式禁止降级",
                operation,
                job_id,
                exc_info=True,
            )
            raise HTTPException(
                503,
                "Redis/Celery 后台任务服务不可用，生产环境禁止降级执行",
            ) from exc
        logger.warning(
            "Celery 无法投递数据集%s任务 %s，已降级为 API 进程内后台任务",
            operation,
            job_id,
            exc_info=True,
        )
        background_tasks.add_task(task.run, job_id)
        execution_mode = "local"
    return update_status(job_id, execution_mode=execution_mode)


class DatasetResponse(BaseModel):
    id: str
    name: str
    kind: str
    class Config:
        from_attributes = True


def _check_upload_file(filename: str | None, content: bytes) -> str:
    """校验上传文件扩展名与大小，返回小写扩展名"""
    from app.config import settings

    ext = (filename or "").rsplit(".", 1)[-1].lower()
    allowed = {e.strip() for e in settings.allowed_upload_extensions.split(",") if e.strip()}
    if ext not in allowed:
        raise HTTPException(400, f"不支持的文件类型: .{ext} (允许: {settings.allowed_upload_extensions})")
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"文件超过大小限制 {settings.max_upload_mb}MB")
    return ext


def _check_manual_import_extension(filename: str | None) -> str:
    """Validate the lightweight upload metadata before streaming to staging."""
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    if ext not in {"csv", "xlsx", "xls"}:
        raise HTTPException(400, "在线新建表格仅支持 CSV、XLSX 或 XLS 文件")
    return ext


def _estimate_rowcount(content: bytes, ext: str) -> int | None:
    """估算数据行数（CSV/Excel/JSON）。

    行数元数据会被流水线的数据可用性校验消费，不能把可正常解析的 JSON
    数组留成 ``None``，否则预览有数据而发布链路会误判为空。
    """
    try:
        if ext == "csv":
            return max(0, content.count(b"\n") - 1)
        if ext in ("xlsx", "xls"):
            # 与正式导入共用格式嗅探和空白行语义；同时覆盖旧版 OLE/BIFF XLS。
            from app.data_channel.datasets.service import _parse_stored_rows
            return len(_parse_stored_rows(content, limit=None))
        if ext == "json":
            import json
            parsed = json.loads(content.decode("utf-8-sig"))
            if isinstance(parsed, list):
                return len(parsed)
            if isinstance(parsed, dict):
                return 1
            return 0
    except Exception:
        pass
    return None


def _dataset_consumers(db: Session, dataset_id: str) -> list[dict]:
    """找出引用了该数据集的流水线（connector files 或 source_dataset_id）"""
    return _consumer_map(db).get(dataset_id, [])


def _consumer_map(db: Session) -> dict[str, list[dict]]:
    """一次扫描所有流水线，构建 dataset_id → 消费流水线列表 的映射"""
    from app.models.v2.pipeline import Pipeline

    mapping: dict[str, list[dict]] = {}

    def add(ds_id: str | None, pl: Pipeline):
        if not ds_id:
            return
        entry = {
            "id": pl.id,
            "name": pl.name,
            "status": pl.status or "draft",
            "domain": pl.domain or "通用",
        }
        bucket = mapping.setdefault(ds_id, [])
        if all(e["id"] != pl.id for e in bucket):
            bucket.append(entry)

    for pl in db.query(Pipeline).all():
        add(pl.source_dataset_id, pl)
        definition = pl.definition or {}
        for node in definition.get("nodes", []) or []:
            if node.get("type") != "connector":
                continue
            for fi in (node.get("config") or {}).get("files", []) or []:
                add(fi.get("dataset_id"), pl)
    return mapping


@router.post("/upload", status_code=201)
async def upload_dataset(
    file: UploadFile = File(...),
    metadata: str = Form(""),
    db: Session = Depends(get_db),
):
    """上传 CSV/Excel 文件并创建人工数据集。

    metadata 为空时兼容旧上传入口；新建表格弹窗会携带字段显示名、类型、
    非空与主键契约，使上传文件直接成为 v1，不产生无意义的空白版本。
    """
    import json
    import os

    name = os.path.splitext(file.filename or "upload")[0]
    content = await file.read()
    ext = _check_upload_file(file.filename, content)
    if metadata:
        if ext not in ("csv", "xlsx", "xls"):
            raise HTTPException(400, "在线新建表格仅支持 CSV、XLSX 或 XLS 文件")
        try:
            body = CreateTableRequest.model_validate(json.loads(metadata))
        except Exception as exc:
            raise HTTPException(400, f"字段设置格式无效：{exc}")
        name, schema = _build_manual_schema(body, origin="upload")
        from app.data_channel.datasets.service import _parse_stored_rows, stored_columns
        try:
            rows = _parse_stored_rows(content, limit=None)
            physical_columns = stored_columns(content)
        except Exception as exc:
            raise HTTPException(400, f"表格解析失败：{exc}")
        if (
            schema.get("manual_field_contract_version")
            == MANUAL_FIELD_CONTRACT_VERSION
        ):
            rows, content = _normalize_manual_contract_upload(
                rows,
                physical_columns,
                schema,
                dataset_name=name,
                scope="上传数据",
                allow_field_key_headers=False,
            )
        else:
            expected_columns = schema["columns"]
            if physical_columns != expected_columns:
                raise HTTPException(
                    400,
                    "上传文件列结构已发生变化"
                    f"（文件：{physical_columns}；当前设置：{expected_columns}），请重新选择文件",
                )
            _validate_manual_rows(
                rows, schema, dataset_name=name, scope="上传数据")

        svc = DatasetService(db)
        ds = svc.create_dataset(
            name=name, kind="structured", schema_json=schema, commit=False)
        version = svc.create_version(
            ds.id, content, rowcount=len(rows), schema_json=schema, _lock_held=True)
        return {"data": {
            "id": ds.id, "name": ds.name, "kind": ds.kind,
            "columns": schema["columns"],
            "primary_key": schema.get("primary_key", ""),
            "version_no": version.version_no, "rowcount": len(rows),
            "source": "upload",
        }}

    # 推断 kind
    if ext in ("csv", "xlsx", "xls"):
        kind = "structured"
    elif ext in ("json", "xml"):
        kind = "semi"
    else:
        kind = "unstructured"

    svc = DatasetService(db)
    # 数据集与首版本必须原子出现；首版本失败时不能在资产湖留下空壳。
    ds = svc.create_dataset(name=name, kind=kind, commit=False)
    svc.create_version(
        ds.id, content, rowcount=_estimate_rowcount(content, ext), _lock_held=True)
    return {"data": {"id": ds.id, "name": ds.name, "kind": ds.kind, "dataset_type": "raw_dataset", "schema_type": "tabular"}}


@router.post("/imports", status_code=202)
async def start_dataset_import(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    """Stream one spreadsheet to an isolated directory and queue server parsing."""
    import aiofiles
    from app.config import settings
    from app.data_channel.datasets.import_jobs import (
        create_import_job, remove_job, source_path, update_manifest, update_status)
    from app.tasks.v2.dataset_import import inspect_dataset_import

    ext = _check_manual_import_extension(file.filename)
    manifest = create_import_job(
        owner_id=current_user.id,
        filename=file.filename or f"upload.{ext}",
        extension=ext,
    )
    job_id = manifest["job_id"]
    target = source_path(job_id, ext)
    max_bytes = settings.max_upload_mb * 1024 * 1024
    total = 0
    try:
        async with aiofiles.open(target, "wb") as output:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        413, f"文件超过大小限制 {settings.max_upload_mb}MB")
                await output.write(chunk)
        update_manifest(job_id, file_size=total)
        update_status(
            job_id,
            status="queued",
            file_size=total,
            progress=5,
            phase="文件上传完成，等待后台解析",
            error=None,
        )
        status = _dispatch_dataset_import_task(
            inspect_dataset_import,
            job_id,
            background_tasks,
            operation="解析",
        )
        return {"data": status}
    except HTTPException:
        if total > max_bytes:
            remove_job(job_id)
        raise
    except Exception:
        remove_job(job_id)
        raise
    finally:
        await file.close()


@router.get("/imports/{job_id}")
def get_dataset_import(
    job_id: str,
    current_user=Depends(get_current_user),
):
    from app.data_channel.datasets.import_jobs import (
        assert_job_owner, read_status)

    try:
        assert_job_owner(job_id, current_user.id)
        return {"data": read_status(job_id)}
    except FileNotFoundError:
        raise HTTPException(404, "导入任务不存在或已被清理")
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class TableColumnDef(BaseModel):
    name: str
    source_key: str = ""
    display_name: str = ""
    type: str = "string"  # CONTRACT_FIELD_TYPES 平台词表；非法值显式拒绝
    nullable: bool = True


class CreateTableRequest(BaseModel):
    name: str
    columns: list[TableColumnDef]
    primary_key: str = ""  # 逗号分隔支持复合主键，可为空（之后仍可声明契约）


def _build_manual_schema(body: CreateTableRequest, *, origin: str) -> tuple[str, dict]:
    """校验人工表字段设置并生成统一 schema_json。"""
    from app.data_channel.datasets.lake_gate import (
        CONTRACT_FIELD_TYPES, normalize_field_type, split_pk)

    name = body.name.strip()
    if not name:
        raise HTTPException(400, "表格名称不能为空")
    if len(name) > 200:
        raise HTTPException(400, "表格名称过长（最多 200 字）")

    explicit_source_keys = [
        definition.source_key.strip()
        for definition in body.columns
        if definition.name.strip()
    ]
    has_explicit_source = any(explicit_source_keys)
    if has_explicit_source and not all(explicit_source_keys):
        raise HTTPException(400, "字段契约中的原始表头必须为每一列完整提供")

    # Every configured file upload uses the stable-key contract.  ``source_key``
    # is optional only for legacy English-header clients, where field key and
    # source header are already identical.  Empty-table API clients opt in by
    # sending source_key; existing Unicode-key tables remain readable unchanged.
    use_stable_field_contract = origin == "upload" or has_explicit_source

    raw_columns: list[tuple[TableColumnDef, str, str]] = []
    seen: set[str] = set()
    seen_sources: set[str] = set()
    for definition in body.columns:
        column = definition.name.strip()
        if not column:
            continue
        if (
            use_stable_field_contract
            and not MANUAL_FIELD_KEY_RE.fullmatch(column)
        ):
            raise HTTPException(
                400,
                f"字段标识「{column}」不合法：必须以小写字母开头，"
                "且只能包含小写字母、数字和下划线",
            )
        if column in seen:
            raise HTTPException(400, f"字段标识「{column}」重复，请修改后重试")
        source_key = definition.source_key.strip() or column
        if origin != "upload" and source_key != column:
            raise HTTPException(400, "空表字段的原始表头必须与字段标识一致")
        if source_key in seen_sources:
            raise HTTPException(
                400, f"原始表头「{source_key}」重复，请修改源文件后重试")
        requested_type = str(definition.type or "").strip().lower()
        if requested_type not in CONTRACT_FIELD_TYPES:
            raise HTTPException(
                400,
                f"列「{column}」的数据类型「{definition.type}」不受支持；可选类型：{', '.join(CONTRACT_FIELD_TYPES)}",
            )
        seen.add(column)
        seen_sources.add(source_key)
        raw_columns.append((definition, column, source_key))
    if not raw_columns:
        raise HTTPException(400, "至少需要定义一列（空白列名会被忽略）")

    pk_columns = split_pk(body.primary_key)
    unknown_pk = [column for column in pk_columns if column not in seen]
    if unknown_pk:
        raise HTTPException(400, f"主键列 {unknown_pk} 不在列定义中")

    columns = []
    for definition, column, source_key in raw_columns:
        item = {
            "name": column,
            "display_name": (
                definition.display_name.strip()
                or (
                    source_key
                    if use_stable_field_contract and origin == "upload"
                    else column
                )
            ),
            "type": normalize_field_type(definition.type),
            "nullable": False if column in pk_columns else bool(definition.nullable),
        }
        if use_stable_field_contract:
            item["source_key"] = source_key
        columns.append(item)
    definitions = [{
        "source_key": column["source_key"],
        "field_key": column["name"],
        "field_name": column["display_name"],
        "field_type": column["type"],
        "is_primary_key": column["name"] in pk_columns,
        "nullable": column["nullable"],
    } for column in columns] if use_stable_field_contract else []
    schema: dict = {
        "columns": [column["name"] for column in columns],
        "columns_typed": columns,
        "field_names": {column["name"]: column["display_name"] for column in columns},
        "types_source": "declared",
        "origin": origin,
    }
    if use_stable_field_contract:
        schema["contract_definitions"] = definitions
        schema["manual_field_contract_version"] = MANUAL_FIELD_CONTRACT_VERSION
    if pk_columns:
        schema["primary_key"] = ",".join(pk_columns)
        schema["pk_source"] = "manual"
    return name, schema


def _serialize_manual_contract_rows(rows: list[dict], columns: list[str]) -> bytes:
    """Store normalized manual rows with authoritative field-key headers.

    Non-empty snapshots use JSON to preserve Excel scalar types.  Empty snapshots
    use a header-only CSV because a JSON empty list cannot carry column metadata.
    """
    if rows:
        import json

        def json_default(value) -> str:
            isoformat = getattr(value, "isoformat", None)
            return isoformat() if callable(isoformat) else str(value)

        return json.dumps(
            rows,
            ensure_ascii=False,
            separators=(",", ":"),
            default=json_default,
        ).encode("utf-8")

    import csv
    import io

    output = io.StringIO()
    csv.writer(output).writerow(columns)
    return output.getvalue().encode("utf-8")


def _normalize_manual_contract_upload(
    rows: list[dict],
    physical_columns: list[str],
    schema: dict,
    *,
    dataset_name: str,
    scope: str,
    allow_field_key_headers: bool,
) -> tuple[list[dict], bytes]:
    """Validate one complete manual-file contract and normalize it for the lake.

    The initial import must use the exact original headers captured during
    inspection.  Later versions may use either the complete original-header set
    or the complete stable field-key set.  Reordered, mixed, added or missing
    columns are rejected before a version is created.
    """
    from app.data_channel.datasets.lake_gate import (
        LakeGateError, apply_column_contract, normalize_definitions)

    definitions = normalize_definitions(schema.get("contract_definitions"))
    source_columns = [item["source_key"] for item in definitions]
    field_columns = [item["field_key"] for item in definitions]
    if not definitions or not field_columns:
        raise HTTPException(400, "人工数据集字段契约缺失，无法安全导入文件")

    active_definitions = definitions
    if physical_columns == source_columns:
        pass
    elif allow_field_key_headers and physical_columns == field_columns:
        active_definitions = [
            {**item, "source_key": item["field_key"]}
            for item in definitions
        ]
    else:
        accepted = f"原始表头 {source_columns}"
        if allow_field_key_headers and field_columns != source_columns:
            accepted += f" 或字段标识 {field_columns}"
        raise HTTPException(
            400,
            f"{scope}列结构与字段契约不一致（文件：{physical_columns}；"
            f"允许：{accepted}）。不允许混用、新增、缺失或调整字段顺序",
        )

    try:
        normalized_rows, _warnings = apply_column_contract(
            rows, active_definitions, dataset_name=dataset_name)
    except LakeGateError as exc:
        raise HTTPException(400, str(exc)) from exc
    _validate_manual_rows(
        normalized_rows, schema, dataset_name=dataset_name, scope=scope)
    return (
        normalized_rows,
        _serialize_manual_contract_rows(normalized_rows, field_columns),
    )


def _validate_manual_rows(rows: list[dict], schema: dict, *,
                          dataset_name: str, scope: str) -> None:
    """上传创建与上传新版本共用的主键、非空、类型全量校验。"""
    from app.data_channel.datasets.lake_gate import (
        LakeGateError, split_pk, validate_declared_types, validate_pk)

    try:
        primary_key = split_pk(schema.get("primary_key"))
        if primary_key and rows:
            validate_pk(rows, primary_key, dataset_name=dataset_name, scope=scope)
        required = {
            str(column.get("name"))
            for column in (schema.get("columns_typed") or [])
            if isinstance(column, dict) and column.get("name") and column.get("nullable") is False
        }
        for row_index, row in enumerate(rows):
            for column in sorted(required):
                value = row.get(column)
                if value is None or (isinstance(value, str) and not value.strip()):
                    raise LakeGateError(
                        f"数据集「{dataset_name}」{scope}第 {row_index + 1} 行的非空列「{column}」不能为空")
        validate_declared_types(
            rows, schema.get("columns_typed"), dataset_name=dataset_name)
    except LakeGateError as exc:
        raise HTTPException(400, str(exc))


@router.post("/imports/{job_id}/commit", status_code=202)
def commit_dataset_import_job(
    job_id: str,
    body: CreateTableRequest,
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user),
):
    from app.data_channel.datasets.import_jobs import (
        assert_job_owner, read_status, update_status, write_metadata)
    from app.tasks.v2.dataset_import import commit_dataset_import

    try:
        assert_job_owner(job_id, current_user.id)
        status = read_status(job_id)
    except FileNotFoundError:
        raise HTTPException(404, "导入任务不存在或已被清理")
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    if status.get("status") != "ready":
        raise HTTPException(
            409, f"导入任务当前状态为 {status.get('status') or 'unknown'}，不能提交")

    # Validate the small field contract synchronously; full-file validation runs
    # after the response in either the local background runner or Celery.
    _build_manual_schema(body, origin="upload")
    write_metadata(job_id, body.model_dump())
    update_status(
        job_id,
        status="import_queued",
        progress=5,
        phase="字段设置已确认，等待后台导入",
        error=None,
    )
    queued = _dispatch_dataset_import_task(
        commit_dataset_import,
        job_id,
        background_tasks,
        operation="导入",
    )
    return {"data": queued}


@router.post("/create-table", status_code=201)
def create_online_table(body: CreateTableRequest, db: Session = Depends(get_db)):
    """在线新建空表格（人工数据集）：定义列名/类型/主键，无需上传文件。

    与上传创建的数据集能力完全一致：在「维护数据」中逐行录入（每次保存
    生成新版本）、声明主键后可被本体映射灌入、可作为流水线数据源、也可
    上传文件批量补数。列类型由用户声明（types_source=declared），在线编辑
    时按声明校验，不再随数据重新推断。
    """
    name, schema = _build_manual_schema(body, origin="manual")

    svc = DatasetService(db)
    ds = svc.create_dataset(
        name=name, kind="structured", schema_json=schema, commit=False)

    # 初始版本 = 只有表头的空表：保持「数据集至少有一个版本」的不变式，
    # 存储对象自描述列结构（解析后 0 行，是合法的空数据集状态）
    import csv
    import io
    buf = io.StringIO()
    csv.writer(buf).writerow(schema["columns"])
    ver = svc.create_version(
        ds.id, buf.getvalue().encode("utf-8"), rowcount=0,
        schema_json=schema, _lock_held=True)

    return {"data": {
        "id": ds.id, "name": ds.name, "kind": ds.kind,
        "columns": schema["columns"], "primary_key": schema.get("primary_key", ""),
        "version_no": ver.version_no, "rowcount": 0, "source": "manual",
    }}


@router.post("/{dataset_id}/upload", status_code=201)
async def upload_dataset_version(
    dataset_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """给已有数据集上传新数据文件，追加为新版本（数据集 ID 保持不变，
    流水线中的绑定不需要改动，下次运行自动读取最新版本）。"""
    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    if ds.kind == "curated":
        raise HTTPException(400, "成品数据集由流水线生成，不支持手动上传数据")
    _require_manual_dataset(ds, "手动上传")

    content = await file.read()
    ext = _check_upload_file(file.filename, content)

    from app.data_channel.datasets.lock import DatasetLockTimeout, dataset_write_lock
    try:
        # 上传、在线编辑和流水线入湖必须竞争同一把数据集锁。否则两个请求可能
        # 同时选择 vN+1 并覆盖同一个对象键，造成 checksum 与实际内容错配。
        with dataset_write_lock(f"dataset::{dataset_id}", bind=db.get_bind(), wait_timeout=30):
            db.refresh(ds)
            return _persist_uploaded_version(db, svc, ds, content, ext)
    except DatasetLockTimeout as e:
        raise HTTPException(423, str(e))


def _persist_uploaded_version(db: Session, svc: DatasetService, ds, content: bytes, ext: str) -> dict:
    """持锁执行人工数据集的新版本校验与落盘。"""
    dataset_id = ds.id

    # 契约管理的数据集（声明过主键 / 在线建表 / 编辑过）需要全量行做校验与列刷新
    schema = dict(ds.schema_json or {})
    declared_pk = str(schema.get("primary_key") or "").strip()
    has_manual_field_contract = (
        schema.get("manual_field_contract_version")
        == MANUAL_FIELD_CONTRACT_VERSION
        and bool(schema.get("contract_definitions"))
    )
    full_rows: list[dict] | None = None
    tabular_ext = ext in ("csv", "xlsx", "xls", "json")
    if not tabular_ext and (declared_pk or schema.get("columns")):
        raise HTTPException(
            400, "已定义字段契约的数据集只支持上传 CSV、XLSX、XLS 或 JSON 数据文件")
    from app.data_channel.datasets.service import stored_columns
    try:
        physical_columns = stored_columns(content) if tabular_ext else []
    except Exception as exc:
        raise HTTPException(400, f"表格解析失败：{exc}")
    if declared_pk or schema.get("columns"):
        from app.data_channel.datasets.service import _parse_stored_rows
        try:
            full_rows = _parse_stored_rows(content, limit=None)
        except Exception as e:
            # 只要存在主键或声明类型契约，就必须在解析后的全量行上校验；
            # 解析失败时放行会让“声明 integer、实际任意文本”的坏版本落湖。
            if declared_pk or schema.get("types_source") == "declared":
                raise HTTPException(400, f"文件解析失败，无法校验数据契约：{e}")
            full_rows = None

    if has_manual_field_contract:
        if full_rows is None:
            raise HTTPException(400, "文件解析失败，无法校验人工数据集字段契约")
        full_rows, content = _normalize_manual_contract_upload(
            full_rows,
            physical_columns,
            schema,
            dataset_name=ds.name,
            scope="上传的新版本",
            allow_field_key_headers=True,
        )
        physical_columns = list(schema.get("columns") or [])

    # 已声明主键契约的数据集：即使零行也必须携带完整主键表头。
    if declared_pk and not has_manual_field_contract:
        from app.data_channel.datasets.lake_gate import split_pk
        missing_pk_columns = [c for c in split_pk(declared_pk) if c not in physical_columns]
        if missing_pk_columns:
            raise HTTPException(
                400,
                f"上传的新版本缺少主键列 {missing_pk_columns}；即使文件为零行，表头也必须包含完整主键",
            )
    # 在线建表声明的类型/非空/主键都是数据契约，文件批量补数也必须遵守。
    if (
        not has_manual_field_contract
        and (declared_pk or schema.get("types_source") == "declared")
    ):
        _validate_manual_rows(
            full_rows or [], schema, dataset_name=ds.name, scope="上传的新版本")

    # 记录旧列，供列变化提示（契约管理的数据集用契约列，比首行采样更可靠）
    old_cols = set(schema.get("columns") or [])
    if not old_cols:
        old_rows = svc.preview(dataset_id, None, limit=1)
        old_cols = set(old_rows[0].keys()) if old_rows else set()

    rowcount = len(full_rows) if full_rows is not None else _estimate_rowcount(content, ext)
    # full_rows 已是将要发布的内容；先算 schema，再与版本同一事务提交。
    new_cols = set(full_rows[0].keys()) if full_rows else set(physical_columns)
    columns_added = sorted(new_cols - old_cols) if old_cols else []
    columns_removed = sorted(old_cols - new_cols) if old_cols else []

    # 契约管理的数据集：新文件落盘后同步刷新列结构，否则预览表头/在线编辑器
    # 仍按旧列渲染。声明类型的列保留用户声明，新出现的列按数据推断
    if schema.get("columns") and full_rows and not has_manual_field_contract:
        from app.data_channel.datasets.lake_gate import infer_columns_typed
        inferred = infer_columns_typed(full_rows)
        if schema.get("types_source") == "declared":
            declared = {c.get("name"): c
                        for c in (schema.get("columns_typed") or []) if isinstance(c, dict)}
            inferred = [{
                **(declared.get(c["name"]) or {}),
                "name": c["name"],
                "type": (declared.get(c["name"]) or {}).get("type") or c["type"],
            } for c in inferred]
        schema["columns"] = [c["name"] for c in inferred]
        schema["columns_typed"] = inferred

    ver = svc.create_version(
        dataset_id, content, rowcount=rowcount,
        schema_json=schema if schema.get("columns") and full_rows is not None else None,
        _lock_held=True)

    return {
        "dataset_id": dataset_id,
        "dataset_name": ds.name,
        "version_no": ver.version_no,
        "rowcount": ver.rowcount,
        "columns_added": columns_added,
        "columns_removed": columns_removed,
        "consumers": _dataset_consumers(db, dataset_id),
    }


class ContractRequest(BaseModel):
    primary_key: str  # 逗号分隔支持复合主键


def _require_manual_dataset(ds, action: str):
    """人工维护类操作的准入：成品/同步数据集各有自己的维护通道。"""
    if ds.kind == "curated":
        raise HTTPException(400, f"成品数据集不支持{action}：主键契约由入湖闸门维护，行级修正请走审核编辑")
    if ds.name.startswith("SYNC::") or ds.source_connection_id:
        raise HTTPException(400, f"该数据集由同步任务维护，不支持{action}——人工改动会在下次同步时被覆盖")


@router.put("/{dataset_id}/contract")
def declare_contract(dataset_id: str, body: ContractRequest, db: Session = Depends(get_db)):
    """声明人工数据集的主键契约（存在·非空·唯一三校验，全量数据上验证）。

    声明后：上传新版本/在线编辑都会校验主键；本体映射可直接绑定该数据集，
    实例身份 = 主键值（否则退化为整行哈希，字段一变就堆积新实例）。
    已被本体映射绑定后主键锁定——改主键 = 整批实例身份作废。
    """
    from app.data_channel.datasets.lake_gate import (
        LakeGateError, infer_columns_typed, split_pk, validate_pk)
    from app.services.v2.dataset_service import DatasetReadError

    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    _require_manual_dataset(ds, "主键契约声明")

    pk_cols = split_pk(body.primary_key)
    if not pk_cols:
        raise HTTPException(400, "主键不能为空（支持逗号分隔的复合主键）")
    new_pk = ",".join(pk_cols)

    schema = dict(ds.schema_json or {})
    old_pk = str(schema.get("primary_key") or "").strip()
    if old_pk and old_pk != new_pk:
        from app.ontologies.mappings.consumers import dataset_mapping_bindings

        bindings = dataset_mapping_bindings(db, dataset_id)
        if bindings:
            object_count = sum(
                binding.get("kind") == "object" for binding in bindings)
            link_count = sum(
                binding.get("kind") == "link" for binding in bindings)
            raise HTTPException(400,
                f"该数据集已被 {len(bindings)} 个本体映射绑定"
                f"（对象映射 {object_count} 个、关系映射 {link_count} 个），"
                "主键已锁定（改主键会让对象、关系端点或关系实例身份作废）。"
                f"如确需变更，请先删除相关映射")

    has_declared_column_contract = (
        schema.get("manual_field_contract_version")
        == MANUAL_FIELD_CONTRACT_VERSION
        or schema.get("types_source") == "declared"
    )
    try:
        rows = svc.load_all_rows(dataset_id)
    except DatasetReadError as e:
        raise HTTPException(502, str(e))
    declared_columns = list(schema.get("columns") or [])
    if rows:
        try:
            validate_pk(rows, pk_cols, dataset_name=ds.name, scope="现有数据")
        except LakeGateError as e:
            raise HTTPException(400, str(e))
        # A stable/manual declared contract is authoritative.  Re-inferring it
        # from one snapshot turns string identifiers such as "001" into
        # integers and also drops display/source/nullability metadata.  Only
        # untyped legacy uploads are migrated by inference here.
        if not has_declared_column_contract:
            schema["columns"] = list(rows[0].keys())
            schema["columns_typed"] = infer_columns_typed(rows)
    else:
        missing = [c for c in pk_cols if c not in declared_columns]
        if missing:
            raise HTTPException(
                400,
                f"空数据集也必须先声明包含主键的列结构；当前缺少主键列 {missing}",
            )

    if has_declared_column_contract:
        pk_set = set(pk_cols)
        typed_columns = []
        typed_nullable: dict[str, bool] = {}
        for raw_column in schema.get("columns_typed") or []:
            if not isinstance(raw_column, dict):
                typed_columns.append(raw_column)
                continue
            column = dict(raw_column)
            column_name = str(column.get("name") or "")
            if column_name in pk_set:
                column["nullable"] = False
            if column_name and "nullable" in column:
                typed_nullable[column_name] = bool(column["nullable"])
            typed_columns.append(column)
        schema["columns_typed"] = typed_columns

        definitions = []
        for raw_definition in schema.get("contract_definitions") or []:
            if not isinstance(raw_definition, dict):
                definitions.append(raw_definition)
                continue
            definition = dict(raw_definition)
            field_key = str(definition.get("field_key") or "")
            definition["is_primary_key"] = field_key in pk_set
            if field_key in pk_set:
                definition["nullable"] = False
            elif field_key in typed_nullable:
                definition["nullable"] = typed_nullable[field_key]
            definitions.append(definition)
        if "contract_definitions" in schema:
            schema["contract_definitions"] = definitions

    schema["primary_key"] = new_pk
    schema["pk_source"] = "manual"
    ds.schema_json = schema  # 赋新 dict, 原地改 JSON 列不会被 SQLAlchemy 跟踪
    db.commit()
    return {"dataset_id": dataset_id, "primary_key": new_pk, "rows_validated": len(rows)}


class RowEditOp(BaseModel):
    key: dict | None = None     # 主键列→值（update/delete 用加载时的原值定位）
    values: dict | None = None  # update/insert 的列值


class RowEditsRequest(BaseModel):
    base_version_no: int  # 乐观并发：客户端所见的最新版本号
    updates: list[RowEditOp] = []
    inserts: list[RowEditOp] = []
    deletes: list[RowEditOp] = []


@router.post("/{dataset_id}/rows/edit")
def edit_rows(dataset_id: str, body: RowEditsRequest, db: Session = Depends(get_db)):
    """人工数据集在线维护：改单元格 / 新增行 / 删除行，整体生成一个新版本。

    update/delete 按声明的主键定位行（未声明主键只能追加）；编辑后的全量
    数据重新过主键三校验，坏身份的数据不落盘。base_version_no 不等于当前
    最新版本时返回 409——说明期间有人上传/编辑过，客户端须刷新重做。
    """
    from app.data_channel.datasets.lock import DatasetLockTimeout, dataset_write_lock
    from app.services.v2.dataset_service import rows_to_csv_bytes
    from app.models.v2.dataset import DatasetVersion
    from app.data_channel.datasets.edit_service import build_edited_snapshot

    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    _require_manual_dataset(ds, "在线编辑")
    if not (body.updates or body.inserts or body.deletes):
        raise HTTPException(400, "没有任何修改")

    try:
        with dataset_write_lock(f"dataset::{dataset_id}", bind=db.get_bind(), wait_timeout=30):
            latest = db.query(DatasetVersion).filter(
                DatasetVersion.dataset_id == dataset_id).order_by(DatasetVersion.version_no.desc()).first()
            latest_no = latest.version_no if latest else 0
            if body.base_version_no != latest_no:
                raise HTTPException(409, detail={
                    "message": f"数据已更新到 v{latest_no}（你正在编辑的是 v{body.base_version_no}），请刷新后重新修改",
                    "current_version_no": latest_no,
                })

            new_rows, columns, schema = build_edited_snapshot(db, svc, ds, body)
            ver = svc.create_version(
                dataset_id, rows_to_csv_bytes(new_rows, columns),
                rowcount=len(new_rows),
                schema_json=schema if new_rows else None,
                _lock_held=True)
    except DatasetLockTimeout as e:
        raise HTTPException(423, str(e))

    return {
        "dataset_id": dataset_id,
        "version_no": ver.version_no,
        "rowcount": len(new_rows),
        "updated": len(body.updates),
        "inserted": len(body.inserts),
        "deleted": len(body.deletes),
    }


@router.get("/overview")
def datasets_overview(
    db: Session = Depends(get_db),
    source: str = "",
    search: str = "",
    sort_by: str = "updated_at",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    paginated: bool = False,
):
    """原始数据集总览；人工资产可按创建时间倒序分页。"""
    from app.models.v2.dataset import Dataset, DatasetVersion
    from app.models.v2.connection import Connection

    q = db.query(Dataset).filter(Dataset.kind != "curated")
    if source == "manual":
        # “人工数据集”同时包含文件上传和在线创建，排除历史同步资产。
        q = q.filter(
            Dataset.source_connection_id.is_(None),
            ~Dataset.name.startswith("SYNC::"),
        )
    elif source == "sync":
        q = q.filter(or_(
            Dataset.source_connection_id.is_not(None),
            Dataset.name.startswith("SYNC::"),
        ))
    keyword = search.strip()
    if keyword:
        q = q.filter(Dataset.name.ilike(f"%{keyword}%"))
    total = q.count()
    order_column = Dataset.created_at if sort_by == "created_at" else Dataset.updated_at
    ordered = q.order_by(order_column.desc(), Dataset.id.desc())
    datasets = (ordered.offset((page - 1) * page_size).limit(page_size).all()
                if paginated else ordered.all())
    consumer_map = _consumer_map(db)
    conn_names = {c.id: c.name for c in db.query(Connection).all()}

    # 一次取全部版本，避免 N+1
    versions_by_ds: dict[str, list[DatasetVersion]] = {}
    dataset_ids = [dataset.id for dataset in datasets]
    version_query = db.query(DatasetVersion)
    if dataset_ids:
        version_query = version_query.filter(DatasetVersion.dataset_id.in_(dataset_ids))
    else:
        version_query = version_query.filter(False)
    for v in version_query.order_by(DatasetVersion.version_no).all():
        versions_by_ds.setdefault(v.dataset_id, []).append(v)

    items = []
    for ds in datasets:
        vers = versions_by_ds.get(ds.id, [])
        latest = vers[-1] if vers else None
        is_sync = ds.name.startswith("SYNC::") or bool(ds.source_connection_id)
        is_manual = (ds.schema_json or {}).get("origin") == "manual"
        items.append({
            "id": ds.id,
            "name": ds.name.removeprefix("SYNC::"),
            "raw_name": ds.name,
            "kind": ds.kind,
            "primary_key": str((ds.schema_json or {}).get("primary_key") or ""),
            # sync 优先于 manual：同步维护的数据集不允许被在线编辑语义覆盖
            "source": "sync" if is_sync else ("manual" if is_manual else "upload"),
            "connection_name": conn_names.get(ds.source_connection_id or "", ""),
            "version_count": len(vers),
            "latest_version_no": latest.version_no if latest else 0,
            "rowcount": latest.rowcount if latest else None,
            "consumers": consumer_map.get(ds.id, []),
            "created_at": ds.created_at.isoformat() if ds.created_at else None,
            "updated_at": ds.updated_at.isoformat() if ds.updated_at else None,
        })
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{dataset_id}/consumers")
def dataset_consumers(dataset_id: str, db: Session = Depends(get_db)):
    """查询哪些流水线使用该数据集作为数据源"""
    svc = DatasetService(db)
    if not svc.get_dataset(dataset_id):
        raise HTTPException(404, "Dataset not found")
    return {"dataset_id": dataset_id, "consumers": _dataset_consumers(db, dataset_id)}


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: str, force: bool = False, db: Session = Depends(get_db),
                   _admin=Depends(require_admin)):
    """删除原始数据集及其版本（仅管理员，与成品数据集删除权限对齐）。
    被流水线 / 本体映射引用时始终返回 409；force 已禁用，避免数据库外键与页面
    “强删成功”语义不一致。
    若数据集由旧版同步任务（DataSyncTask）驱动，自动禁用该任务防止重建。"""
    import logging
    from app.models.v2.dataset import Dataset, DatasetVersion, MediaItem
    from app.data_channel.file_assets.models import PipelineFileAsset
    from app.models.v2.sync_task import DataSyncTask
    from app.data_channel.datasets.service import (
        drain_storage_deletion_outbox,
        enqueue_dataset_storage_deletions,
    )

    _logger = logging.getLogger(__name__)

    ds = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not ds:
        raise HTTPException(404, "Dataset not found")
    if ds.kind == "curated":
        raise HTTPException(400, "成品数据集请在资产湖「成品数据集」中删除")
    if force:
        raise HTTPException(400, "force 强制删除已禁用；请先解除流水线和本体映射依赖")

    consumers = _dataset_consumers(db, dataset_id)
    from app.ontologies.mappings.consumers import dataset_mapping_bindings
    mappings = dataset_mapping_bindings(db, dataset_id)
    if consumers or mappings:
        raise HTTPException(409, detail={
            "message": f"数据集被 {len(consumers)} 条流水线、{len(mappings)} 个本体映射引用，"
                       f"删除后这些流水线将无法运行、本体投影将断源",
            "consumers": consumers,
            "mappings": mappings,
        })

    # ── 联动禁用关联的 DataSyncTask（防止 _get_or_create_dataset 重建）──
    disabled_sync_task: str | None = None
    if ds.name.startswith("SYNC::"):
        legacy_task_name = ds.name.removeprefix("SYNC::")
        sync_task = db.query(DataSyncTask).filter(DataSyncTask.name == legacy_task_name).first()
        if sync_task and sync_task.enabled:
            sync_task.enabled = False
            disabled_sync_task = sync_task.name
            _logger.info(
                "DELETE dataset %s → 已自动禁用关联同步任务「%s」(id=%s)",
                dataset_id, sync_task.name, sync_task.id,
            )
            # 通知调度器移除此 Job
            try:
                from app.data_channel.sync_tasks.scheduler import SyncScheduler
                sched = SyncScheduler.get()
                if sched.started:
                    sched.reload_task(sync_task.id)
            except Exception:
                _logger.warning("DELETE dataset %s → 调度器 reload 失败，任务可能仍会执行一次", dataset_id)

    # 必须在删版本/媒体元数据之前收集 URI，并与这些删除共用一次 commit。
    # commit 失败则 outbox 与元数据删除一起回滚；commit 成功后再 best-effort drain。
    enqueue_dataset_storage_deletions(db, dataset_id)
    ver_ids = [v.id for v in db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset_id).all()]
    if ver_ids:
        db.query(MediaItem).filter(MediaItem.dataset_version_id.in_(ver_ids)).delete(synchronize_session=False)
        db.query(PipelineFileAsset).filter(
            PipelineFileAsset.dataset_version_id.in_(ver_ids)
        ).delete(synchronize_session=False)
    db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset_id).delete(synchronize_session=False)
    db.delete(ds)
    db.commit()
    storage_cleanup = drain_storage_deletion_outbox(db)

    result: dict = {
        "status": "deleted", "id": dataset_id,
        "storage_cleanup": storage_cleanup,
    }
    if disabled_sync_task:
        result["disabled_sync_task"] = disabled_sync_task
        result["message"] = f"已同时禁用同步任务「{disabled_sync_task}」，该数据集不会在下次调度时重建"
    return result

@router.get("", response_model=list[DatasetResponse])
def list_datasets(kind: str | None = None, db: Session = Depends(get_db)):
    svc = DatasetService(db)
    return svc.list_datasets(kind=kind)

@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(dataset_id: str, db: Session = Depends(get_db)):
    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return ds

@router.get("/{dataset_id}/versions")
def list_versions(dataset_id: str, db: Session = Depends(get_db)):
    svc = DatasetService(db)
    versions = svc.list_versions(dataset_id)
    from app.models.v2.dataset import DatasetVersionEvent
    events = {
        event.dataset_version_id: event
        for event in db.query(DatasetVersionEvent).filter(
            DatasetVersionEvent.dataset_id == dataset_id,
            DatasetVersionEvent.event_type == "version_published",
        ).all()
    }
    return [{
        "id": version.id,
        "version_no": version.version_no,
        "rowcount": version.rowcount,
        "storage_uri": version.storage_uri,
        "automation": ({
            "status": events[version.id].status,
            "attempts": events[version.id].attempts,
            "last_error": events[version.id].last_error,
            "result": events[version.id].result_json,
            "processed_at": (
                events[version.id].processed_at.isoformat()
                if events[version.id].processed_at else None),
        } if version.id in events else None),
    } for version in versions]


def _require_curated_preview_approved(db: Session, dataset, version) -> None:
    """通用 Dataset preview 不得绕过成品资产审核门禁。"""
    if dataset.kind != "curated":
        return
    from app.data_channel.curated.review_service import (
        ReviewApprovalError,
        require_version_approved,
        version_review,
    )

    if version is None:
        raise HTTPException(409, detail={
            "code": "dataset_version_not_approved",
            "message": "该成品数据集尚无可预览的数据版本。",
        })
    try:
        require_version_approved(db, dataset.id, version)
    except ReviewApprovalError as exc:
        review = version_review(db, dataset.id, version)
        rejected = review is not None and review.status == "rejected"
        raise HTTPException(409, detail={
            "code": (
                "dataset_version_rejected"
                if rejected else "dataset_version_not_approved"
            ),
            "message": (
                f"数据版本 v{version.version_no} 已拒绝，仅可通过审核差异查看审计快照，"
                "不能用于普通预览或进入本体。"
                if rejected else
                f"数据版本 v{version.version_no} 尚未通过审核，不能用于普通预览或进入本体。"
            ),
            "dataset_version_id": version.id,
            "version_no": version.version_no,
            "review_status": review.status if review is not None else "pending_review",
        }) from exc


@router.get("/{dataset_id}/versions/{version_no}/preview")
def preview_data(dataset_id: str, version_no: int, limit: int = 100, db: Session = Depends(get_db)):
    from app.models.v2.dataset import DatasetVersion

    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    version = db.query(DatasetVersion).filter(
        DatasetVersion.dataset_id == dataset_id,
        DatasetVersion.version_no == version_no,
    ).first()
    _require_curated_preview_approved(db, ds, version)
    return svc.preview(dataset_id, version_no, limit)


@router.get("/{dataset_id}/schema")
def get_schema(dataset_id: str, db: Session = Depends(get_db)):
    """返回数据集字段契约：标识、中文显示名、类型、空值约束与主键。"""
    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")

    # 人工建表或已发布流水线声明过类型的数据集：类型是权威契约，直接返回
    # 声明值而非从物理快照重推断。成品快照为兼容历史 CSV 语义会把标量保存
    # 为字符串；若在这里重推断，会把 float(integer 样值)、timestamp 和
    # boolean 分别误报为 integer/string/string。
    schema_json = ds.schema_json or {}
    from app.data_channel.datasets.lake_gate import split_pk
    pk_columns = set(split_pk(schema_json.get("primary_key")))
    field_names = schema_json.get("field_names") or {}
    definitions = {
        str(item.get("field_key")): item
        for item in (schema_json.get("contract_definitions") or [])
        if isinstance(item, dict) and item.get("field_key")
    }
    typed_columns = {
        str(item.get("name")): item
        for item in (schema_json.get("columns_typed") or [])
        if isinstance(item, dict) and item.get("name")
    }

    def column_contract(name: str, column: dict | None = None) -> dict:
        column = {**(typed_columns.get(name) or {}), **(column or {})}
        definition = definitions.get(name) or {}
        display_name_configured = any((
            bool(str(column.get("display_name") or "").strip()),
            bool(str(column.get("field_name") or "").strip()),
            name in field_names and bool(str(field_names.get(name) or "").strip()),
            bool(str(definition.get("field_name") or "").strip()),
        ))
        display_name = str(
            column.get("display_name")
            or column.get("field_name")
            or field_names.get(name)
            or definition.get("field_name")
            or name
        )
        nullable = bool(column.get(
            "nullable", definition.get("nullable", name not in pk_columns)))
        return {
            "name": name,
            "display_name": display_name,
            "display_name_configured": display_name_configured,
            "type": column.get("type") or definition.get("field_type") or "string",
            "nullable": False if name in pk_columns else nullable,
            "is_primary_key": name in pk_columns,
        }

    if (
        schema_json.get("types_source")
        in {"declared", "published_pipeline_contract"}
        and schema_json.get("columns_typed")
    ):
        rows = svc.preview(dataset_id, None, limit=10)
        columns = []
        for c in schema_json["columns_typed"]:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            name = c["name"]
            samples = [row.get(name) for row in rows if row.get(name) not in (None, "")][:5]
            columns.append({**column_contract(name, c), "sample_values": samples})
        return {"dataset_id": dataset_id, "columns": columns}

    # Use latest version for schema inference
    versions = svc.list_versions(dataset_id)
    if not versions:
        return {"dataset_id": dataset_id, "columns": []}

    latest_version_no = versions[-1].version_no
    rows = svc.preview(dataset_id, latest_version_no, limit=10)
    if not rows:
        return {"dataset_id": dataset_id, "columns": []}

    columns = []
    all_keys = list(rows[0].keys()) if rows else []
    for key in all_keys:
        sample_values = [row.get(key) for row in rows if row.get(key) is not None][:5]
        # Infer type from sample values
        col_type = "string"
        for val in sample_values:
            if isinstance(val, bool):
                col_type = "boolean"
                break
            elif isinstance(val, int):
                col_type = "integer"
                break
            elif isinstance(val, float):
                col_type = "float"
                break
            elif isinstance(val, str):
                try:
                    int(val)
                    col_type = "integer"
                except ValueError:
                    try:
                        float(val)
                        col_type = "float"
                    except ValueError:
                        col_type = "string"
                break
        columns.append({
            **column_contract(key, {"type": col_type}),
            "sample_values": sample_values,
        })

    return {"dataset_id": dataset_id, "columns": columns}


@router.get("/{dataset_id}/export")
def export_dataset(dataset_id: str, format: str = Query("csv", pattern="^(csv|xlsx)$"),
                   db: Session = Depends(get_db)):
    """导出人工数据集最新版本的全部行，格式为 CSV 或 Excel。"""
    import io
    from urllib.parse import quote
    from app.services.v2.dataset_service import DatasetReadError, rows_to_csv_bytes

    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    _require_manual_dataset(ds, "导出")
    try:
        rows = svc.load_all_rows(dataset_id)
    except DatasetReadError as exc:
        raise HTTPException(502, str(exc))

    schema_columns = list((ds.schema_json or {}).get("columns") or [])
    columns = schema_columns or (list(rows[0].keys()) if rows else [])
    safe_name = "".join(c for c in ds.name if c not in '\\/:*?"<>|').strip() or "人工数据集"
    filename = f"{safe_name}.{format}"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"

    if format == "csv":
        # UTF-8 BOM 让 Excel 直接打开中文 CSV 时不乱码。
        data = b"\xef\xbb\xbf" + rows_to_csv_bytes(rows, columns)
        return StreamingResponse(
            io.BytesIO(data), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": disposition},
        )

    import openpyxl
    workbook = openpyxl.Workbook(write_only=True)
    sheet = workbook.create_sheet(title="数据")
    sheet.append(columns)
    for row in rows:
        values = []
        for column in columns:
            value = row.get(column, "")
            if isinstance(value, (dict, list)):
                import json
                value = json.dumps(value, ensure_ascii=False)
            values.append(value)
        sheet.append(values)
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
    )


@router.get("/{dataset_id}/stats")
def get_stats(dataset_id: str, db: Session = Depends(get_db)):
    """返回数据集统计信息"""
    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")

    versions = svc.list_versions(dataset_id)
    version_count = len(versions)

    # Use latest version for row/column counts and null rates
    row_count = 0
    column_count = 0
    null_rates: dict = {}

    if versions:
        latest = versions[-1]
        row_count = latest.rowcount or 0
        rows = svc.preview(dataset_id, latest.version_no, limit=100)
        if rows:
            column_count = len(rows[0].keys())
            # Compute null rates per column
            for key in rows[0].keys():
                null_count = sum(1 for row in rows if row.get(key) is None or row.get(key) == "")
                null_rates[key] = round(null_count / len(rows), 4)

    return {
        "dataset_id": dataset_id,
        "row_count": row_count,
        "column_count": column_count,
        "null_rates": null_rates,
        "version_count": version_count,
    }


@router.get("/{dataset_id}/preview")
def preview_dataset(dataset_id: str, limit: int = 20, offset: int = 0, db: Session = Depends(get_db)):
    """预览数据集最新版本的数据，支持 offset/limit 分页。默认前 20 行。"""
    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    versions = svc.list_versions(dataset_id)
    if not versions:
        return {"dataset_id": dataset_id, "rows": [], "columns": [], "total_rows": 0,
                "offset": 0, "limit": limit}
    latest = versions[-1]
    _require_curated_preview_approved(db, ds, latest)
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    rows = svc.preview(dataset_id, latest.version_no, limit=limit, offset=offset)
    # 分页表头稳定性：offset>0 的页可能因该页某列全空而缺列，优先用契约列
    schema_cols = (ds.schema_json or {}).get("columns") if ds.schema_json else None
    columns = list(schema_cols) if schema_cols else (list(rows[0].keys()) if rows else [])
    return {
        "dataset_id": dataset_id,
        "dataset_name": ds.name,
        "version_no": latest.version_no,
        "total_rows": latest.rowcount or 0,
        "offset": offset,
        "limit": limit,
        "columns": columns,
        "rows": rows,
    }
