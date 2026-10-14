"""End-to-end pins for the live meal-rise path (M1 diagnostic, 2026-10-14).

Why this file exists
--------------------
``tests/detection/test_cron_meal_rise.py`` exercises ``run_cron`` but its
``MockGlucoseReading`` hands the cron *naive-UTC-style* datetimes and never goes
through ``pydexcom``'s real parser. pydexcom 0.5.1 (pinned in ``uv.lock``)
returns a **timezone-aware** ``datetime`` built from the Share ``DT`` field
(``"Date(<epoch ms><+/-HHMM>)"``): the epoch is the true UTC instant and the
trailing offset is the display timezone. ``fetch_dexcom_cgm`` then calls
``r.datetime.replace(tzinfo=timezone.utc)`` which *relabels the wall clock as
UTC* and shifts the instant by the offset (-7 h/-8 h for a Pacific account, 4 h
for the offset in pydexcom's own README example).

Here the fake Dexcom client returns REAL ``pydexcom.glucose_reading.GlucoseReading``
objects built from Share-shaped JSON, ``requests.post`` is patched (so the REAL
``send_telegram_message`` formats the request), storage is ``InMemoryStorage``
and ``now`` is injected. Nothing here touches Dexcom, Telegram or Supabase.

Known bug (NOT fixed here; ``apps/personal/cron`` is owned elsewhere)
---------------------------------------------------------------------
``apps/personal/cron/detect_meal_rise.py`` (``fetch_dexcom_cgm``, the
``dt_utc = r.datetime.replace(tzinfo=timezone.utc)`` line) is wrong for any
nonzero ``DT`` offset. The offset-parametrized tests below are ``xfail(strict=True)``
so the suite stays green today and flips to a hard failure the moment the bug is
fixed (at which point drop the markers). Proposed fix::

    dt = r.datetime
    dt_utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    dt_local = dt_utc.astimezone(tz)

Config sensitivity: these tests read the CURRENT ``config/user_config.yaml``.
The 120 -> 185 mg/dL / 30 min ramp is ~2.17 mg/dL/min; it fires in a meal
window (threshold = base * 0.7) but NOT off-hours (base * 1.3). If the owner
retunes ``base_slope_mgdl_per_min`` the literal-ramp tests may need a new ramp.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

import pytest
from pydexcom.glucose_reading import GlucoseReading

from apps.personal.cron import detect_meal_rise as cron
from core.storage.memory import InMemoryStorage
from detection.config import get_config

UTC = timezone.utc
CFG = get_config()
TZ_NAME = CFG.timezone

# Lunch time (meal window 11-14) on a PDT day: 12:00 PDT == 19:00 UTC.
LUNCH_ANCHOR_UTC = datetime(2026, 10, 14, 19, 0, tzinfo=UTC)
# 03:00 PDT == 10:00 UTC (off-hours multiplier).
NIGHT_ANCHOR_UTC = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)

BOT_TOKEN = "123456:TEST_TOKEN"
CHAT_ID = "424242"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

def _share_reading(value: int, when_utc: datetime, offset: str) -> GlucoseReading:
    """Build a REAL pydexcom reading from a Share-shaped JSON blob.

    ``when_utc`` is the true instant; ``offset`` is the ``+HHMM``/``-HHMM`` the
    Share API appends to ``DT`` (display timezone). The epoch part is UTC ms.
    """
    ms = int(when_utc.timestamp() * 1000)
    return GlucoseReading(
        {"Value": value, "Trend": "Flat", "DT": f"Date({ms}{offset})"}
    )


class FakeDexcom:
    """Stand-in for ``pydexcom.Dexcom`` (newest-first, honours ``max_count``)."""

    readings: list[GlucoseReading] = []

    def __init__(self, username=None, password=None, region=None):  # noqa: D107
        pass

    def get_glucose_readings(self, minutes=1440, max_count=288):
        return list(type(self).readings)[:max_count]


def ramp(
    anchor_utc: datetime,
    *,
    start: int = 120,
    end: int = 185,
    n: int = 7,
    offset: str = "+0000",
    jitter_s: tuple[int, ...] = (0,),
    drop: tuple[int, ...] = (),
    lead_in: int = 5,
) -> list[GlucoseReading]:
    """Newest-first Share readings: ``lead_in`` flat readings, then a linear ramp.

    The ramp spans ``(n - 1) * 5`` minutes ending at ``anchor_utc``. ``drop`` lists
    ramp indices (0 == newest) to omit, modelling missed 5-min samples.
    """
    step = (end - start) / (n - 1)
    out: list[GlucoseReading] = []
    for i in range(n):  # i == 0 is the newest reading
        if i in drop:
            continue
        j = jitter_s[i % len(jitter_s)]
        ts = anchor_utc - timedelta(minutes=5 * i) + timedelta(seconds=j)
        out.append(_share_reading(round(end - step * i), ts, offset))
    for k in range(1, lead_in + 1):  # flat history before the ramp
        ts = anchor_utc - timedelta(minutes=5 * (n - 1 + k))
        out.append(_share_reading(start, ts, offset))
    return out


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DEXCOM_USERNAME", "u")
    monkeypatch.setenv("DEXCOM_PASSWORD", "p")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT_ID)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)


@pytest.fixture
def storage():
    return InMemoryStorage()


class Posts(list):
    """Captured ``requests.post`` calls: list of (url, json_payload)."""

    ok = True


@pytest.fixture
def live(monkeypatch, env, storage):
    """Wire the real ``run_cron`` to in-memory storage, a fake Dexcom and a fake
    ``requests.post``. Returns a callable ``(readings, now) -> exit_code``.
    """
    posts = Posts()

    class _Resp:
        def raise_for_status(self):
            if not posts.ok:
                raise RuntimeError("telegram down")

    def fake_post(url, json=None, timeout=None, **_):
        posts.append((url, json))
        if not posts.ok:
            raise RuntimeError("telegram down")
        return _Resp()

    monkeypatch.setattr(cron.requests, "post", fake_post)
    monkeypatch.setattr(cron, "Dexcom", FakeDexcom)
    monkeypatch.setattr(cron, "get_storage_connection", lambda: (storage, None))

    def _run(readings: list[GlucoseReading], now: datetime) -> int:
        FakeDexcom.readings = readings
        return cron.run_cron(now=now)

    _run.posts = posts  # type: ignore[attr-defined]
    return _run


def _fresh_now(anchor_utc: datetime) -> datetime:
    """A cron tick ~90 s after the newest sensor reading (typical Share lag)."""
    return anchor_utc + timedelta(seconds=90)


def _assert_fired(live: Callable, storage: InMemoryStorage, anchor_utc: datetime):
    assert len(live.posts) == 1, "expected exactly one Telegram send"
    url, payload = live.posts[0]
    assert url == f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    assert payload["chat_id"] == CHAT_ID
    assert payload["parse_mode"] == "HTML"
    assert "Fast glucose rise" in payload["text"]

    results = storage.list_detection_results(kind="meal_rise")
    assert len(results) == 1
    p = results[0].payload
    assert p["telegram_sent"] is True
    assert p["start_level"] < p["end_level"]
    assert p["slope_mgdl_per_min"] >= p["threshold_used"]

    alerts = storage.recent_alerts("meal_rise", timedelta(hours=1))
    assert len(alerts) == 1
    assert alerts[0].event_ref.startswith("meal_rise:")
    # Heartbeat is written on every non-raising cycle.
    assert storage.get_fetch_state("live_cron") is not None
    return p


# --------------------------------------------------------------------------- #
# The headline end-to-end scenario
# --------------------------------------------------------------------------- #

OFFSET_BUG = pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG apps/personal/cron/detect_meal_rise.py fetch_dexcom_cgm: "
        "r.datetime.replace(tzinfo=utc) relabels a tz-aware pydexcom datetime "
        "(DT offset) and shifts the instant by the offset, so every reading "
        "looks >=offset hours stale and the freshness guard skips every cycle."
    ),
)

OFFSETS = [
    pytest.param("+0000", id="dt_offset_0000"),
    pytest.param("-0400", id="dt_offset_-0400", marks=OFFSET_BUG),
    pytest.param("-0700", id="dt_offset_-0700_PDT", marks=OFFSET_BUG),
    pytest.param("-0800", id="dt_offset_-0800_PST", marks=OFFSET_BUG),
]


@pytest.mark.parametrize("offset", OFFSETS)
def test_unbolused_lunch_rise_fires_end_to_end(live, storage, offset):
    """120 -> 185 mg/dL over 30 min at 12:00 PT with fresh, jittered Share data."""
    readings = ramp(
        LUNCH_ANCHOR_UTC, offset=offset, jitter_s=(0, 12, -9, 20, -15, 7, 0)
    )
    assert live(readings, _fresh_now(LUNCH_ANCHOR_UTC)) == 0
    p = _assert_fired(live, storage, LUNCH_ANCHOR_UTC)
    assert p["hour_of_day"] == 12  # local hour drives the multiplier
    lunch_mult = next(
        float(mw["multiplier"]) for mw in CFG.meal_rise.meal_windows
        if int(mw["start_hour"]) <= 12 <= int(mw["end_hour"])
    )
    assert p["time_multiplier"] == lunch_mult
    assert p["start_level"] in range(118, 124)
    assert p["end_level"] in range(183, 188)


@pytest.mark.parametrize("offset", OFFSETS)
def test_fetch_dexcom_cgm_preserves_the_true_instant(env, monkeypatch, offset):
    """The newest parsed timestamp must equal the true UTC instant of the reading."""
    monkeypatch.setattr(cron, "Dexcom", FakeDexcom)
    FakeDexcom.readings = ramp(LUNCH_ANCHOR_UTC, offset=offset)
    df = cron.fetch_dexcom_cgm(CFG.meal_rise, TZ_NAME)
    newest = df["timestamp"].iloc[-1].to_pydatetime()
    assert newest.astimezone(UTC) == LUNCH_ANCHOR_UTC


def test_proposed_fix_recovers_true_instant_for_any_offset():
    """Executable spec for the proposed fix (kept as a pure helper; the shipped
    code is untouched). ``astimezone`` is correct for aware datetimes."""
    for offset in ("+0000", "-0400", "-0700", "-0800"):
        r = _share_reading(120, LUNCH_ANCHOR_UTC, offset)
        dt = r.datetime
        fixed = dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
        assert fixed == LUNCH_ANCHOR_UTC
        buggy = dt.replace(tzinfo=UTC)
        assert (buggy == LUNCH_ANCHOR_UTC) == (offset == "+0000")


# --------------------------------------------------------------------------- #
# Gate pins through the real path (offset +0000 so the bug cannot interfere)
# --------------------------------------------------------------------------- #

def _no_alert(live, storage):
    assert live.posts == []
    assert storage.list_detection_results(kind="meal_rise") == []
    assert storage.recent_alerts("meal_rise", timedelta(days=1)) == []


def test_same_ramp_at_3am_is_suppressed_by_off_hours_multiplier(live, storage):
    """~2.17 mg/dL/min clears 0.7x base but not 1.3x base: the time-of-day gate."""
    off_thr = CFG.meal_rise.base_slope_mgdl_per_min * CFG.meal_rise.off_hours_multiplier
    assert 65 / 30 < off_thr, "premise: literal ramp below off-hours threshold"
    readings = ramp(NIGHT_ANCHOR_UTC)
    assert live(readings, _fresh_now(NIGHT_ANCHOR_UTC)) == 0
    _no_alert(live, storage)


def test_steeper_ramp_at_3am_fires(live, storage):
    off_thr = CFG.meal_rise.base_slope_mgdl_per_min * CFG.meal_rise.off_hours_multiplier
    delta = round(off_thr * 1.3 * 30)
    readings = ramp(NIGHT_ANCHOR_UTC, start=110, end=110 + delta)
    assert live(readings, _fresh_now(NIGHT_ANCHOR_UTC)) == 0
    p = _assert_fired(live, storage, NIGHT_ANCHOR_UTC)
    assert p["time_multiplier"] == CFG.meal_rise.off_hours_multiplier


@pytest.mark.parametrize(
    "delta_from_limit_min, should_fire",
    [(-1, True), (1, False), (225, False)],
    ids=["1min_inside_limit", "1min_past_limit", "far_past_limit"],
)
def test_freshness_guard_boundary(live, storage, delta_from_limit_min, should_fire):
    """``max_reading_age_minutes`` gates the whole cycle (relative to config)."""
    age_min = CFG.meal_rise.max_reading_age_minutes + delta_from_limit_min
    now = LUNCH_ANCHOR_UTC + timedelta(minutes=age_min)
    live(ramp(LUNCH_ANCHOR_UTC), now)
    if should_fire:
        _assert_fired(live, storage, LUNCH_ANCHOR_UTC)
    else:
        _no_alert(live, storage)


@pytest.mark.parametrize(
    "drop, should_fire",
    [((2, 4), True), ((1, 2, 4), False)],
    ids=["5of7_present_fires", "4of7_present_blocked_by_coverage"],
)
def test_coverage_gate(live, storage, drop, should_fire):
    """min_coverage 0.7 of 7 expected samples => >=5 readings needed."""
    live(ramp(LUNCH_ANCHOR_UTC, drop=drop), _fresh_now(LUNCH_ANCHOR_UTC))
    if should_fire:
        _assert_fired(live, storage, LUNCH_ANCHOR_UTC)
    else:
        _no_alert(live, storage)


@pytest.mark.parametrize(
    "start_delta, should_fire",
    [
        (0, True),   # start == start_level_max / min boundaries are inclusive
        (1, False),  # one beyond the boundary
    ],
)
def test_start_level_upper_bound(live, storage, start_delta, should_fire):
    smax = CFG.meal_rise.start_level_max
    start = smax + start_delta
    live(ramp(LUNCH_ANCHOR_UTC, start=start, end=start + 65), _fresh_now(LUNCH_ANCHOR_UTC))
    if should_fire:
        _assert_fired(live, storage, LUNCH_ANCHOR_UTC)
    else:
        _no_alert(live, storage)


def test_start_level_lower_bound(live, storage):
    smin = CFG.meal_rise.start_level_min
    live(ramp(LUNCH_ANCHOR_UTC, start=smin - 1, end=smin + 64), _fresh_now(LUNCH_ANCHOR_UTC))
    _no_alert(live, storage)


def test_refractory_second_cycle_is_suppressed(live, storage):
    """A second qualifying cycle 5 min later sends nothing (60-min refractory).

    Refractory reads ``sent_at`` wall-clock (``datetime.now``) not the injected
    ``now`` -- a test-only quirk; production behaviour is equivalent.
    """
    live(ramp(LUNCH_ANCHOR_UTC), _fresh_now(LUNCH_ANCHOR_UTC))
    nxt = LUNCH_ANCHOR_UTC + timedelta(minutes=5)
    live(ramp(nxt, start=125, end=190), _fresh_now(nxt))
    assert len(live.posts) == 1
    assert len(storage.recent_alerts("meal_rise", timedelta(hours=1))) == 1
    assert len(storage.list_detection_results(kind="meal_rise")) == 1


def test_telegram_failure_still_claims_and_records(live, storage):
    live.posts.ok = False
    live(ramp(LUNCH_ANCHOR_UTC), _fresh_now(LUNCH_ANCHOR_UTC))
    results = storage.list_detection_results(kind="meal_rise")
    assert len(results) == 1
    assert results[0].payload["telegram_sent"] is False
    assert len(storage.recent_alerts("meal_rise", timedelta(hours=1))) == 1


def test_missing_telegram_config_is_a_silent_zero(live, storage, monkeypatch):
    """No bot token/chat id => detector fires but NOTHING is written to
    ``detection_results``/``alerts_sent`` (``failed_config`` returns before any
    write). ``run_cron`` returns 1 (HTTP 500 from the worker) but the heartbeat
    is still written, so ``/status`` looks healthy. This is one way the tables
    can stay empty forever with a "live" loop; check the worker's Vercel env.
    """
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    monkeypatch.delenv("TELEGRAM_CHAT_ID")
    assert live(ramp(LUNCH_ANCHOR_UTC), _fresh_now(LUNCH_ANCHOR_UTC)) == 1
    _no_alert(live, storage)
    assert storage.get_fetch_state("live_cron") is not None
