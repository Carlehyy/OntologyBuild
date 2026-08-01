"""PostgreSQL keyword-search and retired semantic-search API tests."""
from __future__ import annotations

from app.main import app
from app.models.entity import Entity
from app.data_channel.datasets import search_router


def _use_test_db(db):
    def override_search_db():
        yield db

    app.dependency_overrides[search_router.get_db] = override_search_db


def test_keyword_search_uses_postgresql(
    client,
    auth_headers,
    ontology,
    db,
):
    _use_test_db(db)
    db.add_all([
        Entity(
            id="matching-entity",
            ontology_id=ontology["id"],
            name_cn="华为技术有限公司",
            name_en="Huawei",
            type="Organization",
            description="技术公司",
            properties={"city": "深圳"},
        ),
        Entity(
            id="unrelated-entity",
            ontology_id=ontology["id"],
            name_cn="示例银行",
            name_en="Example Bank",
            type="Organization",
            description="金融机构",
            properties={"city": "上海"},
        ),
    ])
    db.commit()

    response = client.get(
        f"/api/v2/ontologies/{ontology['id']}/search/keyword",
        params={"q": "华为", "n": 5},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"results", "query", "search_backend"}
    assert payload["search_backend"] == "postgresql"
    assert payload["query"] == "华为"
    assert [row["id"] for row in payload["results"]] == ["matching-entity"]


def test_unified_keyword_search_uses_postgresql(
    client,
    auth_headers,
    ontology,
    db,
):
    _use_test_db(db)
    db.add(Entity(
        id="unified-entity",
        ontology_id=ontology["id"],
        name_cn="统一搜索目标",
        type="Entity",
        properties={},
    ))
    db.commit()

    response = client.post(
        f"/api/v2/ontologies/{ontology['id']}/search",
        json={"query": "统一搜索", "mode": "keyword", "n_results": 3},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "results": [{
            "id": "unified-entity",
            "document": "统一搜索目标",
            "metadata": {
                "name_cn": "统一搜索目标",
                "name_en": None,
                "entity_type": "Entity",
                "properties": {},
            },
        }],
        "mode": "keyword",
        "search_backend": "postgresql",
    }


def test_semantic_search_is_explicitly_unsupported(
    client,
    auth_headers,
    ontology,
    db,
):
    _use_test_db(db)

    response = client.get(
        f"/api/v2/ontologies/{ontology['id']}/search/semantic",
        params={"q": "相似公司"},
        headers=auth_headers,
    )

    assert response.status_code == 501, response.text
    assert response.json()["detail"] == {
        "code": "semantic_search_unsupported",
        "message": "语义搜索已停用；当前仅支持 PostgreSQL 关键词搜索",
    }


def test_unified_semantic_search_is_explicitly_unsupported(
    client,
    auth_headers,
    ontology,
    db,
):
    _use_test_db(db)

    response = client.post(
        f"/api/v2/ontologies/{ontology['id']}/search",
        json={"query": "相似公司", "mode": "semantic"},
        headers=auth_headers,
    )

    assert response.status_code == 501, response.text
    assert response.json()["detail"]["code"] == "semantic_search_unsupported"
