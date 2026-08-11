"""映射建议编排：知识库 → 规则 → LLM 概念化裁决，全部建议进人工确认队列。

流水线（与 frontend 建议面板约定 camelCase 响应）：
1. 版本守卫：仅草稿且 editing 可生成建议；
2. 飞轮回流：harvest 当前草稿与当前发布快照中人工保存过的映射（幂等）；
3. 逐数据集：湖列契约（get_schema）→ L0 知识库命中 → L1 规则候选 →
   L2 LLM 两段式概念化裁决（AutoSchemaKG 式：先归纳列概念再锚定本体属性）；
4. 服务端二次校验：幻觉属性/类型不兼容/不存在列一律丢弃转 unsure；
5. 无 LLM 时知识库+规则兜底，所有建议标记 unsure（人工确认文化）。
"""
from __future__ import annotations

import json
import logging
from collections import Counter

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.ontologies.mappings import mapping_knowledge
from app.ontologies.mappings.suggestion_candidates import (
    pick_primary_key_column,
    score_column_to_property,
    score_dataset_to_object,
    types_compatible,
)

logger = logging.getLogger(__name__)

# AutoSchemaKG 批处理模式：每批列数上限，大表分批以控制单次 prompt 规模。
_LLM_BATCH_SIZE = 16
_LLM_MAX_BATCHES = 4
# 规则分达到该阈值视为高置信，不再占用 LLM 额度（飞轮降本的关键路径）。
_RULE_CONFIDENT_SCORE = 0.9
_RULE_SUGGEST_SCORE = 0.5

_LLM_PURPOSE_TAGS = ("Ontology映射", "Mapping建议", "自动映射")


def generate_mapping_suggestions(
    db: Session,
    ontology_id: str,
    version_id: str,
    dataset_ids: list[str],
) -> dict:
    draft = _require_editable_draft(db, ontology_id, version_id)

    from app.ontologies.versions.snapshot_contract import complete_snapshot

    draft_snapshot = complete_snapshot(draft.snapshot_formal)
    _harvest_confirmed_mappings(db, ontology_id, draft_snapshot)

    inventory = _object_inventory(draft_snapshot)
    if not inventory:
        raise HTTPException(422, detail={
            "code": "empty_ontology",
            "message": "草稿中还没有对象实体，请先在模型结构中建模后再生成映射建议。",
        })
    property_index = {
        (obj["name"], prop["name"]): prop["type"]
        for obj in inventory
        for prop in obj["properties"]
    }
    existing_by_dataset = _existing_mapping_targets(draft_snapshot, inventory)
    llm_kwargs = _resolve_llm_kwargs(db)

    suggestions = []
    knowledge_hits = 0
    for dataset_id in dataset_ids:
        entry = _suggest_for_dataset(
            db, str(dataset_id), inventory, property_index,
            existing_by_dataset, llm_kwargs,
        )
        knowledge_hits += sum(
            1 for item in entry["fieldMappings"] if item["source"] == "knowledge"
        )
        suggestions.append(entry)
    return {
        "llmAvailable": bool(llm_kwargs),
        "knowledgeHits": knowledge_hits,
        "suggestions": suggestions,
    }


# ── 版本守卫（语义对齐 versions/workspace_service，避免跨域私有依赖）────────

def _require_editable_draft(db: Session, ontology_id: str, version_id: str):
    from app.ontologies.versions.models import OntologyVersion

    draft = db.query(OntologyVersion).filter(
        OntologyVersion.id == version_id,
        OntologyVersion.ontology_id == ontology_id,
    ).first()
    if draft is None:
        raise HTTPException(404, "Version not found")
    if draft.node_kind != "draft":
        raise HTTPException(409, detail={
            "code": "immutable_release",
            "message": "发布版本不可修改，请先创建草稿分支",
        })
    if draft.lifecycle_status == "trial_ready":
        raise HTTPException(409, detail={
            "code": "trial_snapshot_frozen",
            "message": "试跑态快照已冻结；如需继续修改，请从该版本创建新的草稿分支",
        })
    if draft.lifecycle_status != "editing":
        raise HTTPException(409, detail={
            "code": "archived_version_immutable",
            "message": "该版本已归档不可修改，请从该版本创建新的草稿分支",
        })
    return draft


