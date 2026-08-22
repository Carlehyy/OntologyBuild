"""实例事实历史端点的 limit/offset 分页契约：详情弹窗"加载更多"依赖
稳定的倒序窗口，不允许出现重复或遗漏。"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.models.ontology_formal import PropertyFact


def _seed_facts(db, ontology_id: str, instance_id: str, count: int) -> list[str]:
    base = datetime(2026, 8, 1, 12, 0, 0)
    ids = []
    for index in range(count):
        fact = PropertyFact(
            id=f"fact-page-{index}",
            ontology_id=ontology_id,
            instance_id=instance_id,
            property_name="status",
            value={"v": f"s{index}"},
            kind="property",
            source="manual",
            seq=index,
            recorded_at=base + timedelta(minutes=index),
        )
        db.add(fact)
        ids.append(fact.id)
    db.commit()
    return ids


def test_instance_facts_limit_offset_windows(client, auth_headers, ontology, db):
    ids = _seed_facts(db, ontology["id"], "inst-page", 5)

    first = client.get(
        f"/api/v2/formal/ontologies/{ontology['id']}/instances/inst-page/facts"
        "?limit=2",
        headers=auth_headers,
    ).json()["data"]
    assert [item["id"] for item in first] == [ids[4], ids[3]]

    second = client.get(
        f"/api/v2/formal/ontologies/{ontology['id']}/instances/inst-page/facts"
        "?limit=2&offset=2",
        headers=auth_headers,
    ).json()["data"]
    assert [item["id"] for item in second] == [ids[2], ids[1]]

    tail = client.get(
        f"/api/v2/formal/ontologies/{ontology['id']}/instances/inst-page/facts"
        "?limit=2&offset=4",
        headers=auth_headers,
    ).json()["data"]
    assert [item["id"] for item in tail] == [ids[0]]

    exhausted = client.get(
        f"/api/v2/formal/ontologies/{ontology['id']}/instances/inst-page/facts"
        "?limit=2&offset=5",
        headers=auth_headers,
    ).json()["data"]
    assert exhausted == []

    # 不传 offset 时行为与旧契约一致（默认 0，按时间倒序）
    legacy = client.get(
        f"/api/v2/formal/ontologies/{ontology['id']}/instances/inst-page/facts",
        headers=auth_headers,
    ).json()["data"]
    assert [item["id"] for item in legacy] == [ids[4], ids[3], ids[2], ids[1], ids[0]]
