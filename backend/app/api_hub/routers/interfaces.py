import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, List, Literal
from urllib.parse import urlsplit

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from starlette.datastructures import FormData, UploadFile

from .. import config, db, executor, mcp_contract, publication

router = APIRouter(prefix="/interfaces", tags=["api-hub-interfaces"])

_RESERVED_GROUP = "默认分组"
_SLOW_RUN_MS = 500
_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_ALLOWED_BODY_TYPES = {"none", "json", "form", "multipart", "raw"}
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_PROXY_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PROXY_RESERVED_HEADERS = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "upgrade",
}
_RAW_RESPONSE_BLOCKLIST = _PROXY_RESERVED_HEADERS | {
    "content-encoding",  # requests transparently decompresses upstream content
    "set-cookie",       # never write an upstream cookie into the platform origin
}


def _check_group_name(name: str):
    """预留分组名拦截。"""
    if name and name.strip() == _RESERVED_GROUP:
        raise HTTPException(status_code=400, detail=f"「{_RESERVED_GROUP}」为保留名称，请使用其他名称")


class KV(BaseModel):
    key: str = ""
    value: str = ""


class FileField(BaseModel):
    key: str = ""
    accept: str = ""
    multiple: bool = False


class InterfaceParameter(BaseModel):
    """Machine-readable contract used by Agents and n8n bindings.

    Saved request values remain the source of defaults.  This schema describes
    which values callers may provide dynamically without exposing credentials.
    """

    name: str
    location: Literal["path", "query", "header", "body"]
    value_type: Literal["string", "integer", "number", "boolean", "object", "array"] = "string"
    required: bool = False
    default: Any = None
    description: str = ""
    sensitive: bool = False
    dynamic: bool = True

    @field_validator("name")
    @classmethod
    def validate_parameter_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 500 or "\r" in value or "\n" in value:
            raise ValueError("接口参数名无效")
        return value

    @field_validator("description")
    @classmethod
    def validate_parameter_description(cls, value: str) -> str:
        if len(value) > 2000:
            raise ValueError("接口参数说明不能超过 2000 个字符")
        return value


class InterfaceIn(BaseModel):
    name: str = "未命名接口"
    description: str = ""
    group_name: str = ""
    method: str = "GET"
    url: str = ""
    query_params: List[KV] = Field(default_factory=list)
    headers: List[KV] = Field(default_factory=list)
    body_type: str = "none"   # none | json | form | multipart | raw
    body_content: str = ""
    file_fields: List[FileField] = Field(default_factory=list)
    use_w3: bool = False
    mcp_enabled: bool = False
    open_enabled: bool = False
    http_enabled: bool = False
    proxy_slug: str = ""
    proxy_query_keys: List[str] = Field(default_factory=list)
    proxy_header_keys: List[str] = Field(default_factory=list)
    proxy_body_enabled: bool = False
    proxy_body_keys: List[str] = Field(default_factory=list)
    parameter_schema: List[InterfaceParameter] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("接口名称不能为空")
        if len(value) > 200:
            raise ValueError("接口名称不能超过 200 个字符")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if len(value) > 20_000:
            raise ValueError("用途说明不能超过 20000 个字符")
        return value

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        value = value.upper().strip()
        if value not in _ALLOWED_METHODS:
            raise ValueError("不支持的 HTTP 方法")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("请求 URL 必须是无内嵌账号信息的 HTTP/HTTPS 绝对地址")
        if len(value) > 4096:
            raise ValueError("请求 URL 不能超过 4096 个字符")
        return value

    @field_validator("body_type")
    @classmethod
    def validate_body_type(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in _ALLOWED_BODY_TYPES:
            raise ValueError("不支持的请求 Body 类型")
        return value

    @field_validator("body_content")
    @classmethod
    def validate_body_size(cls, value: str) -> str:
        if len(value.encode("utf-8")) > config.PROXY_MAX_REQUEST_BYTES:
            raise ValueError("请求 Body 超过平台允许的最大长度")
        return value

    @field_validator("query_params")
    @classmethod
    def validate_query_params(cls, value: List[KV]) -> List[KV]:
        for item in value:
            if "\r" in item.key or "\n" in item.key:
                raise ValueError("查询参数名不能包含换行符")
            if len(item.key) > 500 or len(item.value) > 100_000:
                raise ValueError("查询参数过长")
        return value

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: List[KV]) -> List[KV]:
        for item in value:
            key = item.key.strip()
            if key and not _HEADER_NAME_RE.fullmatch(key):
                raise ValueError(f"Header 名称无效：{key}")
            if "\r" in item.value or "\n" in item.value:
                raise ValueError(f"Header 值不能包含换行符：{key}")
            if len(item.value) > 100_000:
                raise ValueError(f"Header 值过长：{key}")
            item.key = key
        return value

    @field_validator("file_fields")
    @classmethod
    def validate_file_fields(cls, value: List[FileField]) -> List[FileField]:
        if len(value) > 50:
            raise ValueError("文件字段不能超过 50 个")
        seen = set()
        for item in value:
            key = item.key.strip()
            marker = key.lower()
            if key and not _HEADER_NAME_RE.fullmatch(key):
                raise ValueError(f"文件字段名称无效：{key}")
            if marker and marker in seen:
                raise ValueError(f"文件字段名称重复：{key}")
            if len(item.accept) > 500:
                raise ValueError(f"文件类型限制过长：{key}")
            item.key = key
            item.accept = item.accept.strip()
            if marker:
                seen.add(marker)
        return value

    @field_validator("parameter_schema")
    @classmethod
    def validate_parameter_schema(
        cls, value: List[InterfaceParameter]
    ) -> List[InterfaceParameter]:
        if len(value) > 200:
            raise ValueError("接口参数定义不能超过 200 个")
        seen = set()
        for item in value:
            marker = (item.location, item.name.lower() if item.location == "header" else item.name)
            if marker in seen:
                raise ValueError(f"接口参数定义重复：{item.location}.{item.name}")
            if item.sensitive and item.dynamic:
                raise ValueError(f"敏感参数不能开放动态覆盖：{item.location}.{item.name}")
            seen.add(marker)
        return value


