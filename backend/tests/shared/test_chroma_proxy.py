from __future__ import annotations

from types import SimpleNamespace

import app.shared.chroma_service as chroma_service
from app.shared.chroma_service import (
    _HttpxWithLoopbackBypass,
    _create_chroma_client,
)


def test_chroma_httpx_facade_adds_loopback_mounts() -> None:
    seen = {}

    def create_client(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return object()

    original = SimpleNamespace(Client=create_client, marker="preserved")
    facade = _HttpxWithLoopbackBypass(original, "http://127.0.0.2:8001")

    facade.Client(timeout=None)

    assert seen["kwargs"]["mounts"]["all://127.0.0.1"] is None
    assert seen["kwargs"]["mounts"]["all://localhost"] is None
    assert seen["kwargs"]["mounts"]["all://[::1]"] is None
    assert seen["kwargs"]["mounts"]["all://127.0.0.2"] is None
    assert facade.marker == "preserved"


def test_chroma_loopback_patch_is_scoped_and_restored(monkeypatch) -> None:
    from chromadb.api import fastapi as chroma_fastapi

    original_httpx = chroma_fastapi.httpx
    created = object()
    seen = {}

    def create_client(*, host, port):
        seen["host"] = host
        seen["port"] = port
        seen["httpx"] = chroma_fastapi.httpx
        return created

    monkeypatch.setattr(chroma_service.chromadb, "HttpClient", create_client)

    assert _create_chroma_client("127.0.0.2", 8001) is created
    assert isinstance(seen["httpx"], _HttpxWithLoopbackBypass)
    assert seen["host"] == "127.0.0.2"
    assert seen["port"] == 8001
    assert chroma_fastapi.httpx is original_httpx
