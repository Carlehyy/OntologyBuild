"""「业务澄清」分支②入口：GET /exploration/draft-ontologies 契约。

平台的「草稿态」只存在于版本层（node_kind=draft 且 lifecycle_status=editing），
该接口返回有编辑中草稿版本、且当前用户可写（与合并落地同一写权限口径）的本体，
同一本体只保留最新一个编辑中草稿，按草稿创建时间倒序。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.models.ontology import OntologyProject
from app.models.user import User
from app.ontologies.versions.models import OntologyVersion

BASE = "/api/v2/exploration"


def _create_user(db, username: str, role: str) -> tuple[User, str]:
    from app.services.auth_service import hash_password

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


def _ontology(db, owner: User, name: str) -> tuple[OntologyProject, OntologyVersion]:
    """所有本体出生即冻结 v0 发布基线；草稿是版本树上的另一类节点。"""
    project = OntologyProject(
        id=str(uuid.uuid4()), name=name, domain="测试领域",
        build_mode="manual", created_by=owner.id,
    )
    db.add(project)
    db.flush()
    release = OntologyVersion(
        id=str(uuid.uuid4()), ontology_id=project.id, version_number="v0",
        node_kind="release", lifecycle_status="released", revision=0,
        created_by=owner.id,
    )
    db.add(release)
    db.flush()
    project.current_release_id = release.id
    return project, release


def _draft_version(
    db, project: OntologyProject, owner: User, number: str,
    *, created_at: datetime | None = None,
) -> OntologyVersion:
    version = OntologyVersion(
        id=str(uuid.uuid4()), ontology_id=project.id, version_number=number,
        version_label="业务探索合并", node_kind="draft", lifecycle_status="editing",
        revision=0, created_by=owner.id,
    )
    if created_at is not None:
        version.created_at = created_at
    db.add(version)
    return version


def test_lists_only_ontologies_with_editing_draft(db, client):
    owner, password = _create_user(db, "draft-admin", "admin")
    ontology_a, _ = _ontology(db, owner, "本体A")
    ontology_b, _ = _ontology(db, owner, "本体B")  # 只有发布基线，无草稿
    base = datetime.now(timezone.utc).replace(tzinfo=None)
    _draft_version(db, ontology_a, owner, "v1.1", created_at=base)
    db.commit()

    response = client.get(f"{BASE}/draft-ontologies", headers=_headers(client, owner.username, password))
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert [i["ontologyId"] for i in items] == [ontology_a.id]
    assert items[0]["ontologyName"] == "本体A"
    assert items[0]["versionNumber"] == "v1.1"
    assert items[0]["versionLabel"] == "业务探索合并"
    assert items[0]["versionId"]
    assert items[0]["domain"] == "测试领域"
    assert items[0]["draftCreatedAt"]


def test_keeps_latest_editing_draft_per_ontology(db, client):
    owner, password = _create_user(db, "draft-latest", "admin")
    ontology, _ = _ontology(db, owner, "本体C")
    base = datetime.now(timezone.utc).replace(tzinfo=None)
    _draft_version(db, ontology, owner, "v1.1", created_at=base - timedelta(days=2))
    _draft_version(db, ontology, owner, "v1.2", created_at=base)
    db.commit()

    response = client.get(f"{BASE}/draft-ontologies", headers=_headers(client, owner.username, password))
    items = response.json()["data"]["items"]
    assert [i["versionNumber"] for i in items] == ["v1.2"]


def test_orders_by_latest_draft_activity(db, client):
    owner, password = _create_user(db, "draft-order", "admin")
    ontology_old, _ = _ontology(db, owner, "本体旧")
    ontology_new, _ = _ontology(db, owner, "本体新")
    base = datetime.now(timezone.utc).replace(tzinfo=None)
    _draft_version(db, ontology_old, owner, "v1.1", created_at=base - timedelta(days=1))
    _draft_version(db, ontology_new, owner, "v1.1", created_at=base)
    db.commit()

    response = client.get(f"{BASE}/draft-ontologies", headers=_headers(client, owner.username, password))
    items = response.json()["data"]["items"]
    assert [i["ontologyName"] for i in items] == ["本体新", "本体旧"]


def test_editor_only_sees_owned_draft_ontologies(db, client):
    owner, owner_password = _create_user(db, "draft-owner", "editor")
    other, other_password = _create_user(db, "draft-other", "editor")
    owned, _ = _ontology(db, owner, "本体自有")
    foreign, _ = _ontology(db, other, "本体他人")
    base = datetime.now(timezone.utc).replace(tzinfo=None)
    _draft_version(db, owned, owner, "v1.1", created_at=base)
    _draft_version(db, foreign, other, "v1.1", created_at=base)
    db.commit()

    response = client.get(f"{BASE}/draft-ontologies", headers=_headers(client, owner.username, owner_password))
    items = response.json()["data"]["items"]
    assert [i["ontologyId"] for i in items] == [owned.id]

    response = client.get(f"{BASE}/draft-ontologies", headers=_headers(client, other.username, other_password))
    items = response.json()["data"]["items"]
    assert [i["ontologyId"] for i in items] == [foreign.id]


def test_viewer_gets_empty_items(db, client):
    owner, _ = _create_user(db, "draft-admin-2", "admin")
    viewer, viewer_password = _create_user(db, "draft-viewer", "viewer")
    ontology, _ = _ontology(db, owner, "本体只读")
    base = datetime.now(timezone.utc).replace(tzinfo=None)
    _draft_version(db, ontology, owner, "v1.1", created_at=base)
    db.commit()

    response = client.get(f"{BASE}/draft-ontologies", headers=_headers(client, viewer.username, viewer_password))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["items"] == []
