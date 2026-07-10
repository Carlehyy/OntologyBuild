"""Formal 定义必须约束所有实例/链接写入旁路，而不只约束 /full。"""


def _base(ontology_id: str) -> str:
    return f"/api/v2/formal/ontologies/{ontology_id}"


def _create_type(client, headers, oid: str, name: str, *, properties=None,
                 primary_key: str | None = None) -> str:
    r = client.post(f"{_base(oid)}/object-types", headers=headers, json={
        "name": name,
        "displayName": name,
        "primaryKey": primary_key,
        "properties": properties or [],
    })
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _create_instance(client, headers, oid: str, otid: str, properties: dict):
    return client.post(f"{_base(oid)}/instances", headers=headers, json={
        "objectTypeId": otid,
        "properties": properties,
        "source": "manual",
    })


def _codes(response) -> set[str]:
    return {e["code"] for e in response.json()["detail"]["errors"]}


def test_instance_crud_enforces_type_required_unknown_and_primary_key(
        client, auth_headers, ontology):
    oid = ontology["id"]
    otid = _create_type(client, auth_headers, oid, "Order", primary_key="code", properties=[
        {"id": "p-code", "name": "code", "displayName": "编号",
         "type": "string", "required": True},
        {"id": "p-amount", "name": "amount", "displayName": "金额",
         "type": "number", "required": False},
    ])

    r = _create_instance(client, auth_headers, oid, "missing-type", {"code": "O-1"})
    assert r.status_code == 422
    assert "object_type_not_found" in _codes(r)

    r = _create_instance(client, auth_headers, oid, otid, {"amount": 10})
    assert r.status_code == 422
    assert {"required_property_missing", "primary_key_missing"} <= _codes(r)

    r = _create_instance(client, auth_headers, oid, otid, {"code": "O-1", "amount": "ten"})
    assert r.status_code == 422
    assert "property_type_mismatch" in _codes(r)

    r = _create_instance(client, auth_headers, oid, otid, {"code": "O-1", "secret": "x"})
    assert r.status_code == 422
    assert "unknown_property" in _codes(r)

    r = _create_instance(client, auth_headers, oid, otid,
                         {"code": "O-1", "amount": 10, "name": "兼容展示字段"})
    assert r.status_code == 201, r.text
    instance_id = r.json()["data"]["id"]

    # 独立 schema CRUD 不能通过删属性绕过运行数据契约。
    r = client.put(f"{_base(oid)}/object-types/{otid}", headers=auth_headers, json={
        "properties": [{"id": "p-code", "name": "code", "displayName": "编号",
                        "type": "string", "required": True}],
    })
    assert r.status_code == 422
    assert "unknown_property" in _codes(r)

    r = _create_instance(client, auth_headers, oid, otid, {"code": "O-1", "amount": 20})
    assert r.status_code == 422
    assert "duplicate_primary_key" in _codes(r)

    # 更新旁路同样受约束，且失败不能污染复用 Session 中的 ORM 对象。
    r = client.put(f"{_base(oid)}/instances/{instance_id}", headers=auth_headers,
                   json={"properties": {"code": "O-1", "amount": "bad"}})
    assert r.status_code == 422
    current = client.get(f"{_base(oid)}/instances", headers=auth_headers).json()["data"]
    assert current[0]["properties"]["amount"] == 10


def test_empty_property_definition_remains_open_for_draft_compatibility(
        client, auth_headers, ontology):
    oid = ontology["id"]
    otid = _create_type(client, auth_headers, oid, "DraftObject")
    r = _create_instance(client, auth_headers, oid, otid, {"arbitrary": {"nested": True}})
    assert r.status_code == 201, r.text