def _harvest_confirmed_mappings(db: Session, ontology_id: str, draft_snapshot: dict) -> None:
    """飞轮回流：人工保存过的映射入知识库。失败不阻断建议生成。"""
    from app.models.ontology import OntologyProject
    from app.ontologies.versions.models import OntologyVersion
    from app.ontologies.versions.snapshot_contract import complete_snapshot

    try:
        touched = mapping_knowledge.harvest_snapshot_mappings(db, draft_snapshot)
        project = db.query(OntologyProject).filter(
            OntologyProject.id == ontology_id).first()
        if project and project.current_release_id:
            release = db.query(OntologyVersion).filter(
                OntologyVersion.id == project.current_release_id).first()
            if release is not None:
                touched += mapping_knowledge.harvest_snapshot_mappings(
                    db, complete_snapshot(release.snapshot_formal))
        if touched:
            logger.info("mapping knowledge harvested %s entries for %s", touched, ontology_id)
    except Exception:
        db.rollback()
        logger.exception("mapping knowledge harvest failed for %s", ontology_id)


# ── 本体清单 ─────────────────────────────────────────────────────────────

def _object_inventory(snapshot: dict) -> list[dict]:
    inventory = []
    for item in (snapshot.get("objectTypes") or []):
        if not isinstance(item, dict):
            continue
        properties = []
        for prop in item.get("properties") or []:
            if not isinstance(prop, dict):
                continue
            if prop.get("computed") or prop.get("source") == "computed":
                continue
            name = str(prop.get("name") or "").strip()
            if not name:
                continue
            properties.append({
                "name": name,
                "displayName": str(prop.get("displayName") or prop.get("display_name") or ""),
                "type": str(prop.get("type") or "string"),
            })
        name = str(item.get("name") or "").strip()
        if name:
            inventory.append({
                "id": str(item.get("id") or ""),
                "name": name,
                "displayName": str(item.get("displayName") or item.get("display_name") or ""),
                "primaryKey": item.get("primaryKey") or item.get("primary_key"),
                "properties": properties,
            })
    return inventory


def _existing_mapping_targets(snapshot: dict, inventory: list[dict]) -> dict[str, str]:
    """草稿快照中已有映射的 数据集id → 对象实体id。"""
    by_name = {obj["name"]: obj["id"] for obj in inventory}
    targets: dict[str, str] = {}
    for mapping in snapshot.get("mappings") or []:
        if not isinstance(mapping, dict):
            continue
        dataset_id = mapping.get("curated_dataset_id") or mapping.get("curatedDatasetId")
        if not dataset_id:
            continue
        target = mapping.get("target_object_type_id") or mapping.get("targetObjectTypeId")
        if not target:
            entity_class = mapping.get("entity_class") or mapping.get("entityClass")
            target = by_name.get(str(entity_class or ""))
        if target:
            targets[str(dataset_id)] = str(target)
    return targets


# ── LLM 通道（沿用 AutoMapper 的 patch seam）──────────────────────────────

def _resolve_llm_kwargs(db: Session) -> dict | None:
    from app.services.model_config_selector import (
        llm_call_kwargs,
        select_llm_model_config,
    )

    try:
        return llm_call_kwargs(select_llm_model_config(
            db, purpose_tags=_LLM_PURPOSE_TAGS, allow_vlm=False))
    except Exception:
        logger.info("mapping suggestion: no usable LLM config", exc_info=True)
        return None


def _llm_adjudicate(
    llm_kwargs: dict,
    inventory: list[dict],
    dataset_name: str,
    columns: list[dict],
    examples: list[str],
    pairing_hint: dict | None,
) -> dict:
    """AutoSchemaKG 式两段式：先归纳每列业务概念，再把概念锚定到本体属性。"""
    from app.services import llm_service

    inventory_lines = []
    for obj in inventory:
        props = "、".join(
            f"{prop['name']}（{prop['displayName'] or prop['name']}, {prop['type']}）"
            for prop in obj["properties"]
        )
        inventory_lines.append(
            f"- id={obj['id']} 对象 {obj['name']}（{obj['displayName'] or obj['name']}）：{props}"
        )
    column_lines = []
    for index, col in enumerate(columns, start=1):
        display = str(col.get("display_name") or "").strip()
        samples = json.dumps(col.get("sample_values") or [], ensure_ascii=False)
        flags = "，主键列" if col.get("is_primary_key") else ""
        column_lines.append(
            f"{index}. {col.get('name')}（{display or '无显示名'}）"
            f"类型 {col.get('type') or 'string'}{flags}，样例：{samples}"
        )
    example_block = "\n".join(f"- {line}" for line in examples) or "（无）"
    hint_block = (
        f"当前候选配对：{pairing_hint['name']}（{pairing_hint['displayName']}），可纠正。"
        if pairing_hint else "当前无候选配对。"
    )
    prompt = f"""请把数据资产湖的表映射到本体对象实体，分两步：先归纳每列的业务概念（概念化），再把概念锚定到本体属性。

【本体对象清单】（仅可锚定到下列对象与属性）
{chr(10).join(inventory_lines)}

【数据集】{dataset_name}
列文档：
{chr(10).join(column_lines)}

【已确认的历史映射】（供参考复用）
{example_block}

{hint_block}

【输出要求】只输出 JSON：
{{
  "pairing": {{"object_type_id": "清单中的对象 id 或 null", "verdict": "match 或 unsure", "reason": "配对理由"}},
  "column_concepts": [{{"column": "列名", "concept": "该列的业务概念"}}],
  "field_mappings": [{{"column": "列名", "property": "属性名", "verdict": "match 或 unsure 或 skip", "reason": "理由"}}],
  "primary_key_column": "主键列名或 null"
}}
约束：property 必须来自配对对象的属性清单且类型兼容；拿不准用 unsure；无合适属性用 skip。"""
    raw = llm_service._call_llm(
        **llm_kwargs,
        messages=[
            {"role": "system", "content": "你是数据建模专家。只输出 JSON，不要输出其他文字。"},
            {"role": "user", "content": prompt},
        ],
    )
    return _parse_llm_json(raw)


