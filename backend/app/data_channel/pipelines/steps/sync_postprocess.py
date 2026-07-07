"""同步数据后处理算子：
  - DeduplicateLatestStep: 按主键保留最新记录（基于水印列），适用于 APPEND 模式下的 UPDATE 场景
  - SoftDeleteStep:        识别软删除列并增加 __deleted__ 标记列，适用于源端 is_deleted 逻辑删除场景
"""
from __future__ import annotations
from app.services.v2.pipeline.base import PipelineStep, PipelineContext


class DeduplicateLatestStep(PipelineStep):
    """
    按主键列去重，保留水印列值最大的一行（即"最新版本"）。
    APPEND 同步模式下源端 UPDATE 的记录会作为新行追加进来，
    经过此算子后可以得到当前最新状态的快照。

    spec 选项:
      key_columns:       list[str]  主键列名列表（逗号字符串或数组）
      order_by:          str        水印/排序列名（默认取 watermark_column，或时间戳列）
      order_desc:        bool       默认 True（大值=最新）；若 order_by 是自增 ID 也保持 True
      keep_last:         bool       默认 True（仅保留最新一行）；False=保留非最新的历史
    """

    def run(self, ctx: PipelineContext, data: list[dict]) -> list[dict]:
        if not data:
            return data

        spec = ctx.spec.get("deduplicate_latest", {}) or ctx.spec.get("deduplicate", {})
        key_arg = spec.get("key_columns") or spec.get("key")
        order_by = spec.get("order_by") or spec.get("watermark_column") or ""
        order_desc = spec.get("order_desc", True)

        if isinstance(key_arg, str):
            key_cols = [c.strip() for c in key_arg.split(",") if c.strip()]
        elif isinstance(key_arg, list):
            key_cols = [str(c).strip() for c in key_arg if str(c).strip()]
        else:
            ctx.meta["dedup_skipped"] = True
            ctx.logs.append("[deduplicate_latest] 未配置 key_columns，跳过")
            return data

        # 自动探测 order_by：选第一个 timestamp 列
        if not order_by:
            for k, v in (data[0] or {}).items():
                if k.lower() in ("updated_at", "modified_at", "update_time", "created_at", "id"):
                    order_by = k
                    break
        if not order_by:
            ctx.logs.append("[deduplicate_latest] 未找到 order_by 列，按原顺序保留最后一条")

        def _row_key(row: dict) -> tuple:
            return tuple(str(row.get(k, "")) for k in key_cols)

        latest: dict[tuple, dict] = {}
        for row in data:
            k = _row_key(row)
            if k not in latest:
                latest[k] = row
                continue
            if not order_by:
                latest[k] = row  # 无排序列：后出现覆盖先出现
                continue
            old_val = latest[k].get(order_by)
            new_val = row.get(order_by)
            try:
                if order_desc:
                    if (new_val is not None) and (old_val is None or new_val >= old_val):
                        latest[k] = row
                else:
                    if (new_val is not None) and (old_val is None or new_val <= old_val):
                        latest[k] = row
            except TypeError:
                # 类型不兼容时回退到字符串比较
                if str(new_val) >= str(old_val):
                    latest[k] = row

        ctx.meta["dedup_input_rows"] = len(data)
        ctx.meta["dedup_output_rows"] = len(latest)
        ctx.meta["dedup_dropped"] = len(data) - len(latest)
        ctx.logs.append(f"[deduplicate_latest] key={key_cols} order_by={order_by} 输入{len(data)}行→输出{len(latest)}行")
        return list(latest.values())


class SoftDeleteStep(PipelineStep):
    """
    识别源端软删除列，并输出统一的 __deleted__ 标记列。
    这样本体映射阶段可以读取 __deleted__ 来决定是否将实体标记为 deprecated，
    而不会物理删除任何行（保证知识谱系完整）。

    spec 选项:
      delete_column: str   源端删除标识列名，如 is_deleted/del_flag/deleted
      true_values:  list   判定为"已删除"的值集合，默认 [1, "1", true, "true", "Y", "yes", "T"]
      keep_deleted: bool   True  = 在输出中保留已删除行（加 __deleted__=1）
                           False = 过滤掉已删除行（默认 True，保留以保证溯源）
      output_column: str   输出标记列名，默认 "__deleted__"
    """

    DEFAULT_TRUE_VALUES = {1, "1", True, "true", "TRUE", "Y", "y", "yes", "YES", "T", "t", 1.0}

    def run(self, ctx: PipelineContext, data: list[dict]) -> list[dict]:
        if not data:
            return data

        spec = ctx.spec.get("soft_delete", {})
        delete_col = spec.get("delete_column") or spec.get("column")
        true_vals = set(spec.get("true_values", self.DEFAULT_TRUE_VALUES))
        keep_deleted = spec.get("keep_deleted", True)
        out_col = spec.get("output_column", "__deleted__")

        if not delete_col or delete_col not in (data[0] or {}):
            # 尝试自动识别常见删除列
            for cand in ("is_deleted", "del_flag", "deleted", "is_del", "deleted_flag"):
                if cand in (data[0] or {}):
                    delete_col = cand
                    break

        if not delete_col:
            ctx.logs.append("[soft_delete] 未找到软删除列，跳过")
            return data

        result = []
        del_count = 0
        for row in data:
            val = row.get(delete_col)
            is_deleted = val in true_vals or (isinstance(val, str) and val.strip().lower() in {"1", "true", "y", "yes", "t"})
            out_row = dict(row)
            out_row[out_col] = 1 if is_deleted else 0
            if is_deleted:
                del_count += 1
                if not keep_deleted:
                    continue
            result.append(out_row)

        ctx.meta["soft_delete_column"] = delete_col
        ctx.meta["soft_delete_count"] = del_count
        ctx.meta["soft_delete_kept"] = keep_deleted
        ctx.logs.append(f"[soft_delete] column={delete_col} 识别删除{del_count}行")
        return result