class PreviewInterfaceIn(InterfaceIn):
    id: int | None = Field(default=None, gt=0)


def _body_form_pairs(text: str) -> list[tuple[str, str]]:
    pairs = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            pairs.append((key, value.strip()))
    return pairs


def _matches_file_accept(filename: str, content_type: str, accept: str) -> bool:
    rules = [item.strip().lower() for item in (accept or "").split(",") if item.strip()]
    if not rules:
        return True
    filename = (filename or "").lower()
    content_type = (content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    return any(
        rule == "*/*"
        or (rule.startswith(".") and filename.endswith(rule))
        or (rule.endswith("/*") and content_type.startswith(rule[:-1]))
        or rule == content_type
        for rule in rules
    )


def _raw_run_response(result: dict) -> Response:
    if result.get("status_code") is None:
        status = {
            "timeout": 504,
            "overloaded": 503,
        }.get(result.get("error_type"), 502)
        headers = {"Retry-After": "1"} if result.get("error_type") == "overloaded" else None
        return JSONResponse(
            status_code=status,
            content={
                "detail": result.get("error") or "真实接口调用失败",
                "run_id": result.get("run_id"),
            },
            headers=headers,
        )
    headers = {
        key: value
        for key, value in (result.get("response_headers") or {}).items()
        if key.lower() not in _RAW_RESPONSE_BLOCKLIST
    }
    headers.update(
        {
            "X-Api-Hub-Upstream": "1",
            "X-Api-Hub-Run-Id": str(result.get("run_id") or ""),
            "X-Api-Hub-Elapsed-Ms": str(result.get("elapsed_ms") or 0),
            "X-Api-Hub-Relogin": "1" if result.get("relogin") else "0",
        }
    )
    return Response(
        content=result.get("response_content") or b"",
        status_code=result["status_code"],
        headers=headers,
        media_type=None,
    )


def _load_json_list(value) -> list:
    try:
        data = json.loads(value) if value else []
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _normalize_publish_keys(items: List[str], *, lower: bool = False) -> list[str]:
    out = []
    seen = set()
    for item in items:
        key = (item or "").strip()
        marker = key.lower() if lower else key
        if not key or marker in seen:
            continue
        seen.add(marker)
        out.append(key)
    return out


def _validate_proxy_publish(
    conn, body: InterfaceIn, iid: int | None = None
) -> tuple[str, list[str], list[str], list[str]]:
    slug = (body.proxy_slug or "").strip().lower()
    query_keys = _normalize_publish_keys(body.proxy_query_keys)
    header_keys = _normalize_publish_keys(body.proxy_header_keys, lower=True)
    body_keys = _normalize_publish_keys(body.proxy_body_keys)
    if not body.proxy_body_enabled:
        body_keys = []

    if slug and not _PROXY_SLUG_RE.fullmatch(slug):
        raise HTTPException(
            status_code=400,
            detail="HTTP 公开路径只能包含小写字母、数字、短横线和下划线，长度 1-64 位",
        )
    if body.http_enabled:
        if not slug:
            raise HTTPException(status_code=400, detail="发布 HTTP 接口前必须填写公开路径")
        if not (body.url or "").strip():
            raise HTTPException(status_code=400, detail="发布 HTTP 接口前必须填写真实 URL")
        sql = "SELECT id FROM interfaces WHERE proxy_slug = ? AND http_enabled = 1"
        params: list = [slug]
        if iid is not None:
            sql += " AND id <> ?"
            params.append(iid)
        if conn.execute(sql, params).fetchone():
            raise HTTPException(status_code=409, detail=f"HTTP 公开路径「{slug}」已被其它接口使用")

    reserved = _PROXY_RESERVED_HEADERS | {config.PROXY_KEY_HEADER.lower()}
    blocked = [key for key in header_keys if key.lower() in reserved]
    if blocked:
        raise HTTPException(
            status_code=400,
            detail="以下 Header 由平台代理层管理，不能配置为透传项：" + ", ".join(blocked),
        )
    if body_keys and body.body_type not in {"json", "form", "multipart"}:
        raise HTTPException(status_code=400, detail="只有 JSON、Form 或 Multipart Body 支持字段级开放")
    if body.body_type == "json" and any(not key.startswith("/") for key in body_keys):
        raise HTTPException(status_code=400, detail="JSON Body 字段路径必须以 / 开头")
    return slug, query_keys, header_keys, body_keys


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "group_name": row["group_name"],
        "method": row["method"],
        "url": row["url"],
        "query_params": _load_json_list(row["query_params"]),
        "headers": _load_json_list(row["headers"]),
        "body_type": row["body_type"],
        "body_content": row["body_content"],
        "file_fields": _load_json_list(row["file_fields"]),
        "use_w3": bool(row["use_w3"]),
        # ``mcp_enabled`` is retained only for backup compatibility.  The one
        # authoritative MCP state is ``open_enabled`` so the UI has exactly
        # two publication concepts: MCP and HTTP.
        "mcp_enabled": bool(row["open_enabled"]),
        "open_enabled": bool(row["open_enabled"]),
        "http_enabled": bool(row["http_enabled"]),
        "proxy_slug": row["proxy_slug"],
        "proxy_query_keys": _load_json_list(row["proxy_query_keys"]),
        "proxy_header_keys": _load_json_list(row["proxy_header_keys"]),
        "proxy_body_enabled": bool(row["proxy_body_enabled"]),
        "proxy_body_keys": _load_json_list(row["proxy_body_keys"]),
        "parameter_schema": _load_json_list(row["parameter_schema"]),
        "config_revision": int(row["config_revision"]),
        "created_by": row["created_by"],
        "updated_by": row["updated_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _dump_kv(items: List[KV]) -> str:
    return json.dumps([kv.model_dump() for kv in items], ensure_ascii=False)


def _get_or_404(conn, iid: int):
    row = conn.execute("SELECT * FROM interfaces WHERE id = ?", (iid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="接口不存在")
    return row


@router.get("")
def list_interfaces():
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM interfaces ORDER BY group_name, sort_order, id"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


@router.post("")
def create_interface(body: InterfaceIn):
    _check_group_name(body.group_name)
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        slug, query_keys, header_keys, body_keys = _validate_proxy_publish(conn, body)
        cur = conn.execute(
            "INSERT INTO interfaces(name, description, group_name, method, url, query_params, headers, "
            "body_type, body_content, file_fields, use_w3, mcp_enabled, open_enabled, http_enabled, proxy_slug, "
            "proxy_query_keys, proxy_header_keys, proxy_body_enabled, proxy_body_keys, parameter_schema, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                body.name, body.description, body.group_name, body.method.upper(), body.url,
                _dump_kv(body.query_params), _dump_kv(body.headers),
                body.body_type, body.body_content,
                json.dumps([item.model_dump() for item in body.file_fields], ensure_ascii=False),
                1 if body.use_w3 else 0, 1 if body.open_enabled else 0,
                1 if body.open_enabled else 0, 1 if body.http_enabled else 0, slug,
                json.dumps(query_keys, ensure_ascii=False),
                json.dumps(header_keys, ensure_ascii=False),
                1 if body.proxy_body_enabled else 0,
                json.dumps(body_keys, ensure_ascii=False),
                json.dumps([item.model_dump(mode="json") for item in body.parameter_schema], ensure_ascii=False),
                now, now,
            ),
        )
        row = conn.execute("SELECT * FROM interfaces WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


@router.post("/preview-run")
def preview_run(body: PreviewInterfaceIn):
    """执行当前编辑器草稿，不隐式保存接口配置。"""
    if body.id is not None:
        with db.get_conn() as conn:
            _get_or_404(conn, body.id)
    iface = body.model_dump(mode="json")
    return executor.run_interface(iface)


@router.post("/preview-run/raw")
async def preview_run_raw(request: Request):
    """Execute the editor draft and return the upstream bytes unchanged.

    JSON requests cover ordinary bodies. Multipart requests carry the serialized
    draft in ``__interface`` and runtime files under their configured field names.
    """
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > config.PROXY_MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="请求体超过平台调用上限")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Length 无效") from exc

    form: FormData | None = None
    overrides = executor.RequestOverrides(source="ui")
    try:
        content_type = (request.headers.get("content-type") or "").lower()
        if content_type.startswith("multipart/form-data"):
            form = await request.form(max_files=50, max_fields=200)
            draft_json = form.get("__interface")
            if not isinstance(draft_json, str):
                raise HTTPException(status_code=422, detail="缺少接口调用配置")
            try:
                body = PreviewInterfaceIn.model_validate_json(draft_json)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="接口调用配置无效") from exc

            configured = {item.key: item for item in body.file_fields if item.key}
            file_counts: dict[str, int] = {}
            files: list[executor.RequestFile] = []
            total_size = len(draft_json.encode("utf-8"))
            for field_name, value in form.multi_items():
                if field_name == "__interface":
                    continue
                if not isinstance(value, UploadFile):
                    raise HTTPException(status_code=400, detail=f"文件字段 {field_name} 格式无效")
                definition = configured.get(field_name)
                if definition is None:
                    raise HTTPException(status_code=400, detail=f"文件字段未在接口中配置：{field_name}")
                file_counts[field_name] = file_counts.get(field_name, 0) + 1
                if file_counts[field_name] > 1 and not definition.multiple:
                    raise HTTPException(status_code=400, detail=f"文件字段不允许多文件：{field_name}")
                if not _matches_file_accept(
                    value.filename or "", value.content_type or "", definition.accept
                ):
                    raise HTTPException(status_code=400, detail=f"文件类型不符合字段限制：{field_name}")
                size = value.size
                if size is not None:
                    total_size += size
                files.append(
                    executor.RequestFile(
                        field_name=field_name,
                        filename=value.filename or "upload",
                        stream=value.file,
                        content_type=value.content_type or "application/octet-stream",
                        size=size,
                    )
                )
            if total_size > config.PROXY_MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="请求体超过平台调用上限")
            overrides.multipart_fields = _body_form_pairs(body.body_content)
            overrides.files = files
        else:
            try:
                body = PreviewInterfaceIn.model_validate(await request.json())
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="接口调用配置无效") from exc
            if body.body_type == "multipart":
                overrides.multipart_fields = _body_form_pairs(body.body_content)
                overrides.files = []

        if body.id is not None:
            with db.get_conn() as conn:
                _get_or_404(conn, body.id)
        iface = body.model_dump(mode="json")
        result = await anyio.to_thread.run_sync(
            lambda: executor.run_interface(
                iface, overrides, include_response_content=True
            )
        )
        return _raw_run_response(result)
    finally:
        if form is not None:
            await form.close()