def _parse_llm_json(raw: object) -> dict:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
        text = text.strip()
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("LLM 返回不是 JSON")
        payload = json.loads(text[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM 返回不是 JSON 对象")
    return payload


# ── 单数据集建议 ─────────────────────────────────────────────────────────

def _suggest_for_dataset(
    db: Session,
    dataset_id: str,
    inventory: list[dict],
    property_index: dict[tuple[str, str], str],
    existing_by_dataset: dict[str, str],
    llm_kwargs: dict | None,
) -> dict:
    from app.data_channel.datasets.query_service import get_schema
    from app.models.v2.dataset import Dataset

    base = {
        "datasetId": dataset_id,
        "datasetName": "",
        "objectTypeId": None,
        "pairingVerdict": "unsure",
        "pairingReason": "",
        "primaryKeyColumn": None,
        "existingObjectTypeId": None,
        "fieldMappings": [],
        "skippedColumns": [],
        "error": None,
    }
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if dataset is None:
        return {**base, "error": "数据集不存在或尚未迁入资产湖"}
    base["datasetName"] = dataset.name
    try:
        schema = get_schema(dataset_id, db)
    except Exception:
        logger.info("mapping suggestion: schema unavailable for %s", dataset_id, exc_info=True)
        return {**base, "error": "数据集结构暂不可用"}
    columns = [
        col for col in (schema.get("columns") or [])
        if isinstance(col, dict) and col.get("name")
    ]
    if not columns:
        return {**base, "error": "暂未识别到字段"}

    # ── 配对：已有映射 > 知识库投票 > 规则 > LLM（在 LLM 阶段补充裁决）──
    existing_target = existing_by_dataset.get(dataset_id)
    by_id = {obj["id"]: obj for obj in inventory}
    by_name = {obj["name"]: obj for obj in inventory}

    kb_top: dict[str, object] = {}
    kb_object_votes: Counter[str] = Counter()
    for column in columns:
        hits = mapping_knowledge.lookup(db, column, property_index, limit=1)
        if hits:
            kb_top[str(column["name"])] = hits[0]
            kb_object_votes[hits[0].object_name] += 1

    paired: dict | None = None
    if existing_target and existing_target in by_id:
        paired = by_id[existing_target]
        base["existingObjectTypeId"] = existing_target
        base["pairingVerdict"] = "match"
        base["pairingReason"] = "该数据集在草稿中已有映射，沿用既有配对"
    elif kb_object_votes:
        voted = kb_object_votes.most_common(1)[0][0]
        if voted in by_name:
            paired = by_name[voted]
            base["pairingVerdict"] = "match"
            base["pairingReason"] = "列级历史映射多数指向该对象（数据飞轮复用）"
    if paired is None:
        rule_best = max(
            inventory,
            key=lambda obj: score_dataset_to_object(dataset.name, obj),
            default=None,
        )
        if rule_best is not None and score_dataset_to_object(dataset.name, rule_best) >= 0.5:
            paired = rule_best
            base["pairingVerdict"] = "unsure"
            base["pairingReason"] = "按数据集名称与对象名称相似度推荐，请确认"

    # ── 字段级：L0 知识库 / L1 规则高置信先行，其余交给 LLM ──
    paired_props = (paired or {}).get("properties") or []
    field_mappings: list[dict] = []
    skipped: list[dict] = []
    llm_pending: list[dict] = []
    for column in columns:
        name = str(column["name"])
        hit = kb_top.get(name)
        if hit is not None and paired is not None and hit.object_name == paired["name"]:
            field_mappings.append({
                "column": name,
                "property": hit.property_name,
                "verdict": "match",
                "confidence": min(0.99, 0.8 + 0.02 * (hit.confirm_count or 1)),
                "reason": f"历史映射复用·已确认 {hit.confirm_count or 1} 次",
                "source": "knowledge",
            })
            continue
        best_prop, best_score = _best_rule_property(column, paired_props)
        # 高置信规则命中仅在 LLM 可用时直接标 match；LLM 缺失时全部转人工确认。
        if (
            best_prop is not None
            and best_score >= _RULE_CONFIDENT_SCORE
            and llm_kwargs
        ):
            field_mappings.append({
                "column": name,
                "property": best_prop["name"],
                "verdict": "match",
                "confidence": best_score,
                "reason": "列名与属性精确匹配",
                "source": "rule",
            })
            continue
        llm_pending.append(column)

    # ── L2 LLM 概念化裁决（无 LLM 时规则兜底全部 unsure）──
    llm_failed = False
    if llm_kwargs and (llm_pending or paired is None):
        examples = mapping_knowledge.few_shot_examples(db, columns, property_index)
        for batch in _batches(llm_pending, _LLM_BATCH_SIZE, _LLM_MAX_BATCHES):
            try:
                payload = _llm_adjudicate(
                    llm_kwargs, inventory, dataset.name, batch, examples, paired)
            except Exception:
                logger.info("mapping suggestion: LLM adjudication failed", exc_info=True)
                llm_failed = True
                break
            if paired is None:
                candidate = _validate_llm_pairing(payload, by_id)
                if candidate is not None:
                    paired = candidate
                    base["pairingVerdict"] = "unsure"
                    base["pairingReason"] = str(
                        (payload.get("pairing") or {}).get("reason") or "LLM 推荐配对，请确认")
                    paired_props = paired["properties"]
            accepted = _validate_llm_field_mappings(payload, batch, paired_props)
            for column in batch:
                name = str(column["name"])
                if name in accepted:
                    field_mappings.append(accepted[name])
                    llm_pending.remove(column)

    for column in llm_pending:
        name = str(column["name"])
        best_prop, best_score = _best_rule_property(column, paired_props)
        if best_prop is not None and best_score >= _RULE_SUGGEST_SCORE:
            field_mappings.append({
                "column": name,
                "property": best_prop["name"],
                "verdict": "unsure",
                "confidence": best_score,
                "reason": "LLM 不可用，按名称相似度推荐，请人工确认" if not llm_kwargs or llm_failed
                else "LLM 未给出结论，按名称相似度推荐，请人工确认",
                "source": "rule",
            })
        else:
            skipped.append({"column": name, "reason": "未找到可信的本体属性对应"})

    if paired is not None:
        base["objectTypeId"] = paired["id"]
    base["primaryKeyColumn"] = pick_primary_key_column(
        columns, dataset.schema_json if isinstance(dataset.schema_json, dict) else {})
    base["fieldMappings"] = field_mappings
    base["skippedColumns"] = skipped
    return base


def _best_rule_property(column: dict, properties: list[dict]) -> tuple[dict | None, float]:
    best_prop, best_score = None, 0.0
    for prop in properties:
        score = score_column_to_property(column, prop)
        if score > best_score:
            best_prop, best_score = prop, score
    return best_prop, best_score


def _batches(columns: list[dict], size: int, max_batches: int) -> list[list[dict]]:
    batches = [columns[index:index + size] for index in range(0, len(columns), size)]
    return batches[:max_batches]


def _validate_llm_pairing(payload: dict, by_id: dict[str, dict]) -> dict | None:
    pairing = payload.get("pairing")
    if not isinstance(pairing, dict):
        return None
    object_type_id = str(pairing.get("object_type_id") or "").strip()
    return by_id.get(object_type_id)


def _validate_llm_field_mappings(
    payload: dict,
    batch: list[dict],
    properties: list[dict],
) -> dict[str, dict]:
    """服务端二次校验：幻觉属性/类型不兼容/不存在列一律丢弃。"""
    column_types = {str(col["name"]): col.get("type") for col in batch}
    prop_by_name = {prop["name"]: prop for prop in properties}
    accepted: dict[str, dict] = {}
    raw_items = payload.get("field_mappings")
    if not isinstance(raw_items, list):
        return accepted
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        column = str(item.get("column") or "")
        prop_name = str(item.get("property") or "")
        verdict = str(item.get("verdict") or "unsure")
        if column not in column_types or prop_name not in prop_by_name:
            continue
        if verdict == "skip":
            continue
        if not types_compatible(column_types[column], prop_by_name[prop_name]["type"]):
            continue
        accepted[column] = {
            "column": column,
            "property": prop_name,
            "verdict": "match" if verdict == "match" else "unsure",
            "confidence": 0.85 if verdict == "match" else 0.5,
            "reason": str(item.get("reason") or "LLM 概念化锚定"),
            "source": "llm",
        }
    return accepted
