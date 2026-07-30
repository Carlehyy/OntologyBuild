"""普通 HTTP 接口发布、代理密钥管理与公共转发入口。"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import List

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.datastructures import FormData, UploadFile

from .. import config, db, executor, publication
from ..interface_service import _row_to_dict

admin_router = APIRouter(prefix="/proxy", tags=["api-hub-http-proxy-admin"])
public_router = APIRouter(prefix=config.PROXY_PATH, tags=["api-hub-http-proxy"])

_OUTBOUND_RESPONSE_BLOCKLIST = {
    "connection",
    "content-encoding",  # requests 已自动解压，不能继续声明原压缩格式
    "content-length",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class ProxyKeyCreate(BaseModel):
    name: str
    enabled: bool = True
    valid_from: datetime | None = None
    expires_at: datetime | None = None
    scope_all: bool = False
    interface_ids: List[int] = Field(default_factory=list)


class ProxyKeyUpdate(ProxyKeyCreate):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    value = _as_utc(value)
    return value.isoformat() if value else None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _hash_key(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _validate_key_input(
    conn,
    name: str,
    valid_from: datetime | None,
    expires_at: datetime | None,
    scope_all: bool,
    interface_ids: list[int],
    *,
    allow_expired: bool = False,
) -> tuple[str, str | None, str | None, list[int]]:
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="密钥名称不能为空")
    valid_from = _as_utc(valid_from)
    expires_at = _as_utc(expires_at)
    if valid_from and expires_at and expires_at <= valid_from:
        raise HTTPException(status_code=400, detail="过期时间必须晚于生效时间")
    if expires_at and expires_at <= _now() and not allow_expired:
        raise HTTPException(status_code=400, detail="过期时间必须晚于当前时间")

    try:
        ids = sorted({int(item) for item in interface_ids if int(item) > 0})
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="接口权限 ID 必须是正整数") from exc
    if not scope_all and not ids:
        raise HTTPException(status_code=400, detail="请选择至少一个可调用接口，或授权全部接口")
    if ids:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id FROM interfaces WHERE id IN ({placeholders})", ids
        ).fetchall()
        found = {row["id"] for row in rows}
        missing = [str(item) for item in ids if item not in found]
        if missing:
            raise HTTPException(status_code=400, detail="以下接口不存在：" + ", ".join(missing))
    return name, _iso(valid_from), _iso(expires_at), ids


def _replace_key_scope(conn, key_id: int, interface_ids: list[int]) -> None:
    conn.execute("DELETE FROM proxy_key_interfaces WHERE key_id = ?", (key_id,))
    conn.executemany(
        "INSERT INTO proxy_key_interfaces(key_id, interface_id) VALUES(?, ?)",
        [(key_id, interface_id) for interface_id in interface_ids],
    )


def _key_status(row) -> str:
    if not bool(row["enabled"]):
        return "disabled"
    now = _now()
    valid_from = _parse_iso(row["valid_from"])
    expires_at = _parse_iso(row["expires_at"])
    if valid_from and valid_from > now:
        return "scheduled"
    if expires_at and expires_at <= now:
        return "expired"
    return "active"


def _key_view(conn, row) -> dict:
    scopes = conn.execute(
        "SELECT interface_id FROM proxy_key_interfaces WHERE key_id = ? ORDER BY interface_id",
        (row["id"],),
    ).fetchall()
    return {
        "id": row["id"],
        "name": row["name"],
        "key_prefix": row["key_prefix"],
        "masked_key": row["key_prefix"] + "••••••••",
        "enabled": bool(row["enabled"]),
        "valid_from": row["valid_from"],
        "expires_at": row["expires_at"],
        "scope_all": bool(row["scope_all"]),
        "interface_ids": [item["interface_id"] for item in scopes],
        "status": _key_status(row),
        "last_used_at": row["last_used_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _insert_proxy_key(
    conn,
    *,
    name: str,
    enabled: bool,
    valid_from: str | None,
    expires_at: str | None,
    scope_all: bool,
    interface_ids: list[int],
) -> tuple[dict, str]:
    now = _now().isoformat()
    secret = "hub_" + secrets.token_urlsafe(32)
    cur = conn.execute(
        "INSERT INTO proxy_keys(name, key_prefix, key_hash, enabled, valid_from, "
        "expires_at, scope_all, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            name,
            secret[:12],
            _hash_key(secret),
            1 if enabled else 0,
            valid_from,
            expires_at,
            1 if scope_all else 0,
            now,
            now,
        ),
    )
    _replace_key_scope(conn, int(cur.lastrowid), interface_ids)
    row = conn.execute("SELECT * FROM proxy_keys WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _key_view(conn, row), secret


@admin_router.get("/info")
def proxy_info():
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, method, proxy_slug FROM interfaces "
            "WHERE http_enabled = 1 ORDER BY group_name, sort_order, id"
        ).fetchall()
        key_count = conn.execute("SELECT COUNT(*) FROM proxy_keys").fetchone()[0]
    return {
        "path": config.PROXY_PATH,
        "key_header": config.PROXY_KEY_HEADER,
        "port": config.APP_PORT,
        "key_count": int(key_count),
        "published": [dict(row) for row in rows],
    }


@admin_router.get("/keys")
def list_proxy_keys():
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM proxy_keys ORDER BY id DESC").fetchall()
        return [_key_view(conn, row) for row in rows]


@admin_router.post("/keys")
def create_proxy_key(body: ProxyKeyCreate):
    with db.get_conn() as conn:
        name, valid_from, expires_at, ids = _validate_key_input(
            conn,
            body.name,
            body.valid_from,
            body.expires_at,
            body.scope_all,
            body.interface_ids,
        )
        result, secret = _insert_proxy_key(
            conn,
            name=name,
            enabled=body.enabled,
            valid_from=valid_from,
            expires_at=expires_at,
            scope_all=body.scope_all,
            interface_ids=ids,
        )
    result["secret"] = secret
    return result


@admin_router.post("/packages/{interface_id}")
def create_proxy_package(interface_id: int):
    """Create a ready-to-share caller credential scoped to one published interface."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM interfaces WHERE id = ?", (interface_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="接口不存在")
        interface = _row_to_dict(row)
        if not interface.get("http_enabled"):
            raise HTTPException(status_code=409, detail="请先自动生成转发配置")
        generated_at = _now()
        name = f"{interface['name']} · 调用包 · {generated_at.strftime('%Y%m%d-%H%M')}"
        key_view, secret = _insert_proxy_key(
            conn,
            name=name,
            enabled=True,
            valid_from=None,
            expires_at=None,
            scope_all=False,
            interface_ids=[interface_id],
        )

    query_defaults = {
        str(item.get("key")): str(item.get("value", ""))
        for item in interface.get("query_params") or []
        if isinstance(item, dict) and item.get("key")
    }
    return {
        "key_id": key_view["id"],
        "key_name": key_view["name"],
        "secret": secret,
        "path": f"{config.PROXY_PATH}/{interface['proxy_slug']}",
        "key_header": config.PROXY_KEY_HEADER,
        "method": interface["method"],
        "query_params": [
            {"key": key, "value": query_defaults.get(key, "")}
            for key in interface.get("proxy_query_keys") or []
        ],
        # Header values remain platform-owned. Only show placeholders to callers.
        "header_params": [
            {"key": key, "value": ""}
            for key in interface.get("proxy_header_keys") or []
        ],
        "body_type": interface.get("body_type") or "none",
        "body_enabled": bool(interface.get("proxy_body_enabled")),
        "body_template": publication.body_template(interface),
        "editable_body_keys": interface.get("proxy_body_keys") or [],
        "multipart_fields": (
            publication.multipart_text_fields(interface)
            if interface.get("proxy_body_enabled")
            else []
        ),
        "file_fields": (
            publication.multipart_file_fields(interface)
            if interface.get("proxy_body_enabled")
            else []
        ),
        "generated_at": generated_at.isoformat(),
    }


