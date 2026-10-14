"""Regression: pydexcom returns tz-AWARE datetimes carrying the Share ``DT`` offset.

``fetch_dexcom_cgm`` used ``r.datetime.replace(tzinfo=utc)``, which relabels the
wall clock as UTC and shifts every reading by the offset (a Pacific account:
a 5-minute-old reading looked ~425 minutes old), so the freshness guard skipped
EVERY cycle and the meal-rise alert could never fire.

These tests use REAL ``pydexcom.glucose_reading.GlucoseReading`` objects built
from Share-style JSON (``"DT": "Date(<epoch ms><+/-HHMM>)"``), not the
hand-built naive ``MockGlucoseReading``. Nothing touches Dexcom, Telegram or
Supabase: Dexcom is a fake client, Telegram is a fake sender, storage is
``InMemoryStorage``, and credentials are fake values set per test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydexcom.glucose_reading import GlucoseReading

from apps.personal.cron import detect_meal_rise as cron
from core.storage.memory import InMemoryStorage
from detection.config import load_config

UTC = timezone.utc
# 12:00 PDT == 19:00 UTC (meal window 11-14).
LUNCH_UTC = datetime(2026, 10, 14, 19, 0, tzinfo=UTC)
OFFSETS = ["-0700", "-0400", "+0000"]


def _reading(value: int, when_utc: datetime, offset: str) -> GlucoseReading:
    ms = int(when_utc.timestamp() * 1000)
    return GlucoseReading({"Value": value, "Trend": "Flat", "DT": f"Date({ms}{offset})"})


def _series(newest_utc: datetime, offset: str, values_newest_first) -> list[GlucoseReading]:
    return [
        _reading(v, newest_utc - timedelta(minutes=5 * i), offset)
        for i, v in enumerate(values_newest_first)
    ]


# newest first: 120 -> 185 over 30 min, then flat lead-in
RISE = [185, 174, 163, 153, 142, 131, 120, 120, 120, 120, 120, 120]
FLAT = [120] * 12


class FakeDexcom:
    readings: list[GlucoseReading] = []

    def __init__(self, username=None, password=None, region=None):
        pass

    def get_glucose_readings(self, minutes=1440, max_count=288):
        return list(type(self).readings)[:max_count]


class Sender:
    def __init__(self):
        self.messages: list[str] = []

    def __call__(self, msg: str) -> bool:
        self.messages.append(msg)
        return True


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    # The real .env is discoverable from a worktree; override with fakes.
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("DEXCOM_USERNAME", "fake_user")
    monkeypatch.setenv("DEXCOM_PASSWORD", "fake_pass")
    monkeypatch.setattr(cron, "Dexcom", FakeDexcom)

    def _boom(*a, **k):
        raise AssertionError("real I/O attempted")

    monkeypatch.setattr(cron, "send_telegram_message", _boom)
    monkeypatch.setattr(cron, "get_storage_connection", _boom)


@pytest.fixture
def config():
    return load_config()


def _run(storage, config, readings, now, sender):
    FakeDexcom.readings = readings
    return cron.run_cron(now=now, storage=storage, config=config, send=sender)


@pytest.mark.parametrize("offset", OFFSETS)
def test_fetch_preserves_true_instant(config, offset):
    FakeDexcom.readings = _series(LUNCH_UTC, offset, RISE)
    df = cron.fetch_dexcom_cgm(config.meal_rise, config.timezone)
    assert df["timestamp"].iloc[-1].to_pydatetime().astimezone(UTC) == LUNCH_UTC
    assert len(df) == len(RISE)


def test_fetch_still_handles_naive_datetimes(config):
    """Defensive: a naive datetime is treated as UTC (legacy mock shape)."""

    class Naive:
        value = 120
        datetime = LUNCH_UTC.replace(tzinfo=None)

    FakeDexcom.readings = [Naive()]
    df = cron.fetch_dexcom_cgm(config.meal_rise, config.timezone)
    assert df["timestamp"].iloc[-1].to_pydatetime().astimezone(UTC) == LUNCH_UTC


@pytest.mark.parametrize("offset", OFFSETS)
def test_reading_age_is_correct_in_heartbeat(config, offset):
    storage = InMemoryStorage()
    now = LUNCH_UTC + timedelta(minutes=5)  # truly 5.0 min old
    _run(storage, config, _series(LUNCH_UTC, offset, FLAT), now, Sender())
    st = storage.get_fetch_state("live_cron")
    assert st.payload["last_reading_age_min"] == pytest.approx(5.0)
    assert st.payload["last_outcome"] == "no_candidate"  # NOT "stale"
    assert st.last_fetched_at == now


@pytest.mark.parametrize("offset", OFFSETS)
def test_lunch_rise_fires_end_to_end(config, offset):
    storage = InMemoryStorage()
    sender = Sender()
    now = LUNCH_UTC + timedelta(seconds=90)
    assert _run(storage, config, _series(LUNCH_UTC, offset, RISE), now, sender) == 0
    assert len(sender.messages) == 1
    assert "Fast glucose rise" in sender.messages[0]
    results = storage.list_detection_results(kind="meal_rise")
    assert len(results) == 1 and results[0].payload["telegram_sent"] is True
    assert len(storage.recent_alerts("meal_rise", timedelta(hours=1))) == 1
    st = storage.get_fetch_state("live_cron")
    assert st.payload["last_outcome"] == "alert_sent"
    assert st.last_fetched_at == now


@pytest.mark.parametrize("offset", OFFSETS)
def test_no_false_stale_alert_with_fresh_readings_over_a_day(config, offset):
    """A healthy G7 (a reading every 5 min, ~90 s Share lag) for 24h of
    5-minute cycles: zero stale alerts, heartbeat advances every cycle."""
    storage = InMemoryStorage()
    sender = Sender()
    start = LUNCH_UTC
    for step in range(24 * 12):
        newest = start + timedelta(minutes=5 * step)
        now = newest + timedelta(seconds=90)
        _run(storage, config, _series(newest, offset, FLAT), now, sender)
        st = storage.get_fetch_state("live_cron")
        assert st.last_fetched_at == now
    assert sender.messages == []
    p = storage.get_fetch_state("live_cron").payload
    assert "stale_since" not in p and "stale_alert_sent_at" not in p
    assert sum(sum(d.values()) for d in p["outcome_counts"].values()) <= 24 * 12
    assert all(set(d) <= {"no_candidate"} for d in p["outcome_counts"].values())
