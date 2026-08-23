"""本体网络（跨本体全局图只读视图）的行为测试：

  1. overview：全部本体清单、发布口径与规模统计；
  2. 全局图：逐本体投影合并、节点带本体标注、关系边不跨本体；
  3. 同名类型桥接：跨本体同名 → 虚线桥接边；开关关闭 / 本体内同名 → 不桥接；
  4. 发布口径：已发布本体读发布快照实例，未发布回退实时数据并标注；
  5. 路径/影响/详情：单本体作用域，越本体实例不可见。
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import OperationalError

from app.models.ontology_formal import LinkInstance, ObjectInstance
from app.models.ontology import OntologyProject
from app.models.ontology_version import OntologyVersion


def _network(path: str) -> str:
    return f"/api/v2/ontology-network{path}"


def _create_ontology(client, auth_headers, name: str) -> dict:
    r = client.post(
        "/api/v1/ontologies", headers=auth_headers,
        json={"name": name, "domain": "供应链"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


def _save_model(client, auth_headers, ontology_id: str, *, tag: str = "a",
                supplier_name: str = "供应商"):
    """两个对象类型 + 链接 + 实例；供应商 display_name 可控以便做同名桥接。

    主键全库唯一，因此所有固定 id 都带本体级 tag 前缀，避免多本体夹具冲突。
    """
    body = {
        "objectTypes": [
            {"id": f"ot-order-{tag}", "name": "Order", "displayName": "订单", "primaryKey": "order_no",
             "properties": [
                 {"id": f"p1-{tag}", "name": "order_no", "displayName": "订单号", "type": "string", "required": True},
             ], "positionX": 0, "positionY": 0},
            {"id": f"ot-supplier-{tag}", "name": "Supplier", "displayName": supplier_name, "primaryKey": "sname",
             "properties": [
                 {"id": f"p4-{tag}", "name": "sname", "displayName": "名称", "type": "string", "required": True},
             ], "positionX": 0, "positionY": 0},
        ],
        "linkTypes": [
            {"id": f"lt-1-{tag}", "name": "order_supplier", "displayName": "订单-供应商",
             "sourceObjectTypeId": f"ot-order-{tag}", "targetObjectTypeId": f"ot-supplier-{tag}",
             "cardinality": "many-to-one"},
        ],
        "actions": [],
        "functions": [],
        "instances": [
            {"id": f"inst-o1-{tag}", "objectTypeId": f"ot-order-{tag}",
             "properties": {"order_no": "SO-001"}, "computed": {}},
            {"id": f"inst-s1-{tag}", "objectTypeId": f"ot-supplier-{tag}",
             "properties": {"sname": "华南电子"}, "computed": {}},
        ],
        "linkInstances": [
            {"id": f"li-1-{tag}", "linkTypeId": f"lt-1-{tag}",
             "sourceObjectId": f"inst-o1-{tag}", "targetObjectId": f"inst-s1-{tag}"},
        ],
    }
    r = client.put(f"/api/v2/formal/ontologies/{ontology_id}/full", headers=auth_headers, json=body)
    assert r.status_code == 200, r.text
    return body


def _publish_draft(db, ontology: dict, body: dict):
    """模拟发布语义：冻结模型快照到当前发布版，并把工作区实例归入该发布版。

    编辑器 full-save 写的是草稿投影（实例 release 为空、v0 快照不含新模型），
    真实发布由 promotion_service 完成这两件事；测试里直接等价模拟。
    """
    release_id = ontology["current_release_id"]
    release = db.query(OntologyVersion).filter_by(id=release_id).one()
    release.snapshot_formal = {
        "objectTypes": body["objectTypes"],
        "linkTypes": body["linkTypes"],
        "actions": body["actions"],
        "functions": body["functions"],
    }
    for model in (ObjectInstance, LinkInstance):
        rows = db.query(model).filter(
            model.ontology_id == ontology["id"]).all()
        for row in rows:
            row.ontology_release_id = release_id
    db.commit()


# ---------------------------------------------------------------- overview


def test_network_overview_lists_all_ontologies_with_counts(client, auth_headers, db):
    first = _create_ontology(client, auth_headers, "网络概览-A")
    second = _create_ontology(client, auth_headers, "网络概览-B")
    _publish_draft(db, first, _save_model(client, auth_headers, first["id"]))

    r = client.get(_network("/overview"), headers=auth_headers)
    assert r.status_code == 200, r.text
    items = r.json()["data"]
    by_id = {item["id"]: item for item in items}
    assert first["id"] in by_id and second["id"] in by_id

    a = by_id[first["id"]]
    # 本体创建即拥有 v0 基线 → 已发布口径
    assert a["published"] is True
    assert a["releaseId"] == first["current_release_id"]
    assert a["typeCount"] == 2
    assert a["linkTypeCount"] == 1
    assert a["instanceCount"] == 2


# ---------------------------------------------------------------- 全局图


def test_network_graph_merges_sections_and_keeps_edges_intra_ontology(client, auth_headers, db):
    first = _create_ontology(client, auth_headers, "全局图-A")
    second = _create_ontology(client, auth_headers, "全局图-B")
    _publish_draft(db, first, _save_model(client, auth_headers, first["id"], tag="ga"))
    _publish_draft(db, second, _save_model(client, auth_headers, second["id"], tag="gb"))

    r = client.get(_network("/graph"), headers=auth_headers, params={
        "ontology_ids": f"{first['id']},{second['id']}",
        "level": 2,
        "bridge_same_name": "true",
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    section_ids = [section["id"] for section in data["ontologies"]]
    assert set(section_ids) == {first["id"], second["id"]}

    node_ids = {node["id"] for node in data["nodes"]}
    assert len(data["nodes"]) >= 8          # 每本体 2 类型 + 2 实例
    for node in data["nodes"]:
        assert node["ontologyId"] in (first["id"], second["id"])
        assert node["ontologyName"]

    # 关系边必须两端同本体；桥接边允许跨本体且 kind=bridge
    sections_by_node = {node["id"]: node["ontologyId"] for node in data["nodes"]}
    relation_edges = [e for e in data["edges"] if e["kind"] == "relation"]
    assert relation_edges, "两个本体各有一条 订单→供应商 关系"
    for edge in relation_edges:
        assert sections_by_node[edge["source"]] == sections_by_node[edge["target"]]

    # 「订单」「供应商」在两个本体中都同名 → 各成一组桥接
    bridge_edges = [e for e in data["edges"] if e["kind"] == "bridge"]
    assert len(bridge_edges) == 2
    for bridge in bridge_edges:
        assert bridge["source"] in node_ids and bridge["target"] in node_ids
    assert data["bridges"]["enabled"] is True
    groups = data["bridges"]["groups"]
    assert {g["label"] for g in groups} == {"订单", "供应商"}
    for group in groups:
        assert {m["ontologyId"] for m in group["members"]} == {first["id"], second["id"]}
        assert len(group["members"]) == 2
    assert data["meta"]["truncated"] is False


def test_network_graph_bridge_disabled_and_intra_name_not_bridged(client, auth_headers, db):
    first = _create_ontology(client, auth_headers, "桥接关-A")
    second = _create_ontology(client, auth_headers, "桥接关-B")
    _save_model(client, auth_headers, first["id"], tag="ba")
    # 第二个本体故意也用 display_name=供应商，但同一本体内部的「订单」「供应商」
    # 属于不同类型也不该互相桥接（桥接只发生在跨本体之间）。
    _save_model(client, auth_headers, second["id"], tag="bb")

    r = client.get(_network("/graph"), headers=auth_headers, params={
        "ontology_ids": f"{first['id']},{second['id']}",
        "level": 1,
        "bridge_same_name": "false",
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["level"] == 1
    assert all(edge["kind"] != "bridge" for edge in data["edges"])
    assert data["bridges"]["enabled"] is False
    assert data["bridges"]["groups"] == []
    assert not any(node["kind"] == "instance" for node in data["nodes"]), "L1 不应展开实例"


def test_network_graph_requires_ids_and_validates_count(client, auth_headers):
    r = client.get(_network("/graph"), headers=auth_headers, params={"ontology_ids": ""})
    assert r.status_code == 422


def test_network_graph_marks_unknown_ontology_as_error(client, auth_headers, ontology):
    missing = str(uuid.uuid4())
    r = client.get(_network("/graph"), headers=auth_headers, params={
        "ontology_ids": f"{ontology['id']},{missing}",
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert [s["id"] for s in data["ontologies"]] == [ontology["id"]]
    assert data["errors"] == [{"ontologyId": missing, "message": "本体不存在"}]


# ---------------------------------------------------------------- 发布口径


def test_network_graph_unpublished_fallback_reads_live_data(client, auth_headers, db, ontology):
    """无发布指针的本体回退工作区实时数据，并显式标注未发布。"""
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology["id"]).first()
    project.current_release_id = None
    db.commit()

    _save_model(client, auth_headers, ontology["id"])
    r = client.get(_network("/graph"), headers=auth_headers, params={
        "ontology_ids": ontology["id"], "level": 2,
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    section = data["ontologies"][0]
    assert section["published"] is False
    assert section["releaseId"] is None
    assert section["instanceCount"] == 2
    assert sum(1 for n in data["nodes"] if n["kind"] == "instance") == 2


def test_network_graph_published_scope_hides_draft_only_instances(client, auth_headers, db, ontology):
    """已发布本体固定读发布版：未晋升的草稿实例不得出现在全局图里。"""
    # inst-o1 归入发布版；inst-s1 发布后新增草稿修改保持草稿态。
    _publish_draft(db, ontology, _save_model(client, auth_headers, ontology["id"], tag="dp"))
    draft_only = db.query(ObjectInstance).filter(
        ObjectInstance.id == "inst-s1-dp",
        ObjectInstance.ontology_id == ontology["id"]).one()
    draft_only.ontology_release_id = None
    db.commit()

    r = client.get(_network("/graph"), headers=auth_headers, params={
        "ontology_ids": ontology["id"], "level": 2,
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    instance_labels = [n["label"] for n in data["nodes"] if n["kind"] == "instance"]
    assert instance_labels == ["SO-001"]


# ------------------------------------------------- 路径 / 影响 / 详情


def test_network_paths_scoped_to_single_ontology(client, auth_headers, db):
    first = _create_ontology(client, auth_headers, "路径-A")
    second = _create_ontology(client, auth_headers, "路径-B")
    _publish_draft(db, first, _save_model(client, auth_headers, first["id"], tag="pa"))
    _publish_draft(db, second, _save_model(client, auth_headers, second["id"], tag="pb"))

    # 同本体寻路可用
    r = client.post(_network(f"/{first['id']}/paths"), headers=auth_headers, json={
        "sourceInstanceId": "inst-o1-pa", "targetInstanceId": "inst-s1-pa",
    })
    assert r.status_code == 200, r.text
    result = r.json()["data"]
    assert result["found"] is True
    path_nodes = {node["entityId"] for node in result["nodes"]}
    assert path_nodes == {"inst-o1-pa", "inst-s1-pa"}

    # 越本体引用第二本体的实例 → 边界硬失败
    r = client.post(_network(f"/{first['id']}/paths"), headers=auth_headers, json={
        "sourceInstanceId": "inst-o1-pa", "targetInstanceId": "inst-s1-pb",
    })
    assert r.status_code == 422


def test_network_instance_detail_returns_fields(client, auth_headers, db, ontology):
    _publish_draft(db, ontology, _save_model(client, auth_headers, ontology["id"], tag="ov"))

    r = client.get(
        _network(f"/{ontology['id']}/instances/inst-o1-ov"), headers=auth_headers)
    assert r.status_code == 200, r.text
    detail = r.json()["data"]
    assert detail["id"] == "inst-o1-ov"
    assert detail["label"] == "SO-001"
    assert detail["objectType"]["displayName"] == "订单"

    r = client.get(
        _network(f"/{ontology['id']}/instances/not-exist"), headers=auth_headers)
    assert r.status_code == 404


# ------------------------------------------------- 计数缓存与超时护栏


from app.ontologies.agent_runtime.boundary import AgentScope
from app.ontologies.network import service
from app.ontologies.network.service import NetworkRequestError, _raise_timeout


def test_network_overview_and_graph_accept_fresh_param(client, auth_headers, db):
    """fresh=true 是可选透传参数：跳过计数缓存强制直查，响应契约不变。"""
    first = _create_ontology(client, auth_headers, "缓存穿透-A")
    _publish_draft(db, first, _save_model(client, auth_headers, first["id"]))

    r = client.get(_network("/overview?fresh=true"), headers=auth_headers)
    assert r.status_code == 200, r.text
    item = next(item for item in r.json()["data"] if item["id"] == first["id"])
    assert item["instanceCount"] == 2

    r = client.get(
        _network("/graph"), headers=auth_headers,
        params={"ontology_ids": first["id"], "level": 2, "fresh": "true"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["meta"]["totalInstances"] == 2


def test_service_threads_fresh_into_instance_counts(client, auth_headers, db, monkeypatch):
    first = _create_ontology(client, auth_headers, "缓存穿透-B")
    calls: list[bool] = []
    monkeypatch.setattr(
        AgentScope, "instance_counts",
        lambda self, *, fresh=False: (calls.append(fresh), {})[1],
    )

    service.list_overview(db, fresh=True)
    assert calls == [True]

    service.list_overview(db)
    assert calls == [True, False]

    calls.clear()
    service.build_network_graph(db, ontology_ids=[first["id"]], level=1, fresh=True)
    assert True in calls, "fresh 必须透传到 scope.instance_counts"


def test_statement_timeout_maps_to_friendly_error():
    timed_out = Exception("canceling statement due to statement timeout")
    timed_out.pgcode = "57014"
    with pytest.raises(NetworkRequestError, match="数据量过大"):
        _raise_timeout(OperationalError("stmt", {}, timed_out))

    other = Exception("no such table")
    other.pgcode = "42P01"
    with pytest.raises(OperationalError):
        _raise_timeout(OperationalError("stmt", {}, other))