@admin_router.put("/keys/{key_id}")
def update_proxy_key(key_id: int, body: ProxyKeyUpdate):
    now = _now().isoformat()
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM proxy_keys WHERE id = ?", (key_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="密钥不存在")
        name, valid_from, expires_at, ids = _validate_key_input(
            conn,
            body.name,
            body.valid_from,
            body.expires_at,
            body.scope_all,
            body.interface_ids,
            allow_expired=True,
        )
        conn.execute(
            "UPDATE proxy_keys SET name=?, enabled=?, valid_from=?, expires_at=?, "
            "scope_all=?, updated_at=? WHERE id=?",
            (
                name,
                1 if body.enabled else 0,
                valid_from,
                expires_at,
                1 if body.scope_all else 0,
                now,
                key_id,
            ),
        )
        _replace_key_scope(conn, key_id, ids)
        row = conn.execute("SELECT * FROM proxy_keys WHERE id = ?", (key_id,)).fetchone()
        return _key_view(conn, row)


@admin_router.delete("/keys/{key_id}")
def delete_proxy_key(key_id: int):
    with db.get_conn() as conn:
        row = conn.execute("SELECT id FROM proxy_keys WHERE id = ?", (key_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="密钥不存在")
        conn.execute("DELETE FROM proxy_keys WHERE id = ?", (key_id,))
    return {"ok": True}


def _authenticate_proxy_key(conn, secret: str, interface_id: int):
    if not secret:
        raise HTTPException(status_code=401, detail=f"缺少请求头 {config.PROXY_KEY_HEADER}")
    row = conn.execute(
        "SELECT * FROM proxy_keys WHERE key_hash = ?", (_hash_key(secret),)
    ).fetchone()
    if not row or _key_status(row) != "active":
        raise HTTPException(status_code=401, detail="代理密钥无效、未生效、已停用或已过期")
    if not bool(row["scope_all"]):
        allowed = conn.execute(
            "SELECT 1 FROM proxy_key_interfaces WHERE key_id = ? AND interface_id = ?",
            (row["id"], interface_id),
        ).fetchone()
        if not allowed:
            raise HTTPException(status_code=403, detail="该密钥无权调用此接口")
    return row


def _response_headers(headers: dict, *, use_w3: bool) -> dict[str, str]:
    return {
        key: value
        for key, value in (headers or {}).items()
        if key.lower() not in _OUTBOUND_RESPONSE_BLOCKLIST
        and not (use_w3 and key.lower() == "set-cookie")
    }


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


async def _multipart_request_parts(
    request: Request,
    interface: dict,
) -> tuple[FormData, list[tuple[str, str]], list[executor.RequestFile], bool]:
    """Parse a caller multipart body and close its temporary files on validation errors."""
    form = await request.form(max_files=50, max_fields=200)
    try:
        allowed = set(interface.get("proxy_body_keys") or [])
        incoming_fields: list[tuple[str, str]] = []
        incoming_files: list[executor.RequestFile] = []
        incoming_names: set[str] = set()
        file_counts: dict[str, int] = {}
        file_config = {
            item.get("key"): item
            for item in interface.get("file_fields") or []
            if isinstance(item, dict) and item.get("key")
        }
        total_size = 0
        for field_name, value in form.multi_items():
            incoming_names.add(field_name)
            if allowed and field_name not in allowed:
                raise HTTPException(status_code=400, detail=f"Body 字段未开放：{field_name}")
            if isinstance(value, UploadFile):
                definition = file_config.get(field_name)
                if definition is None:
                    raise HTTPException(status_code=400, detail=f"文件字段未在接口中配置：{field_name}")
                file_counts[field_name] = file_counts.get(field_name, 0) + 1
                if file_counts[field_name] > 1 and not definition.get("multiple"):
                    raise HTTPException(status_code=400, detail=f"文件字段不允许多文件：{field_name}")
                if not _matches_file_accept(
                    value.filename or "", value.content_type or "", definition.get("accept") or ""
                ):
                    raise HTTPException(status_code=400, detail=f"文件类型不符合字段限制：{field_name}")
                if value.size is not None:
                    total_size += value.size
                incoming_files.append(
                    executor.RequestFile(
                        field_name=field_name,
                        filename=value.filename or "upload",
                        stream=value.file,
                        content_type=value.content_type or "application/octet-stream",
                        size=value.size,
                    )
                )
            else:
                text_value = str(value)
                total_size += len(text_value.encode("utf-8"))
                incoming_fields.append((field_name, text_value))
        if total_size > config.PROXY_MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="请求体超过平台代理上限")
        defaults = [
            item
            for item in publication.parse_saved_form(interface.get("body_content") or "")
            if item[0] not in incoming_names
        ]
        fields = defaults + incoming_fields
        return form, fields, incoming_files, bool(incoming_fields or incoming_files)
    except Exception:
        await form.close()
        raise


