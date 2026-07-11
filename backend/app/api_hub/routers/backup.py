"""数据备份与还原：导出 / 导入接口清单（JSON 格式）。

备份内容只含接口清单（interfaces），不含调用历史（runs）、设置（settings）、
登录态等本地敏感数据 —— 符合「备份接口清单，其它的都不用」。

还原时以「接口名 + 请求方法 + URL」三元组作为自然键去重：已存在的接口跳过，
不重复导入，也不覆盖已有数据。接口自带的 group_name 直接写入，分组自然出现。
"""
import json
import re
from datetime import datetime, timezone
from typing import List
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from .. import db

router = APIRouter(prefix="/backup", tags=["api-hub-backup"])

# 备份文件的标识，还原时用于校验来源
BACKUP_APP = "API-Hub"
BACKUP_VERSION = 1

# 可移植的接口字段（不含 id、created_at、updated_at、sort_order 等本地/派生字段）
_IFACE_FIELDS = (
    "name", "group_name", "method", "url", "query_params", "headers",
    "body_type", "body_content", "use_w3", "mcp_enabled", "open_enabled",
)


class ExportIn(BaseModel):
    name: str = ""
    mode: str = "full"            # full | partial
    ids: List[int] = Field(default_factory=list)


class ImportIn(BaseModel):
    app: str = ""
    version: int = 0
    name: str = ""
    exported_at: str = ""
    mode: str = ""
    interfaces: List[dict] = Field(default_factory=list)

# 合法的 body_type 取值
_ALLOWED_BODY_TYPES = {"none", "json", "form", "raw"}


def _row_to_portable(row) -> dict:
    """把一行接口记录转为可移植的纯数据。"""
    out = {}
    for f in _IFACE_FIELDS:
        v = row[f]
        if f in ("query_params", "headers"):
            try:
                v = json.loads(v) if v else []
            except (json.JSONDecodeError, TypeError):
                v = []
        elif f in ("use_w3", "mcp_enabled", "open_enabled"):
            v = bool(v)
        out[f] = v
    return out


def _safe_filename(name: str) -> str:
    """把备份名变成 ASCII 安全的文件名（HTTP 头不能直接含非 ASCII 字符）。"""
    name = name.strip() or "Backup"
    name = re.sub(r"[^A-Za-z0-9._-]", "-", name)
    name = re.sub(r"-{2,}", "-", name)
    return name.strip("-") or "Backup"


@router.post("/export")
def export_backup(body: ExportIn):
    """导出接口清单为 JSON 文件（触发浏览器下载）。"""
    mode = "partial" if body.mode == "partial" else "full"
    with db.get_conn() as conn:
        if mode == "partial":
            if not body.ids:
                raise HTTPException(
                    status_code=400,
                    detail="部分备份模式下必须提供接口 ID 列表",
                )
            placeholders = ",".join("?" * len(body.ids))
            rows = conn.execute(
                f"SELECT * FROM interfaces WHERE id IN ({placeholders}) "
                "ORDER BY group_name, sort_order, id",
                body.ids,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM interfaces ORDER BY group_name, sort_order, id"
            ).fetchall()

    payload = {
        "app": BACKUP_APP,
        "version": BACKUP_VERSION,
        "name": body.name.strip() or "Backup",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "interface_count": len(rows),
        "interfaces": [_row_to_portable(r) for r in rows],
    }
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    # RFC 5987：filename 给旧浏览器兜底（ASCII），filename* 给现代浏览器用原名（UTF-8）
    safe_name = _safe_filename(body.name)
    utf8_name = quote(body.name.strip() or "Backup") + ".json"
    return Response(
        content=data,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="{safe_name}.json"; filename*=UTF-8\'\'{utf8_name}'
        },
    )


@router.post("/import")
def import_backup(body: ImportIn):
    """导入备份包：合并到现有接口清单，重复的（名+方法+URL 一致）跳过。"""
    # ── 来源校验 ──
    if body.app != BACKUP_APP:
        raise HTTPException(
            status_code=400,
            detail=f"备份文件来源不匹配：期望 app={BACKUP_APP}，实际 app={body.app or '(空)'}",
        )
    if body.version > BACKUP_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"备份文件版本过高（{body.version}），当前平台最高支持 v{BACKUP_VERSION}",
        )

    items = body.interfaces
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="备份文件格式不正确：interfaces 应为数组")

    now = datetime.now(timezone.utc).isoformat()
    imported = 0
    skipped = 0
    with db.get_conn() as conn:
        # 预加载已有接口的自然键，做内存去重（接口数通常很少，无需复杂查询）
        existing = set()
        for r in conn.execute("SELECT name, method, url FROM interfaces").fetchall():
            existing.add(((r["name"] or "").strip(), (r["method"] or ""), (r["url"] or "")))

        for it in items:
            name = (it.get("name") or "").strip() or "未命名接口"
            method = (it.get("method") or "GET").upper()
            url = it.get("url") or ""
            key = (name, method, url)
            if key in existing:
                skipped += 1
                continue

            # ── 字段校验 ──
            query_params = it.get("query_params")
            if query_params is not None and not isinstance(query_params, list):
                raise HTTPException(
                    status_code=400,
                    detail=f"接口「{name}」的 query_params 必须为数组",
                )
            headers = it.get("headers")
            if headers is not None and not isinstance(headers, list):
                raise HTTPException(
                    status_code=400,
                    detail=f"接口「{name}」的 headers 必须为数组",
                )
            body_type = it.get("body_type") or "none"
            if body_type not in _ALLOWED_BODY_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=f"接口「{name}」的 body_type 无效（{body_type}），允许的值：{', '.join(sorted(_ALLOWED_BODY_TYPES))}",
                )

            conn.execute(
                "INSERT INTO interfaces(name, group_name, method, url, query_params, headers, "
                "body_type, body_content, use_w3, mcp_enabled, open_enabled, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    name,
                    it.get("group_name") or "",
                    method,
                    url,
                    json.dumps(query_params or [], ensure_ascii=False),
                    json.dumps(headers or [], ensure_ascii=False),
                    body_type,
                    it.get("body_content") or "",
                    1 if it.get("use_w3", True) else 0,
                    1 if it.get("mcp_enabled", False) else 0,
                    1 if it.get("open_enabled", False) else 0,
                    now, now,
                ),
            )
            existing.add(key)
            imported += 1

    return {
        "imported": imported,
        "skipped": skipped,
        "total": len(items),
        "name": body.name or "",
    }
