import pytest

from app import dev_server


def test_dev_server_uses_local_settings(monkeypatch: pytest.MonkeyPatch):
    called = {}

    monkeypatch.setattr(dev_server.settings, "environment", "development")
    monkeypatch.setattr(
        dev_server.settings,
        "local_backend_host",
        "127.0.0.1",
    )
    monkeypatch.setattr(dev_server.settings, "local_backend_port", 8123)
    monkeypatch.setattr(
        dev_server.uvicorn,
        "run",
        lambda *args, **kwargs: called.update(
            {"args": args, "kwargs": kwargs}
        ),
    )

    dev_server.main()

    assert called == {
        "args": ("app.main:app",),
        "kwargs": {
            "host": "127.0.0.1",
            "port": 8123,
            "reload": True,
        },
    }


def test_dev_server_refuses_production(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dev_server.settings, "environment", "production")

    with pytest.raises(RuntimeError, match="仅用于本地开发"):
        dev_server.main()
