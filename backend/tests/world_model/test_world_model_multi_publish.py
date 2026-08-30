"""多本体发布（Q1' 路径 A）：一个推演模型项目可发布 N 个服务，每个绑定恰好一个本体。

- 同一本体重发布 = 覆盖该本体对应的服务，不影响发布到其他本体的服务；
- 项目级状态切换批量作用于全部服务；删除守卫检查任一在线服务；
- 兼容入口 GET /projects/{id}/service 返回代表性（最近更新）服务。
"""

from app.world_model import service
from tests.world_model.test_world_model import (
    BASE,
    _PUBLISH_BODY,
    _save_version,
    project,
)


def _publish(client, headers, project_id, ontology_id, name="服务", type_ids=None):
    body = {**_PUBLISH_BODY, "name": name,
            "applicable_ontology_id": ontology_id,
            "applicable_object_type_ids": type_ids or ["ot-line"]}
    r = client.post(f"{BASE}/projects/{project_id}/publish",
                    json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_multi_ontology_publish_and_republish_semantics(
        client, auth_headers, project, monkeypatch):
    pid = project["id"]
    _save_version(client, auth_headers, pid, monkeypatch)

    first = _publish(client, auth_headers, pid, "ontology-1", name="A本体服务")
    second = _publish(client, auth_headers, pid, "ontology-2", name="B本体服务",
                      type_ids=["ot-b1", "ot-b2"])
    assert first["id"] != second["id"]
    assert first["endpoint_path"] != second["endpoint_path"]

    listed = client.get(f"{BASE}/projects/{pid}/services", headers=auth_headers)
    assert listed.status_code == 200, listed.text
    rows = listed.json()["data"]
    assert len(rows) == 2
    by_ontology = {row["applicable_object_types"]["ontology_id"]: row for row in rows}
    assert set(by_ontology) == {"ontology-1", "ontology-2"}
    assert by_ontology["ontology-2"]["applicable_object_types"]["object_type_ids"] == ["ot-b1", "ot-b2"]

    # 同一本体重发布：覆盖该本体的服务（id 不变），另一本体的服务不受影响
    republish = _publish(client, auth_headers, pid, "ontology-1", name="A本体服务v2")
    assert republish["id"] == first["id"]
    assert republish["name"] == "A本体服务v2"
    listed = client.get(f"{BASE}/projects/{pid}/services", headers=auth_headers).json()["data"]
    assert len(listed) == 2
    assert {row["name"] for row in listed} == {"A本体服务v2", "B本体服务"}

    # 兼容入口返回代表性（最近更新）服务；项目列表摘要给出 service_count
    legacy = client.get(f"{BASE}/projects/{pid}/service", headers=auth_headers).json()["data"]
    assert legacy["id"] == first["id"]  # 最后一次发布作用于 ontology-1
    projects = client.get(f"{BASE}/projects", headers=auth_headers).json()["data"]["items"]
    mine = next(item for item in projects if item["id"] == pid)
    assert mine["service_count"] == 2
    assert mine["service_name"] == "A本体服务v2"


def test_project_status_toggle_applies_to_all_services(
        client, auth_headers, project, monkeypatch):
    pid = project["id"]
    _save_version(client, auth_headers, pid, monkeypatch)
    _publish(client, auth_headers, pid, "ontology-1", name="A服务")
    _publish(client, auth_headers, pid, "ontology-2", name="B服务")

    r = client.post(f"{BASE}/projects/{pid}/service/status",
                    json={"status": "offline"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    rows = client.get(f"{BASE}/projects/{pid}/services", headers=auth_headers).json()["data"]
    assert {row["status"] for row in rows} == {"offline"}


def test_delete_blocked_while_any_service_online_then_cleans_all(
        client, auth_headers, project, monkeypatch, db):
    pid = project["id"]
    _save_version(client, auth_headers, pid, monkeypatch)
    _publish(client, auth_headers, pid, "ontology-1", name="A服务")
    _publish(client, auth_headers, pid, "ontology-2", name="B服务")

    # 任一服务在线即阻止删除：全部下线后单独把一个上线，仍应阻止
    client.post(f"{BASE}/projects/{pid}/service/status",
                json={"status": "offline"}, headers=auth_headers)
    rows = client.get(f"{BASE}/projects/{pid}/services", headers=auth_headers).json()["data"]
    client.post(f"{BASE}/services/{rows[0]['id']}/status",
                json={"status": "online"}, headers=auth_headers)
    blocked = client.delete(f"{BASE}/projects/{pid}", headers=auth_headers)
    assert blocked.status_code == 409, blocked.text

    # 全部下线后可删，且两个服务一并清理
    client.post(f"{BASE}/projects/{pid}/service/status",
                json={"status": "offline"}, headers=auth_headers)
    ok = client.delete(f"{BASE}/projects/{pid}", headers=auth_headers)
    assert ok.status_code == 200 and ok.json()["data"]["status"] == "deleted", ok.text
    from app.world_model.models import WorldModelService
    assert db.query(WorldModelService).filter(
        WorldModelService.project_id == pid).count() == 0
