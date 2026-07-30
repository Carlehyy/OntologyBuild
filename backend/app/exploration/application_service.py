"""Human-reviewed draft application workflow for Exploration."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.exploration import converter
from app.exploration.draft_service import _require_draft
from app.exploration.session_service import _ok
from app.models.ontology import OntologyProject
from app.ontologies.access import (
    require_ontology_access,
    require_ontology_create_access,
)
from app.ontologies.release_context import create_initial_release
from app.ontologies.versions.release_service import (
    collect_publishable_snapshot,
    resolve_current_release,
)


def apply_draft(
    draft_id: str,
    body,
    db: Session,
    current_user,
    *,
    require_draft_fn=_require_draft,
    require_ontology_access_fn=require_ontology_access,
    require_ontology_create_access_fn=require_ontology_create_access,
    converter_module=converter,
    project_model=OntologyProject,
    resolve_current_release_fn=resolve_current_release,
    create_initial_release_fn=create_initial_release,
    collect_publishable_snapshot_fn=collect_publishable_snapshot,
    ok_fn: Callable[[Any], dict] = _ok,
):
    """Apply one reviewed selection in a single relational transaction."""
    draft = require_draft_fn(db, draft_id, current_user)
    if draft.status == "discarded":
        raise HTTPException(
            409,
            "该草稿已废弃，不可应用；如需落地请重新生成草稿",
        )
    if (
        body.selected_keys is not None
        and len(body.selected_keys) == 0
    ):
        raise HTTPException(422, "未勾选任何草稿元素")

    validation_target_id = (
        draft.applied_ontology_id
        or draft.target_ontology_id
    )
    if validation_target_id:
        require_ontology_access_fn(
            db,
            validation_target_id,
            current_user,
            write=True,
        )
    else:
        require_ontology_create_access_fn(current_user)
    validation_existing = (
        converter_module.existing_name_sets(
            db,
            validation_target_id,
        )
        if validation_target_id
        else None
    )
    validation = converter_module.validate_draft_selection(
        draft.draft or {},
        body.selected_keys,
        existing=validation_existing,
    )
    if not validation["valid"]:
        raise HTTPException(
            422,
            detail={
                "code": "draft_validation_failed",
                "message": (
                    "本体草稿选择集预检未通过"
                    f"（{len(validation['errors'])} 项错误），已拒绝落地"
                ),
                "validation": validation,
            },
        )

    project = None
    created_project = False
    if draft.applied_ontology_id:
        # Re-application stays pinned to the first materialized ontology.
        project = db.query(project_model).filter(
            project_model.id == draft.applied_ontology_id
        ).first()
    if project is None and draft.target_ontology_id:
        project = db.query(project_model).filter(
            project_model.id == draft.target_ontology_id
        ).first()
        if not project:
            raise HTTPException(
                404,
                "目标本体不存在（可能已被删除）",
            )
    if project is None:
        if (
            not body.new_ontology
            or not (body.new_ontology.name or "").strip()
        ):
            raise HTTPException(
                422,
                "该草稿目标为新建本体，请提供 newOntology.name",
            )
        name = body.new_ontology.name.strip()
        if db.query(project_model).filter(
            project_model.name.ilike(name)
        ).first():
            raise HTTPException(409, f"本体名称「{name}」已存在")
        project = project_model(
            name=name,
            domain=(
                body.new_ontology.domain or "业务探索"
            ).strip() or "业务探索",
            description=(
                body.new_ontology.description
                or f"由业务探索会话生成（草稿 {draft.id[:8]}）"
            ),
            build_mode="business_exploration",
            created_by=getattr(current_user, "id", None),
        )
        db.add(project)
        db.flush()
        created_project = True

    if not created_project:
        # Freeze legacy structure before merging outside its current release.
        resolve_current_release_fn(db, project)

    result = converter_module.apply_draft(
        db,
        draft.draft or {},
        body.selected_keys,
        project.id,
        lineage={
            "sessionId": draft.session_id,
            "documentId": draft.document_id,
            "draftId": draft.id,
        },
    )
    if created_project:
        create_initial_release_fn(
            db,
            project,
            snapshot=collect_publishable_snapshot_fn(
                db,
                project.id,
            ),
            created_by=getattr(current_user, "id", None),
            version_label="业务探索初始基线",
            description="由业务探索草稿生成的完整发布基线",
        )
    draft.status = "applied"
    draft.applied_ontology_id = project.id
    db.commit()
    return ok_fn({
        "ontologyId": project.id,
        "ontologyName": project.name,
        **result,
    })
