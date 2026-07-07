"""能力注册中心（capabilities）测试：

  1. skills CRUD、name 校验、scope 过滤
  2. builtin 禁删（409）但可编辑/停用
  3. seed 幂等：不覆盖用户对内置技能的编辑
"""
from app.capabilities.builtin import BUILTIN_SKILLS, seed_builtin_skills
from app.capabilities.models import CapSkill

BASE = "/api/v2/capabilities"


def test_skill_crud_and_scope_filter(client, auth_headers):
    r = client.post(f"{BASE}/skills", headers=auth_headers, json={
        "name": "uml_class", "displayName": "UML 类图",
        "description": "画 UML 类图时使用", "instructions": "# UML\n输出 mermaid classDiagram",
        "scopes": ["exploration"]})
    assert r.status_code == 201, r.text
    sid = r.json()["data"]["id"]

    # 非法 name / 重名 / 非法 scope
    assert client.post(f"{BASE}/skills", headers=auth_headers, json={
        "name": "Bad Name!", "displayName": "x"}).status_code == 422
    assert client.post(f"{BASE}/skills", headers=auth_headers, json={
        "name": "uml_class", "displayName": "重复"}).status_code == 409
    assert client.post(f"{BASE}/skills", headers=auth_headers, json={
        "name": "s2", "displayName": "x", "scopes": ["nope"]}).status_code == 422

    r = client.put(f"{BASE}/skills/{sid}", headers=auth_headers,
                   json={"enabled": False, "description": "改过了"})
    assert r.json()["data"]["enabled"] is False

    r = client.get(f"{BASE}/skills?scope=agent", headers=auth_headers)
    assert all("agent" in s["scopes"] for s in r.json()["data"])
    r = client.get(f"{BASE}/skills?scope=exploration", headers=auth_headers)
    assert any(s["name"] == "uml_class" for s in r.json()["data"])

    assert client.delete(f"{BASE}/skills/{sid}", headers=auth_headers).status_code == 204
    assert client.get(f"{BASE}/skills", headers=auth_headers).status_code == 200


def test_builtin_seed_idempotent_and_undeletable(client, auth_headers, db):
    created = seed_builtin_skills(db)
    assert created == len(BUILTIN_SKILLS)
    assert seed_builtin_skills(db) == 0            # 幂等

    er = db.query(CapSkill).filter(CapSkill.name == "er_diagram").first()
    r = client.delete(f"{BASE}/skills/{er.id}", headers=auth_headers)
    assert r.status_code == 409                    # builtin 禁删

    # 用户编辑后再 seed 不被覆盖
    r = client.put(f"{BASE}/skills/{er.id}", headers=auth_headers,
                   json={"instructions": "我自己改的指令"})
    assert r.status_code == 200
    seed_builtin_skills(db)
    db.refresh(er)
    assert er.instructions == "我自己改的指令"

    # builtin 可停用
    r = client.put(f"{BASE}/skills/{er.id}", headers=auth_headers, json={"enabled": False})
    assert r.json()["data"]["enabled"] is False