@router.get("/{iid}")
def get_interface(iid: int):
    with db.get_conn() as conn:
        row = _get_or_404(conn, iid)
    return _row_to_dict(row)


@router.get("/{iid}/mcp-contract")
def get_mcp_contract(iid: int):
    """Expose the exact, secret-free MCP contract for the management UI.

    This preview is available before an interface is enabled for MCP so an
    administrator can verify the mapping first.  The public MCP endpoint still
    checks ``open_enabled`` again at call time.
    """
    with db.get_conn() as conn:
        row = _get_or_404(conn, iid)
    interface = _row_to_dict(row)
    return {
        "interface_id": interface["id"],
        "interface_name": interface["name"],
        "open_enabled": interface["open_enabled"],
        "parameters": mcp_contract.public_parameters(interface),
        "call_example": mcp_contract.call_example(interface),
    }


@router.put("/{iid}")
def update_interface(iid: int, body: InterfaceIn):
    _check_group_name(body.group_name)
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        _get_or_404(conn, iid)
        slug, query_keys, header_keys, body_keys = _validate_proxy_publish(conn, body, iid)
        conn.execute(
            "UPDATE interfaces SET name=?, description=?, group_name=?, method=?, url=?, query_params=?, "
            "headers=?, body_type=?, body_content=?, file_fields=?, use_w3=?, mcp_enabled=?, open_enabled=?, "
            "http_enabled=?, proxy_slug=?, proxy_query_keys=?, proxy_header_keys=?, "
            "proxy_body_enabled=?, proxy_body_keys=?, parameter_schema=?, "
            "config_revision=config_revision+1, updated_at=? WHERE id=?",
            (
                body.name, body.description, body.group_name, body.method.upper(), body.url,
                _dump_kv(body.query_params), _dump_kv(body.headers),
                body.body_type, body.body_content,
                json.dumps([item.model_dump() for item in body.file_fields], ensure_ascii=False),
                1 if body.use_w3 else 0, 1 if body.open_enabled else 0,
                1 if body.open_enabled else 0, 1 if body.http_enabled else 0, slug,
                json.dumps(query_keys, ensure_ascii=False),
                json.dumps(header_keys, ensure_ascii=False),
                1 if body.proxy_body_enabled else 0,
                json.dumps(body_keys, ensure_ascii=False),
                json.dumps([item.model_dump(mode="json") for item in body.parameter_schema], ensure_ascii=False),
                now, iid,
            ),
        )
        row = conn.execute("SELECT * FROM interfaces WHERE id = ?", (iid,)).fetchone()
    return _row_to_dict(row)


