"""Tests for the Telegram webhook orchestration (no network)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from apps.personal.telegram.handler import (
    is_allowed_chat,
    process_webhook,
    verify_secret,
)
from core.storage.memory import InMemoryStorage
from core.storage.records import AlertRecord, DetectionResult


# ── auth helpers ─────────────────────────────────────────────────────────


def test_verify_secret_constant_time_match():
    assert verify_secret({"X-Telegram-Bot-Api-Secret-Token": "s3cret"}, "s3cret")
    assert not verify_secret({"X-Telegram-Bot-Api-Secret-Token": "wrong"}, "s3cret")


def test_verify_secret_empty_expected_rejected():
    assert not verify_secret({"x-telegram-bot-api-secret-token": ""}, "")
    assert not verify_secret({}, "s3cret")


def test_is_allowed_chat():
    assert is_allowed_chat("123", "123")
    assert not is_allowed_chat("999", "123")
    assert not is_allowed_chat(None, "123")
    assert not is_allowed_chat("123", "")


# ── full webhook ─────────────────────────────────────────────────────────

TZ = "America/Los_Angeles"


def _storage_with_day(day_local: datetime) -> InMemoryStorage:
    s = InMemoryStorage()
    # Two CGM readings on the target local day, one in range, one high.
    s.upsert_table(
        "cgm",
        pd.DataFrame(
            {
                "timestamp": [
                    day_local.replace(hour=8),
                    day_local.replace(hour=9),
                ],
                "bg_mgdl": [120.0, 250.0],
                "pump_serial": ["p1", "p1"],
                "seqnum": [1, 2],
            }
        ),
    )
    return s


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "topsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "555")


def _config(default_config):
    return default_config


def _headers(secret="topsecret"):
    return {"X-Telegram-Bot-Api-Secret-Token": secret}


def _update(text, chat_id=555):
    return {"message": {"chat": {"id": chat_id}, "text": text}}


def test_bad_secret_returns_401(default_config):
    sent = []
    status, body = process_webhook(
        body=_update("/today"),
        headers=_headers("nope"),
        storage_factory=InMemoryStorage,
        config=default_config,
        send=lambda c, t: sent.append((c, t)) or True,
    )
    assert status == 401
    assert sent == []


def test_wrong_chat_silently_ignored(default_config):
    sent = []
    status, body = process_webhook(
        body=_update("/today", chat_id=999),
        headers=_headers(),
        storage_factory=InMemoryStorage,
        config=default_config,
        send=lambda c, t: sent.append((c, t)) or True,
    )
    assert status == 200
    assert body["replied"] is False
    assert sent == []


def test_storage_not_opened_for_unauthorized(default_config):
    # Neither a bad secret nor a wrong chat should open a connection.
    calls = []

    def factory():
        calls.append(1)
        return InMemoryStorage()

    process_webhook(
        body=_update("/today"),
        headers=_headers("nope"),
        storage_factory=factory,
        config=default_config,
        send=lambda c, t: True,
    )
    process_webhook(
        body=_update("/today", chat_id=999),
        headers=_headers(),
        storage_factory=factory,
        config=default_config,
        send=lambda c, t: True,
    )
    assert calls == []


def test_today_dispatch_sends_digest(default_config):
    tz = ZoneInfo(default_config.timezone)
    now = datetime(2026, 4, 14, 12, 0, tzinfo=tz)
    storage = _storage_with_day(now)
    sent = []
    status, body = process_webhook(
        body=_update("/today"),
        headers=_headers(),
        storage_factory=lambda: storage,
        config=default_config,
        send=lambda c, t: sent.append((c, t)) or True,
        now=now,
    )
    assert status == 200 and body["replied"] is True
    chat, text = sent[0]
    assert chat == "555"
    assert "Today" in text and "50%" in text  # 1 of 2 readings in range


def test_unknown_command_sends_help(default_config):
    now = datetime(2026, 4, 14, 12, 0, tzinfo=ZoneInfo(default_config.timezone))
    sent = []
    process_webhook(
        body=_update("/wat"),
        headers=_headers(),
        storage_factory=InMemoryStorage,
        config=default_config,
        send=lambda c, t: sent.append((c, t)) or True,
        now=now,
    )
    assert "/today" in sent[0][1]  # help text


def test_status_dispatch(default_config):
    tz = ZoneInfo(default_config.timezone)
    now = datetime(2026, 4, 14, 12, 0, tzinfo=tz)
    storage = _storage_with_day(now)
    storage.record_detection_result(
        DetectionResult(
            kind="meal_rise",
            anchor_timestamp=now,
            payload={},
            created_at=now,
        )
    )
    storage.record_alert(
        AlertRecord(
            id=None,
            alert_kind="meal_rise",
            event_ref="e1",
            sent_at=now,
            payload={},
            pump_serial="p1",
            delivery="sent",
        )
    )
    sent = []
    process_webhook(
        body=_update("/status"),
        headers=_headers(),
        storage_factory=lambda: storage,
        config=default_config,
        send=lambda c, t: sent.append((c, t)) or True,
        now=now,
    )
    text = sent[0][1]
    assert "Status" in text and "Last detection" in text


class _RaisingStorage(InMemoryStorage):
    """Every read raises — simulates a storage outage."""

    def read_table(self, *a, **k):  # type: ignore[override]
        raise RuntimeError("storage down")

    def read_all_table(self, *a, **k):  # type: ignore[override]
        raise RuntimeError("storage down")

    def recent_alerts(self, *a, **k):  # type: ignore[override]
        raise RuntimeError("storage down")

    def list_detection_results(self, *a, **k):  # type: ignore[override]
        raise RuntimeError("storage down")


def test_storage_read_failure_logs_warning(default_config, caplog):
    """A storage outage during a command must leave a WARNING trace rather
    than failing silently (the reply still degrades gracefully)."""
    tz = ZoneInfo(default_config.timezone)
    now = datetime(2026, 4, 14, 12, 0, tzinfo=tz)
    sent = []
    with caplog.at_level("WARNING", logger="apps.personal.telegram.handler"):
        status, body = process_webhook(
            body=_update("/today"),
            headers=_headers(),
            storage_factory=_RaisingStorage,
            config=default_config,
            send=lambda c, t: sent.append((c, t)) or True,
            now=now,
        )
    # Command still completes (degrades to empty data), and a warning is logged.
    assert status == 200 and body["replied"] is True
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, "expected a swallowed-storage-error warning"
    assert any("read_table" in r.getMessage() for r in warnings)


def test_status_read_failure_logs_warning(default_config, caplog):
    tz = ZoneInfo(default_config.timezone)
    now = datetime(2026, 4, 14, 12, 0, tzinfo=tz)
    sent = []
    with caplog.at_level("WARNING", logger="apps.personal.telegram.handler"):
        process_webhook(
            body=_update("/status"),
            headers=_headers(),
            storage_factory=_RaisingStorage,
            config=default_config,
            send=lambda c, t: sent.append((c, t)) or True,
            now=now,
        )
    messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("read_all_table(cgm)" in m for m in messages)
    assert any("list_detection_results" in m for m in messages)
