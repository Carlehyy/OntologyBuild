"""
入库方式（write_mode）合并逻辑：
流水线的最终产物如何与资产湖中已有数据合并成新版本。

- overwrite     全量覆盖：资产 = 本次输出
- append        直接追加：已有数据 + 本次输出
- append_dedup  去重追加：追加后按整行内容去重（无主键场景防重复导入）
- upsert        主键合并：按主键去重保留最新，可选软删除列打 __deleted__ 标记
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def load_latest_rows(db, dataset_id: str) -> list[dict]:
    """加载资产湖中该数据集最新版本的全部行"""
    from app.services.v2.dataset_service import DatasetService
    svc = DatasetService(db)
    try:
        return svc.preview(dataset_id, None, limit=1_000_000)
    except Exception:
        return []


def _row_signature(row: dict) -> str:
    try:
        return json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return str(sorted(row.items()))


def _dedup_by_key(rows: list[dict], key_cols: list[str]) -> list[dict]:
    """按主键列去重：保留最后出现的记录（即最新）"""
    if not key_cols or not rows:
        return rows
    seen: dict[tuple, int] = {}
    for i, r in enumerate(rows):
        key = tuple(str(r.get(k, "")) for k in key_cols)
        seen[key] = i
    keep_idx = sorted(seen.values())
    return [rows[i] for i in keep_idx]


def _dedup_by_row(rows: list[dict]) -> list[dict]:
    """按整行内容去重：保留首次出现"""
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        sig = _row_signature(r)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(r)
    return out


def _apply_soft_delete(rows: list[dict], deleted_col: str) -> list[dict]:
    """软删除标记：deleted_col 为真值的行加 __deleted__，不物理删除"""
    if not deleted_col or not rows:
        return rows
    truthy = {"1", "true", "yes", "y", "t", "是", "删除", "已删除"}
    for r in rows:
        v = r.get(deleted_col)
        is_del = str(v).strip().lower() in truthy if v is not None else False
        if is_del:
            r["__deleted__"] = True
            r["__deleted_at__"] = datetime.utcnow().isoformat()
        else:
            r.pop("__deleted__", None)
            r.pop("__deleted_at__", None)
    return rows


def merge_rows(old: list[dict], new: list[dict], opts: dict[str, Any]) -> tuple[list[dict], dict]:
    """按入库方式合并，返回 (合并后的全量行, 合并统计)"""
    mode = (opts or {}).get("mode") or "overwrite"

    if mode == "append":
        merged = old + new
    elif mode == "append_dedup":
        merged = _dedup_by_row(old + new)
    elif mode == "upsert":
        key_cols = [c.strip() for c in str(opts.get("primary_key") or "").split(",") if c.strip()]
        merged = _dedup_by_key(old + new, key_cols) if key_cols else (old + new)
        merged = _apply_soft_delete(merged, str(opts.get("soft_delete_column") or ""))
    else:  # overwrite
        merged = new

    return merged, {
        "mode": mode,
        "rows_before": len(old),
        "rows_new": len(new),
        "rows_after": len(merged),
    }
