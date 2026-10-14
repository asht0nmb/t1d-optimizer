"""Live-loop observability: truthful heartbeat, stale-CGM alert, outcome log.

Everything here runs against ``InMemoryStorage`` with an injected fake Dexcom
fetcher and a fake Telegram sender. The repo's real ``.env`` is discoverable
from a worktree (``load_dotenv`` walks parent dirs), so an autouse fixture
scrubs credentials and makes every real I/O entry point raise.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from apps.personal.cron import detect_meal_rise as cron
from apps.personal.cron.live_health import (
    LiveHealthConfig,
    summarize_live_state,
)
from core.storage.memory import InMemoryStorage
from core.storage.records import FetchState
from detection.config import load_config

UTC = timezone.utc
NOW = datetime(2026, 10, 14, 20, 0, tzinfo=UTC)  # 13:00 PDT (a meal window)


@pytest.fixture(autouse=True)
def _no_real_io(monkeypatch):
    for var in (
        "SUPABASE_DB_URL",
        "DEXCOM_USERNAME",
        "DEXCOM_PASSWORD",
        "DEXCOM_OUS",
        "TCONNECT_EMAIL",
        "TCONNECT_PASSWORD",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "CRON_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)

    def _boom(*a, **k):
        raise AssertionError("real I/O attempted in a live_health test")

    monkeypatch.setattr(cron, "Dexcom", _boom)
    monkeypatch.setattr(cron, "send_telegram_message", _boom)
    monkeypatch.setattr(cron, "get_storage_connection", _boom)


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def storage():
    return InMemoryStorage()


class Sender:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.messages: list[str] = []

    def __call__(self, msg: str) -> bool:
        self.messages.append(msg)
        return self.ok


def _cgm(values_newest_first, newest_at):
    rows = [
        {
            "timestamp": pd.Timestamp(newest_at - timedelta(minutes=5 * i)).tz_convert(
                "America/Los_Angeles"
            ),
            "bg_mgdl": v,
        }
        for i, v in enumerate(values_newest_first)
    ]
    return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)


def _empty():
    return pd.DataFrame(columns=["timestamp", "bg_mgdl"])


def _run(storage, config, cgm, sender, now=NOW):
    return cron.run_cron(
        now=now,
        storage=storage,
        config=config,
        fetch_cgm=lambda: cgm,
        send=sender,
    )


def _flat(newest_at=NOW - timedelta(minutes=2), n=10):
    return _cgm([120] * n, newest_at)


def _state(storage):
    st = storage.get_fetch_state("live_cron")
    assert st is not None
    return st


# ── heartbeat semantics ──────────────────────────────────────────────────


def test_fresh_cycle_advances_last_synced_and_records_meta(storage, config):
    s = Sender()
    assert _run(storage, config, _flat(), s) == 0
    st = _state(storage)
    assert st.last_fetched_at == NOW
    p = st.payload
    assert p["last_outcome"] == "no_candidate"
    assert p["last_cycle_at"] == NOW.isoformat()
    assert p["last_reading_age_min"] == pytest.approx(2.0)
    assert "last_reading_at" in p
    assert "last_cursor" not in p
    json.dumps(p)  # must survive strict JSON (psycopg2 Json) with no default=
    assert s.messages == []


def test_no_data_does_not_advance_last_synced(storage, config):
    s = Sender()
    _run(storage, config, _flat(), s, now=NOW)
    later = NOW + timedelta(minutes=5)
    assert _run(storage, config, _empty(), s, now=later) == 0
    st = _state(storage)
    assert st.last_fetched_at == NOW  # carried forward, not advanced
    assert st.payload["last_outcome"] == "no_data"
    assert st.payload["last_cycle_at"] == later.isoformat()
    # last known reading info is carried forward, not nulled
    assert st.payload["last_reading_at"] is not None


def test_stale_reading_does_not_advance_last_synced(storage, config):
    s = Sender()
    _run(storage, config, _flat(), s, now=NOW)
    later = NOW + timedelta(minutes=30)  # newest reading now ~32 min old
    _run(storage, config, _flat(), s, now=later)
    st = _state(storage)
    assert st.last_fetched_at == NOW
    assert st.payload["last_outcome"] == "stale"
    assert st.payload["last_reading_age_min"] == pytest.approx(32.0)


def test_first_ever_no_data_leaves_last_synced_none(storage, config):
    _run(storage, config, _empty(), Sender())
    st = _state(storage)
    assert st.last_fetched_at is None
    assert st.payload["last_outcome"] == "no_data"


def test_insufficient_window_outcome(storage, config):
    # Only 2 samples < min_samples.
    _run(storage, config, _cgm([120, 118], NOW - timedelta(minutes=1)), Sender())
    assert _state(storage).payload["last_outcome"] == "insufficient_window"


def test_error_outcome_does_not_advance_and_alerts_worker_error(storage, config):
    s = Sender()
    _run(storage, config, _flat(), s, now=NOW)
    s.messages.clear()

    def boom():
        raise RuntimeError("dexcom exploded")

    later = NOW + timedelta(minutes=5)
    code = cron.run_cron(
        now=later, storage=storage, config=config, fetch_cgm=boom, send=s
    )
    assert code == 1
    st = _state(storage)
    assert st.last_fetched_at == NOW
    assert st.payload["last_outcome"] == "error"
    assert any("error" in m.lower() for m in s.messages)


def test_counters_per_local_day_and_bounded(storage, config):
    s = Sender()
    # 20 consecutive days, one fresh no_candidate cycle each (local day keys).
    for d in range(20):
        _run(
            storage,
            config,
            _flat(NOW + timedelta(days=d) - timedelta(minutes=2)),
            s,
            now=NOW + timedelta(days=d),
        )
    counts = _state(storage).payload["outcome_counts"]
    assert len(counts) == LiveHealthConfig().outcome_counter_days == 14
    assert all(v == {"no_candidate": 1} for v in counts.values())
    assert "2026-11-02" in counts  # last day (local date key, ISO string)


def test_counters_accumulate_same_day(storage, config):
    s = Sender()
    for i in range(3):
        _run(
            storage,
            config,
            _flat(NOW + timedelta(minutes=5 * i) - timedelta(minutes=2)),
            s,
            now=NOW + timedelta(minutes=5 * i),
        )
    _run(storage, config, _empty(), s, now=NOW + timedelta(minutes=15))
    counts = _state(storage).payload["outcome_counts"]["2026-10-14"]
    assert counts == {"no_candidate": 3, "no_data": 1}


def test_cycle_outcomes_never_written_to_detection_results(storage, config):
    _run(storage, config, _flat(), Sender())
    _run(storage, config, _empty(), Sender())
    assert storage.list_detection_results(kind="meal_rise") == []


def test_heartbeat_not_written_when_prior_state_read_fails(config):
    class Broken(InMemoryStorage):
        def get_fetch_state(self, source_id):
            raise RuntimeError("db down")

    b = Broken()
    # Must not raise and must not write a blank state that would wipe counters.
    _run(b, config, _flat(), Sender())
    assert b.list_fetch_state() == []


# ── detection outcomes ───────────────────────────────────────────────────

RISE = [150, 140, 130, 120, 112, 105, 100]  # newest first, sharp rise


def test_detected_alert_sent_outcome(storage, config):
    s = Sender()
    _run(storage, config, _cgm(RISE, NOW - timedelta(minutes=2)), s)
    assert _state(storage).payload["last_outcome"] == "alert_sent"
    assert len(s.messages) == 1


def test_detected_but_delivery_failed_outcome(storage, config):
    s = Sender(ok=False)
    _run(storage, config, _cgm(RISE, NOW - timedelta(minutes=2)), s)
    assert _state(storage).payload["last_outcome"] == "detected"


def test_suppressed_refractory_outcome(storage, config):
    s = Sender()
    _run(storage, config, _cgm(RISE, NOW - timedelta(minutes=2)), s)
    later = NOW + timedelta(minutes=5)
    _run(storage, config, _cgm(RISE, later - timedelta(minutes=2)), s, now=later)
    assert _state(storage).payload["last_outcome"] == "suppressed_refractory"
    assert len(s.messages) == 1


# ── stale-CGM alert ──────────────────────────────────────────────────────


def _blind(storage, config, sender, minutes_list):
    for m in minutes_list:
        _run(storage, config, _empty(), sender, now=NOW + timedelta(minutes=m))


def test_no_alert_below_threshold(storage, config):
    s = Sender()
    _run(storage, config, _flat(), s, now=NOW)
    _blind(storage, config, s, [5, 30, 55, 57])
    assert s.messages == []  # newest reading is 59 min old: not "more than 60"


def test_single_alert_per_episode_then_realert_then_recovery(storage, config):
    s = Sender()
    _run(storage, config, _flat(), s, now=NOW)
    # crosses 60 min at +65
    _blind(storage, config, s, [5, 30, 65, 70, 120, 300])
    assert len(s.messages) == 1
    msg = s.messages[0]
    assert "No Dexcom readings" in msg and "G7" in msg and "Share" in msg
    assert "~67" in msg  # 65 min after the cycle + 2 min reading lag
    # still blind just past 12h after the first alert -> second alert
    _blind(storage, config, s, [65 + 12 * 60 + 5])
    assert len(s.messages) == 2
    # data returns
    back = NOW + timedelta(minutes=65 + 12 * 60 + 15)
    _run(storage, config, _flat(back - timedelta(minutes=1)), s, now=back)
    assert len(s.messages) == 3
    assert "flowing again" in s.messages[2].lower()
    # one more fresh cycle -> no further recovery note
    nxt = back + timedelta(minutes=5)
    _run(storage, config, _flat(nxt - timedelta(minutes=1)), s, now=nxt)
    assert len(s.messages) == 3
    p = _state(storage).payload
    assert "stale_alert_sent_at" not in p and "stale_since" not in p


def test_short_gap_no_alert_no_recovery(storage, config):
    s = Sender()
    _run(storage, config, _flat(), s, now=NOW)
    _blind(storage, config, s, [5, 20])
    back = NOW + timedelta(minutes=25)
    _run(storage, config, _flat(back - timedelta(minutes=1)), s, now=back)
    assert s.messages == []


def test_failed_alert_send_retries_next_cycle(storage, config):
    bad = Sender(ok=False)
    _run(storage, config, _flat(), bad, now=NOW)
    _blind(storage, config, bad, [65])
    assert len(bad.messages) == 1
    good = Sender()
    _blind(storage, config, good, [70])
    assert len(good.messages) == 1  # retried because first send failed


def test_never_seen_reading_measured_from_first_blind_cycle(storage, config):
    s = Sender()
    _blind(storage, config, s, [0, 30, 60])
    assert s.messages == []
    _blind(storage, config, s, [61])
    assert len(s.messages) == 1


def test_stale_reading_age_triggers_alert(storage, config):
    s = Sender()
    old = _flat(NOW - timedelta(minutes=90))
    _run(storage, config, old, s, now=NOW)
    assert len(s.messages) == 1
    assert "~9" in s.messages[0]  # about 90 min
    _run(storage, config, old, s, now=NOW + timedelta(minutes=5))
    assert len(s.messages) == 1


def test_stale_thresholds_come_from_config(storage, config):
    raw = dict(config.raw)
    raw["live_health"] = {"cgm_stale_alert_minutes": 20, "cgm_stale_realert_hours": 1}
    cfg = dataclasses.replace(config, raw=raw)
    s = Sender()
    _run(storage, cfg, _flat(), s, now=NOW)
    _blind(storage, cfg, s, [25])
    assert len(s.messages) == 1
    _blind(storage, cfg, s, [25 + 61])
    assert len(s.messages) == 2


def test_messages_contain_no_pii(storage, config, monkeypatch):
    monkeypatch.setenv("DEXCOM_USERNAME", "someone@example.com")
    s = Sender()
    _run(storage, config, _flat(), s, now=NOW)
    _blind(storage, config, s, [90])
    back = NOW + timedelta(minutes=100)
    _run(storage, config, _flat(back - timedelta(minutes=1)), s, now=back)
    blob = " ".join(s.messages)
    assert len(s.messages) == 2
    assert "someone" not in blob and "@" not in blob
    assert "mg/dL" not in blob


# ── config + summary ─────────────────────────────────────────────────────


def test_live_health_config_defaults_and_validation():
    c = LiveHealthConfig.from_raw({})
    assert (c.cgm_stale_alert_minutes, c.cgm_stale_realert_hours) == (60, 12)
    assert c.outcome_counter_days == 14
    with pytest.raises(ValueError):
        LiveHealthConfig.from_raw({"cgm_stale_alert_minutes": 0})


def test_checked_in_config_has_live_health_block(config):
    assert config.raw["live_health"]["cgm_stale_alert_minutes"] == 60


def test_summarize_handles_missing_and_legacy_state():
    cfg = LiveHealthConfig()
    assert summarize_live_state(None, NOW, "America/Los_Angeles", cfg) is None
    legacy = FetchState("live_cron", None, NOW, {}, "pydexcom")
    s = summarize_live_state(legacy, NOW, "America/Los_Angeles", cfg)
    assert s is not None and s.last_outcome is None and s.today_counts == {}
