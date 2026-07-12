"""Pure validation/application step shared by platform and anonymous editors."""
from __future__ import annotations

from fastapi import HTTPException


def build_edited_snapshot(db, svc, ds, body) -> tuple[list[dict], list[str], dict]:
    """Apply row operations in memory and validate the complete proposed snapshot.

    No database or object-storage writes happen here. Callers may therefore use the
    same function both when an external editor submits a proposal and when a reviewer
    later applies it.
    """
    from app.data_channel.datasets.lake_gate import (
        LakeGateError, infer_columns_typed, split_pk, validate_declared_types, validate_pk)
    from app.services.v2.dataset_service import DatasetReadError

    schema = dict(ds.schema_json or {})
    pk_cols = split_pk(schema.get("primary_key"))
    if (body.updates or body.deletes) and not pk_cols:
        raise HTTPException(400, "修改/删除行需要先声明主键契约（用于定位行并保证身份稳定）；未声明主键时仅支持新增行")

    try:
        rows = svc.load_all_rows(ds.id)
    except DatasetReadError as exc:
        raise HTTPException(502, str(exc))

    columns = list(rows[0].keys()) if rows else list(schema.get("columns") or [])
    if not columns:
        for op in body.inserts:
            for key in (op.values or {}).keys():
                if key not in columns:
                    columns.append(key)
    if not columns:
        raise HTTPException(400, "无法确定列结构：数据集为空且插入行未携带任何列")

    def check_known_cols(values: dict, what: str):
        unknown = [key for key in values if key not in columns]
        if unknown:
            raise HTTPException(400, f"{what}包含不存在的列 {unknown}（在线编辑不支持加列，请通过上传新版本调整列结构）")

    pk_index: dict[tuple, int] = {}
    for index, row in enumerate(rows):
        key_tuple = tuple(str(row.get(col, "") or "").strip() for col in pk_cols) if pk_cols else (index,)
        if pk_cols:
            pk_index[key_tuple] = index

    def locate(op, what: str) -> int:
        key = op.key or {}
        missing = [col for col in pk_cols if str(key.get(col, "") or "").strip() == ""]
        if missing:
            raise HTTPException(400, f"{what}缺少主键列 {missing} 的定位值")
        key_tuple = tuple(str(key.get(col, "")).strip() for col in pk_cols)
        index = pk_index.get(key_tuple)
        if index is None:
            raise HTTPException(400, f"{what}未找到主键为 {dict(zip(pk_cols, key_tuple))} 的行（可能已被删除，请刷新）")
        return index

    from app.models.v2.mapping import OntologyMapping
    mapping_count = db.query(OntologyMapping).filter(
        OntologyMapping.curated_dataset_id == ds.id).count()
    if mapping_count:
        for op in body.updates:
            values, key = op.values or {}, op.key or {}
            changed_pk = [
                col for col in pk_cols
                if col in values and str(values.get(col, "") or "").strip()
                != str(key.get(col, "") or "").strip()
            ]
            if changed_pk:
                raise HTTPException(409, detail={
                    "code": "mapped_primary_key_rekey_forbidden",
                    "message": (
                        f"该数据集已被 {mapping_count} 条本体映射绑定，不能直接修改主键列 {changed_pk}。"
                        "请先解除映射，或使用可迁移下游实例与关系的显式 re-key 流程。"
                    ),
                })

    work = [dict(row) for row in rows]
    for op in body.updates:
        check_known_cols(op.values or {}, "修改的行")
        work[locate(op, "修改")].update(op.values or {})
    tombstones = {locate(op, "删除") for op in body.deletes}
    new_rows = [row for index, row in enumerate(work) if index not in tombstones]
    for op in body.inserts:
        check_known_cols(op.values or {}, "新增的行")
        new_rows.append({col: (op.values or {}).get(col, "") for col in columns})

    # 主键是行身份，只能用于定位，在线维护不提供 re-key 能力。
    for op in body.updates:
        values, key = op.values or {}, op.key or {}
        changed_pk = [
            col for col in pk_cols
            if col in values and str(values.get(col, "") or "").strip()
            != str(key.get(col, "") or "").strip()
        ]
        if changed_pk:
            raise HTTPException(
                400,
                f"主键列 {changed_pk} 不允许修改；如业务身份发生变化，请新增一行并删除原行",
            )

    # 主键与显式 nullable=False 字段都属于非空契约。保存时校验完整快照，
    # 避免只校验当前分页而让其他页上的坏数据随新版本一起发布。
    required_columns = set(pk_cols)
    for column in schema.get("columns_typed") or []:
        if isinstance(column, dict) and column.get("name") and column.get("nullable") is False:
            required_columns.add(str(column["name"]))
    for definition in schema.get("contract_definitions") or []:
        if isinstance(definition, dict) and definition.get("field_key") and definition.get("nullable") is False:
            required_columns.add(str(definition["field_key"]))
    for row_index, row in enumerate(new_rows):
        for column in sorted(required_columns):
            value = row.get(column)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise HTTPException(
                    400,
                    f"第 {row_index + 1} 行的非空列「{column}」不能为空，请补全后再保存",
                )

    if schema.get("columns_typed"):
        try:
            validate_declared_types(
                new_rows,
                schema.get("columns_typed"), dataset_name=ds.name)
        except LakeGateError as exc:
            raise HTTPException(400, str(exc))

    if pk_cols:
        try:
            validate_pk(new_rows, pk_cols, dataset_name=ds.name, scope="编辑后的数据")
        except LakeGateError as exc:
            raise HTTPException(400, str(exc))

    if new_rows:
        schema["columns"] = columns
        if schema.get("types_source") != "declared":
            schema["columns_typed"] = infer_columns_typed(new_rows)
    return new_rows, columns, schema
