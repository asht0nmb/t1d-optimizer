"""Tests for the advisory live-path replay (scripts/replay_meal_rise_live.py).

Pure/synthetic: no database, no network. The DB loader is not exercised.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

import scripts.replay_meal_rise_live as rp
from detection.config import get_config

CFG = get_config()
TZ = ZoneInfo(CFG.timezone)


def _cgm(start_local: datetime, values: list[int]) -> pd.DataFrame:
    ts = [start_local + timedelta(minutes=5 * i) for i in range(len(values))]
    return pd.DataFrame({"timestamp": pd.to_datetime(ts, utc=True), "bg_mgdl": values})


def _lunch_day() -> tuple[pd.DataFrame, datetime, datetime]:
    t0 = datetime(2026, 3, 10, 11, 0, tzinfo=TZ)
    # 90 min flat at 110, then +65 mg/dL over 30 min, then plateau.
    vals = [110] * 18 + [110 + round(65 * i / 6) for i in range(1, 7)] + [175] * 12
    return _cgm(t0, vals), t0, t0 + timedelta(minutes=5 * len(vals))


def test_replay_finds_exactly_one_alert_and_mirror_agrees():
    cgm, a, b = _lunch_day()
    res = rp.replay(cgm, CFG, start=a, end=b, latency_min=1.0)
    assert res.mirror_mismatches == 0
    assert len(res.alerts) == 1
    assert 11 <= res.alerts[0].hour_of_day <= 14
    # every tick is accounted for by exactly one terminal outcome
    terminal = (
        rp.G_NO_DATA, rp.G_STALE, rp.G_SAMPLES, rp.G_COVERAGE, rp.G_HAS_GAP,
        rp.G_START_LOW, rp.G_START_HIGH, rp.G_SLOPE_MEAL, rp.G_SLOPE_OFF,
        rp.G_REFRACTORY, rp.G_ALERT,
    )
    assert sum(res.gate_counts[g] for g in terminal) == res.ticks


def test_refractory_collapses_a_sustained_rise_to_one_alert():
    cgm, a, b = _lunch_day()
    res = rp.replay(cgm, CFG, start=a, end=b, latency_min=1.0)
    assert res.gate_counts[rp.G_FIRED] >= res.gate_counts[rp.G_ALERT] == 1


def test_feed_gap_is_attributed_to_freshness_not_detector():
    cgm, a, b = _lunch_day()
    cgm = cgm[(cgm["timestamp"] < pd.Timestamp(a + timedelta(minutes=95)))
              | (cgm["timestamp"] > pd.Timestamp(a + timedelta(minutes=150)))]
    res = rp.replay(cgm, CFG, start=a, end=b, latency_min=1.0)
    assert res.gate_counts[rp.G_STALE] > 0
    assert res.mirror_mismatches == 0


def test_off_hours_same_ramp_does_not_alert():
    cgm, _, _ = _lunch_day()
    shifted = cgm.copy()
    shifted["timestamp"] = shifted["timestamp"] - pd.Timedelta(hours=9)  # ~02:00-04:00
    a = shifted["timestamp"].iloc[0].to_pydatetime()
    b = shifted["timestamp"].iloc[-1].to_pydatetime() + timedelta(minutes=5)
    res = rp.replay(shifted, CFG, start=a, end=b, latency_min=1.0)
    assert res.alerts == []
    assert res.gate_counts[rp.G_SLOPE_OFF] > 0


def test_precision_summary_labels(monkeypatch):
    cgm, a, b = _lunch_day()
    res = rp.replay(cgm, CFG, start=a, end=b, latency_min=1.0)
    from detection.calibration.meal_rise_scoring import score_instances

    empty = pd.DataFrame(columns=["timestamp", "bolus_category", "carbs_g"])
    s = rp.summarize_precision(score_instances(res.alerts, empty, CFG.meal_rise_calibration))
    assert (s.total, s.uncovered, s.strict) == (1, 1, 1.0)

    anchor = res.alerts[0].anchor_timestamp
    covered = pd.DataFrame({
        "timestamp": [anchor - timedelta(minutes=40)],
        "bolus_category": ["user_meal"], "carbs_g": [40]})
    s2 = rp.summarize_precision(score_instances(res.alerts, covered, CFG.meal_rise_calibration))
    assert s2.pre_bolused == 1 and s2.strict == 0.0


def test_p_zero_model():
    assert rp.p_zero_given_fresh_fraction(0, 0.5) == 1.0
    assert abs(rp.p_zero_given_fresh_fraction(10, 0.5) - 0.5**10) < 1e-12


def test_event_run_lengths():
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ticks = [(t + timedelta(minutes=m), "alert") for m in (0, 5, 10, 60, 65)]
    assert rp.event_run_lengths(ticks, 5) == [3, 2]
