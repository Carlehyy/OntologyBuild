"""图谱编辑器地基改造的端到端测试：

  1. 乐观并发：GET /full 返回 revision；错误的 baseRevision 保存 → 409；正确 → 200 且 revision 前进
  2. Fact 溯源层：保存/修改实例属性会追加 fo_property_facts，supersedes 链正确
  3. 版本发布：快照包含正规模型（snapshot_formal），回滚可恢复被删的对象类型
"""
import uuid


def _fo(ontology_id: str) -> str:
    return f"/api/v2/formal/ontologies/{ontology_id}"


def _minimal_payload(instance_props: dict, base_revision: str | None = None,
                     ot_id: str = "ot-1", inst_id: str = "inst-1") -> dict:
    body = {
        "objectTypes": [{
            "id": ot_id, "name": "Flight", "displayName": "航班",
            "primaryKey": "flight_no",
            "properties": [
                {"id": "p1", "name": "flight_no", "displayName": "航班号", "type": "string", "required": True},
                {"id": "p2", "name": "status", "displayName": "状态", "type": "string", "required": False},
            ],
            "positionX": 0, "positionY": 0,
        }],
        "linkTypes": [], "actions": [], "functions": [],
        "instances": [{
            "id": inst_id, "objectTypeId": ot_id,
            "properties": instance_props, "computed": {}, "source": "manual",
        }],
        "linkInstances": [],
    }
    if base_revision is not None:
        body["baseRevision"] = base_revision
    return body


