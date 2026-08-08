import copy


def _formal_url(ontology_id: str) -> str:
    return f"/api/v2/formal/ontologies/{ontology_id}"


def _seed_portable_structure(client, auth_headers, ontology_id: str) -> dict:
    payload = {
        "objectTypes": [
            {
                "id": "ot-order",
                "name": "Order",
                "displayName": "订单",
                "primaryKey": "order_no",
                "properties": [
                    {
                        "id": "p-order-no",
                        "name": "order_no",
                        "displayName": "订单号",
                        "type": "string",
                        "required": True,
                    },
                    {
                        "id": "p-total",
                        "name": "total",
                        "displayName": "订单总额",
                        "type": "number",
                        "required": False,
                        "source": "computed",
                        "computed": True,
                        "functionId": "fn-total",
                        "dataBinding": {"mappingId": "source-only-mapping"},
                    },
                    {
                        "id": "p-supplier",
                        "name": "supplier_id",
                        "displayName": "供应商",
                        "type": "reference",
                        "required": False,
                        "referenceType": "ot-supplier",
                    },
                ],
                "positionX": 20,
                "positionY": 40,
            },
            {
                "id": "ot-supplier",
                "name": "Supplier",
                "displayName": "供应商",
                "primaryKey": "supplier_no",
                "properties": [
                    {
                        "id": "p-supplier-no",
                        "name": "supplier_no",
                        "displayName": "供应商编号",
                        "type": "string",
                        "required": True,
                    },
                ],
                "positionX": 360,
                "positionY": 40,
            },
        ],
        "linkTypes": [
            {
                "id": "lt-supplied-by",
                "name": "suppliedBy",
                "displayName": "由供应商供货",
                "sourceObjectTypeId": "ot-order",
                "targetObjectTypeId": "ot-supplier",
                "cardinality": "many-to-one",
                "properties": [],
            },
        ],
        "actions": [
            {
                "id": "act-review",
                "name": "reviewOrder",
                "displayName": "复核订单",
                "objectTypeId": "ot-order",
                "parameters": [],
                "validationFunctionId": "fn-validate",
                "requiresApproval": True,
                "rules": [
                    {
                        "id": "rule-link",
                        "type": "create_link",
                        "name": "关联供应商",
                        "enabled": True,
                        "order": 1,
                        "config": {
                            "type": "create_link",
                            "linkTypeId": "lt-supplied-by",
                            "targetSource": "parameter",
                            "targetValue": "supplier_id",
                        },
                    },
                    {
                        "id": "rule-function",
                        "type": "function",
                        "name": "计算总额",
                        "enabled": True,
                        "order": 2,
                        "config": {
                            "type": "function",
                            "functionId": "fn-total",
                            "parameterMappings": [],
                        },
                    },
                ],
            },
        ],
        "functions": [
            {
                "id": "fn-total",
                "name": "calculateTotal",
                "displayName": "计算订单总额",
                "functionType": "object",
                "language": "expression",
                "targetObjectTypeId": "ot-order",
                "parameters": [],
                "returnType": "number",
                "body": "0",
                "enabled": True,
            },
            {
                "id": "fn-validate",
                "name": "validateReview",
                "displayName": "校验订单复核",
                "functionType": "action_validation",
                "language": "expression",
                "targetActionId": "act-review",
                "parameters": [],
                "returnType": "validation_result",
                "body": "true",
                "enabled": True,
            },
        ],
        "instances": [
            {
                "id": "order-1",
                "objectTypeId": "ot-order",
                "properties": {"order_no": "PO-001", "supplier_id": "SUP-001"},
                "computed": {"total": 1200},
                "source": "manual",
            },
            {
                "id": "supplier-1",
                "objectTypeId": "ot-supplier",
                "properties": {"supplier_no": "SUP-001"},
                "computed": {},
                "source": "manual",
            },
        ],
        "linkInstances": [
            {
                "id": "link-1",
                "linkTypeId": "lt-supplied-by",
                "sourceObjectId": "order-1",
                "targetObjectId": "supplier-1",
                "properties": {},
            },
        ],
    }
    response = client.put(
        f"{_formal_url(ontology_id)}/full",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    return payload


def _export(client, auth_headers, ontology_id: str) -> tuple[dict, object]:
    response = client.get(
        f"/api/v1/ontologies/{ontology_id}/export",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    return response.json(), response


def test_export_json_contains_current_formal_structure_only(client, auth_headers, ontology):
    ontology_id = ontology["id"]
    _seed_portable_structure(client, auth_headers, ontology_id)

    package, response = _export(client, auth_headers, ontology_id)

    assert response.headers["content-type"].startswith("application/json")
    assert ".json" in response.headers["content-disposition"]
    assert package["format"] == "ontology-structure"
    assert package["formatVersion"] == 1
    assert package["ontology"]["id"] == ontology_id
    assert package["ontology"]["sourceVersion"] == ontology["version"]
    assert len(package["structure"]["objectTypes"]) == 2
    assert len(package["structure"]["linkTypes"]) == 1
    assert len(package["structure"]["actions"]) == 1
    assert len(package["structure"]["functions"]) == 2
    exported_order = next(
        item for item in package["structure"]["objectTypes"] if item["name"] == "Order"
    )
    exported_total = next(
        item for item in exported_order["properties"] if item["name"] == "total"
    )
    assert "dataBinding" not in exported_total
    assert "instances" not in package["structure"]
    assert "linkInstances" not in package["structure"]
    assert "executionLogs" not in package["structure"]


def test_export_rejects_legacy_non_json_format(client, auth_headers, ontology):
    response = client.get(
        f"/api/v1/ontologies/{ontology['id']}/export?format=ttl",
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_export_import_round_trip_remaps_references_and_publishes_v0(
    client, auth_headers, ontology,
):
    source_id = ontology["id"]
    _seed_portable_structure(client, auth_headers, source_id)
    package, _ = _export(client, auth_headers, source_id)

    response = client.post(
        "/api/v1/ontologies/import",
        json=package,
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    imported = response.json()["data"]
    imported_id = imported["ontology"]["id"]
    assert imported_id != source_id
    assert imported["ontology"]["status"] == "published"
    assert imported["ontology"]["version"] == "v0"
    assert imported["ontology"]["current_release_id"] == imported["version"]["id"]
    assert imported["ontology"]["current_release_version"] == "v0"
    assert imported["version"]["version_number"] == "v0"
    assert imported["version"]["node_kind"] == "release"
    assert imported["version"]["lifecycle_status"] == "released"
    assert len(imported["version"]["snapshot_hash"]) == 64
    assert imported["version"]["published_at"]
    assert imported["counts"] == {
        "objectTypes": 2,
        "linkTypes": 1,
        "actions": 1,
        "functions": 2,
    }

    full = client.get(
        f"{_formal_url(imported_id)}/full",
        headers=auth_headers,
    ).json()["data"]
    assert full["instances"] == []
    assert full["linkInstances"] == []

    objects = {item["name"]: item for item in full["objectTypes"]}
    functions = {item["name"]: item for item in full["functions"]}
    action = full["actions"][0]
    link = full["linkTypes"][0]

    assert objects["Order"]["id"] != "ot-order"
    assert link["sourceObjectTypeId"] == objects["Order"]["id"]
    assert link["targetObjectTypeId"] == objects["Supplier"]["id"]
    assert functions["calculateTotal"]["targetObjectTypeId"] == objects["Order"]["id"]
    assert functions["validateReview"]["targetActionId"] == action["id"]
    assert action["objectTypeId"] == objects["Order"]["id"]
    assert action["validationFunctionId"] == functions["validateReview"]["id"]
    assert action["rules"][0]["config"]["linkTypeId"] == link["id"]
    assert action["rules"][1]["config"]["functionId"] == functions["calculateTotal"]["id"]

    order_properties = {item["name"]: item for item in objects["Order"]["properties"]}
    assert order_properties["total"]["functionId"] == functions["calculateTotal"]["id"]
    assert "dataBinding" not in order_properties["total"]
    assert order_properties["supplier_id"]["referenceType"] == objects["Supplier"]["id"]

    versions = client.get(
        f"/api/v2/ontologies/{imported_id}/versions",
        headers=auth_headers,
    ).json()
    assert versions["total"] == 1
    assert versions["current_release_id"] == imported["version"]["id"]
    assert versions["current_release_version"] == "v0"
    assert versions["data"][0]["version_number"] == "v0"
    assert versions["data"][0]["node_kind"] == "release"
    assert versions["data"][0]["lifecycle_status"] == "released"
    assert versions["data"][0]["snapshot_hash"] == imported["version"]["snapshot_hash"]

    project = client.get(
        f"/api/v1/ontologies/{imported_id}", headers=auth_headers,
    ).json()["data"]
    assert project["current_release_id"] == imported["version"]["id"]
    assert project["current_release_version"] == "v0"

    detail = client.get(
        f"/api/v2/ontologies/{imported_id}/versions/{imported['version']['id']}",
        headers=auth_headers,
    ).json()["data"]
    assert detail["snapshot"]["formal"]["objectTypes"]
    assert "instances" not in detail["snapshot"]["formal"]
    assert "linkInstances" not in detail["snapshot"]["formal"]


def test_import_auto_creates_missing_domain(client, auth_headers, ontology):
    _seed_portable_structure(client, auth_headers, ontology["id"])
    package, _ = _export(client, auth_headers, ontology["id"])
    package["ontology"]["domain"] = "跨境风险"

    response = client.post(
        "/api/v1/ontologies/import",
        json=package,
        headers=auth_headers,
    )

    assert response.status_code == 201, response.text
    assert response.json()["data"]["ontology"]["domain"] == "跨境风险"
    domains = client.get(
        "/api/v1/domains",
        headers=auth_headers,
    ).json()["data"]
    imported_domain = next(item for item in domains if item["name"] == "跨境风险")
    assert imported_domain["description"] == "由本体本地导入自动创建"


def test_import_rejects_dangling_structure_without_partial_project(
    client, auth_headers, ontology,
):
    source_id = ontology["id"]
    _seed_portable_structure(client, auth_headers, source_id)
    package, _ = _export(client, auth_headers, source_id)
    broken = copy.deepcopy(package)
    broken["ontology"]["domain"] = "校验失败不应创建"
    broken["structure"]["actions"][0]["rules"][0]["config"]["linkTypeId"] = "missing-link"

    before = client.get(
        "/api/v1/ontologies?page_size=1000",
        headers=auth_headers,
    ).json()["data"]["total"]
    response = client.post(
        "/api/v1/ontologies/import",
        json=broken,
        headers=auth_headers,
    )
    after = client.get(
        "/api/v1/ontologies?page_size=1000",
        headers=auth_headers,
    ).json()["data"]["total"]

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ontology_import_validation_failed"
    assert after == before
    domains = client.get(
        "/api/v1/domains",
        headers=auth_headers,
    ).json()["data"]
    assert "校验失败不应创建" not in {item["name"] for item in domains}


def test_import_rejects_runtime_data_outside_structure_contract(
    client, auth_headers, ontology,
):
    _seed_portable_structure(client, auth_headers, ontology["id"])
    package, _ = _export(client, auth_headers, ontology["id"])
    package["structure"]["instances"] = []

    response = client.post(
        "/api/v1/ontologies/import",
        json=package,
        headers=auth_headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


def test_importing_same_package_twice_uses_distinct_names_and_ids(
    client, auth_headers, ontology,
):
    _seed_portable_structure(client, auth_headers, ontology["id"])
    package, _ = _export(client, auth_headers, ontology["id"])

    first = client.post(
        "/api/v1/ontologies/import", json=package, headers=auth_headers,
    )
    second = client.post(
        "/api/v1/ontologies/import", json=package, headers=auth_headers,
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    first_ontology = first.json()["data"]["ontology"]
    second_ontology = second.json()["data"]["ontology"]
    assert first_ontology["id"] != second_ontology["id"]
    assert first_ontology["name"].endswith("（导入）")
    assert second_ontology["name"].endswith("（导入 2）")
