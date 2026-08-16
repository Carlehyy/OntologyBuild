"""时序推演模板回归 — 在进程内真实执行模板脚本（statsmodels 真实依赖）。"""
from __future__ import annotations

import contextlib
import io
import uuid

import pytest

from app.auth.models import User
from app.services.auth_service import hash_password
from app.world_model import service

BASE = "/api/v2/world-model"


def _make_user(db, username: str, role: str) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=username,
        email=f"{username}@test.com",
        password_hash=hash_password("test123"),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client, username: str) -> dict:
    r = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "test123"},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


@pytest.fixture
def custom_headers(client, db):
    _make_user(db, "ts_custom", "custom")
    return _login(client, "ts_custom")


def _run_template_script(test_input: dict):
    """在进程内执行模板脚本（含调试收尾注入），返回 simulate 的 payload。"""
    template = service.get_time_series_template()
    code = service._build_debug_code(template.script, test_input)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exec(compile(code, "<world-model-ts-template>", "exec"), {})
    from app.data_channel.pipelines.python_engine.client import extract_payload

    return extract_payload(buffer.getvalue())


def test_template_endpoint_returns_script_and_defaults(client, auth_headers):
    r = client.get(f"{BASE}/templates/time-series", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["key"] == "time-series"
    assert "simulate" in data["script"]
    assert "statsmodels" in data["script"]
    series = data["test_input"]["context"]["series"]
    assert len(series) == 36
    assert data["test_input"]["context"]["period"] == 12
    assert data["test_input"]["horizon"] == 6


def test_template_endpoint_requires_menu(client, custom_headers):
    r = client.get(f"{BASE}/templates/time-series", headers=custom_headers)
    assert r.status_code == 403


def test_template_runs_seasonal_forecast_in_process():
    template = service.get_time_series_template()
    payload = _run_template_script(template.test_input)

    assert payload["model_summary"]["method"] in ("ARIMA", "SARIMA")
    assert len(payload["trajectory"]) == 6
    assert 0.0 <= payload["confidence"] <= 1.0
    assert "外推 6 步" in payload["boundary"]
    summary = payload["model_summary"]
    assert summary["order"][1] >= 0
    assert isinstance(summary["aic"], float)
    # 确定性生成序列，结果必须稳定可复现
    assert payload["trajectory"][0] == pytest.approx(
        _run_template_script(template.test_input)["trajectory"][0])


def test_template_applies_action_deltas():
    template = service.get_time_series_template()
    base = _run_template_script(template.test_input)["trajectory"]
    payload = _run_template_script({
        **template.test_input,
        "actions": [{"step": 0, "delta": 10.0}],
    })
    assert payload["trajectory"][0] == pytest.approx(base[0] + 10.0)
    assert payload["trajectory"][1] == pytest.approx(base[1])


def test_template_short_series_returns_explanation():
    payload = _run_template_script({
        "context": {"series": [1.0, 2.0, 3.0], "period": 12},
        "actions": [],
        "horizon": 3,
    })
    assert payload["trajectory"] == []
    assert payload["model_summary"] is None
    assert "series" in payload["boundary"]