@router.delete("/{iid}")
def delete_interface(iid: int):
    with db.get_conn() as conn:
        _get_or_404(conn, iid)
        # 硬删除接口时，显式连带头删除其全部调用记录，避免残留冗余数据。
        # （虽然 schema 上有 ON DELETE CASCADE，这里不依赖隐式级联，确保一定删干净。）
        conn.execute("DELETE FROM runs WHERE interface_id = ?", (iid,))
        conn.execute("DELETE FROM interfaces WHERE id = ?", (iid,))
    return {"ok": True}


class OpenBody(BaseModel):
    open: bool


class HttpPublishIn(BaseModel):
    enabled: bool = False
    slug: str = ""
    query_keys: List[str] = Field(default_factory=list)
    header_keys: List[str] = Field(default_factory=list)
    body_enabled: bool = False
    body_keys: List[str] = Field(default_factory=list)


class MoveBody(BaseModel):
    group_name: str = ""
    target_index: int = 0  # 0-based


@router.put("/{iid}/move")
def move_interface(iid: int, body: MoveBody):
    """移动接口到指定分组的指定位置。后端重排该组所有接口的 sort_order。"""
    _check_group_name(body.group_name)
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        current = _row_to_dict(_get_or_404(conn, iid))
        source_group = current["group_name"]
        # 更新分组
        conn.execute(
            "UPDATE interfaces SET group_name = ?, updated_at = ? WHERE id = ?",
            (body.group_name, now, iid),
        )
        # 取出目标分组所有接口，按当前 sort_order, id 排序
        rows = conn.execute(
            "SELECT id FROM interfaces WHERE group_name = ? ORDER BY sort_order, id",
            (body.group_name,),
        ).fetchall()
        ids = [r["id"] for r in rows]
        # 把当前接口从原位置移除，插入到 target_index
        if iid in ids:
            ids.remove(iid)
        idx = max(0, min(body.target_index, len(ids)))
        ids.insert(idx, iid)
        # 重新编号 sort_order
        for i, rid in enumerate(ids):
            conn.execute("UPDATE interfaces SET sort_order = ? WHERE id = ?", (i, rid))
        if source_group != body.group_name:
            source_rows = conn.execute(
                "SELECT id FROM interfaces WHERE group_name = ? ORDER BY sort_order, id",
                (source_group,),
            ).fetchall()
            for i, row in enumerate(source_rows):
                conn.execute(
                    "UPDATE interfaces SET sort_order = ? WHERE id = ?",
                    (i, row["id"]),
                )
    return {"ok": True}


