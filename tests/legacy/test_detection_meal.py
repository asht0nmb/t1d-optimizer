"""Tests for `detection.legacy.meal.detect_meals`."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from detection.legacy.meal import detect_meals

pytestmark = pytest.mark.legacy


PST = timezone(timedelta(hours=-8))

EXPECTED_COLUMNS = [
    "timestamp",
    "bg_start",
    "bg_peak",
    "rise_rate_per_5min",
    "meal_window",
    "confidence",
]


def _cgm_series(
    readings,
    start: datetime = datetime(2026, 3, 19, 12, 0, tzinfo=PST),
    step_min: int = 5,
) -> pd.DataFrame:
    """Build a DataFrame matching `ingestion.builders.build_cgm_df` output."""
    rows = []
    for i, bg in enumerate(readings):
        rows.append(
            {
                "timestamp": start + timedelta(minutes=i * step_min),
                "bg_mgdl": int(bg),
                "backfilled": False,
                "sensor_timestamp": None,
                "pump_serial": "TEST",
                "seqnum": i,
            }
        )
    columns = [
        "timestamp",
        "bg_mgdl",
        "backfilled",
        "sensor_timestamp",
        "pump_serial",
        "seqnum",
    ]
    return pd.DataFrame(rows, columns=columns)


_REQUESTS_COLUMNS = [
    "timestamp",
    "bolus_id",
    "carbs_g",
    "bg_mgdl",
    "iob",
    "bolus_source",
    "food_insulin",
    "correction_insulin",
    "total_requested",
    "pump_serial",
    "bolus_category",
    "override_delta",
]


def _empty_requests() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in _REQUESTS_COLUMNS})


def _requests_frame(rows: list[dict]) -> pd.DataFrame:
    """Build a requests_df with `bolus_category` / `bolus_source` populated.

    Accepts partial dicts; fills missing columns with reasonable defaults.
    """
    filled = []
    for r in rows:
        base = {
            "bolus_id": 0,
            "carbs_g": 0,
            "bg_mgdl": 0,
            "iob": 0.0,
            "bolus_source": "user",
            "food_insulin": 0.0,
            "correction_insulin": 0.0,
            "total_requested": 0.0,
            "pump_serial": "TEST",
            "bolus_category": "unknown",
            "override_delta": float("nan"),
        }
        base.update(r)
        filled.append(base)
    return pd.DataFrame(filled, columns=_REQUESTS_COLUMNS)


class TestSustainedRiseDetection:
    def test_sustained_rise_without_bolus_detected(self, default_config):
        # deltas: NaN, 0, 12, 15, 18, 10. Run of 3 consecutive >= 8 at idx 2..4.
        cgm = _cgm_series([100, 100, 112, 127, 145, 155])
        out = detect_meals(cgm, _empty_requests(), default_config)
        assert len(out) == 1
        row = out.iloc[0]
        assert row["timestamp"] == cgm.iloc[2]["timestamp"]
        assert int(row["bg_start"]) == 100
        assert row["rise_rate_per_5min"] == pytest.approx((12 + 15 + 18) / 3)

    def test_short_rise_below_sustained_intervals_ignored(self, default_config):
        # only 2 rising intervals, need 3
        cgm = _cgm_series([100, 112, 127, 127])
        out = detect_meals(cgm, _empty_requests(), default_config)
        assert out.empty

    def test_gap_in_cgm_breaks_run(self, default_config):
        # 12-min gap between idx 1 and idx 2 should break sustained-intervals.
        t0 = datetime(2026, 3, 19, 8, 0, tzinfo=PST)
        rows = [
            {"timestamp": t0,                         "bg_mgdl": 100},
            {"timestamp": t0 + timedelta(minutes=5),  "bg_mgdl": 112},
            {"timestamp": t0 + timedelta(minutes=17), "bg_mgdl": 127},  # 12-min gap
            {"timestamp": t0 + timedelta(minutes=22), "bg_mgdl": 145},
        ]
        df = pd.DataFrame(rows)
        for col in ["backfilled", "sensor_timestamp", "pump_serial", "seqnum"]:
            df[col] = None
        out = detect_meals(df, _empty_requests(), default_config)
        assert out.empty

    def test_empty_cgm_returns_empty_with_schema(self, default_config):
        out = detect_meals(_cgm_series([]), _empty_requests(), default_config)
        assert out.empty
        assert list(out.columns) == EXPECTED_COLUMNS

    def test_empty_requests_still_detects(self, default_config):
        cgm = _cgm_series([100, 112, 127, 145])
        out = detect_meals(cgm, _empty_requests(), default_config)
        assert len(out) == 1


class TestBolusSuppression:
    def test_user_meal_suppresses(self, default_config):
        cgm = _cgm_series([100, 112, 127, 145])
        requests = _requests_frame([
            {
                "timestamp": cgm.iloc[0]["timestamp"] - timedelta(minutes=10),
                "bolus_category": "user_meal",
                "bolus_source": "user",
                "carbs_g": 40,
                "food_insulin": 9.5,
                "total_requested": 9.5,
            },
        ])
        out = detect_meals(cgm, requests, default_config)
        assert out.empty

    def test_user_meal_and_correction_suppresses(self, default_config):
        cgm = _cgm_series([100, 112, 127, 145])
        requests = _requests_frame([
            {
                "timestamp": cgm.iloc[0]["timestamp"] - timedelta(minutes=10),
                "bolus_category": "user_meal_and_correction",
                "bolus_source": "user",
                "carbs_g": 30,
                "food_insulin": 7.0,
                "correction_insulin": 1.5,
                "total_requested": 8.5,
            },
        ])
        out = detect_meals(cgm, requests, default_config)
        assert out.empty

    def test_override_up_suppresses(self, default_config):
        cgm = _cgm_series([100, 112, 127, 145])
        requests = _requests_frame([
            {
                "timestamp": cgm.iloc[0]["timestamp"] - timedelta(minutes=10),
                "bolus_category": "override_up",
                "bolus_source": "override",
                "carbs_g": 25,
                "food_insulin": 6.0,
                "total_requested": 7.5,
                "override_delta": 1.5,
            },
        ])
        out = detect_meals(cgm, requests, default_config)
        assert out.empty

    def test_auto_correction_does_not_suppress(self, default_config):
        cgm = _cgm_series([100, 112, 127, 145])
        requests = _requests_frame([
            {
                "timestamp": cgm.iloc[0]["timestamp"] - timedelta(minutes=10),
                "bolus_category": "auto_correction",
                "bolus_source": "auto",
                "correction_insulin": 1.2,
                "total_requested": 1.2,
            },
        ])
        out = detect_meals(cgm, requests, default_config)
        assert len(out) == 1

    def test_user_correction_only_does_not_suppress(self, default_config):
        cgm = _cgm_series([100, 112, 127, 145])
        requests = _requests_frame([
            {
                "timestamp": cgm.iloc[0]["timestamp"] - timedelta(minutes=10),
                "bolus_category": "user_correction_only",
                "bolus_source": "user",
                "correction_insulin": 1.5,
                "total_requested": 1.5,
            },
        ])
        out = detect_meals(cgm, requests, default_config)
        assert len(out) == 1

    def test_bolus_outside_lookback_window_does_not_suppress(self, default_config):
        # Bolus 45 min before run start, no_bolus_window_minutes is 30.
        cgm = _cgm_series([100, 112, 127, 145])
        requests = _requests_frame([
            {
                "timestamp": cgm.iloc[0]["timestamp"] - timedelta(minutes=45),
                "bolus_category": "user_meal",
                "bolus_source": "user",
                "carbs_g": 40,
                "food_insulin": 9.5,
                "total_requested": 9.5,
            },
        ])
        out = detect_meals(cgm, requests, default_config)
        assert len(out) == 1


class TestMealWindowLabel:
    def test_meal_window_label_applied_inside_window(self, default_config):
        # Run starts at 07:05 (first rising interval end) -- hour 7 is in [6,10].
        cgm = _cgm_series(
            [100, 112, 127, 145],
            start=datetime(2026, 3, 19, 7, 0, tzinfo=PST),
        )
        out = detect_meals(cgm, _empty_requests(), default_config)
        assert out.iloc[0]["meal_window"] != "off_window"

    def test_off_hours_labeled_off_window(self, default_config):
        cgm = _cgm_series(
            [100, 112, 127, 145],
            start=datetime(2026, 3, 19, 3, 0, tzinfo=PST),
        )
        out = detect_meals(cgm, _empty_requests(), default_config)
        assert out.iloc[0]["meal_window"] == "off_window"


class TestBgPeak:
    def test_bg_peak_is_max_within_two_hours(self, default_config):
        # First rising interval ends at idx 1. Within 2h (= +24 readings) after
        # that timestamp, we expect max bg == 230.
        readings = [100, 112, 127, 145, 160, 180, 200, 215, 230, 220, 210, 200]
        cgm = _cgm_series(readings)
        out = detect_meals(cgm, _empty_requests(), default_config)
        assert int(out.iloc[0]["bg_peak"]) == 230

    def test_bg_peak_clipped_to_end_of_cgm(self, default_config):
        # Only 4 readings -- 2h window extends past available data.
        cgm = _cgm_series([100, 112, 127, 145])
        out = detect_meals(cgm, _empty_requests(), default_config)
        assert int(out.iloc[0]["bg_peak"]) == 145


class TestConfidence:
    def test_confidence_in_range(self, default_config):
        cgm = _cgm_series([100, 112, 127, 145])
        out = detect_meals(cgm, _empty_requests(), default_config)
        c = float(out.iloc[0]["confidence"])
        assert 0.0 <= c <= 1.0

    def test_confidence_increases_with_rise_rate(self, default_config):
        slow = _cgm_series([100, 109, 118, 127])   # deltas 9,9,9
        fast = _cgm_series([100, 125, 150, 175])   # deltas 25,25,25
        c_slow = float(
            detect_meals(slow, _empty_requests(), default_config).iloc[0]["confidence"]
        )
        c_fast = float(
            detect_meals(fast, _empty_requests(), default_config).iloc[0]["confidence"]
        )
        assert c_fast > c_slow

    def test_confidence_bonuses_for_meal_window_and_high_peak(self, default_config):
        # Fast rise (base=1.0), inside meal window (+0.1 → clamp),
        # and peak above bg_targets.high (+0.1 → clamp). Clamped confidence = 1.0.
        cgm = _cgm_series(
            [100, 125, 150, 200],
            start=datetime(2026, 3, 19, 7, 0, tzinfo=PST),
        )
        out = detect_meals(cgm, _empty_requests(), default_config)
        row = out.iloc[0]
        assert row["meal_window"] != "off_window"
        assert int(row["bg_peak"]) > default_config.bg_targets.high
        assert float(row["confidence"]) == pytest.approx(1.0)

    def test_confidence_without_bonuses_below_one(self, default_config):
        # Rise just above threshold and well below bg_targets.high, at 3am.
        # base = 9 / (2*8) = 0.5625; no bonuses → 0.5625.
        cgm = _cgm_series(
            [100, 109, 118, 127],
            start=datetime(2026, 3, 19, 3, 0, tzinfo=PST),
        )
        out = detect_meals(cgm, _empty_requests(), default_config)
        row = out.iloc[0]
        assert row["meal_window"] == "off_window"
        assert int(row["bg_peak"]) < default_config.bg_targets.high
        assert float(row["confidence"]) == pytest.approx(9 / 16)


class TestOutputSchema:
    def test_columns_exact(self, default_config):
        cgm = _cgm_series([100, 112, 127, 145])
        out = detect_meals(cgm, _empty_requests(), default_config)
        assert list(out.columns) == EXPECTED_COLUMNS
