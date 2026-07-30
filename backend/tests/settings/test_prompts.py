import hashlib
import json


def test_legacy_prompt_facades_preserve_object_identity():
    from app.models import prompt as legacy_model
    from app.routers import prompts as legacy_router
    from app.schemas import prompt as legacy_schema
    from app.settings.prompts import models, router, schemas, templates

    assert legacy_router.router is router.router
    assert legacy_router.BUILTIN_PROMPTS is templates.BUILTIN_PROMPTS
    assert legacy_model.Prompt is models.Prompt
    assert legacy_schema.PromptCreate is schemas.PromptCreate
    assert legacy_schema.PromptUpdate is schemas.PromptUpdate
    assert legacy_schema.PromptOut is schemas.PromptOut


def test_builtin_prompt_template_checksum_is_stable(client, auth_headers):
    from app.settings.prompts.templates import BUILTIN_PROMPTS

    response = client.get("/api/v1/prompts/templates", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["data"] == BUILTIN_PROMPTS
    payload = json.dumps(
        response.json()["data"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert len(BUILTIN_PROMPTS) == 8
    assert hashlib.sha256(payload).hexdigest() == (
        "59eba658462c269b993519349b7941290a8e1e5d229f827811e81c4c1dd3bc38"
    )


def test_prompt_openapi_contract_is_stable(client):
    schema = client.app.openapi()
    expected = {
        ("get", "/api/v1/prompts"): "list_prompts_api_v1_prompts_get",
        ("post", "/api/v1/prompts"): "create_prompt_api_v1_prompts_post",
        (
            "get",
            "/api/v1/prompts/by-domain/{domain}",
        ): "get_prompts_by_domain_api_v1_prompts_by_domain__domain__get",
        (
            "post",
            "/api/v1/prompts/generate-template",
        ): "generate_prompt_template_api_v1_prompts_generate_template_post",
        (
            "get",
            "/api/v1/prompts/templates",
        ): "get_builtin_templates_api_v1_prompts_templates_get",
        (
            "get",
            "/api/v1/prompts/{prompt_id}",
        ): "get_prompt_api_v1_prompts__prompt_id__get",
        (
            "put",
            "/api/v1/prompts/{prompt_id}",
        ): "update_prompt_api_v1_prompts__prompt_id__put",
        (
            "delete",
            "/api/v1/prompts/{prompt_id}",
        ): "delete_prompt_api_v1_prompts__prompt_id__delete",
    }

    for (method, path), operation_id in expected.items():
        operation = schema["paths"][path][method]
        assert operation["operationId"] == operation_id
        assert operation["tags"] == ["prompts"]


def test_create_prompt(client, auth_headers):
    r = client.post("/api/v1/prompts",
                    json={"name": "测试提示词", "domain": "供应链", "content": "提取实体..."},
                    headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["data"]["name"] == "测试提示词"

def test_list_prompts(client, auth_headers):
    client.post("/api/v1/prompts", json={"name": "P1", "domain": "供应链", "content": "content"}, headers=auth_headers)
    r = client.get("/api/v1/prompts", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()["data"]) >= 1

def test_list_prompts_by_domain(client, auth_headers):
    client.post("/api/v1/prompts", json={"name": "SC", "domain": "供应链", "content": "c"}, headers=auth_headers)
    client.post("/api/v1/prompts", json={"name": "FIN", "domain": "财务", "content": "c"}, headers=auth_headers)
    r = client.get("/api/v1/prompts?domain=供应链", headers=auth_headers)
    assert all(p["domain"] == "供应链" for p in r.json()["data"])

def test_update_prompt(client, auth_headers):
    r = client.post("/api/v1/prompts", json={"name": "P", "domain": "医疗", "content": "old"}, headers=auth_headers)
    pid = r.json()["data"]["id"]
    r2 = client.put(f"/api/v1/prompts/{pid}", json={"content": "new content"}, headers=auth_headers)
    assert r2.json()["data"]["content"] == "new content"

def test_delete_prompt(client, auth_headers):
    r = client.post("/api/v1/prompts", json={"name": "Del", "domain": "其他", "content": "c"}, headers=auth_headers)
    pid = r.json()["data"]["id"]
    r2 = client.delete(f"/api/v1/prompts/{pid}", headers=auth_headers)
    assert r2.status_code == 204