class DeleteGroupBody(BaseModel):
    group_name: str


@router.post("/groups/delete")
def delete_group(body: DeleteGroupBody):
    """删除指定分组：将该分组下所有接口移入默认分组（group_name 置空）。"""
    name = (body.group_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="分组名不能为空")
    if name == _RESERVED_GROUP:
        raise HTTPException(status_code=400, detail=f"「{_RESERVED_GROUP}」不可删除")
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        cur = conn.execute(
            "UPDATE interfaces SET group_name = '', updated_at = ? WHERE group_name = ?",
            (now, name),
        )
        count = cur.rowcount
    return {"ok": True, "count": count}


@router.post("/{iid}/open")
def set_open(iid: int, body: OpenBody):
    """只翻转 MCP 开放状态，不动其它接口字段。"""
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        _get_or_404(conn, iid)
        conn.execute(
            "UPDATE interfaces SET open_enabled = ?, mcp_enabled = ?, updated_at = ? WHERE id = ?",
            (1 if body.open else 0, 1 if body.open else 0, now, iid),
        )
        row = conn.execute("SELECT * FROM interfaces WHERE id = ?", (iid,)).fetchone()
    return _row_to_dict(row)


@router.put("/{iid}/http-publication")
def set_http_publication(iid: int, body: HttpPublishIn):
    """独立更新普通 HTTP 发布配置，不覆盖编辑器里其它接口字段。"""
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        row = _get_or_404(conn, iid)
        draft = InterfaceIn(
            **{
                **_row_to_dict(row),
                "http_enabled": body.enabled,
                "proxy_slug": body.slug,
                "proxy_query_keys": body.query_keys,
                "proxy_header_keys": body.header_keys,
                "proxy_body_enabled": body.body_enabled,
                "proxy_body_keys": body.body_keys,
            }
        )
        slug, query_keys, header_keys, body_keys = _validate_proxy_publish(conn, draft, iid)
        conn.execute(
            "UPDATE interfaces SET http_enabled=?, proxy_slug=?, proxy_query_keys=?, "
            "proxy_header_keys=?, proxy_body_enabled=?, proxy_body_keys=?, updated_at=? WHERE id=?",
            (
                1 if body.enabled else 0,
                slug,
                json.dumps(query_keys, ensure_ascii=False),
                json.dumps(header_keys, ensure_ascii=False),
                1 if body.body_enabled else 0,
                json.dumps(body_keys, ensure_ascii=False),
                now,
                iid,
            ),
        )
        row = conn.execute("SELECT * FROM interfaces WHERE id = ?", (iid,)).fetchone()
    return _row_to_dict(row)


