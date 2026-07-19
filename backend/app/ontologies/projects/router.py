from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from app.deps import get_db, get_current_user
from app.models.ontology import OntologyProject
from app.models.user import User
from app.models.domain import Domain
from app.models.ontology_version import OntologyVersion
from app.ontologies.release_context import create_initial_release
from app.ontologies.versions.evolution_service import complete_snapshot
from app.ontologies.access import require_ontology_access
from app.ontologies.export.schemas import OntologyStructurePackage
from app.ontologies.export.service import import_structure_package
from app.schemas.ontology import OntologyCreate, OntologyOut, OntologyListItem, OntologyUpdate
import uuid

router = APIRouter()

# Historical installations can contain ontologies created before configurable
# domains existed. Keep those names valid while accepting every domain managed
# through System Settings.
LEGACY_DOMAINS = {
    "供应链", "采购", "财务", "医疗", "金融", "法律", "教育", "科技",
    "制造", "能源", "其他",
}


def _validate_domain(db: Session, domain: str) -> None:
    exists = db.query(Domain.id).filter(Domain.name == domain).first()
    if exists is None and domain not in LEGACY_DOMAINS:
        raise HTTPException(422, detail={
            "error": "INVALID_DOMAIN",
            "message": f"领域「{domain}」不存在，请先在系统设置中添加",
        })


def _release_map(db: Session, projects: list[OntologyProject]) -> dict[str, OntologyVersion]:
    ids = {item.current_release_id for item in projects if item.current_release_id}
    if not ids:
        return {}
    return {item.id: item for item in db.query(OntologyVersion).filter(
        OntologyVersion.id.in_(ids),
        OntologyVersion.node_kind == "release",
        OntologyVersion.lifecycle_status == "released",
    ).all()}


def _resolved_release_map(
    db: Session,
    projects: list[OntologyProject],
) -> dict[str, OntologyVersion]:
    """Resolve every project's current immutable release, repairing legacy rows.

    Ontologies created before the version-tree migration may have definitions
    but no release row/pointer.  The version service already knows how to
    freeze that complete projection into a v0 migration baseline; resolve it
    here as well so every list consumer (especially the assistant) observes
    the same invariant without first opening the version tree.
    """
    releases = _release_map(db, projects)
    repaired = False
    for project in projects:
        if releases.get(project.current_release_id) is not None:
            continue
        locked_project = db.query(OntologyProject).filter(
            OntologyProject.id == project.id,
        ).with_for_update().populate_existing().one()
        locked_release = _release_map(db, [locked_project]).get(
            locked_project.current_release_id)
        if locked_release is not None:
            releases[locked_release.id] = locked_release
            continue
        # Local import avoids a router import cycle at application startup.
        from app.ontologies.versions.router import _current_release

        release = _current_release(db, locked_project)
        releases[release.id] = release
        repaired = True
    if repaired:
        db.commit()
    return releases


def _project_payload(project: OntologyProject, schema, release: OntologyVersion | None = None) -> dict:
    """对外版本始终来自发布指针，避免列表、详情和版本树显示不一致。"""
    data = schema.model_validate(project).model_dump()
    data["current_release_id"] = release.id if release else project.current_release_id
    data["current_release_version"] = release.version_number if release else None
    if release is not None:
        data["version"] = release.version_number
    return data


def _release_structure_counts(release: OntologyVersion | None) -> dict[str, int]:
    """Return card metrics from the immutable current release snapshot."""
    snapshot = complete_snapshot(release.snapshot_formal if release else None)
    return {
        "entity_count": len(snapshot["objectTypes"]),
        "relation_count": len(snapshot["linkTypes"]),
        "action_count": len(snapshot["actions"]),
        "sentinel_count": len(snapshot["sentinels"]),
    }

@router.get("")
def list_ontologies(
    name: Optional[str] = None,
    domain: Optional[str] = None,
    page: int = 1, page_size: int = 20,
    db: Session = Depends(get_db), _=Depends(get_current_user)
):
    q = db.query(OntologyProject)
    if name:
        q = q.filter(OntologyProject.name.ilike(f"%{name}%"))
    if domain:
        q = q.filter(OntologyProject.domain == domain)
    total = q.count()
    items = q.order_by(OntologyProject.created_at.desc()).offset((page-1)*page_size).limit(page_size).all()
    releases = _resolved_release_map(db, items)
    result = []
    for item in items:
        release = releases.get(item.current_release_id)
        d = _project_payload(item, OntologyListItem, release)
        d.update(_release_structure_counts(release))
        result.append(d)
    return {"data": {"items": result, "total": total, "page": page, "page_size": page_size}}

@router.post("", status_code=201)
def create_ontology(body: OntologyCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if getattr(current_user, "role", "") not in ("admin", "editor"):
        raise HTTPException(403, "Viewer role is read-only")
    _validate_domain(db, body.domain)
    existing = db.query(OntologyProject).filter(OntologyProject.name.ilike(body.name)).first()
    if existing:
        raise HTTPException(status_code=409, detail={"error": "DUPLICATE_NAME", "message": f"Ontology 名称「{body.name}」已存在", "existing_id": existing.id})
    project = OntologyProject(id=str(uuid.uuid4()), name=body.name, domain=body.domain,
                               description=body.description, icon=body.icon,
                               # Internal compatibility value only; creation no
                               # longer branches into LLM/Pipeline workflows.
                               build_mode=body.build_mode or "manual", version="v0",
                               created_by=current_user.id)
    db.add(project)
    db.flush()
    # v0 是不可变的完整空结构发布基线。project.status 暂时保留为 draft 仅兼容
    # 旧编辑接口；新版本工作流只认 version.node_kind/current_release_id。
    root = create_initial_release(
        db,
        project,
        snapshot=None,
        created_by=current_user.id,
        description="系统创建的完整空结构基线",
    )
    db.commit(); db.refresh(project)
    return {"data": _project_payload(project, OntologyOut, root)}


@router.post("/import", status_code=201)
def import_ontology_structure(
    body: OntologyStructurePackage,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a published v0 ontology from a local structure JSON package."""
    if getattr(current_user, "role", "") not in ("admin", "editor"):
        raise HTTPException(403, "Viewer role is read-only")
    _validate_domain(db, body.ontology.domain)
    return {"data": import_structure_package(db, body, current_user=current_user)}

@router.get("/{ontology_id}")
def get_ontology(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    release = _resolved_release_map(db, [p]).get(p.current_release_id)
    return {"data": _project_payload(p, OntologyOut, release)}

@router.put("/{ontology_id}")
def update_ontology(ontology_id: str, body: OntologyUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    p = require_ontology_access(db, ontology_id, current_user, write=True)
    update = body.model_dump(exclude_none=True)
    if "domain" in update and update["domain"] != p.domain:
        _validate_domain(db, update["domain"])
    if "name" in update:
        existing = db.query(OntologyProject).filter(
            OntologyProject.name.ilike(update["name"]),
            OntologyProject.id != ontology_id,
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail={
                "error": "DUPLICATE_NAME",
                "message": f"Ontology 名称「{update['name']}」已存在",
                "existing_id": existing.id,
            })
    for k, v in update.items():
        setattr(p, k, v)
    db.commit(); db.refresh(p)
    release = _resolved_release_map(db, [p]).get(p.current_release_id)
    return {"data": _project_payload(p, OntologyOut, release)}

@router.delete("/{ontology_id}", status_code=204)
def delete_ontology(ontology_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    p = require_ontology_access(db, ontology_id, current_user, write=True)
    db.delete(p); db.commit()
