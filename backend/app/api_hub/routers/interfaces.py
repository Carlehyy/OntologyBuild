import json
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db, executor

router = APIRouter(prefix="/interfaces", tags=["api-hub-interfaces"])

_RESERVED_GROUP = "默认分组"


def _check_group_name(name: str):
    """预留分组名拦截。"""
    if name and name.strip() == _RESERVED_GROUP:
        raise HTTPException(status_code=400, detail=f"「{_RESERVED_GROUP}」为保留名称，请使用其他名称")


class KV(BaseModel):
    key: str = ""
    value: str = ""


class InterfaceIn(BaseModel):
    name: str = "未命名接口"
    description: str = ""
    group_name: str = ""
    method: str = "GET"
    url: str = ""
    query_params: List[KV] = Field(default_factory=list)
    headers: List[KV] = Field(default_factory=list)
    body_type: str = "none"   # none | json | form | raw
    body_content: str = ""
    use_w3: bool = True
    mcp_enabled: bool = False
    open_enabled: bool = False


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "group_name": row["group_name"],
        "method": row["method"],
        "url": row["url"],
        "query_params": json.loads(row["query_params"]),
        "headers": json.loads(row["headers"]),
        "body_type": row["body_type"],
        "body_content": row["body_content"],
        "use_w3": bool(row["use_w3"]),
        "mcp_enabled": bool(row["mcp_enabled"]),
        "open_enabled": bool(row["open_enabled"]),
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
        cur = conn.execute(
            "INSERT INTO interfaces(name, description, group_name, method, url, query_params, headers, "
            "body_type, body_content, use_w3, mcp_enabled, open_enabled, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                body.name, body.description, body.group_name, body.method.upper(), body.url,
                _dump_kv(body.query_params), _dump_kv(body.headers),
                body.body_type, body.body_content,
                1 if body.use_w3 else 0, 1 if body.mcp_enabled else 0,
                1 if body.open_enabled else 0, now, now,
            ),
        )
        row = conn.execute("SELECT * FROM interfaces WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


@router.get("/{iid}")
def get_interface(iid: int):
    with db.get_conn() as conn:
        row = _get_or_404(conn, iid)
    return _row_to_dict(row)


@router.put("/{iid}")
def update_interface(iid: int, body: InterfaceIn):
    _check_group_name(body.group_name)
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        _get_or_404(conn, iid)
        conn.execute(
            "UPDATE interfaces SET name=?, description=?, group_name=?, method=?, url=?, query_params=?, "
            "headers=?, body_type=?, body_content=?, use_w3=?, mcp_enabled=?, open_enabled=?, updated_at=? WHERE id=?",
            (
                body.name, body.description, body.group_name, body.method.upper(), body.url,
                _dump_kv(body.query_params), _dump_kv(body.headers),
                body.body_type, body.body_content,
                1 if body.use_w3 else 0, 1 if body.mcp_enabled else 0,
                1 if body.open_enabled else 0, now, iid,
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


class MoveBody(BaseModel):
    group_name: str = ""
    target_index: int = 0  # 0-based


@router.put("/{iid}/move")
def move_interface(iid: int, body: MoveBody):
    """移动接口到指定分组的指定位置。后端重排该组所有接口的 sort_order。"""
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        _get_or_404(conn, iid)
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
    """只翻转 open_enabled（加入/移出开放接口清单），不动其它字段。供「开放接口」卡片即时切换。"""
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        _get_or_404(conn, iid)
        conn.execute(
            "UPDATE interfaces SET open_enabled = ?, updated_at = ? WHERE id = ?",
            (1 if body.open else 0, now, iid),
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
def run_overview():
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=6)
    days = [(start + timedelta(days=i)).isoformat() for i in range(7)]
    with db.get_conn() as conn:
        total_interfaces = conn.execute("SELECT COUNT(*) FROM interfaces").fetchone()[0]
        executed = conn.execute("SELECT COUNT(DISTINCT interface_id) FROM runs").fetchone()[0]
        today_traffic = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE substr(created_at, 1, 10) = ?",
            (today.isoformat(),),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS count "
            "FROM runs WHERE substr(created_at, 1, 10) >= ? "
            "GROUP BY substr(created_at, 1, 10)",
            (start.isoformat(),),
        ).fetchall()
    by_day = {row["day"]: row["count"] for row in rows}
    daily = [{"date": day, "count": int(by_day.get(day, 0))} for day in days]
    return {
        "total_interfaces": int(total_interfaces),
        "executed_interfaces": int(executed),
        "unexecuted_interfaces": max(0, int(total_interfaces) - int(executed)),
        "today_traffic": int(today_traffic),
        "seven_day_traffic": sum(item["count"] for item in daily),
        "daily": daily,
    }


@runs_router.get("")
def list_all_runs(
    keyword: str = "",
    start: str = "",
    end: str = "",
    page: int = 1,
    size: int = 20,
):
    """分页查询全局调用记录，按时间倒序（最新在顶）。

    - keyword：按接口名称模糊匹配（LIKE %keyword%）
    - start / end：时间范围，前端传入的完整 ISO 时间戳（已按用户本地
      时区换算为 UTC 边界）。用 SQLite datetime() 比较，兼容 Z / +00:00。
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
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    base_from = "FROM runs r JOIN interfaces i ON i.id = r.interface_id"
    with db.get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) " + base_from + where_sql, params
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT r.id, r.interface_id, i.name, i.method, r.ok, r.status_code, "
            "r.elapsed_ms, r.error, r.relogin, r.created_at "
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