def _auto_slug(conn, interface: dict) -> str:
    current = (interface.get("proxy_slug") or "").strip().lower()
    candidate = current if _PROXY_SLUG_RE.fullmatch(current) else publication.slug_suggestion(interface)
    base = candidate[:64]
    suffix = 1
    while conn.execute(
        "SELECT 1 FROM interfaces WHERE proxy_slug = ? AND http_enabled = 1 AND id <> ?",
        (candidate, interface["id"]),
    ).fetchone():
        marker = f"-{interface['id']}" if suffix == 1 else f"-{interface['id']}-{suffix}"
        candidate = base[: 64 - len(marker)] + marker
        suffix += 1
    return candidate


@router.post("/{iid}/http-publication/auto")
def auto_http_publication(iid: int):
    """Infer a safe forwarding contract and publish without exposing protocol details."""
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        row = _get_or_404(conn, iid)
        interface = _row_to_dict(row)
        body_keys = publication.infer_body_keys(interface)
        draft = InterfaceIn(
            **{
                **interface,
                "http_enabled": True,
                "proxy_slug": _auto_slug(conn, interface),
                "proxy_query_keys": publication.infer_query_keys(interface),
                "proxy_header_keys": publication.infer_header_keys(
                    interface, config.PROXY_KEY_HEADER
                ),
                "proxy_body_enabled": bool(body_keys),
                "proxy_body_keys": body_keys,
            }
        )
        slug, query_keys, header_keys, body_keys = _validate_proxy_publish(
            conn, draft, iid
        )
        conn.execute(
            "UPDATE interfaces SET http_enabled=1, proxy_slug=?, proxy_query_keys=?, "
            "proxy_header_keys=?, proxy_body_enabled=?, proxy_body_keys=?, updated_at=? "
            "WHERE id=?",
            (
                slug,
                json.dumps(query_keys, ensure_ascii=False),
                json.dumps(header_keys, ensure_ascii=False),
                1 if body_keys else 0,
                json.dumps(body_keys, ensure_ascii=False),
                now,
                iid,
            ),
        )
        row = conn.execute("SELECT * FROM interfaces WHERE id = ?", (iid,)).fetchone()
    return _row_to_dict(row)


