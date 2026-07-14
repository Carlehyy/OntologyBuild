"""
核心功能端到端测试 - 金融风控知识图谱
覆盖: 认证、本体CRUD、实体、规则、图谱、版本化、属性Schema、词表、影子试跑、推理运行、审计日志、导出
"""
import sys, os

# This file is a legacy executable smoke-test script, not a pytest module: it
# owns a database, invokes functions at import time and exits the interpreter.
# Never let pytest collection execute destructive setup or terminate the suite.
if "pytest" in sys.modules:
    import pytest
    pytest.skip("legacy executable smoke script; covered by isolated API tests", allow_module_level=True)

sys.path.insert(0, "/mnt/agents/nano-ontoprompt/backend")
os.environ["DATABASE_URL"] = "sqlite:////tmp/test_ontoprompt.db"
os.environ["SECRET_KEY"] = "test-secret-key"

import uuid, json
from fastapi.testclient import TestClient

# Create test DB fresh
from app.database import engine, Base
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

from app.main import app
from app.database import SessionLocal
from app.models.user import User
from app.services.auth_service import hash_password

client = TestClient(app)
db = SessionLocal()

# ── Setup: Create admin ──
admin_id = str(uuid.uuid4())
admin = User(id=admin_id, username="testadmin", email="test@local", password_hash=hash_password("test123"), role="admin")
db.add(admin)
db.commit()

# Login
token = client.post("/api/v1/auth/login", json={"username": "testadmin", "password": "test123"}).json()["data"]["access_token"]
headers = {"Authorization": f"Bearer {token}"}

print(f"=== Token acquired: {token[:20]}... ===\n")

passed = 0
failed = 0

def test(name, func):
    global passed, failed
    try:
        func()
        print(f"  PASS: {name}")
        passed += 1
    except Exception as e:
        print(f"  FAIL: {name} -> {str(e)[:100]}")
        failed += 1

# ── Tests ──
oid = None

# Test 1: Auth
def t_auth_login():
    r = client.post("/api/v1/auth/login", json={"username": "testadmin", "password": "test123"})
    assert r.status_code == 200
    assert "access_token" in r.json()["data"]

def t_auth_profile():
    r = client.get("/api/v1/auth/profile", headers=headers)
    assert r.status_code == 200
    assert r.json()["data"]["username"] == "testadmin"

test("Auth Login", t_auth_login)
test("Auth Profile", t_auth_profile)

# Test 2: Ontology CRUD
def t_create_ontology():
    global oid
    r = client.post("/api/v1/ontologies", json={"name": "测试本体", "domain": "测试领域", "description": "测试"}, headers=headers)
    assert r.status_code == 200
    oid = r.json()["data"]["id"]
    assert oid is not None

def t_list_ontologies():
    r = client.get("/api/v1/ontologies", headers=headers)
    assert r.status_code == 200
    assert len(r.json()["data"]["items"]) >= 1

def t_get_ontology():
    r = client.get(f"/api/v1/ontologies/{oid}", headers=headers)
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "测试本体"

test("Create Ontology", t_create_ontology)
test("List Ontologies", t_list_ontologies)
test("Get Ontology", t_get_ontology)

# Test 3: Entities
def t_create_entity():
    from app.models.entity import Entity
    for i in range(5):
        e = Entity(id=str(uuid.uuid4()), ontology_id=oid, name_cn=f"企业{i+1}", type="企业",
                   description=f"描述{i+1}", properties={"industry": "科技", "risk_level": "中风险"}, confidence=1.0)
        db.add(e)
    db.commit()

def t_list_entities():
    r = client.get(f"/api/v1/ontologies/{oid}/entities", headers=headers)
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    assert len(items) >= 5

test("Create Entities", t_create_entity)
test("List Entities", t_list_entities)

# Test 4: Rules
def t_create_rules():
    from app.models.logic import LogicRule
    for i in range(2):
        lr = LogicRule(id=str(uuid.uuid4()), ontology_id=oid, name_cn=f"规则{i+1}", name_en=f"rule_{i}",
                       description="测试规则", formula="risk_level = 中风险 AND industry = 科技",
                       confidence=0.85, enabled=True, status="published")
        db.add(lr)
    db.commit()

def t_list_rules():
    r = client.get(f"/api/v1/ontologies/{oid}/logic", headers=headers)
    assert r.status_code == 200
    assert len(r.json()["data"]) == 2

test("Create Rules", t_create_rules)
test("List Rules", t_list_rules)

# Test 5: Actions
def t_create_action():
    from app.models.action import Action
    a = Action(id=str(uuid.uuid4()), ontology_id=oid, name_cn="测试动作", name_en="test_action",
               description="测试", execution_rule="NOTIFY(团队)", confidence=0.9, enabled=True, status="published")
    db.add(a)
    db.commit()

