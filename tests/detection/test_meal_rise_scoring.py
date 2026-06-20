"""Unit tests for the M2 meal-rise calibration scorer (pure, no I/O)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from core.detection.meal_rise import MealRiseDetection
from detection.config import MealRiseCalibrationConfig, get_config
from detection.calibration.meal_rise_scoring import (
    FOOD_CARRYING,
    LABEL_LATE,
    LABEL_PRE,
    LABEL_UNCOVERED,
    ScoredInstance,
    find_meal_rise_instances,
    score_instances,
    summarize,
)

TZ = timezone(timedelta(hours=-7), name="PDT")


def _calib() -> MealRiseCalibrationConfig:
    return MealRiseCalibrationConfig(
        pre_bolus_lookback_minutes=30,
        late_bolus_lookahead_minutes=45,
        correction_lookahead_minutes=180,
    )


def _detection(rise_start: datetime, *, span_min: int = 30, start=110, end=200) -> MealRiseDetection:
    anchor = rise_start + timedelta(minutes=span_min)
    return MealRiseDetection(
        anchor_timestamp=anchor,
        slope_mgdl_per_min=(end - start) / span_min,
        start_level=start,
        end_level=end,
        delta=end - start,
        n_samples=7,
        coverage=1.0,
        minutes_span=float(span_min),
        hour_of_day=anchor.hour,
        threshold_used=1.26,
        time_multiplier=0.7,
        glucose_values=[start, end],
        window_start=rise_start,
        window_end=anchor,
    )


def _requests(rows: list[tuple[datetime, str, int]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"timestamp": ts, "bolus_category": cat, "carbs_g": carbs} for ts, cat, carbs in rows]
    )


# ── score_instances: three-way label ────────────────────────────────────────

def test_pre_bolused_when_food_bolus_precedes_rise():
    rise_start = datetime(2026, 5, 25, 12, 0, tzinfo=TZ)
    det = _detection(rise_start)
    reqs = _requests([(rise_start - timedelta(minutes=20), "user_meal", 45)])

    scored = score_instances([det], reqs, _calib(), pump_serial="P1")

    assert len(scored) == 1
    s = scored[0]
    assert s.label == LABEL_PRE
    assert s.matched_bolus_category == "user_meal"
    assert s.matched_bolus_carbs == 45
    assert s.bolus_delay_min == pytest.approx(-20.0)
    assert s.pump_serial == "P1"
    assert s.resolution is None


def test_late_bolused_when_food_bolus_after_rise_start():
    rise_start = datetime(2026, 5, 25, 12, 0, tzinfo=TZ)
    det = _detection(rise_start)
    reqs = _requests([(rise_start + timedelta(minutes=25), "user_meal_and_correction", 60)])

    scored = score_instances([det], reqs, _calib(), pump_serial="P1")
    s = scored[0]
    assert s.label == LABEL_LATE
    assert s.bolus_delay_min == pytest.approx(25.0)
    assert s.matched_bolus_category == "user_meal_and_correction"


def test_uncovered_with_user_correction_resolution():
    rise_start = datetime(2026, 5, 25, 12, 0, tzinfo=TZ)
    det = _detection(rise_start)
    # No food bolus; a user correction 90 min later (the "took the pump suggestion" case).
    reqs = _requests([(rise_start + timedelta(minutes=90), "user_correction_only", 0)])

    scored = score_instances([det], reqs, _calib(), pump_serial="P1")
    s = scored[0]
    assert s.label == LABEL_UNCOVERED
    assert s.matched_bolus_ts is None
    assert s.resolution == "user_correction"
    assert s.resolution_delay_min == pytest.approx(90.0)


def test_uncovered_with_auto_correction_resolution():
    rise_start = datetime(2026, 5, 25, 12, 0, tzinfo=TZ)
    det = _detection(rise_start)
    reqs = _requests([(rise_start + timedelta(minutes=40), "auto_correction", 0)])

    scored = score_instances([det], reqs, _calib(), pump_serial="P1")
    s = scored[0]
    assert s.label == LABEL_UNCOVERED
    assert s.resolution == "auto_correction"


def test_uncovered_none_when_no_bolus_at_all():
    rise_start = datetime(2026, 5, 25, 12, 0, tzinfo=TZ)
    det = _detection(rise_start)

    for reqs in (None, _requests([])):
        scored = score_instances([det], reqs, _calib(), pump_serial="P1")
        s = scored[0]
        assert s.label == LABEL_UNCOVERED
        assert s.resolution == "none"
        assert s.resolution_ts is None


def test_correction_outside_lookahead_is_not_attributed():
    rise_start = datetime(2026, 5, 25, 12, 0, tzinfo=TZ)
    det = _detection(rise_start)
    # Correction 4h later — beyond correction_lookahead_minutes (180).
    reqs = _requests([(rise_start + timedelta(minutes=240), "auto_correction", 0)])

    s = score_instances([det], reqs, _calib(), pump_serial="P1")[0]
    assert s.label == LABEL_UNCOVERED
    assert s.resolution == "none"


def test_nearest_food_bolus_wins_when_both_sides_present():
    rise_start = datetime(2026, 5, 25, 12, 0, tzinfo=TZ)
    det = _detection(rise_start)
    reqs = _requests([
        (rise_start - timedelta(minutes=10), "user_meal", 30),   # nearer (|-10|)
        (rise_start + timedelta(minutes=40), "user_meal", 50),   # farther (|40|)
    ])
    s = score_instances([det], reqs, _calib(), pump_serial="P1")[0]
    assert s.label == LABEL_PRE
    assert s.bolus_delay_min == pytest.approx(-10.0)
    assert s.matched_bolus_carbs == 30


def test_food_bolus_beyond_late_lookahead_is_uncovered():
    # late_bolus_lookahead is measured from rise_start (matches the config name).
    rise_start = datetime(2026, 5, 25, 12, 0, tzinfo=TZ)
    det = _detection(rise_start)  # anchor = rise_start + 30 min
    reqs = _requests([(rise_start + timedelta(minutes=50), "user_meal", 40)])  # 50 > 45
    s = score_instances([det], reqs, _calib(), pump_serial="P1")[0]
    assert s.label == LABEL_UNCOVERED


def test_correction_only_bolus_does_not_count_as_food_coverage():
    # A correction in the food window must NOT make it pre/late_bolused.
    rise_start = datetime(2026, 5, 25, 12, 0, tzinfo=TZ)
    det = _detection(rise_start)
    reqs = _requests([(rise_start - timedelta(minutes=10), "user_correction_only", 0)])
    s = score_instances([det], reqs, _calib(), pump_serial="P1")[0]
    assert s.label == LABEL_UNCOVERED


def test_event_ref_matches_live_format():
    rise_start = datetime(2026, 5, 25, 12, 0, tzinfo=TZ)
    det = _detection(rise_start)
    s = score_instances([det], None, _calib(), pump_serial="P1")[0]
    expected = f"meal_rise:{det.anchor_timestamp.isoformat(timespec='minutes')}"
    assert s.event_ref == expected


# ── summarize ────────────────────────────────────────────────────────────────

def test_summarize_counts_and_uncovered_rate():
    base = datetime(2026, 5, 25, 8, 0, tzinfo=TZ)
    dets = [_detection(base + timedelta(hours=i)) for i in range(4)]
    reqs = _requests([
        (base - timedelta(minutes=20), "user_meal", 40),                    # det0 pre
        (base + timedelta(hours=1) + timedelta(minutes=20), "user_meal", 40),  # det1 late
        # det2, det3 uncovered (no bolus near them)
    ])
    scored = score_instances(dets, reqs, _calib())
    summary = summarize(scored)
    assert summary["total"] == 4
    assert summary["counts"][LABEL_UNCOVERED] == 2
    assert summary["uncovered_rate"] == pytest.approx(0.5)


# ── find_meal_rise_instances: detection + refractory dedup ───────────────────

def test_find_instances_flat_cgm_returns_nothing():
    cfg = get_config()
    start = datetime(2026, 5, 25, 12, 0, tzinfo=TZ)
    cgm = pd.DataFrame({
        "timestamp": [start + timedelta(minutes=5 * i) for i in range(12)],
        "bg_mgdl": [120] * 12,
    })
    assert find_meal_rise_instances(cgm, cfg) == []


def test_find_instances_detects_rise_and_applies_refractory():
    cfg = get_config()
    start = datetime(2026, 5, 25, 12, 0, tzinfo=TZ)
    # ~60 min of continuous steep rise sampled every 5 min: many anchors fire,
    # but the refractory window must collapse them to widely-spaced keepers.
    bgs = [110 + 7 * i for i in range(13)]
    cgm = pd.DataFrame({
        "timestamp": [start + timedelta(minutes=5 * i) for i in range(13)],
        "bg_mgdl": bgs,
    })
    dets = find_meal_rise_instances(cgm, cfg)
    assert len(dets) >= 1
    refractory = timedelta(minutes=cfg.meal_rise.refractory_minutes)
    anchors = sorted(d.anchor_timestamp for d in dets)
    for prev, nxt in zip(anchors, anchors[1:]):
        assert nxt - prev >= refractory
