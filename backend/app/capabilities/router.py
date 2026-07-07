"""能力注册中心 API — /api/v2/capabilities/*

  GET    /skills            技能列表（?scope= 过滤）
  POST   /skills            新建（管理员）
  PUT    /skills/{id}       更新（管理员；builtin 亦可编辑）
  DELETE /skills/{id}       删除（管理员；builtin 禁删 409）
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_admin
from app.capabilities import schemas as S
from app.capabilities.models import CapSkill

router = APIRouter()

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def _ok(data):
    return {"data": data}


def _out(s: CapSkill) -> dict:
    return S.SkillOut.model_validate(s).model_dump(by_alias=True)


def _require_skill(db: Session, skill_id: str) -> CapSkill:
    s = db.query(CapSkill).filter(CapSkill.id == skill_id).first()
    if not s:
        raise HTTPException(404, "技能不存在")
    return s


def _validate_scopes(scopes: list[str]) -> list[str]:
    bad = [x for x in scopes if x not in S.VALID_SCOPES]
    if bad:
        raise HTTPException(422, f"非法作用域: {bad}（可选: {sorted(S.VALID_SCOPES)}）")
    return scopes


@router.get("/skills")
def list_skills(scope: str | None = None, db: Session = Depends(get_db),
                _=Depends(get_current_user)):
    rows = db.query(CapSkill).order_by(CapSkill.builtin.desc(), CapSkill.created_at.asc()).all()
    if scope:
        rows = [r for r in rows if scope in (r.scopes or [])]
    return _ok([_out(r) for r in rows])


@router.post("/skills", status_code=201)
def create_skill(body: S.SkillCreate, db: Session = Depends(get_db), _=Depends(require_admin)):
    name = body.name.strip().lower()
    if not _NAME_RE.match(name):
        raise HTTPException(422, "name 必须是小写英文标识符（字母开头，可含数字/下划线，2-64 位）")
    if db.query(CapSkill).filter(CapSkill.name == name).first():
        raise HTTPException(409, f"技能「{name}」已存在")
    _validate_scopes(body.scopes)
    s = CapSkill(name=name, display_name=body.display_name.strip() or name,
                 description=body.description, instructions=body.instructions,
                 scopes=body.scopes, enabled=body.enabled, builtin=False)
    db.add(s)
    db.commit()
    db.refresh(s)
    return _ok(_out(s))


@router.put("/skills/{skill_id}")
def update_skill(skill_id: str, body: S.SkillUpdate,
                 db: Session = Depends(get_db), _=Depends(require_admin)):
    s = _require_skill(db, skill_id)
    data = body.model_dump(exclude_unset=True)
    if "scopes" in data and data["scopes"] is not None:
        _validate_scopes(data["scopes"])
    for k, v in data.items():
        if v is not None:
            setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return _ok(_out(s))


@router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    s = _require_skill(db, skill_id)
    if s.builtin:
        raise HTTPException(409, "内置技能不可删除（可停用或编辑）")
    db.delete(s)
    db.commit()
