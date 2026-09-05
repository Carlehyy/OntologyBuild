"""
入库方式（write_mode）合并逻辑：
流水线的最终产物如何与资产湖中已有数据合并成新版本。

- overwrite     全量覆盖：资产 = 本次输出
- append        直接追加：已有数据 + 本次输出
- append_dedup  去重追加：追加后按整行内容去重（无主键场景防重复导入）
- upsert        主键合并：按主键去重保留最新，可选软删除列打 __deleted__ 标记

参考实现：语义权威，供等价性测试对照；流水线入湖的运行时路径已切换
lake_store 物理湖表（merge_engine 曾短暂下推 DuckDB，现已退役）。
本模块函数不得删除——merge_rows /
compute_lake_impact / _row_signature 同时被行级审核（curated review）使用。
normalize_write_mode / _apply_soft_delete / VALID_WRITE_MODES 已下沉到
write_modes.py 叶子模块，此处再导出保持既有导入表面。
"""
from __future__ import annotations

import json
from typing import Any

from app.data_channel.datasets.service import snapshot_cell_text
from app.data_channel.pipeline_tasks.write_modes import (  # noqa: F401 — 再导出
    VALID_WRITE_MODES,
    _apply_soft_delete,
    normalize_write_mode,
)


def load_latest_rows(db, dataset_id: str) -> list[dict]:
    """加载资产湖中该数据集最新版本的全部行（合并基座）。

    必须全量、读失败必须抛错（DatasetReadError → 本次运行失败）：
    截断或把读失败当空列表，都会让 append/upsert 基于残缺基座合并，
    湖中存量在新版本里静默消失、且运行状态还是成功。
    数据集尚无数据时返回 []，属合法初始状态。
    """
    from app.data_channel.datasets.service import DatasetService
    return DatasetService(db).load_all_rows(dataset_id)


def _row_signature(row: dict) -> str:
    """Compare rows by the canonical representation that is persisted.

    Curated Parquet snapshots intentionally preserve the legacy CSV round-trip
    contract: scalar values are strings, ``None`` is an empty string, and
    nested values are JSON text.  Merge inputs, however, still carry native
    n8n values.  Comparing their Python/JSON runtime types would therefore
    report a replayed ``True``/``86000`` as different from the already stored
    ``"True"``/``"86000"`` even though the next snapshot is byte-equivalent.
    ``content`` is deliberately absent from persisted tabular snapshots.
    """
    canonical = {
        str(key): snapshot_cell_text(value)
        for key, value in row.items()
        if str(key) != "content"
    }
    try:
        return json.dumps(
            canonical, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"),
        )
    except Exception:
        return str(sorted(canonical.items()))


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


def merge_rows(old: list[dict], new: list[dict], opts: dict[str, Any]) -> tuple[list[dict], dict]:
    """按入库方式合并，返回 (合并后的全量行, 合并统计)

    参考实现：语义权威，供等价性测试对照；运行时路径已切换 lake_store 物理湖表。
    """
    opts = opts or {}
    mode = normalize_write_mode(opts.get("mode"))

    if mode == "append":
        merged = old + new
    elif mode == "append_dedup":
        merged = _dedup_by_row(old + new)
    elif mode == "upsert":
        key_cols = [c.strip() for c in str(opts.get("primary_key") or "").split(",") if c.strip()]
        merged = _dedup_by_key(old + new, key_cols) if key_cols else (old + new)
        merged = _apply_soft_delete(merged, str(opts.get("soft_delete_column") or ""))
    elif mode == "overwrite":
        merged = new

    return merged, {
        "mode": mode,
        "rows_before": len(old),
        "rows_new": len(new),
        "rows_after": len(merged),
    }


# ── 审计：资产湖影响 = 本次入库前后的行级差异 ─────────────────────────
def _slim_row(row: dict, max_len: int = 200) -> dict:
    """审计样本裁剪：丢弃二进制 content、超长值截断，避免 run.stats 膨胀。"""
    out: dict = {}
    for k, v in row.items():
        if k == "content":
            continue
        if isinstance(v, str) and len(v) > max_len:
            out[k] = v[:max_len] + "…"
        elif isinstance(v, (dict, list)):
            s = json.dumps(v, ensure_ascii=False, default=str)
            out[k] = s[:max_len] + ("…" if len(s) > max_len else "")
        else:
            out[k] = v
    return out


def compute_lake_impact(before: list[dict], after: list[dict],
                        pk_cols: list[str], sample_limit: int = 50) -> dict:
    """本次入库对资产湖的行级影响 = diff(入库前全量, 入库后全量)。

    参考实现：语义权威，供等价性测试对照；运行时路径已切换 lake_store 物理湖表。
    有主键 → 按主键组合识别同一行，能区分「更新」（键在、内容变）。
    无主键 → 退化为整行内容比对，只有「新增/删除」，没有「更新」。
    返回计数 + 各类别的行样本（截断），供执行记录逐条追溯审计。
    """
    def key(r: dict):
        if pk_cols:
            return tuple(str(r.get(c, "")) for c in pk_cols)
        return _row_signature(r)

    before_map: dict = {}
    for r in before:
        before_map[key(r)] = r
    after_map: dict = {}
    for r in after:
        after_map[key(r)] = r

    added: list[dict] = []
    updated: list[dict] = []
    for k, r in after_map.items():
        prev = before_map.get(k)
        if prev is None:
            added.append(r)
        elif _row_signature(prev) != _row_signature(r):
            updated.append({"before": _slim_row(prev), "after": _slim_row(r)})
    deleted = [r for k, r in before_map.items() if k not in after_map]

    return {
        "keyed_by": list(pk_cols) if pk_cols else None,
        "total_before": len(before),
        "total_after": len(after),
        "added_count": len(added),
        "updated_count": len(updated),
        "deleted_count": len(deleted),
        "unchanged_count": max(0, len(after_map) - len(added) - len(updated)),
        "added_sample": [_slim_row(r) for r in added[:sample_limit]],
        "updated_sample": updated[:sample_limit],
        "deleted_sample": [_slim_row(r) for r in deleted[:sample_limit]],
        "sample_truncated": (len(added) > sample_limit or len(updated) > sample_limit
                             or len(deleted) > sample_limit),
    }
