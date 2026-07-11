import httpx
import pytest

from app.settings.workflows.n8n_client import N8nApiError, N8nClient


class _StubHttpClient:
    def __init__(self, response: httpx.Response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def request(self, *_args, **_kwargs):
        return self.response


def test_empty_gateway_error_keeps_readable_reason(monkeypatch):
    response = httpx.Response(
        502,
        content=b"",
        request=httpx.Request("GET", "http://n8n.example/api/v1/workflows"),
    )
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **_kwargs: _StubHttpClient(response),
    )

    client = N8nClient("http://n8n.example/api/v1", "test-key")
    with pytest.raises(N8nApiError) as caught:
        client.list_workflows(limit=1)

    assert caught.value.status_code == 502
    assert caught.value.message == "Bad Gateway"
    assert str(caught.value) == "HTTP 502: Bad Gateway"


def test_nested_json_error_message_is_extracted(monkeypatch):
    response = httpx.Response(
        400,
        json={"error": {"message": "workflow is invalid"}},
        request=httpx.Request("PUT", "http://n8n.example/api/v1/workflows/1"),
    )
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **_kwargs: _StubHttpClient(response),
    )

    client = N8nClient("http://n8n.example/api/v1", "test-key")
    with pytest.raises(N8nApiError) as caught:
        client.update_workflow("1", {"name": "test"})

    assert caught.value.message == "workflow is invalid"
