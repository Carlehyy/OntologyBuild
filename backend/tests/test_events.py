from datetime import datetime, timezone

from app.events import models as event_models
from app.events.models import RegisteredEvent


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