def test_optimistic_concurrency_409(client, auth_headers, ontology):
    oid = ontology["id"]
    r = client.get(f"{_fo(oid)}/full", headers=auth_headers)
    assert r.status_code == 200
    rev = r.json()["data"]["revision"]
    assert rev

    # 携带过期 revision 保存 → 409 且包含最新 revision
    r = client.put(f"{_fo(oid)}/full", headers=auth_headers,
                   json=_minimal_payload({"flight_no": "CA1234"}, base_revision="1999-01-01T00:00:00"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "conflict"
    assert r.json()["detail"]["currentRevision"] == rev

    # 携带正确 revision → 保存成功，revision 前进
    r = client.put(f"{_fo(oid)}/full", headers=auth_headers,
                   json=_minimal_payload({"flight_no": "CA1234"}, base_revision=rev))
    assert r.status_code == 200
    new_rev = r.json()["data"]["revision"]
    assert new_rev and new_rev != rev

    # 用旧 revision 再保存 → 再次 409（他人视角）
    r = client.put(f"{_fo(oid)}/full", headers=auth_headers,
                   json=_minimal_payload({"flight_no": "CA1234"}, base_revision=rev))
    assert r.status_code == 409

    # 不带 baseRevision（强制覆盖 / 旧客户端）→ 放行
    r = client.put(f"{_fo(oid)}/full", headers=auth_headers,
                   json=_minimal_payload({"flight_no": "CA1234"}))
    assert r.status_code == 200


def test_property_facts_append_and_supersede(client, auth_headers, ontology):
    oid = ontology["id"]

    # 首次保存：创建实例 → 每个属性一条首事实
    r = client.put(f"{_fo(oid)}/full", headers=auth_headers,
                   json=_minimal_payload({"flight_no": "CA1234", "status": "SCHEDULED"}))
    assert r.status_code == 200

    r = client.get(f"{_fo(oid)}/instances/inst-1/facts", headers=auth_headers)
    assert r.status_code == 200
    facts = r.json()["data"]
    assert {f["propertyName"] for f in facts} == {"flight_no", "status"}
    assert all(f["supersedesId"] is None for f in facts)
    assert all(f["source"] == "editor-save" for f in facts)

    # 第二次保存：status 变化 → 追加新事实并 supersede 旧事实；flight_no 未变不追加
    r = client.put(f"{_fo(oid)}/full", headers=auth_headers,
                   json=_minimal_payload({"flight_no": "CA1234", "status": "DELAYED"}))
    assert r.status_code == 200

    r = client.get(f"{_fo(oid)}/instances/inst-1/facts",
                   headers=auth_headers, params={"property_name": "status"})
    status_facts = r.json()["data"]
    assert len(status_facts) == 2
    latest, first = status_facts[0], status_facts[1]
    assert latest["value"] == "DELAYED"
    assert latest["supersedesId"] == first["id"]
    assert first["value"] == "SCHEDULED"

    r = client.get(f"{_fo(oid)}/instances/inst-1/facts",
                   headers=auth_headers, params={"property_name": "flight_no"})
    assert len(r.json()["data"]) == 1


def test_backend_validation_rejects_invalid_model(client, auth_headers, ontology):
    """修复1：后端强制校验——重名/悬挂端点被 422 拒绝，草稿态放行。"""
    oid = ontology["id"]

    # 对象类型重名 → 422
    dup = _minimal_payload({"flight_no": "CA1"})
    dup["objectTypes"].append({**dup["objectTypes"][0], "id": "ot-2"})
    r = client.put(f"{_fo(oid)}/full", headers=auth_headers, json=dup)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "validation_failed"
    assert any(e["code"] == "duplicate_name" for e in detail["errors"])

    # 关系端点悬挂 → 422
    bad_link = _minimal_payload({"flight_no": "CA1"})
    bad_link["linkTypes"] = [{
        "id": "lt-1", "name": "operates", "displayName": "执飞",
        "sourceObjectTypeId": "ot-1", "targetObjectTypeId": "ot-missing",
        "cardinality": "one-to-many", "properties": [],
    }]
    r = client.put(f"{_fo(oid)}/full", headers=auth_headers, json=bad_link)
    assert r.status_code == 422
    assert any(e["code"] == "dangling_endpoint" for e in r.json()["detail"]["errors"])

    # 草稿态（无主键、无属性的类型）→ 放行
    draft = _minimal_payload({"flight_no": "CA1"})
    draft["objectTypes"][0]["primaryKey"] = None
    r = client.put(f"{_fo(oid)}/full", headers=auth_headers, json=draft)
    assert r.status_code == 200


def _payload_with_computed(props: dict, base_revision: str | None = None) -> dict:
    """带一个 computed 属性（total = price*qty，expression 函数）的模型。"""
    body = _minimal_payload(props, base_revision)
    body["functions"] = [{
        "id": "fn-1", "name": "calc_total", "displayName": "算总价",
        "functionType": "object", "language": "expression",
        "targetObjectTypeId": "ot-1", "parameters": [],
        "returnType": "number", "body": 'object["price"] * object["qty"]',
        "enabled": True,
    }]
    body["objectTypes"][0]["properties"] += [
        {"id": "p3", "name": "price", "displayName": "单价", "type": "number", "required": False},
        {"id": "p4", "name": "qty", "displayName": "数量", "type": "number", "required": False},
        {"id": "p5", "name": "total", "displayName": "总价", "type": "number", "required": False,
         "source": "computed", "functionId": "fn-1"},
    ]
    return body


def test_derived_recompute_on_save(client, auth_headers, ontology):
    """修复2：存储属性变化 → 派生属性自动重算 + derived 事实带推导链。"""
    oid = ontology["id"]

    r = client.put(f"{_fo(oid)}/full", headers=auth_headers,
                   json=_payload_with_computed({"flight_no": "CA1", "price": 10, "qty": 3}))
    assert r.status_code == 200
    inst = r.json()["data"]["instances"][0]
    assert inst["computed"]["total"] == 30  # 保存即重算

    facts = client.get(f"{_fo(oid)}/instances/inst-1/facts",
                       headers=auth_headers, params={"property_name": "total"}).json()["data"]
    assert len(facts) == 1
    assert facts[0]["kind"] == "derived"
    assert facts[0]["source"] == "fn:calc_total"
    assert len(facts[0]["derivedFrom"]) > 0  # 指向触发本次重算的输入事实

    # 输入变化 → 重算 + 新派生事实 supersede 旧的
    r = client.put(f"{_fo(oid)}/full", headers=auth_headers,
                   json=_payload_with_computed({"flight_no": "CA1", "price": 10, "qty": 5}))
    assert r.json()["data"]["instances"][0]["computed"]["total"] == 50
    facts = client.get(f"{_fo(oid)}/instances/inst-1/facts",
                       headers=auth_headers, params={"property_name": "total"}).json()["data"]
    assert len(facts) == 2
    assert facts[0]["value"] == 50 and facts[0]["supersedesId"] == facts[1]["id"]


def test_delta_patch_save(client, auth_headers, ontology):
    """修复3：增量保存——delta 生效、并发检测、校验闸门都与全量一致。"""
    oid = ontology["id"]

    r = client.put(f"{_fo(oid)}/full", headers=auth_headers,
                   json=_payload_with_computed({"flight_no": "CA1", "price": 10, "qty": 3}))
    assert r.status_code == 200
    rev = r.json()["data"]["revision"]

    # 只提交一个实例的属性变化
    delta = {
        "baseRevision": rev,
        "upserts": {"instances": [{
            "id": "inst-1", "objectTypeId": "ot-1",
            "properties": {"flight_no": "CA1", "price": 20, "qty": 3},
            "computed": {}, "source": "manual",
        }]},
        "deletes": {},
    }
    r = client.patch(f"{_fo(oid)}/full", headers=auth_headers, json=delta)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["revision"] != rev                       # revision 前进
    assert data["instances"][0]["computed"]["total"] == 60  # 派生重算随 delta 生效

    # price 属性事实已追加
    facts = client.get(f"{_fo(oid)}/instances/inst-1/facts",
                       headers=auth_headers, params={"property_name": "price"}).json()["data"]
    assert len(facts) == 2 and facts[0]["value"] == 20

    # 过期 revision → 409
    r = client.patch(f"{_fo(oid)}/full", headers=auth_headers, json=delta)
    assert r.status_code == 409

    # delta 引入重名 → 422（合并视图校验）
    bad = {
        "baseRevision": data["revision"],
        "upserts": {"objectTypes": [{
            "id": "ot-dup", "name": "Flight", "displayName": "航班2",
            "primaryKey": None, "properties": [], "positionX": 0, "positionY": 0,
        }]},
        "deletes": {},
    }
    r = client.patch(f"{_fo(oid)}/full", headers=auth_headers, json=bad)
    assert r.status_code == 422

    # 删除实例的 delta
    r = client.patch(f"{_fo(oid)}/full", headers=auth_headers, json={
        "baseRevision": data["revision"],
        "upserts": {}, "deletes": {"instances": ["inst-1"]},
    })
    assert r.status_code == 200
    r = client.get(f"{_fo(oid)}/full", headers=auth_headers)
    assert r.json()["data"]["instances"] == []


def test_version_publish_snapshots_formal_and_rollback(client, auth_headers, ontology):
    oid = ontology["id"]

    # 建模 + 发布 v1
    r = client.put(f"{_fo(oid)}/full", headers=auth_headers,
                   json=_minimal_payload({"flight_no": "CA1234"}))
    assert r.status_code == 200
    r = client.post(f"/api/v2/ontologies/{oid}/versions", headers=auth_headers,
                    json={"version_label": "基线"})
    assert r.status_code == 201
    version_id = r.json()["data"]["id"]
    formal_diff = r.json()["data"]["change_summary"]["formal"]["total"]
    assert formal_diff["added"] >= 1  # 对象类型入快照

    # 版本详情包含正规模型快照
    r = client.get(f"/api/v2/ontologies/{oid}/versions/{version_id}", headers=auth_headers)
    snap = r.json()["data"]["snapshot"]["formal"]
    assert snap and len(snap["objectTypes"]) == 1
    assert snap["objectTypes"][0]["name"] == "Flight"

    # 删除对象类型（模拟误操作后的空模型保存）
    empty = {"objectTypes": [], "linkTypes": [], "actions": [], "functions": [],
             "instances": [], "linkInstances": []}
    r = client.put(f"{_fo(oid)}/full", headers=auth_headers, json=empty)
    assert r.status_code == 200
    r = client.get(f"{_fo(oid)}/full", headers=auth_headers)
    assert r.json()["data"]["objectTypes"] == []

    # 回滚到 v1 → 对象类型恢复
    r = client.post(f"/api/v2/ontologies/{oid}/versions/{version_id}/rollback", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["data"]["formal_restored"]["objectTypes"] == 1
    r = client.get(f"{_fo(oid)}/full", headers=auth_headers)
    types = r.json()["data"]["objectTypes"]
    assert len(types) == 1 and types[0]["name"] == "Flight"
