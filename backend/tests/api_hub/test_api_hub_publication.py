import json
from urllib.parse import parse_qsl

import pytest

from app.api_hub import publication


def test_inference_excludes_authentication_fields_without_hiding_business_fields():
    interface = {
        "query_params": [
            {"key": "author_id", "value": "42"},
            {"key": "authCode", "value": "hidden"},
            {"key": "signature", "value": "hidden"},
        ],
        "headers": [
            {"key": "X-Tenant-ID", "value": "tenant"},
            {"key": "X-Client-Credential", "value": "hidden"},
        ],
    }
    assert publication.infer_query_keys(interface) == ["author_id"]
    assert publication.infer_header_keys(interface, "X-API-Key") == ["X-Tenant-ID"]


def test_form_contract_merges_editable_values_and_keeps_sensitive_defaults():
    interface = {
        "body_type": "form",
        "body_content": "customer_id=C-001\npassword=platform-secret\npage=1",
    }
    keys = publication.infer_body_keys(interface)
    assert keys == ["customer_id", "page"]
    interface["proxy_body_keys"] = keys

    merged = publication.merge_caller_body(
        interface, b"customer_id=C-002&page=3"
    ).decode()
    assert dict(parse_qsl(merged)) == {
        "customer_id": "C-002",
        "password": "platform-secret",
        "page": "3",
    }
    assert dict(parse_qsl(publication.body_template(interface))) == {
        "customer_id": "C-001",
        "page": "1",
    }

    with pytest.raises(publication.PublicationBodyError, match="未开放"):
        publication.merge_caller_body(interface, b"password=caller-value")


def test_nested_json_contract_uses_partial_template():
    interface = {
        "body_type": "json",
        "body_content": json.dumps(
            {"profile": {"name": "Ada", "api_key": "secret"}, "tags": ["a"]}
        ),
    }
    assert publication.infer_body_keys(interface) == ["/profile/name", "/tags"]
    interface["proxy_body_keys"] = ["/profile/name", "/tags"]
    assert json.loads(publication.body_template(interface)) == {
        "profile": {"name": "Ada"},
        "tags": ["a"],
    }
