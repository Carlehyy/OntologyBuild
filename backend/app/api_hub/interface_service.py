"""Canonical persistence service for API Hub interfaces."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import List

from fastapi import HTTPException

from . import config, db
from .interface_contracts import DeleteGroupBody, InterfaceIn, KV


_RESERVED_GROUP = "默认分组"
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


def _check_group_name(name: str) -> None:
    """Reject the display-only reserved group name."""
    if name and name.strip() == _RESERVED_GROUP:
        raise HTTPException(
            status_code=400,
            detail=f"「{_RESERVED_GROUP}」为保留名称，请使用其他名称",
        )


def _load_json_list(value) -> list:
    try:
        data = json.loads(value) if value else []
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _normalize_publish_keys(
    items: List[str],
    *,
    lower: bool = False,
) -> list[str]:
    output = []
    seen = set()
    for item in items:
        key = (item or "").strip()
        marker = key.lower() if lower else key
        if not key or marker in seen:
            continue
        seen.add(marker)
        output.append(key)
    return output


def _validate_proxy_publish(
    conn,
    body: InterfaceIn,
    iid: int | None = None,
) -> tuple[str, list[str], list[str], list[str]]:
    slug = (body.proxy_slug or "").strip().lower()
    query_keys = _normalize_publish_keys(body.proxy_query_keys)
    header_keys = _normalize_publish_keys(
        body.proxy_header_keys,
        lower=True,
    )
    body_keys = _normalize_publish_keys(body.proxy_body_keys)
    if not body.proxy_body_enabled:
        body_keys = []

    if slug and not _PROXY_SLUG_RE.fullmatch(slug):
        raise HTTPException(
            status_code=400,
            detail=(
                "HTTP 公开路径只能包含小写字母、数字、短横线和下划线，"
                "长度 1-64 位"
            ),
        )
    if body.http_enabled:
        if not slug:
            raise HTTPException(
                status_code=400,
                detail="发布 HTTP 接口前必须填写公开路径",
            )
        if not (body.url or "").strip():
            raise HTTPException(
                status_code=400,
                detail="发布 HTTP 接口前必须填写真实 URL",
            )
        sql = (
            "SELECT id FROM interfaces "
            "WHERE proxy_slug = ? AND http_enabled = 1"
        )
        params: list = [slug]
        if iid is not None:
            sql += " AND id <> ?"
            params.append(iid)
        if conn.execute(sql, params).fetchone():
            raise HTTPException(
                status_code=409,
                detail=f"HTTP 公开路径「{slug}」已被其它接口使用",
            )

    reserved = _PROXY_RESERVED_HEADERS | {
        config.PROXY_KEY_HEADER.lower(),
    }
    blocked = [
        key
        for key in header_keys
        if key.lower() in reserved
    ]
    if blocked:
        raise HTTPException(
            status_code=400,
            detail=(
                "以下 Header 由平台代理层管理，不能配置为透传项："
                + ", ".join(blocked)
            ),
        )
    if body_keys and body.body_type not in {"json", "form", "multipart"}:
        raise HTTPException(
            status_code=400,
            detail="只有 JSON、Form 或 Multipart Body 支持字段级开放",
        )
    if (
        body.body_type == "json"
        and any(not key.startswith("/") for key in body_keys)
    ):
        raise HTTPException(
            status_code=400,
            detail="JSON Body 字段路径必须以 / 开头",
        )
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
        # ``mcp_enabled`` is retained only for backup compatibility. The one
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
    return json.dumps(
        [item.model_dump() for item in items],
        ensure_ascii=False,
    )


def _get_or_404(conn, iid: int):
    row = conn.execute(
        "SELECT * FROM interfaces WHERE id = ?",
        (iid,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="接口不存在")
    return row


def create_interface(body: InterfaceIn):
    _check_group_name(body.group_name)
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        slug, query_keys, header_keys, body_keys = _validate_proxy_publish(
            conn,
            body,
        )
        cursor = conn.execute(
            "INSERT INTO interfaces(name, description, group_name, method, "
            "url, query_params, headers, body_type, body_content, file_fields, "
            "mcp_enabled, open_enabled, http_enabled, proxy_slug, "
            "proxy_query_keys, proxy_header_keys, proxy_body_enabled, "
            "proxy_body_keys, parameter_schema, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                body.name,
                body.description,
                body.group_name,
                body.method.upper(),
                body.url,
                _dump_kv(body.query_params),
                _dump_kv(body.headers),
                body.body_type,
                body.body_content,
                json.dumps(
                    [item.model_dump() for item in body.file_fields],
                    ensure_ascii=False,
                ),
                1 if body.open_enabled else 0,
                1 if body.open_enabled else 0,
                1 if body.http_enabled else 0,
                slug,
                json.dumps(query_keys, ensure_ascii=False),
                json.dumps(header_keys, ensure_ascii=False),
                1 if body.proxy_body_enabled else 0,
                json.dumps(body_keys, ensure_ascii=False),
                json.dumps(
                    [
                        item.model_dump(mode="json")
                        for item in body.parameter_schema
                    ],
                    ensure_ascii=False,
                ),
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM interfaces WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return _row_to_dict(row)


def update_interface(iid: int, body: InterfaceIn):
    _check_group_name(body.group_name)
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        _get_or_404(conn, iid)
        slug, query_keys, header_keys, body_keys = _validate_proxy_publish(
            conn,
            body,
            iid,
        )
        conn.execute(
            "UPDATE interfaces SET name=?, description=?, group_name=?, "
            "method=?, url=?, query_params=?, headers=?, body_type=?, "
            "body_content=?, file_fields=?, mcp_enabled=?, "
            "open_enabled=?, http_enabled=?, proxy_slug=?, "
            "proxy_query_keys=?, proxy_header_keys=?, proxy_body_enabled=?, "
            "proxy_body_keys=?, parameter_schema=?, "
            "config_revision=config_revision+1, updated_at=? WHERE id=?",
            (
                body.name,
                body.description,
                body.group_name,
                body.method.upper(),
                body.url,
                _dump_kv(body.query_params),
                _dump_kv(body.headers),
                body.body_type,
                body.body_content,
                json.dumps(
                    [item.model_dump() for item in body.file_fields],
                    ensure_ascii=False,
                ),
                1 if body.open_enabled else 0,
                1 if body.open_enabled else 0,
                1 if body.http_enabled else 0,
                slug,
                json.dumps(query_keys, ensure_ascii=False),
                json.dumps(header_keys, ensure_ascii=False),
                1 if body.proxy_body_enabled else 0,
                json.dumps(body_keys, ensure_ascii=False),
                json.dumps(
                    [
                        item.model_dump(mode="json")
                        for item in body.parameter_schema
                    ],
                    ensure_ascii=False,
                ),
                now,
                iid,
            ),
        )
        row = conn.execute(
            "SELECT * FROM interfaces WHERE id = ?",
            (iid,),
        ).fetchone()
    return _row_to_dict(row)


def delete_interface(iid: int):
    with db.get_conn() as conn:
        _get_or_404(conn, iid)
        # Explicitly delete history even though the schema also has a cascade.
        conn.execute("DELETE FROM runs WHERE interface_id = ?", (iid,))
        conn.execute("DELETE FROM interfaces WHERE id = ?", (iid,))
    return {"ok": True}


def delete_group(body: DeleteGroupBody):
    """删除指定分组：将该分组下所有接口移入默认分组（group_name 置空）。"""
    name = (body.group_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="分组名不能为空")
    if name == _RESERVED_GROUP:
        raise HTTPException(
            status_code=400,
            detail=f"「{_RESERVED_GROUP}」不可删除",
        )
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        cursor = conn.execute(
            "UPDATE interfaces SET group_name = '', updated_at = ? "
            "WHERE group_name = ?",
            (now, name),
        )
        count = cursor.rowcount
    return {"ok": True, "count": count}