@public_router.api_route(
    "/{slug}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def call_published_interface(slug: str, request: Request):
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM interfaces WHERE proxy_slug = ? AND http_enabled = 1",
            (slug.lower(),),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="HTTP 接口不存在或未发布")
        iface = _row_to_dict(row)
        key_row = _authenticate_proxy_key(
            conn,
            (request.headers.get(config.PROXY_KEY_HEADER) or "").strip(),
            iface["id"],
        )

    expected_method = (iface.get("method") or "GET").upper()
    if request.method.upper() != expected_method:
        return JSONResponse(
            status_code=405,
            content={"detail": f"该接口只允许使用 {expected_method} 方法"},
            headers={"Allow": expected_method},
        )

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > config.PROXY_MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="请求体超过平台代理上限")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Length 无效") from exc

    allowed_query = set(iface.get("proxy_query_keys") or [])
    query_items = list(request.query_params.multi_items())
    denied_query = sorted({key for key, _ in query_items if key not in allowed_query})
    if denied_query:
        raise HTTPException(
            status_code=400,
            detail="以下 Query 参数未在发布配置中开放：" + ", ".join(denied_query),
        )

    allowed_headers = {key.lower() for key in (iface.get("proxy_header_keys") or [])}
    header_items = [
        (key, value)
        for key, value in request.headers.items()
        if key.lower() in allowed_headers
    ]

    body_type = (iface.get("body_type") or "none").lower()
    body_announced = "content-length" in request.headers or "transfer-encoding" in request.headers
    body = b""
    form: FormData | None = None
    multipart_fields: list[tuple[str, str]] | None = None
    multipart_files: list[executor.RequestFile] | None = None

    if body_type == "multipart" and body_announced:
        request_content_type = (request.headers.get("content-type") or "").lower()
        if not request_content_type.startswith("multipart/form-data"):
            raise HTTPException(status_code=400, detail="该接口要求 multipart/form-data 请求")
        form, multipart_fields, multipart_files, has_body = await _multipart_request_parts(
            request, iface
        )
    else:
        body = await request.body() if body_announced else b""
        if len(body) > config.PROXY_MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="请求体超过平台代理上限")
        has_body = bool(body)
        if has_body:
            try:
                body = publication.merge_caller_body(iface, body)
            except publication.PublicationBodyError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    if has_body and not iface.get("proxy_body_enabled"):
        if form is not None:
            await form.close()
        raise HTTPException(status_code=400, detail="该接口未开放请求 Body")

    managed_content_type = {
        "json": "application/json; charset=utf-8",
        "form": "application/x-www-form-urlencoded",
    }.get(body_type)
    override_args = {
        "query_params": query_items,
        "headers": header_items,
        "content_type": managed_content_type if has_body else None,
        "source": "http_proxy",
        "proxy_key_id": key_row["id"],
        "proxy_key_name": key_row["name"],
        "source_ip": request.client.host if request.client else None,
    }
    if multipart_fields is not None or multipart_files is not None:
        override_args["multipart_fields"] = multipart_fields or []
        override_args["files"] = multipart_files or []
    elif has_body:
        override_args["body"] = body
    overrides = executor.RequestOverrides(**override_args)
    try:
        result = await anyio.to_thread.run_sync(
            lambda: executor.run_interface(
                iface, overrides, include_response_content=True
            )
        )
    finally:
        if form is not None:
            await form.close()

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

    return Response(
        content=(
            b""
            if request.method.upper() == "HEAD"
            else (result.get("response_content") or b"")
        ),
        status_code=result["status_code"],
        headers=_response_headers(
            result.get("response_headers") or {},
            use_w3=bool(iface.get("use_w3")),
        ),
        media_type=None,
    )