def test_link_crud_enforces_endpoints_duplicates_and_cardinality(
        client, auth_headers, ontology):
    oid = ontology["id"]
    order = _create_type(client, auth_headers, oid, "Order", primary_key="code", properties=[
        {"id": "order-code", "name": "code", "displayName": "编号",
         "type": "string", "required": True},
    ])
    customer = _create_type(client, auth_headers, oid, "Customer", primary_key="code", properties=[
        {"id": "customer-code", "name": "code", "displayName": "编号",
         "type": "string", "required": True},
    ])
    order_id = _create_instance(client, auth_headers, oid, order, {"code": "O-1"}).json()["data"]["id"]
    customer_1 = _create_instance(client, auth_headers, oid, customer, {"code": "C-1"}).json()["data"]["id"]
    customer_2 = _create_instance(client, auth_headers, oid, customer, {"code": "C-2"}).json()["data"]["id"]

    # 独立 LinkType CRUD 不再允许悬挂端点。
    bad_type = client.post(f"{_base(oid)}/link-types", headers=auth_headers, json={
        "name": "bad", "displayName": "bad",
        "sourceObjectTypeId": order, "targetObjectTypeId": "missing",
        "cardinality": "one-to-one", "properties": [],
    })
    assert bad_type.status_code == 422
    assert "dangling_endpoint" in _codes(bad_type)

    r = client.post(f"{_base(oid)}/link-types", headers=auth_headers, json={
        "name": "owned_by", "displayName": "归属",
        "sourceObjectTypeId": order, "targetObjectTypeId": customer,
        "cardinality": "one-to-one", "properties": [
            {"id": "link-weight", "name": "weight", "displayName": "权重",
             "type": "number", "required": False},
        ],
    })
    assert r.status_code == 201, r.text
    link_type = r.json()["data"]["id"]

    def create_link(src, tgt, ltid=link_type, props=None):
        return client.post(f"{_base(oid)}/link-instances", headers=auth_headers, json={
            "linkTypeId": ltid, "sourceObjectId": src, "targetObjectId": tgt,
            "properties": props or {},
        })

    r = create_link(customer_1, order_id)
    assert r.status_code == 422
    assert {"source_type_mismatch", "target_type_mismatch"} <= _codes(r)

    r = create_link(order_id, customer_1, props={"weight": "heavy"})
    assert r.status_code == 422
    assert "property_type_mismatch" in _codes(r)

    r = create_link(order_id, customer_1, props={"undeclared": 1})
    assert r.status_code == 422
    assert "unknown_property" in _codes(r)

    r = create_link(order_id, customer_1)
    assert r.status_code == 201, r.text
    link_id = r.json()["data"]["id"]

    r = create_link(order_id, customer_1)
    assert r.status_code == 422
    assert "duplicate_link" in _codes(r)

    r = create_link(order_id, customer_2)
    assert r.status_code == 422
    assert "cardinality_violation" in _codes(r)

    r = create_link(order_id, customer_1, ltid="missing-link-type", props={"x": 1})
    assert r.status_code == 422
    assert "link_type_not_found" in _codes(r)

    # 删除类型/端点必须先显式处理依赖，不能留下裸字符串悬挂引用。
    r = client.put(f"{_base(oid)}/link-types/{link_type}", headers=auth_headers,
                   json={"targetObjectTypeId": order})
    assert r.status_code == 422
    assert "target_type_mismatch" in _codes(r)
    assert client.delete(f"{_base(oid)}/link-types/{link_type}",
                         headers=auth_headers).status_code == 422
    assert client.delete(f"{_base(oid)}/object-types/{order}",
                         headers=auth_headers).status_code == 422
    assert client.delete(f"{_base(oid)}/instances/{order_id}",
                         headers=auth_headers).status_code == 422
    assert client.delete(f"{_base(oid)}/link-instances/{link_id}",
                         headers=auth_headers).status_code == 204


def test_full_and_patch_validate_merged_runtime_view(client, auth_headers, ontology):
    oid = ontology["id"]
    payload = {
        "objectTypes": [{
            "id": "ot-order", "name": "Order", "displayName": "订单",
            "primaryKey": "code",
            "properties": [{"id": "p-code", "name": "code", "displayName": "编号",
                            "type": "string", "required": True}],
            "positionX": 0, "positionY": 0,
        }],
        "linkTypes": [], "actions": [], "functions": [],
        "instances": [{
            "id": "order-1", "objectTypeId": "ot-order",
            "properties": {"code": "O-1"}, "computed": {}, "source": "manual",
        }],
        "linkInstances": [],
    }
    r = client.put(f"{_base(oid)}/full", headers=auth_headers, json=payload)
    assert r.status_code == 200, r.text
    revision = r.json()["data"]["revision"]

    # PATCH 必须与库中未提交部分合并后校验，不能只检查 schema delta。
    r = client.patch(f"{_base(oid)}/full", headers=auth_headers, json={
        "baseRevision": revision,
        "upserts": {"instances": [{
            "id": "order-2", "objectTypeId": "ot-order",
            "properties": {"code": "O-1"}, "computed": {}, "source": "manual",
        }]},
        "deletes": {},
    })
    assert r.status_code == 422
    assert "duplicate_primary_key" in _codes(r)

    broken = dict(payload)
    broken["instances"] = [{
        "id": "order-x", "objectTypeId": "ot-order",
        "properties": {"unknown": 1}, "computed": {}, "source": "manual",
    }]
    r = client.put(f"{_base(oid)}/full", headers=auth_headers, json=broken)
    assert r.status_code == 422
    assert {"unknown_property", "required_property_missing", "primary_key_missing"} <= _codes(r)
