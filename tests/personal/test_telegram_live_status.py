"""/status shows the live loop truthfully (digest + webhook level)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apps.personal.cron.live_health import LiveSummary
from apps.personal.telegram.digest import build_status_digest
from apps.personal.telegram.handler import process_webhook
from core.storage.memory import InMemoryStorage
from core.storage.records import FetchState


def _digest(live, now):
    return build_status_digest(
        latest_cgm_ts=None,
        latest_detection_ts=None,
        latest_alert_ts=None,
        latest_alert_delivery=None,
        now=now,
        live=live,
    )


def test_status_digest_live_section():
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    live = LiveSummary(
        last_cycle_at=now - timedelta(minutes=3),
        last_outcome="no_data",
        last_reading_at=now - timedelta(minutes=95),
        last_reading_age_min=95.0,
        last_fresh_at=now - timedelta(minutes=95),
        today_counts={"no_data": 40, "no_candidate": 3},
        cycle_stale=False,
        reading_stale=True,
    )
    text = _digest(live, now)
    assert "Live loop" in text
    assert "3 min ago" in text  # last cycle
    assert "no_data 40" in text and "no_candidate 3" in text
    assert "stale" in text.lower()


def test_status_digest_live_cycle_stale_warns():
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    live = LiveSummary(
        last_cycle_at=now - timedelta(hours=2),
        last_outcome="no_candidate",
        last_reading_at=now - timedelta(hours=2),
        last_reading_age_min=3.0,
        last_fresh_at=now - timedelta(hours=2),
        today_counts={},
        cycle_stale=True,
        reading_stale=False,
    )
    assert "worker may be down" in _digest(live, now).lower()


def test_status_digest_without_live_unchanged():
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    assert "Live loop" not in _digest(None, now)


def _headers():
    return {"x-telegram-bot-api-secret-token": "s3cret"}


def _update(text):
    return {"message": {"chat": {"id": 555}, "text": text}}


def _run_status(storage, config, now, monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "555")
    sent = []
    result = process_webhook(
        body=_update("/status"),
        headers=_headers(),
        storage_factory=lambda: storage,
        config=config,
        send=lambda c, t: sent.append((c, t)) or True,
        now=now,
    )
    return result, sent


def test_status_reports_live_loop_truthfully(default_config, monkeypatch):
    tz = ZoneInfo(default_config.timezone)
    now = datetime(2026, 10, 14, 12, 0, tzinfo=tz)
    storage = InMemoryStorage()
    storage.set_fetch_state(
        "live_cron",
        FetchState(
            source_id="live_cron",
            last_cursor=None,
            last_fetched_at=now - timedelta(hours=26),
            payload={
                "last_cycle_at": (now - timedelta(minutes=2)).isoformat(),
                "last_outcome": "no_data",
                "last_reading_at": (now - timedelta(hours=26)).isoformat(),
                "last_reading_age_min": 1560.0,
                "outcome_counts": {
                    "2026-10-14": {"no_data": 7},
                    "2026-10-13": {"no_data": 99},
                },
            },
            source_kind="pydexcom",
        ),
    )
    _, sent = _run_status(storage, default_config, now, monkeypatch)
    text = sent[0][1]
    assert "Live loop" in text
    assert "no_data 7" in text
    assert "99" not in text  # only today's counts
    assert "stale" in text.lower()


def test_status_survives_malformed_live_state(default_config, monkeypatch):
    now = datetime(2026, 10, 14, 12, 0, tzinfo=ZoneInfo(default_config.timezone))
    storage = InMemoryStorage()
    storage.set_fetch_state(
        "live_cron",
        FetchState(
            "live_cron",
            None,
            now,
            {"outcome_counts": "garbage", "last_cycle_at": "nope"},
            "pydexcom",
        ),
    )
    (status, body), sent = _run_status(storage, default_config, now, monkeypatch)
    assert status == 200 and body["replied"] is True


def test_status_without_live_row_still_replies(default_config, monkeypatch):
    now = datetime(2026, 10, 14, 12, 0, tzinfo=ZoneInfo(default_config.timezone))
    (status, body), sent = _run_status(InMemoryStorage(), default_config, now, monkeypatch)
    assert status == 200 and body["replied"] is True
    assert "Live loop" in sent[0][1]  # explicit "no heartbeat recorded"