def t_list_actions():
    r = client.get(f"/api/v1/ontologies/{oid}/actions", headers=headers)
    assert r.status_code == 200
    assert len(r.json()["data"]) == 1

test("Create Action", t_create_action)
test("List Actions", t_list_actions)

# Test 6: Graph
def t_graph_v1():
    r = client.get(f"/api/v1/ontologies/{oid}/graph", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data["nodes"]) >= 5

def t_graph_v2():
    r = client.get(f"/api/v2/ontologies/{oid}/graph", headers=headers)
    assert r.status_code == 200
    assert "nodes" in r.json()

def t_nl_query():
    r = client.post(f"/api/v2/ontologies/{oid}/graph/nl-query", json={"query": "科技", "language": "zh"}, headers=headers)
    assert r.status_code == 200
    assert "results" in r.json()

test("Graph V1", t_graph_v1)
test("Graph V2", t_graph_v2)
test("NL2Cypher Query", t_nl_query)

# Test 7: Versioning
def t_create_version():
    r = client.post(f"/api/v2/ontologies/{oid}/versions", json={"version_label": "v1", "description": "测试"}, headers=headers)
    assert r.status_code == 201
    assert "version_number" in r.json()["data"]

def t_list_versions():
    r = client.get(f"/api/v2/ontologies/{oid}/versions", headers=headers)
    assert r.status_code == 200
    assert len(r.json()["data"]) >= 1

test("Create Version", t_create_version)
test("List Versions", t_list_versions)

# Test 8: Attribute Schemas
def t_create_schema():
    r = client.post(f"/api/v2/ontologies/{oid}/attribute-schemas", json={
        "name": "test_capital", "display_name": "注册资本",
        "data_type": "number", "constraints": {"min": 0, "unit": "万元"},
        "applies_to_types": ["企业"],
    }, headers=headers)
    assert r.status_code == 201

def t_list_schemas():
    r = client.get(f"/api/v2/ontologies/{oid}/attribute-schemas", headers=headers)
    assert r.status_code == 200
    assert len(r.json()["data"]) >= 1

def t_validate_value():
    r = client.get(f"/api/v2/ontologies/{oid}/attribute-schemas", headers=headers)
    sid = r.json()["data"][0]["id"]
    r = client.post(f"/api/v2/ontologies/{oid}/attribute-schemas/{sid}/validate", json={"value": "10000"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["valid"] == True

test("Create Schema", t_create_schema)
test("List Schemas", t_list_schemas)
test("Validate Value", t_validate_value)

# Test 9: Vocabulary
def t_create_vocab():
    r = client.post(f"/api/v2/ontologies/{oid}/vocabulary", json={
        "canonical": "测试词", "synonyms": ["别名1", "别名2"],
    }, headers=headers)
    assert r.status_code == 201

def t_list_vocab():
    r = client.get(f"/api/v2/ontologies/{oid}/vocabulary", headers=headers)
    assert r.status_code == 200
    assert len(r.json()["data"]) >= 1

test("Create Vocab", t_create_vocab)
test("List Vocab", t_list_vocab)

# Test 10: Shadow Run
def t_shadow_run():
    r = client.get(f"/api/v1/ontologies/{oid}/logic", headers=headers)
    rid = r.json()["data"][0]["id"]
    rname = r.json()["data"][0]["name_cn"]
    r = client.post(f"/api/v2/ontologies/{oid}/shadow-runs", json={"rule_id": rid, "rule_name": rname}, headers=headers)
    assert r.status_code == 201
    data = r.json()["data"]
    assert data["status"] == "completed"
    assert "verdict" in data

test("Shadow Run", t_shadow_run)

# Test 11: Inference Run
def t_inference_run():
    r = client.post(f"/api/v2/ontologies/{oid}/inference-runs", json={
        "name": "测试推理", "description": "测试",
    }, headers=headers)
    assert r.status_code == 201
    data = r.json()["data"]
    assert data["status"] == "completed"
    assert data["total_checked"] >= 5

def t_list_inference_runs():
    r = client.get(f"/api/v2/ontologies/{oid}/inference-runs", headers=headers)
    assert r.status_code == 200
    assert len(r.json()["data"]) >= 1

test("Inference Run", t_inference_run)
test("List Inference Runs", t_list_inference_runs)

# Test 12: Audit Logs
def t_audit_logs():
    r = client.get(f"/api/v2/ontologies/{oid}/audit-logs", headers=headers)
    assert r.status_code == 200
    assert isinstance(r.json()["data"], list)

test("Audit Logs", t_audit_logs)

# Test 13: Export
def t_export_json():
    r = client.get(f"/api/v1/ontologies/{oid}/export", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["format"] == "ontology-structure"

test("Export JSON", t_export_json)

# Summary
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
print(f"{'='*50}")
db.close()

if failed > 0:
    sys.exit(1)