@router.post("/{iid}/run")
def run(iid: int):
    with db.get_conn() as conn:
        iface = _row_to_dict(_get_or_404(conn, iid))
    return executor.run_interface(iface)


@router.get("/{iid}/runs")
def list_runs(iid: int):
    with db.get_conn() as conn:
        _get_or_404(conn, iid)
        rows = conn.execute(
            "SELECT id, ok, status_code, elapsed_ms, error, relogin, created_at "
            "FROM runs WHERE interface_id = ? ORDER BY id DESC",
            (iid,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{iid}/runs/{run_id}")
def get_run(iid: int, run_id: int):
    with db.get_conn() as conn:
        _get_or_404(conn, iid)
        row = conn.execute(
            "SELECT * FROM runs WHERE id = ? AND interface_id = ?", (run_id, iid)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="调用记录不存在")
    d = dict(row)
    d["ok"] = bool(d["ok"])
    d["relogin"] = bool(d["relogin"])
    for k in ("request_snapshot", "response_headers"):
        try:
            d[k] = json.loads(d[k]) if d[k] else None
        except (json.JSONDecodeError, TypeError):
            pass
    return d


# ============================================================
#  全局调用历史（跨所有接口，供右栏日志面板使用）
#  独立 router，挂在 /api/runs，与上面的接口 CRUD 分开。
# ============================================================
runs_router = APIRouter(prefix="/runs", tags=["api-hub-runs"])


@runs_router.get("/overview")
def run_overview(timezone_offset_minutes: int = 0):
    timezone_offset_minutes = max(-840, min(840, timezone_offset_minutes))
    local_now = datetime.now(timezone.utc) - timedelta(
        minutes=timezone_offset_minutes
    )
    today = local_now.date()
    start = today - timedelta(days=6)
    days = [(start + timedelta(days=i)).isoformat() for i in range(7)]
    sqlite_modifier = f"{-timezone_offset_minutes:+d} minutes"
    with db.get_conn() as conn:
        total_interfaces = conn.execute("SELECT COUNT(*) FROM interfaces").fetchone()[0]
        executed = conn.execute("SELECT COUNT(DISTINCT interface_id) FROM runs").fetchone()[0]
        today_traffic = conn.execute(
            "SELECT COUNT(*) FROM runs "
            "WHERE date(datetime(created_at), ?) = ?",
            (sqlite_modifier, today.isoformat()),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT date(datetime(created_at), ?) AS day, COUNT(*) AS count, "
            "SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS failed "
            "FROM runs WHERE date(datetime(created_at), ?) >= ? "
            "AND date(datetime(created_at), ?) <= ? "
            "GROUP BY date(datetime(created_at), ?)",
            (
                sqlite_modifier,
                sqlite_modifier, start.isoformat(),
                sqlite_modifier, today.isoformat(),
                sqlite_modifier,
            ),
        ).fetchall()
        recent_rows = conn.execute(
            "SELECT ok, elapsed_ms FROM runs "
            "WHERE date(datetime(created_at), ?) >= ? "
            "AND date(datetime(created_at), ?) <= ?",
            (
                sqlite_modifier, start.isoformat(),
                sqlite_modifier, today.isoformat(),
            ),
        ).fetchall()
    by_day = {
        row["day"]: {"count": int(row["count"]), "failed": int(row["failed"] or 0)}
        for row in rows
    }
    daily = [
        {
            "date": day,
            "count": by_day.get(day, {}).get("count", 0),
            "failed": by_day.get(day, {}).get("failed", 0),
        }
        for day in days
    ]
    seven_day_traffic = len(recent_rows)
    seven_day_success = sum(1 for row in recent_rows if bool(row["ok"]))
    seven_day_failed = seven_day_traffic - seven_day_success
    latencies = sorted(
        int(row["elapsed_ms"])
        for row in recent_rows
        if row["elapsed_ms"] is not None
    )
    p95_elapsed_ms = (
        latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)]
        if latencies else None
    )
    return {
        "total_interfaces": int(total_interfaces),
        "executed_interfaces": int(executed),
        "unexecuted_interfaces": max(0, int(total_interfaces) - int(executed)),
        "today_traffic": int(today_traffic),
        "seven_day_traffic": seven_day_traffic,
        "seven_day_success": seven_day_success,
        "seven_day_failed": seven_day_failed,
        "success_rate": round(
            seven_day_success * 100 / seven_day_traffic, 1
        ) if seven_day_traffic else 0,
        "p95_elapsed_ms": p95_elapsed_ms,
        "slow_threshold_ms": _SLOW_RUN_MS,
        "retention_limit_per_interface": config.MAX_RUNS_PER_INTERFACE,
        "daily": daily,
    }


