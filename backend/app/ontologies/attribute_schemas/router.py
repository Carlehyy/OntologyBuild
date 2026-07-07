"""类型化属性 Schema + 词表管理路由"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import desc
import re
import uuid
from app.deps import get_db, get_current_user
from app.models.attribute_schema import AttributeSchema, VocabularyEntry

router = APIRouter(dependencies=[Depends(get_current_user)])


class AttrSchemaCreate(BaseModel):
    name: str
    display_name: str
    description: str | None = None
    data_type: str = "string"  # string, number, integer, boolean, date, enum, range, url, email
    constraints: dict = Field(default_factory=dict)
    default_value: str | None = None
    applies_to_types: list[str] = Field(default_factory=list)
    sort_order: int = 0


class VocabCreate(BaseModel):
    canonical: str
    synonyms: list[str] = Field(default_factory=list)
    abbreviations: list[str] = Field(default_factory=list)
    entity_type: str | None = None
    linked_entity_id: str | None = None


# ── 属性 Schema ──────────────────────────────────────────────────────

@router.get("/{ontology_id}/attribute-schemas")
def list_schemas(ontology_id: str, db: Session = Depends(get_db)):
    items = db.query(AttributeSchema).filter(
        AttributeSchema.ontology_id == ontology_id,
        AttributeSchema.enabled == True,
    ).order_by(AttributeSchema.sort_order).all()
    return {"data": [{
        "id": s.id, "name": s.name, "display_name": s.display_name,
        "description": s.description, "data_type": s.data_type,
        "constraints": s.constraints or {}, "default_value": s.default_value,
        "applies_to_types": s.applies_to_types or [], "sort_order": s.sort_order,
    } for s in items]}


@router.post("/{ontology_id}/attribute-schemas", status_code=201)
def create_schema(ontology_id: str, body: AttrSchemaCreate, db: Session = Depends(get_db)):
    existing = db.query(AttributeSchema).filter(
        AttributeSchema.ontology_id == ontology_id,
        AttributeSchema.name == body.name,
    ).first()
    if existing:
        raise HTTPException(409, f"Attribute '{body.name}' already exists")

    s = AttributeSchema(
        id=str(uuid.uuid4()),
        ontology_id=ontology_id,
        **body.model_dump(),
    )
    db.add(s)
    db.commit()
    return {"data": {"id": s.id, "name": s.name, "display_name": s.display_name}}


@router.put("/{ontology_id}/attribute-schemas/{schema_id}")
def update_schema(ontology_id: str, schema_id: str, body: AttrSchemaCreate, db: Session = Depends(get_db)):
    s = db.query(AttributeSchema).filter(
        AttributeSchema.id == schema_id,
        AttributeSchema.ontology_id == ontology_id,
    ).first()
    if not s:
        raise HTTPException(404, "Schema not found")
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    db.commit()
    return {"data": {"id": s.id}}


@router.delete("/{ontology_id}/attribute-schemas/{schema_id}", status_code=204)
def delete_schema(ontology_id: str, schema_id: str, db: Session = Depends(get_db)):
    s = db.query(AttributeSchema).filter(
        AttributeSchema.id == schema_id,
        AttributeSchema.ontology_id == ontology_id,
    ).first()
    if s:
        db.delete(s)
        db.commit()


@router.post("/{ontology_id}/attribute-schemas/{schema_id}/validate")
def validate_value(ontology_id: str, schema_id: str, body: dict, db: Session = Depends(get_db)):
    """验证单个值是否符合属性约束"""
    s = db.query(AttributeSchema).filter(
        AttributeSchema.id == schema_id,
        AttributeSchema.ontology_id == ontology_id,
    ).first()
    if not s:
        raise HTTPException(404, "Schema not found")

    value = body.get("value")
    constraints = s.constraints or {}
    errors = []

    # 必填检查
    if constraints.get("required") and (value is None or value == ""):
        errors.append("该字段为必填项")
        return {"valid": False, "errors": errors}

    if value is None or value == "":
        return {"valid": True, "errors": []}

    val_str = str(value)

    # 类型检查
    if s.data_type == "number":
        try:
            float(val_str)
        except ValueError:
            errors.append("必须为数字")
    elif s.data_type == "integer":
        try:
            int(val_str)
        except ValueError:
            errors.append("必须为整数")
    elif s.data_type == "boolean":
        if val_str.lower() not in ("true", "false", "1", "0", "yes", "no"):
            errors.append("必须为布尔值")
    elif s.data_type == "date":
        fmt = constraints.get("date_format", "%Y-%m-%d")
        from datetime import datetime
        try:
            datetime.strptime(val_str, fmt)
        except ValueError:
            errors.append(f"日期格式必须为 {fmt}")
    elif s.data_type == "enum":
        enum_vals = constraints.get("enum", [])
        if val_str not in enum_vals:
            errors.append(f"取值必须在 {enum_vals} 中")
    elif s.data_type == "url":
        if not re.match(r'^https?://', val_str):
            errors.append("必须为有效URL")
    elif s.data_type == "email":
        if not re.match(r'^[^@]+@[^@]+\.[^@]+$', val_str):
            errors.append("必须为有效邮箱")

    # 长度检查
    if "min_length" in constraints and len(val_str) < constraints["min_length"]:
        errors.append(f"最少 {constraints['min_length']} 个字符")
    if "max_length" in constraints and len(val_str) > constraints["max_length"]:
        errors.append(f"最多 {constraints['max_length']} 个字符")

    # 范围检查
    if s.data_type in ("number", "integer"):
        try:
            num_val = float(val_str)
            if "min" in constraints and num_val < constraints["min"]:
                errors.append(f"最小值为 {constraints['min']}")
            if "max" in constraints and num_val > constraints["max"]:
                errors.append(f"最大值为 {constraints['max']}")
        except ValueError:
            pass

    # 正则检查
    if "pattern" in constraints:
        if not re.match(constraints["pattern"], val_str):
            errors.append("格式不符合要求")

    return {"valid": len(errors) == 0, "errors": errors}


# ── 词表管理 ─────────────────────────────────────────────────────────

@router.get("/{ontology_id}/vocabulary")
def list_vocabulary(ontology_id: str, q: str = None, entity_type: str = None, db: Session = Depends(get_db)):
    query = db.query(VocabularyEntry).filter(VocabularyEntry.ontology_id == ontology_id)
    if q:
        query = query.filter(
            (VocabularyEntry.canonical.ilike(f"%{q}%")) |
            (VocabularyEntry.synonyms.cast(db.bind.dialect.implementation_dbapi_args[0] if hasattr(db.bind.dialect, 'implementation_dbapi_args') else str).ilike(f"%{q}%"))
        )
    if entity_type:
        query = query.filter(VocabularyEntry.entity_type == entity_type)
    items = query.order_by(desc(VocabularyEntry.created_at)).all()
    return {"data": [{
        "id": v.id, "canonical": v.canonical,
        "synonyms": v.synonyms or [], "abbreviations": v.abbreviations or [],
        "entity_type": v.entity_type, "linked_entity_id": v.linked_entity_id,
        "source": v.source, "confidence": v.confidence,
    } for v in items]}


@router.post("/{ontology_id}/vocabulary", status_code=201)
def create_vocab(ontology_id: str, body: VocabCreate, db: Session = Depends(get_db),
                 current_user=Depends(get_current_user)):
    v = VocabularyEntry(
        id=str(uuid.uuid4()),
        ontology_id=ontology_id,
        canonical=body.canonical,
        synonyms=body.synonyms,
        abbreviations=body.abbreviations,
        entity_type=body.entity_type,
        linked_entity_id=body.linked_entity_id,
        source="manual",
        confidence=1.0,
    )
    db.add(v)
    db.commit()
    return {"data": {"id": v.id, "canonical": v.canonical}}


@router.put("/{ontology_id}/vocabulary/{vocab_id}")
def update_vocab(ontology_id: str, vocab_id: str, body: VocabCreate, db: Session = Depends(get_db)):
    v = db.query(VocabularyEntry).filter(
        VocabularyEntry.id == vocab_id,
        VocabularyEntry.ontology_id == ontology_id,
    ).first()
    if not v:
        raise HTTPException(404, "Vocabulary entry not found")
    v.canonical = body.canonical
    v.synonyms = body.synonyms
    v.abbreviations = body.abbreviations
    v.entity_type = body.entity_type
    v.linked_entity_id = body.linked_entity_id
    db.commit()
    return {"data": {"id": v.id}}


@router.delete("/{ontology_id}/vocabulary/{vocab_id}", status_code=204)
def delete_vocab(ontology_id: str, vocab_id: str, db: Session = Depends(get_db)):
    v = db.query(VocabularyEntry).filter(
        VocabularyEntry.id == vocab_id,
        VocabularyEntry.ontology_id == ontology_id,
    ).first()
    if v:
        db.delete(v)
        db.commit()


@router.post("/{ontology_id}/vocabulary/bulk")
def bulk_import_vocab(ontology_id: str, body: list[VocabCreate], db: Session = Depends(get_db)):
    """批量导入词表"""
    imported = 0
    for item in body:
        v = VocabularyEntry(
            id=str(uuid.uuid4()),
            ontology_id=ontology_id,
            canonical=item.canonical,
            synonyms=item.synonyms,
            abbreviations=item.abbreviations,
            entity_type=item.entity_type,
            linked_entity_id=item.linked_entity_id,
            source="import",
            confidence=1.0,
        )
        db.add(v)
        imported += 1
    db.commit()
    return {"imported": imported}
