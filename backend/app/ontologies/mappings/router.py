"""v2 Ontology Mapping API — 含 Link Mapping 手动配置"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from app.database import SessionLocal
from app.deps import get_current_user
from app.ontologies.access import ontology_access_guard

router = APIRouter(dependencies=[Depends(ontology_access_guard)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class SuggestRequest(BaseModel):
    dataset_name: str
    columns: list[str]
    sample_rows: list[dict] = []
    ontology_domain: str = ""


class CreateMappingRequest(BaseModel):
    curated_dataset_id: str
    entity_class: str
    field_mapping: dict
    primary_key_column: Optional[str] = None
    property_mappings: Optional[list[dict]] = None
    confidence: float = 1.0
    # 人工绑定：灌入到图谱里已有的对象实体（空=按名匹配，再无则由数据生成新类型）
    target_object_type_id: Optional[str] = None
    # 审核通过后自动灌入本体（存入 field_mapping.__auto_apply_on_review__）
    auto_apply_on_review: bool = False


class UpdateMappingRequest(BaseModel):
    entity_class: Optional[str] = None
    field_mapping: Optional[dict] = None
    primary_key_column: Optional[str] = None
    target_object_type_id: Optional[str] = None
    auto_apply_on_review: Optional[bool] = None


@router.post("/{ontology_id}/mappings/suggest")
def suggest_mapping(ontology_id: str, body: SuggestRequest, db: Session = Depends(get_db)):
    from app.services.v2.mapping.auto_mapper import AutoMapper
    mapper = AutoMapper(db)
    suggestion = mapper.suggest_field_mapping(
        body.dataset_name, body.columns, body.sample_rows, body.ontology_domain
    )
    return {
        "entity_class": suggestion.entity_class,
        "entity_class_cn": suggestion.entity_class_cn,
        "description": suggestion.description,
        "primary_key_column": suggestion.primary_key_column,
        "field_mappings": [
            {
                "column_name": fm.column_name,
                "property_name": fm.property_name,
                "property_type": fm.property_type,
                "confidence": fm.confidence,
                "reason": fm.reason,
            }
            for fm in suggestion.field_mappings
        ],
    }


def _validate_target_type(db: Session, ontology_id: str, type_id: Optional[str]):
    if not type_id:
        return None
    from app.models.ontology_formal import ObjectType
    ot = db.query(ObjectType).filter(
        ObjectType.id == type_id, ObjectType.ontology_id == ontology_id).first()
    if not ot:
        raise HTTPException(422, f"绑定的对象实体不存在: {type_id}")
    return ot


def _require_draft_ontology(db: Session, ontology_id: str) -> None:
    """发布后冻结 Mapping 结构；数据实例仍可通过 apply-from-dataset 更新。"""
    from app.models.ontology import OntologyProject
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).with_for_update().first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    if project.status != "draft":
        raise HTTPException(
            409,
            "本体已发布，Mapping/LinkMapping 结构已冻结；"
            "请先创建或切换到 draft 版本再维护映射")


def _reject_reserved_mapping_keys(value: Optional[dict], field_name: str) -> None:
    """System lineage keys are write-only for the mapping runtime."""
    reserved = sorted(
        str(key) for key in (value or {}) if str(key).startswith("__"))
    if reserved:
        raise HTTPException(422, detail={
            "code": "reserved_mapping_keys",
            "message": f"{field_name} 包含平台保留键，客户端不得写入",
            "keys": reserved,
        })


@router.post("/{ontology_id}/mappings")
def create_mapping(ontology_id: str, body: CreateMappingRequest, db: Session = Depends(get_db)):
    from app.services.v2.mapping.mapping_service import MappingService
    _require_draft_ontology(db, ontology_id)
    _validate_target_type(db, ontology_id, body.target_object_type_id)
    _reject_reserved_mapping_keys(body.field_mapping, "field_mapping")

    # 人工数据集（非 curated）可直接灌入本体，但必须先声明主键契约——
    # 无主键时实例身份退化为整行哈希，字段一变就堆积新实例
    from app.models.v2.dataset import Dataset
    ds = db.query(Dataset).filter(Dataset.id == body.curated_dataset_id).first()
    if ds is not None and ds.kind != "curated":
        declared_pk = str((ds.schema_json or {}).get("primary_key") or "").strip()
        if not declared_pk:
            raise HTTPException(400,
                f"人工数据集「{ds.name}」尚未声明主键契约，无法灌入本体。"
                f"请先到 数据资产湖 → 人工数据集 → 维护数据 中声明主键")

    svc = MappingService(db)
    field_mapping = dict(body.field_mapping or {})
    if body.property_mappings:
        field_mapping["__properties__"] = body.property_mappings
    if body.auto_apply_on_review:
        field_mapping["__auto_apply_on_review__"] = True
    mapping = svc.create_mapping(
        ontology_id=ontology_id,
        curated_dataset_id=body.curated_dataset_id,
        entity_class=body.entity_class,
        field_mapping=field_mapping,
        primary_key_column=body.primary_key_column,
        confidence=body.confidence,
        target_object_type_id=body.target_object_type_id,
    )
    return {"mapping_id": mapping.id, "status": mapping.status}


@router.put("/{ontology_id}/mappings/{mapping_id}")
def update_mapping(ontology_id: str, mapping_id: str, body: UpdateMappingRequest,
                   db: Session = Depends(get_db)):
    """映射维护：改绑定 / 改字段映射 / 开关审核后自动灌入。"""
    _require_draft_ontology(db, ontology_id)
    from app.models.v2.mapping import OntologyMapping
    m = db.query(OntologyMapping).filter(
        OntologyMapping.id == mapping_id,
        OntologyMapping.ontology_id == ontology_id).first()
    if not m:
        raise HTTPException(404, "Mapping not found")
    provided = body.model_fields_set
    if "target_object_type_id" in provided:
        _validate_target_type(db, ontology_id, body.target_object_type_id)
        m.target_object_type_id = body.target_object_type_id
    if body.entity_class is not None:
        m.entity_class = body.entity_class
    fm = dict(m.field_mapping or {})
    if body.field_mapping is not None:
        _reject_reserved_mapping_keys(body.field_mapping, "field_mapping")
        # Preserve runtime-owned keys; user payloads containing them are rejected.
        sys_keys = {k: v for k, v in fm.items() if k.startswith("__")}
        fm = {**sys_keys, **body.field_mapping}
    if body.primary_key_column is not None:
        fm["__primary_key__"] = body.primary_key_column
    if body.auto_apply_on_review is not None:
        if body.auto_apply_on_review:
            fm["__auto_apply_on_review__"] = True
        else:
            fm.pop("__auto_apply_on_review__", None)
    projection_changed = bool({
        "entity_class", "field_mapping", "primary_key_column",
        "target_object_type_id",
    } & provided)
    if projection_changed:
        # Any definition change invalidates the previous apply attestation.  The
        # old marker must never be reused by the release gate for new semantics.
        for key in list(fm):
            if key.startswith("__applied_") or key == "__last_apply_error__":
                fm.pop(key, None)
        m.status = "draft"
    m.field_mapping = fm
    db.commit(); db.refresh(m)
    return {"mapping_id": m.id, "status": m.status,
            "target_object_type_id": m.target_object_type_id,
            "auto_apply_on_review": bool(fm.get("__auto_apply_on_review__"))}


@router.delete("/{ontology_id}/mappings/{mapping_id}", status_code=204)
def delete_mapping(ontology_id: str, mapping_id: str, db: Session = Depends(get_db)):
    """删除映射并撤销其当前态投影；不可变事实历史通过墓碑保留。"""
    _require_draft_ontology(db, ontology_id)
    from app.models.v2.mapping import OntologyMapping
    from app.services.v2.mapping.mapping_service import MappingApplyError, MappingService
    m = db.query(OntologyMapping).filter(
        OntologyMapping.id == mapping_id,
        OntologyMapping.ontology_id == ontology_id).first()
    if not m:
        raise HTTPException(404, "Mapping not found")
    svc = MappingService(db)
    try:
        stale_ids = svc.remove_mapping_projection(m)
        db.delete(m)
        db.commit()
    except MappingApplyError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    # Neo4j is a rebuildable read projection and is reconciled after truth commits.
    svc._delete_neo4j_entities(ontology_id, stale_ids)


@router.get("/{ontology_id}/mappings")
def list_mappings(ontology_id: str, db: Session = Depends(get_db)):
    from app.services.v2.mapping.mapping_service import MappingService
    from app.models.v2.dataset import Dataset
    from app.models.v2.curated import CuratedDataset
    from app.models.ontology_formal import ObjectType
    svc = MappingService(db)
    mappings = svc.get_mappings(ontology_id)
    # 绑定/同名解析结果预载：让前端直接显示"灌到哪个对象实体"
    ot_by_id = {o.id: o for o in db.query(ObjectType).filter(
        ObjectType.ontology_id == ontology_id).all()}
    ot_by_name = {}
    for o in ot_by_id.values():
        ot_by_name.setdefault(o.name, o)
        if o.display_name:
            ot_by_name.setdefault(o.display_name, o)
    result = []
    for m in mappings:
        dataset_name = None
        row_count = None
        if m.curated_dataset_id:
            ds = db.query(Dataset).filter(Dataset.id == m.curated_dataset_id).first()
            if ds:
                dataset_name = ds.name
            else:
                cd = db.query(CuratedDataset).filter(CuratedDataset.id == m.curated_dataset_id).first()
                if cd:
                    dataset_name = cd.name
            from app.models.v2.dataset import DatasetVersion
            ver = db.query(DatasetVersion).filter(
                DatasetVersion.dataset_id == m.curated_dataset_id
            ).order_by(DatasetVersion.version_no.desc()).first()
            if ver:
                row_count = ver.rowcount
        # 解析该映射会灌到哪个对象实体（显式绑定 → 按名匹配 → 数据新建）
        bound_ot = ot_by_id.get(m.target_object_type_id) if m.target_object_type_id else None
        matched_ot = bound_ot or ot_by_name.get(m.entity_class)
        if bound_ot is not None:
            binding_mode = "bound"        # 人工绑定
        elif matched_ot is not None:
            binding_mode = "name_match"   # 自动按名复用
        else:
            binding_mode = "auto_create"  # 由数据生成新类型
        result.append({
            "id": m.id,
            "curated_dataset_id": m.curated_dataset_id,
            "dataset_name": dataset_name,
            "row_count": row_count,
            "entity_class": m.entity_class,
            "field_mapping": m.field_mapping,
            "property_mappings": (m.field_mapping or {}).get("__properties__", []),
            "status": m.status,
            "confidence": m.confidence,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "target_object_type_id": m.target_object_type_id,
            "binding_mode": binding_mode,
            "resolved_object_type": ({
                "id": matched_ot.id,
                "name": matched_ot.name,
                "display_name": matched_ot.display_name,
            } if matched_ot else None),
            "auto_apply_on_review": bool((m.field_mapping or {}).get("__auto_apply_on_review__")),
        })
    return result


@router.post("/{ontology_id}/mappings/{mapping_id}/apply")
def apply_mapping(ontology_id: str, mapping_id: str, data: list[dict], db: Session = Depends(get_db)):
    """已禁用的原始数据旁路。

    正式 Apply 必须从映射绑定的、可追溯的 DatasetVersion 读取；否则请求方可
    用任意 JSON 冒充资产湖数据，审批、checksum 与版本血缘全部失效。
    """
    from app.models.v2.mapping import OntologyMapping
    mapping = db.query(OntologyMapping).filter(
        OntologyMapping.id == mapping_id,
        OntologyMapping.ontology_id == ontology_id).first()
    if not mapping:
        raise HTTPException(404, "Mapping not found in this ontology")
    raise HTTPException(
        409,
        "禁止直接提交原始数据执行映射；请使用 apply-from-dataset，"
        "由服务端读取已绑定且通过校验的数据版本")


@router.post("/{ontology_id}/mappings/{mapping_id}/apply-from-dataset")
def apply_mapping_from_dataset(ontology_id: str, mapping_id: str, db: Session = Depends(get_db)):
    from app.models.v2.mapping import OntologyMapping
    from app.services.v2.mapping.mapping_service import (
        MappingApplyError, MappingSourceError, MappingService)

    mapping = db.query(OntologyMapping).filter(
        OntologyMapping.id == mapping_id,
        OntologyMapping.ontology_id == ontology_id,
    ).first()
    if not mapping:
        raise HTTPException(404, "Mapping not found")
    if not mapping.curated_dataset_id:
        raise HTTPException(400, "Mapping has no curated_dataset_id")

    svc = MappingService(db)
    try:
        result = svc.build_all(ontology_id, require_approved=True)
        result["trigger_mapping_id"] = mapping_id
        return result
    except MappingSourceError as e:
        raise HTTPException(422, str(e))
    except MappingApplyError as e:
        raise HTTPException(500, str(e))


@router.post("/{ontology_id}/mappings/build-all")
def build_all_mappings(ontology_id: str, db: Session = Depends(get_db)):
    from app.services.v2.mapping.mapping_service import (
        MappingApplyError, MappingSourceError, MappingService)
    from app.models.v2.mapping import OntologyLinkMapping
    svc = MappingService(db)
    try:
        result = svc.build_all(ontology_id, require_approved=True)
        active_links = db.query(OntologyLinkMapping).filter(
            OntologyLinkMapping.ontology_id == ontology_id,
            OntologyLinkMapping.status == "active",
        ).count()
        inferred_links = db.query(OntologyLinkMapping).filter(
            OntologyLinkMapping.ontology_id == ontology_id,
            OntologyLinkMapping.status == "inferred",
        ).count()
        result["link_mappings_configured"] = active_links
        result["link_mappings_inferred"] = inferred_links
        return result
    except MappingSourceError as e:
        raise HTTPException(422, detail=str(e))
    except MappingApplyError as e:
        raise HTTPException(500, detail=str(e))
    except Exception as e:
        raise HTTPException(500, detail=str(e))


class LinkMappingCreate(BaseModel):
    src_dataset_id: str
    tgt_dataset_id: str
    relation_type: str
    src_key: str
    tgt_key: str
    # —— 胖关系（连接表 + 边属性）——
    # link_type_id: 绑定手绘 LinkType，令边属性名对齐其 properties schema（为空则按 relation_type 名匹配/自建）
    link_type_id: str | None = None
    # edge_dataset_id: 连接表/关系数据集。有值 → 胖关系（src_key/tgt_key/field_mapping 列均在连接表内）
    edge_dataset_id: str | None = None
    # field_mapping: {边属性名: 连接表列名}
    field_mapping: dict = {}


@router.post("/{ontology_id}/link-mappings")
def create_link_mapping(ontology_id: str, body: LinkMappingCreate, db: Session = Depends(get_db)):
    from app.models.v2.mapping import OntologyLinkMapping
    from app.services.v2.dataset_service import DatasetService
    _require_draft_ontology(db, ontology_id)
    _reject_reserved_mapping_keys(body.field_mapping, "field_mapping")

    svc = DatasetService(db)
    try:
        src_rows = svc.preview(body.src_dataset_id, None, limit=10000)
        tgt_rows = svc.preview(body.tgt_dataset_id, None, limit=10000)
    except Exception as e:
        raise HTTPException(400, f"Failed to preview datasets for link mapping: {e}")
    if not src_rows:
        raise HTTPException(400, "Source dataset has no rows")
    if not tgt_rows:
        raise HTTPException(400, "Target dataset has no rows")

    if body.edge_dataset_id:
        # —— 连接表胖关系：src_key/tgt_key 及 field_mapping 的列都在连接表内 ——
        try:
            edge_rows = svc.preview(body.edge_dataset_id, None, limit=10000)
        except Exception as e:
            raise HTTPException(400, f"Failed to preview edge dataset for link mapping: {e}")
        if not edge_rows:
            raise HTTPException(400, "Edge (junction) dataset has no rows")
        edge_cols = set(edge_rows[0].keys())
        if body.src_key not in edge_cols:
            raise HTTPException(400, f"Source FK '{body.src_key}' not found in edge dataset")
        if body.tgt_key not in edge_cols:
            raise HTTPException(400, f"Target FK '{body.tgt_key}' not found in edge dataset")
        missing = [c for c in (body.field_mapping or {}).values() if c not in edge_cols]
        if missing:
            raise HTTPException(400, f"Edge property columns not found in edge dataset: {missing}")
        # 两端外键须命中端点数据集（跨所有列做宽松交集，容错端点主键列未知）
        src_all = {str(r.get(c, "")).strip() for r in src_rows for c in src_rows[0].keys()}
        tgt_all = {str(r.get(c, "")).strip() for r in tgt_rows for c in tgt_rows[0].keys()}
        match_count = sum(
            1 for er in edge_rows
            if str(er.get(body.src_key, "")).strip() in src_all
            and str(er.get(body.tgt_key, "")).strip() in tgt_all
        )
        if match_count == 0:
            raise HTTPException(400, "连接表两端外键未命中任何端点实体；请检查 FK 列选择")
    else:
        # —— 直连外键瘦关系（原行为不变）——
        if body.src_key not in src_rows[0]:
            raise HTTPException(400, f"Source key '{body.src_key}' not found in source dataset")
        if body.tgt_key not in tgt_rows[0]:
            raise HTTPException(400, f"Target key '{body.tgt_key}' not found in target dataset")
        tgt_values = {str(row.get(body.tgt_key, "")).strip() for row in tgt_rows if row.get(body.tgt_key)}
        match_count = sum(1 for row in src_rows if str(row.get(body.src_key, "")).strip() in tgt_values)
        if match_count == 0:
            raise HTTPException(400, "Link mapping produced 0 matches; choose different source/target keys")

    lm = OntologyLinkMapping(
        ontology_id=ontology_id,
        src_dataset_id=body.src_dataset_id,
        tgt_dataset_id=body.tgt_dataset_id,
        relation_type=body.relation_type,
        src_key=body.src_key,
        tgt_key=body.tgt_key,
        link_type_id=body.link_type_id,
        edge_dataset_id=body.edge_dataset_id,
        field_mapping=body.field_mapping or {},
        status="active",
    )
    db.add(lm)
    db.commit()
    db.refresh(lm)
    return {"link_mapping_id": lm.id, "relation_type": lm.relation_type,
            "match_count": match_count, "edge_dataset_id": lm.edge_dataset_id,
            "edge_properties": list((body.field_mapping or {}).keys())}


@router.get("/{ontology_id}/link-mappings")
def list_link_mappings(ontology_id: str, db: Session = Depends(get_db)):
    from app.models.v2.mapping import OntologyLinkMapping
    links = db.query(OntologyLinkMapping).filter(
        OntologyLinkMapping.ontology_id == ontology_id
    ).all()
    return [{
        "id": l.id, "src_dataset_id": l.src_dataset_id, "tgt_dataset_id": l.tgt_dataset_id,
        "relation_type": l.relation_type, "src_key": l.src_key, "tgt_key": l.tgt_key,
        "status": l.status,
        "link_type_id": l.link_type_id, "edge_dataset_id": l.edge_dataset_id,
        "field_mapping": l.field_mapping or {},
        "is_fat": bool(l.edge_dataset_id),
    } for l in links]


@router.delete("/{ontology_id}/link-mappings/{link_mapping_id}", status_code=204)
def delete_link_mapping(ontology_id: str, link_mapping_id: str, db: Session = Depends(get_db)):
    """删除关系映射并撤销当前态边；Link Fact 历史以墓碑保留。"""
    _require_draft_ontology(db, ontology_id)
    from app.models.v2.mapping import OntologyLinkMapping
    from app.services.v2.mapping.mapping_service import MappingApplyError, MappingService
    lm = db.query(OntologyLinkMapping).filter(
        OntologyLinkMapping.id == link_mapping_id,
        OntologyLinkMapping.ontology_id == ontology_id,
    ).first()
    if not lm:
        raise HTTPException(404, "Link mapping not found")
    svc = MappingService(db)
    try:
        svc.remove_link_mapping_projection(lm)
        db.delete(lm)
        db.commit()
    except MappingApplyError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    svc._rebuild_neo4j_projection(ontology_id)
    return None
