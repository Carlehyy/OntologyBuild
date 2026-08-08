"""Domain-registry invariants shared by Settings and ontology management."""

from app.settings.domains.models import Domain


def _create_domain(client, auth_headers, name: str) -> dict:
    response = client.post(
        "/api/v1/domains",
        json={"name": name, "description": f"{name} description"},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_legacy_ontology_domain_is_registered_for_compatibility(
    client,
    auth_headers,
    db,
):
    response = client.post(
        "/api/v1/ontologies",
        json={"name": "历史领域本体", "domain": "教育"},
        headers=auth_headers,
    )

    assert response.status_code == 201, response.text
    registered = db.query(Domain).filter(Domain.name == "教育").one()
    assert registered.description == "由历史兼容本体自动登记"


def test_regular_ontology_create_still_rejects_unconfigured_domain(
    client,
    auth_headers,
    db,
):
    response = client.post(
        "/api/v1/ontologies",
        json={"name": "未配置领域本体", "domain": "未配置普通领域"},
        headers=auth_headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "INVALID_DOMAIN"
    assert db.query(Domain).filter(Domain.name == "未配置普通领域").first() is None


def test_domain_rename_cascades_to_ontology_projects(
    client,
    auth_headers,
):
    domain = _create_domain(client, auth_headers, "客户运营")
    ontology_response = client.post(
        "/api/v1/ontologies",
        json={"name": "客户运营本体", "domain": "客户运营"},
        headers=auth_headers,
    )
    assert ontology_response.status_code == 201, ontology_response.text
    ontology_id = ontology_response.json()["data"]["id"]

    renamed = client.put(
        f"/api/v1/domains/{domain['id']}",
        json={"name": "客户成功"},
        headers=auth_headers,
    )

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["data"]["name"] == "客户成功"
    ontology = client.get(
        f"/api/v1/ontologies/{ontology_id}",
        headers=auth_headers,
    )
    assert ontology.status_code == 200, ontology.text
    assert ontology.json()["data"]["domain"] == "客户成功"

    old_filter = client.get(
        "/api/v1/ontologies?domain=客户运营",
        headers=auth_headers,
    ).json()["data"]
    new_filter = client.get(
        "/api/v1/ontologies?domain=客户成功",
        headers=auth_headers,
    ).json()["data"]
    assert old_filter["total"] == 0
    assert new_filter["total"] == 1


def test_domain_in_use_cannot_be_deleted(client, auth_headers):
    domain = _create_domain(client, auth_headers, "订单履约")
    ontology_response = client.post(
        "/api/v1/ontologies",
        json={"name": "订单履约本体", "domain": "订单履约"},
        headers=auth_headers,
    )
    assert ontology_response.status_code == 201, ontology_response.text

    deleted = client.delete(
        f"/api/v1/domains/{domain['id']}",
        headers=auth_headers,
    )

    assert deleted.status_code == 409
    assert "已被 1 个本体使用" in deleted.json()["detail"]
    names = {
        item["name"]
        for item in client.get("/api/v1/domains", headers=auth_headers).json()["data"]
    }
    assert "订单履约" in names


def test_domain_names_are_trimmed_before_uniqueness_check(client, auth_headers):
    created = client.post(
        "/api/v1/domains",
        json={"name": "  风险管理  ", "description": ""},
        headers=auth_headers,
    )
    duplicate = client.post(
        "/api/v1/domains",
        json={"name": "风险管理", "description": ""},
        headers=auth_headers,
    )

    assert created.status_code == 201, created.text
    assert created.json()["data"]["name"] == "风险管理"
    assert duplicate.status_code == 409
