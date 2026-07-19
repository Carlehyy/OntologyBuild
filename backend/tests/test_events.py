import hashlib
from datetime import datetime, timezone

from app.events import models as event_models
from app.events.models import EventAttachment, RegisteredEvent
from app.shared.config import settings


def _event(event_no: str, severity: str, recorded_at: datetime) -> RegisteredEvent:
    return RegisteredEvent(
        event_no=event_no,
        title=event_no,
        severity=severity,
        recorded_at=recorded_at,
        source_type=event_models.SOURCE_PLATFORM,
        status=event_models.STATUS_ACTIVE,
    )


def test_event_stats_returns_real_seven_day_shanghai_trend(
    client, auth_headers, db, monkeypatch,
):
    monkeypatch.setattr(
        "app.events.router._now_utc",
        lambda: datetime(2026, 7, 19, 3, 0, tzinfo=timezone.utc),
    )
    db.add_all([
        # UTC 7/18 16:30 = 上海 7/19 00:30，应计入上海“今日”。
        _event("EVT-TODAY-INFO", "info", datetime(2026, 7, 18, 16, 30)),
        _event("EVT-YESTERDAY-MEDIUM", "medium", datetime(2026, 7, 17, 16, 30)),
        # UTC 7/12 15:59 = 上海 7/12 23:59，已经超出最近 7 个自然日。
        _event("EVT-OUTSIDE-HIGH", "high", datetime(2026, 7, 12, 15, 59)),
    ])
    db.commit()

    response = client.get("/api/v2/events/stats/summary", headers=auth_headers)

    assert response.status_code == 200
    stats = response.json()["data"]
    assert stats["today"] == 1
    assert len(stats["trend7d"]) == 7
    assert [item["date"] for item in stats["trend7d"]] == [
        "2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16",
        "2026-07-17", "2026-07-18", "2026-07-19",
    ]
    by_day = {item["date"]: item for item in stats["trend7d"]}
    assert by_day["2026-07-19"]["bySeverity"]["info"] == 1
    assert by_day["2026-07-18"]["bySeverity"]["medium"] == 1
    assert sum(item["total"] for item in stats["trend7d"]) == 2


def test_ingest_keys_support_server_side_pagination_and_filters(client, auth_headers):
    created = []
    for name, source_system in [
        ("分页测试-MES-主密钥", "MES"),
        ("分页测试-CRM-主密钥", "CRM"),
        ("分页测试-MES-旧密钥", "MES-LEGACY"),
    ]:
        response = client.post(
            "/api/v2/events/ingest-keys",
            headers=auth_headers,
            json={"name": name, "allowedSourceSystem": source_system},
        )
        assert response.status_code == 201
        created.append(response.json()["data"])

    revoked = client.delete(
        f"/api/v2/events/ingest-keys/{created[2]['id']}",
        headers=auth_headers,
    )
    assert revoked.status_code == 200

    first_page = client.get(
        "/api/v2/events/ingest-keys",
        headers=auth_headers,
        params={"q": "分页测试", "page": 1, "page_size": 2},
    )
    assert first_page.status_code == 200
    page_data = first_page.json()["data"]
    assert page_data["total"] == 3
    assert page_data["page"] == 1
    assert page_data["pageSize"] == 2
    assert len(page_data["items"]) == 2

    second_page = client.get(
        "/api/v2/events/ingest-keys",
        headers=auth_headers,
        params={"q": "分页测试", "page": 2, "page_size": 2},
    )
    assert second_page.status_code == 200
    assert len(second_page.json()["data"]["items"]) == 1

    active_mes = client.get(
        "/api/v2/events/ingest-keys",
        headers=auth_headers,
        params={
            "q": "分页测试",
            "status": "active",
            "source_system": "MES",
            "page_size": 10,
        },
    )
    assert active_mes.status_code == 200
    active_data = active_mes.json()["data"]
    assert active_data["total"] == 1
    assert active_data["items"][0]["name"] == "分页测试-MES-主密钥"

    revoked_keys = client.get(
        "/api/v2/events/ingest-keys",
        headers=auth_headers,
        params={"q": "分页测试", "status": "revoked", "page_size": 10},
    )
    assert revoked_keys.status_code == 200
    revoked_data = revoked_keys.json()["data"]
    assert revoked_data["total"] == 1
    assert revoked_data["items"][0]["name"] == "分页测试-MES-旧密钥"


def test_event_attachment_streams_and_cleans_file_when_limit_exceeded(
    client, auth_headers, db, monkeypatch, tmp_path,
):
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    created = client.post(
        "/api/v2/events",
        headers=auth_headers,
        json={"title": "附件流式上传测试", "severity": "info"},
    )
    assert created.status_code == 201
    event_id = created.json()["data"]["id"]

    oversized = client.post(
        f"/api/v2/events/{event_id}/attachments",
        headers=auth_headers,
        files={"file": ("oversized.pdf", b"x" * (1024 * 1024 + 1), "application/pdf")},
    )
    assert oversized.status_code == 413
    assert oversized.json()["detail"] == "文件超过大小限制 1MB"
    assert db.query(EventAttachment).filter(EventAttachment.event_id == event_id).count() == 0
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]

    valid_content = b"stable-stream" * 4096
    uploaded = client.post(
        f"/api/v2/events/{event_id}/attachments",
        headers=auth_headers,
        files={"file": ("valid.pdf", valid_content, "application/pdf")},
    )
    assert uploaded.status_code == 201
    attachment = uploaded.json()["data"]
    assert attachment["fileSize"] == len(valid_content)
    assert attachment["sha256"] == hashlib.sha256(valid_content).hexdigest()
