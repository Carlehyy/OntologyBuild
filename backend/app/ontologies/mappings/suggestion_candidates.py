"""映射建议的规则候选层：纯函数、无 IO。

信号优先级（与生产资产湖实测一致）：中文 display_name 是第一锚点，
列名归一化次之，token 重叠兜底；类型不兼容直接判 0（与保存校验同一套
`_mapping_types_compatible` 词表，避免建议出保存时会被拒绝的连线）。
"""
from __future__ import annotations

import re

from app.ontologies.mappings.request_validation import (
    _mapping_types_compatible,
    _normal_mapping_type,
)

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def normalize_token(name: object) -> str:
    """小写、驼峰/PascalCase 转下划线、非单词字符压缩为单下划线。"""
    text = str(name or "").strip()
    if not text:
        return ""
    text = _CAMEL_BOUNDARY.sub("_", text)
    text = _NON_WORD.sub("_", text.lower())
    return text.strip("_")


def _tokens(normalized: str) -> set[str]:
    return {token for token in normalized.split("_") if token}


def types_compatible(source_type: object, target_type: object) -> bool:
    return _mapping_types_compatible(
        _normal_mapping_type(source_type),
        _normal_mapping_type(target_type),
    )


def normal_type(raw: object) -> str:
    """归一化类型词表（知识库存储与唯一键使用，保证语义稳定）。"""
    return _normal_mapping_type(raw)


def score_column_to_property(column_doc: dict, prop: dict) -> float:
    """列文档 → 本体属性的规则匹配分（0~1）。类型不兼容硬过滤为 0。

    column_doc: {name, display_name, type}；prop: {name, displayName, type}。
    """
    if not types_compatible(column_doc.get("type"), prop.get("type")):
        return 0.0
    col_name = normalize_token(column_doc.get("name"))
    col_display = str(column_doc.get("display_name") or "").strip()
    prop_name = normalize_token(prop.get("name"))
    prop_display = str(prop.get("displayName") or prop.get("display_name") or "").strip()
    if not col_name or not prop_name:
        return 0.0
    if col_name == prop_name:
        return 1.0
    if col_display and prop_display and col_display == prop_display:
        return 0.95
    if col_display and col_display == str(prop.get("name") or "").strip():
        return 0.85
    if prop_display and prop_display == str(column_doc.get("name") or "").strip():
        return 0.85
    col_tokens, prop_tokens = _tokens(col_name), _tokens(prop_name)
    if col_tokens and prop_tokens:
        overlap = len(col_tokens & prop_tokens)
        if overlap:
            jaccard = overlap / len(col_tokens | prop_tokens)
            return 0.5 + 0.3 * jaccard
    return 0.0


def score_dataset_to_object(dataset_name: object, object_type: dict) -> float:
    """数据集名 → 对象实体的配对分（0~1）。"""
    ds_name = normalize_token(dataset_name)
    obj_name = normalize_token(object_type.get("name"))
    obj_display = str(
        object_type.get("displayName") or object_type.get("display_name") or ""
    ).strip()
    raw_name = str(dataset_name or "").strip()
    if not ds_name or not obj_name:
        return 0.0
    if ds_name == obj_name:
        return 1.0
    if obj_display and raw_name == obj_display:
        return 0.95
    if ds_name in obj_name or obj_name in ds_name:
        return 0.6
    if obj_display and (raw_name in obj_display or obj_display in raw_name):
        return 0.55
    return 0.0


def pick_primary_key_column(columns: list[dict], schema_json: dict) -> str | None:
    """主键列：湖侧主键契约优先，其次 id / *_id 词元启发。"""
    from app.data_channel.datasets.lake_gate import split_pk

    names = [str(col.get("name") or "") for col in columns if col.get("name")]
    contract = split_pk((schema_json or {}).get("primary_key"))
    for pk in contract:
        if pk in names:
            return pk
    for col in columns:
        if col.get("is_primary_key") and col.get("name") in names:
            return str(col["name"])
    lowered = {name.lower(): name for name in names}
    if "id" in lowered:
        return lowered["id"]
    for name in names:
        if name.lower().endswith("_id"):
            return name
    return None