@runs_router.get("")
def list_all_runs(
    keyword: str = "",
    start: str = "",
    end: str = "",
    result: str = "",
    page: int = 1,
    size: int = 20,
):
    """分页查询全局调用记录，按时间倒序（最新在顶）。

    - keyword：按接口名称模糊匹配（LIKE %keyword%）
    - start / end：时间范围，前端传入的完整 ISO 时间戳（已按用户本地
      时区换算为 UTC 边界）。用 SQLite datetime() 比较，兼容 Z / +00:00。
    - result：success / failed / slow，slow 表示耗时不低于 500ms。
    - page / size：分页，size 上限 100
    """
    page = max(page, 1)
    size = max(min(size, 100), 1)

    where = []
    params: list = []
    kw = keyword.strip()
    if kw:
        where.append("i.name LIKE ?")
        params.append(f"%{kw}%")
    s = start.strip()
    if s:
        where.append("datetime(r.created_at) >= datetime(?)")
        params.append(s)
    e = end.strip()
    if e:
        where.append("datetime(r.created_at) <= datetime(?)")
        params.append(e)
    result_mode = result.strip().lower()
    if result_mode not in {"", "all", "success", "failed", "slow"}:
        raise HTTPException(status_code=400, detail="不支持的调用结果筛选")
    if result_mode == "success":
        where.append("r.ok = 1")
    elif result_mode == "failed":
        where.append("r.ok = 0")
    elif result_mode == "slow":
        where.append("r.elapsed_ms >= ?")
        params.append(_SLOW_RUN_MS)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    base_from = "FROM runs r JOIN interfaces i ON i.id = r.interface_id"
    with db.get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) " + base_from + where_sql, params
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT r.id, r.interface_id, i.name, i.method, r.ok, r.status_code, "
            "r.elapsed_ms, r.error, r.relogin, r.source, r.proxy_key_name, "
            "r.source_ip, r.created_at "
            + base_from + where_sql +
            " ORDER BY r.id DESC LIMIT ? OFFSET ?",
            params + [size, (page - 1) * size],
        ).fetchall()
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "size": size,
    }
