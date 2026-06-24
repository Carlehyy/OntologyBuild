"""
Inference & QA Router

Handles rule-based reasoning and AI-powered question answering.
This is where the "knowledge -> conclusions" pipeline runs.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import (
    Rule, Entity, Relation, ObjectType, RelationType,
    FeedbackRecord, AuditLog, Document
)
from app.schemas import InferenceRequest, InferenceResult
from app.services.llm_service import get_llm_service

router = APIRouter(prefix="/inference", tags=["Inference"])


@router.post("/query", response_model=InferenceResult)
async def run_inference(data: InferenceRequest, db: Session = Depends(get_db)):
    """
    Run inference: combine rule matching with LLM Q&A.

    1. Match rules against the graph
    2. If LLM is available, augment with LLM reasoning
    3. Return combined results with confidence scores
    """
    rule_hits = []
    referenced_entities = []
    answer = None
    reasoning = None
    confidence = 0.0

    # ── Rule-based reasoning ──
    if data.use_rules:
        active_rules = db.query(Rule).filter(
            Rule.domain_id == data.domain_id,
            Rule.is_active == True,
            Rule.is_draft == False,
        ).all()

        for rule in active_rules:
            try:
                hits = _match_rule(db, data.domain_id, rule)
                if hits:
                    rule_hits.append({
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "action_type": rule.action_type,
                        "matched_entities": hits,
                        "message": rule.action_config.get("message_template", "Rule matched"),
                        "severity": rule.action_config.get("severity", "medium"),
                    })
                    referenced_entities.extend(hits)
            except Exception as e:
                print(f"Rule {rule.id} matching error: {e}")

    # ── LLM Q&A ──
    if data.use_llm:
        llm = get_llm_service()

        # Build context from graph
        context = await _build_graph_context(db, data.domain_id, data.query, data.context_entity_ids)

        # Get object types for the domain
        object_types = db.query(ObjectType).filter(
            ObjectType.domain_id == data.domain_id,
        ).all()
        ot_list = [{"name": ot.name, "description": ot.description or ""} for ot in object_types]

        llm_result = await llm.answer_question(data.query, context, ot_list)
        answer = llm_result.get("answer", "")
        confidence = llm_result.get("confidence", 0.5)
        reasoning = llm_result.get("reasoning", "")

    # ── Combine results ──
    if not answer and rule_hits:
        # If no LLM answer but rules hit, construct answer from rules
        hit_messages = [f"- {h['rule_name']}: {h['message']}" for h in rule_hits[:5]]
        answer = "Rule-based findings:\n" + "\n".join(hit_messages)
        confidence = 0.6 + (0.05 * len(rule_hits))
        reasoning = "Based on rule pattern matching in the knowledge graph."
    elif not answer:
        answer = "No matching rules or LLM response. Try refining your query."
        confidence = 0.0

    # Record inference for feedback
    db.add(AuditLog(
        action="inference",
        resource_type="query",
        domain_id=data.domain_id,
        details={"query": data.query, "rule_hits": len(rule_hits), "used_llm": data.use_llm},
    ))
    db.commit()

    return InferenceResult(
        query=data.query,
        answer=answer,
        rule_hits=rule_hits,
        referenced_entities=list(set(referenced_entities)),
        confidence=min(confidence, 1.0),
        reasoning=reasoning,
    )


@router.post("/feedback")
def submit_inference_feedback(
    inference_query: str,
    verdict: str,  # "useful", "false_positive", "needs_correction"
    correction: Optional[str] = None,
    rule_hit_ids: Optional[List[str]] = None,
    db: Session = Depends(get_db),
):
    """Submit feedback on an inference result."""
    feedback = FeedbackRecord(
        feedback_type="inference",
        target_id="query",
        target_type="inference_query",
        verdict=verdict,
        correction_data={"query": inference_query, "correction": correction, "rule_hits": rule_hit_ids or []},
    )
    db.add(feedback)
    db.commit()

    # Update rule statistics
    if rule_hit_ids and verdict == "false_positive":
        for rule_id in rule_hit_ids:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            if rule:
                rule.false_positive_count += 1
        db.commit()

    return {"success": True, "message": "Feedback recorded"}


async def _build_graph_context(
    db: Session,
    domain_id: str,
    query: str,
    entity_ids: Optional[List[str]] = None,
) -> str:
    """Build a text context from the graph for LLM consumption."""
    context_parts = []

    if entity_ids:
        # Get specific entities and their neighbors
        entities = db.query(Entity).filter(
            Entity.domain_id == domain_id,
            Entity.id.in_(entity_ids),
        ).limit(50).all()
    else:
        # Search for relevant entities based on query
        entities = db.query(Entity).filter(
            Entity.domain_id == domain_id,
            Entity.name.contains(query[:30]),
        ).limit(50).all()

        if len(entities) < 5:
            # Get most connected entities
            entities = db.query(Entity).filter(
                Entity.domain_id == domain_id,
            ).limit(30).all()

    # Get object type names
    ot_map = {ot.id: ot.name for ot in db.query(ObjectType).filter(ObjectType.domain_id == domain_id).all()}

    context_parts.append(f"Knowledge Graph Context (Domain: {domain_id}):\n")
    context_parts.append(f"Total entities: {db.query(Entity).filter(Entity.domain_id == domain_id).count()}\n\n")

    # Entity descriptions
    context_parts.append("Key Entities:\n")
    for entity in entities[:30]:
        ot_name = ot_map.get(entity.object_type_id, "Unknown")
        props_str = ", ".join([f"{k}={v}" for k, v in (entity.properties or {}).items()[:5]])
        context_parts.append(f"- [{ot_name}] {entity.name} ({props_str})\n")

    # Relations for these entities
    entity_ids_set = {e.id for e in entities}
    relations = db.query(Relation).filter(
        Relation.domain_id == domain_id,
        Relation.source_id.in_(entity_ids_set),
        Relation.target_id.in_(entity_ids_set),
    ).limit(50).all()

    rt_map = {rt.id: rt.name for rt in db.query(RelationType).filter(RelationType.domain_id == domain_id).all()}

    if relations:
        context_parts.append("\nKey Relations:\n")
        for rel in relations:
            source = next((e for e in entities if e.id == rel.source_id), None)
            target = next((e for e in entities if e.id == rel.target_id), None)
            if source and target:
                rel_name = rt_map.get(rel.relation_type_id, "relates to")
                context_parts.append(f"- {source.name} --[{rel_name}]--> {target.name}\n")

    return "".join(context_parts)


def _match_rule(db: Session, domain_id: str, rule: Rule) -> List[str]:
    """
    Match a rule against the graph.
    Returns list of matched entity IDs.
    """
    condition = rule.condition or {}
    pattern = condition.get("pattern", "")
    params = condition.get("parameters", {})

    matched = []

    try:
        # Simple pattern matching implementations
        if "entity_type" in params:
            # Match entities of a specific type
            target_type = params["entity_type"]
            ot = db.query(ObjectType).filter(
                ObjectType.domain_id == domain_id,
                ObjectType.name == target_type,
            ).first()

            if ot:
                entities = db.query(Entity).filter(
                    Entity.domain_id == domain_id,
                    Entity.object_type_id == ot.id,
                ).all()

                # Apply additional filters from parameters
                for entity in entities:
                    props = entity.properties or {}
                    match = True

                    for key, value in params.items():
                        if key == "entity_type":
                            continue
                        if key.startswith("prop_"):
                            prop_name = key[5:]
                            if str(props.get(prop_name, "")).lower() != str(value).lower():
                                match = False
                                break
                        elif key == "min_confidence":
                            if entity.confidence < float(value):
                                match = False
                                break

                    if match:
                        matched.append(entity.id)

        elif "property_check" in params:
            # Match entities with specific property conditions
            prop_name = params.get("property_name", "")
            prop_value = params.get("property_value", "")

            entities = db.query(Entity).filter(
                Entity.domain_id == domain_id,
            ).all()

            for entity in entities:
                props = entity.properties or {}
                if prop_name in props:
                    val = str(props[prop_name]).lower()
                    target = str(prop_value).lower()
                    op = params.get("operator", "equals")

                    if op == "equals" and val == target:
                        matched.append(entity.id)
                    elif op == "contains" and target in val:
                        matched.append(entity.id)
                    elif op == "exists":
                        matched.append(entity.id)

        elif "relation_pattern" in params:
            # Match based on relation patterns
            rel_type_name = params.get("relation_type", "")
            rt = db.query(RelationType).filter(
                RelationType.domain_id == domain_id,
                RelationType.name == rel_type_name,
            ).first()

            if rt:
                relations = db.query(Relation).filter(
                    Relation.domain_id == domain_id,
                    Relation.relation_type_id == rt.id,
                ).all()
                for rel in relations:
                    matched.append(rel.source_id)

        else:
            # Default: match all entities (fallback)
            pass

    except Exception as e:
        print(f"Rule matching error: {e}")

    return matched


@router.get("/domain/{domain_id}/recent-activity")
def get_recent_activity(domain_id: str, db: Session = Depends(get_db)):
    """Get recent inference activity for the dashboard."""
    from datetime import datetime, timedelta

    since = datetime.utcnow() - timedelta(days=7)

    # Recent entities
    recent_entities = db.query(Entity).filter(
        Entity.domain_id == domain_id,
        Entity.created_at >= since,
    ).order_by(Entity.created_at.desc()).limit(10).all()

    # Recent feedback
    recent_feedback = db.query(FeedbackRecord).filter(
        FeedbackRecord.domain_id == domain_id,
        FeedbackRecord.created_at >= since,
    ).order_by(FeedbackRecord.created_at.desc()).limit(10).all()

    return {
        "recent_entities": [
            {"id": e.id, "name": e.name, "created_at": e.created_at.isoformat()}
            for e in recent_entities
        ],
        "recent_feedback": [
            {"id": f.id, "verdict": f.verdict, "created_at": f.created_at.isoformat()}
            for f in recent_feedback
        ],
    }
