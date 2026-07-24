"""Business-exploration draft endpoints must honor ontology write ownership."""
from __future__ import annotations

import uuid

from app.exploration import canvas as C
from app.exploration import converter
from app.exploration.models import (
    ExplorationDocument,
    ExplorationDraft,
    ExplorationSession,
)
from app.models.ontology import OntologyProject
from app.ontologies.formal_modeling.models import ObjectType
from app.models.user import User
from app.services.auth_service import hash_password


BASE = "/api/v2/exploration"
EMPTY_DRAFT = {
    "objectTypes": [],
    "linkTypes": [],
    "actions": [],
    "functions": [],
    "sentinels": [],
}


def _create_user(db, username: str, role: str) -> tuple[User, str]:
    password = f"{username}-password"
    user = User(
        id=str(uuid.uuid4()),
        username=username,
        email=f"{username}@test.local",
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, password


def _headers(client, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _session_document(db, user: User) -> tuple[ExplorationSession, ExplorationDocument]:
    session = ExplorationSession(
        id=str(uuid.uuid4()),
        user_id=user.id,
        title=f"{user.username}-exploration",
        canvas=C.empty_canvas(),
        canvas_version=0,
    )
    db.add(session)
    db.flush()
    document = ExplorationDocument(
        id=str(uuid.uuid4()),
        session_id=session.id,
        title="权限测试文档",
        content_md="# 权限测试",
        canvas_snapshot=C.empty_canvas(),
        version=1,
    )
    db.add(document)
    db.commit()
    return session, document


def _draft(db, session: ExplorationSession, document: ExplorationDocument,
           *, target_id: str | None = None,
           applied_id: str | None = None) -> ExplorationDraft:
    row = ExplorationDraft(
        id=str(uuid.uuid4()),
        session_id=session.id,
        document_id=document.id,
        target_ontology_id=target_id,
        applied_ontology_id=applied_id,
        status="applied" if applied_id else "draft",
        draft=dict(EMPTY_DRAFT),
        report={},
    )
    db.add(row)
    db.commit()
    return row


def test_create_draft_target_requires_owner_write_access_but_admin_is_allowed(
        client, db, editor_user, auth_headers):
    owner_headers = _headers(client, "editor", "editor123")
    project_response = client.post(
        "/api/v1/ontologies",
        headers=owner_headers,
        json={"name": "OwnerOnlyOntology", "domain": "供应链"},
    )
    assert project_response.status_code == 201, project_response.text
    project_id = project_response.json()["data"]["id"]

    intruder, intruder_password = _create_user(db, "other-editor", "editor")
    intruder_headers = _headers(client, intruder.username, intruder_password)
    _, intruder_document = _session_document(db, intruder)
    denied = client.post(
        f"{BASE}/documents/{intruder_document.id}/drafts",
        headers=intruder_headers,
        json={"targetOntologyId": project_id, "force": True},
    )
    assert denied.status_code == 403, denied.text

    _, owner_document = _session_document(db, editor_user)
    owner_allowed = client.post(
        f"{BASE}/documents/{owner_document.id}/drafts",
        headers=owner_headers,
        json={"targetOntologyId": project_id, "force": True},
    )
    assert owner_allowed.status_code == 201, owner_allowed.text

    # Administrators retain the shared guard's explicit cross-owner override.
    admin_user = db.query(User).filter_by(username="admin").one()
    _, admin_document = _session_document(db, admin_user)
    admin_allowed = client.post(
        f"{BASE}/documents/{admin_document.id}/drafts",
        headers=auth_headers,
        json={"targetOntologyId": project_id, "force": True},
    )
    assert admin_allowed.status_code == 201, admin_allowed.text


def test_validate_apply_reentry_and_new_ontology_enforce_shared_write_permissions(
        client, db, editor_user):
    owner_headers = _headers(client, "editor", "editor123")
    project_response = client.post(
        "/api/v1/ontologies",
        headers=owner_headers,
        json={"name": "ProtectedOntology", "domain": "供应链"},
    )
    assert project_response.status_code == 201, project_response.text
    project_id = project_response.json()["data"]["id"]

    intruder, intruder_password = _create_user(db, "intruder-editor", "editor")
    intruder_headers = _headers(client, intruder.username, intruder_password)
    intruder_session, intruder_document = _session_document(db, intruder)
    target_draft = _draft(
        db, intruder_session, intruder_document, target_id=project_id)

    denied_validate = client.post(
        f"{BASE}/drafts/{target_draft.id}/validate",
        headers=intruder_headers,
        json={},
    )
    assert denied_validate.status_code == 403, denied_validate.text
    denied_apply = client.post(
        f"{BASE}/drafts/{target_draft.id}/apply",
        headers=intruder_headers,
        json={},
    )
    assert denied_apply.status_code == 403, denied_apply.text

    # A previously applied draft must re-check the actual applied target on reuse.
    reentry = _draft(
        db, intruder_session, intruder_document, applied_id=project_id)
    denied_reentry = client.post(
        f"{BASE}/drafts/{reentry.id}/apply",
        headers=intruder_headers,
        json={},
    )
    assert denied_reentry.status_code == 403, denied_reentry.text

    owner_session, owner_document = _session_document(db, editor_user)
    owner_draft = _draft(
        db, owner_session, owner_document, target_id=project_id)
    owner_validate = client.post(
        f"{BASE}/drafts/{owner_draft.id}/validate",
        headers=owner_headers,
        json={},
    )
    assert owner_validate.status_code == 200, owner_validate.text
    owner_apply = client.post(
        f"{BASE}/drafts/{owner_draft.id}/apply",
        headers=owner_headers,
        json={},
    )
    assert owner_apply.status_code == 200, owner_apply.text

    viewer, viewer_password = _create_user(db, "readonly-viewer", "viewer")
    viewer_headers = _headers(client, viewer.username, viewer_password)
    viewer_session, viewer_document = _session_document(db, viewer)
    new_target_draft = _draft(db, viewer_session, viewer_document)
    before = db.query(OntologyProject).count()
    denied_create = client.post(
        f"{BASE}/drafts/{new_target_draft.id}/apply",
        headers=viewer_headers,
        json={"newOntology": {"name": "ViewerMustNotCreate", "domain": "供应链"}},
    )
    assert denied_create.status_code == 403, denied_create.text
    assert db.query(OntologyProject).count() == before


def test_default_apply_never_promotes_a_previously_conflicting_item(ontology, db):
    """Target changes after review must not make an excluded conflict implicit-selected."""
    conflict = {
        "key": "object:order",
        "name": "Order",
        "displayName": "订单",
        "description": "生成草稿时与目标本体同名",
        "properties": [{
            "id": "prop-order-id",
            "name": "order_id",
            "displayName": "订单号",
            "type": "string",
            "required": True,
        }],
        "primaryKey": "prop-order-id",
        "sourceRefs": ["canvas-order"],
        "conflict": True,
    }
    draft = {
        **EMPTY_DRAFT,
        "objectTypes": [conflict],
    }

    # This models an item marked conflicting during generation whose target-side
    # namesake was deleted before apply. Omitted selectedKeys must still mean the
    # same empty reviewed selection in both stages.
    validation = converter.validate_draft_selection(draft, selected_keys=None)
    assert validation["valid"] is True
    assert validation["selectedCount"] == 0
    result = converter.apply_draft(
        db, draft, selected_keys=None, ontology_id=ontology["id"])
    assert result["created"]["objectTypes"] == 0
    assert db.query(ObjectType).filter(
        ObjectType.ontology_id == ontology["id"],
        ObjectType.name == "Order",
    ).count() == 0
