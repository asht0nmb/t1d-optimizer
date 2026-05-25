import pytest
from datetime import datetime, timedelta, timezone
import pandas as pd
from core.detection.windowing import Anchor, make_window
from core.detection.meal_rise import MealRiseConfig, detect_meal_rise

TZ = timezone(timedelta(hours=-7), name="PDT")


@pytest.fixture
def base_config() -> MealRiseConfig:
    return MealRiseConfig(
        window_minutes=30,
        min_samples=4,
        min_coverage=0.7,
        base_slope_mgdl_per_min=1.8,
        start_level_min=70,
        start_level_max=250,
        meal_windows=(
            {"start_hour": 6, "end_hour": 10, "multiplier": 0.7},  # Breakfast: 6am - 10am inclusive
            {"start_hour": 11, "end_hour": 14, "multiplier": 0.7},  # Lunch: 11am - 2pm inclusive
            {"start_hour": 17, "end_hour": 21, "multiplier": 0.7},  # Dinner: 5pm - 9pm inclusive
        ),
        off_hours_multiplier=1.3,
        refractory_minutes=60,
        alert_template="Alert!",
        fetch_buffer_minutes=15,
        expected_interval_minutes=5,
        fetch_readings_padding=3,
    )


def test_detect_meal_rise_flat(base_config):
    anchor_ts = datetime(2026, 5, 25, 8, 0, tzinfo=TZ)  # Breakfast window (multiplier 0.7)
    anchor = Anchor(anchor_ts, "live")

    # Flat glucose at 120
    timestamps = [anchor_ts - timedelta(minutes=30) + timedelta(minutes=5 * i) for i in range(7)]
    cgm_df = pd.DataFrame({
        "timestamp": timestamps,
        "bg_mgdl": [120] * 7
    })

    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0))
    detection = detect_meal_rise(window, base_config)

    assert detection is None


def test_detect_meal_rise_sharp_rise_breakfast(base_config):
    anchor_ts = datetime(2026, 5, 25, 8, 0, tzinfo=TZ)  # 8:00 AM (multiplier 0.7)
    anchor = Anchor(anchor_ts, "live")

    # Sharp rise from 100 to 150 over 30 mins (slope = 50 / 30 = 1.67 mg/dL/min)
    # Threshold = 1.8 * 0.7 = 1.26 mg/dL/min.
    # Since 1.67 >= 1.26, this should fire!
    bg_values = [100, 105, 112, 120, 130, 140, 150]
    timestamps = [anchor_ts - timedelta(minutes=30) + timedelta(minutes=5 * i) for i in range(7)]
    cgm_df = pd.DataFrame({
        "timestamp": timestamps,
        "bg_mgdl": bg_values
    })

    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0))
    detection = detect_meal_rise(window, base_config)

    assert detection is not None
    assert detection.start_level == 100
    assert detection.end_level == 150
    assert detection.delta == 50
    assert detection.hour_of_day == 8
    assert detection.time_multiplier == 0.7
    assert detection.threshold_used == 1.26
    assert detection.slope_mgdl_per_min >= 1.26
    assert len(detection.glucose_values) == 7

    # Test payload serialization
    payload = detection.to_payload()
    assert payload["start_level"] == 100
    assert payload["end_level"] == 150
    assert payload["delta"] == 50
    assert isinstance(payload["glucose_values"], list)
    assert len(payload["glucose_values"]) == 7


def test_detect_meal_rise_off_hours_suppressed(base_config):
    # 3:00 PM is off-hours (multiplier 1.3)
    # Threshold = 1.8 * 1.3 = 2.34 mg/dL/min.
    # A rise of 1.67 mg/dL/min should NOT fire here.
    anchor_ts = datetime(2026, 5, 25, 15, 0, tzinfo=TZ)
    anchor = Anchor(anchor_ts, "live")

    bg_values = [100, 105, 112, 120, 130, 140, 150]
    timestamps = [anchor_ts - timedelta(minutes=30) + timedelta(minutes=5 * i) for i in range(7)]
    cgm_df = pd.DataFrame({
        "timestamp": timestamps,
        "bg_mgdl": bg_values
    })

    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0))
    detection = detect_meal_rise(window, base_config)

    assert detection is None


def test_detect_meal_rise_start_level_gating(base_config):
    anchor_ts = datetime(2026, 5, 25, 8, 0, tzinfo=TZ)
    anchor = Anchor(anchor_ts, "live")

    # Sharp rise but starting below start_level_min (low-recovery)
    # 60 to 110 (slope 1.67) - should be blocked because start_level=60 < start_level_min=70
    bg_values = [60, 65, 72, 80, 90, 100, 110]
    timestamps = [anchor_ts - timedelta(minutes=30) + timedelta(minutes=5 * i) for i in range(7)]
    cgm_df = pd.DataFrame({
        "timestamp": timestamps,
        "bg_mgdl": bg_values
    })

    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0))
    detection = detect_meal_rise(window, base_config)
    assert detection is None

    # Sharp rise starting above start_level_max (hyper)
    # 260 to 310 - should be blocked because start_level=260 > start_level_max=250
    bg_values = [260, 265, 272, 280, 290, 300, 310]
    cgm_df = pd.DataFrame({
        "timestamp": timestamps,
        "bg_mgdl": bg_values
    })

    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0))
    detection = detect_meal_rise(window, base_config)
    assert detection is None


def test_detect_meal_rise_coverage_and_samples_guard(base_config):
    anchor_ts = datetime(2026, 5, 25, 8, 0, tzinfo=TZ)
    anchor = Anchor(anchor_ts, "live")

    # Sparse samples (only 3 readings present)
    # min_samples is 4 - should fail
    timestamps = [
        anchor_ts - timedelta(minutes=30),
        anchor_ts - timedelta(minutes=15),
        anchor_ts
    ]
    cgm_df = pd.DataFrame({
        "timestamp": timestamps,
        "bg_mgdl": [100, 120, 150]
    })

    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0))
    detection = detect_meal_rise(window, base_config)
    assert detection is None


def test_theil_sen_outlier_robustness(base_config):
    anchor_ts = datetime(2026, 5, 25, 8, 0, tzinfo=TZ)
    anchor = Anchor(anchor_ts, "live")

    # Sharp rise from 100 to 150 (slope 1.67) but with a single massive outlier spike at index 3 (BG 300 due to sensor jitter)
    # Theil-Sen estimator (median of pairwise slopes) should suppress this single outlier and STILL detect the overall rise correctly
    bg_values = [100, 105, 112, 300, 130, 140, 150]
    timestamps = [anchor_ts - timedelta(minutes=30) + timedelta(minutes=5 * i) for i in range(7)]
    cgm_df = pd.DataFrame({
        "timestamp": timestamps,
        "bg_mgdl": bg_values
    })

    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0))
    detection = detect_meal_rise(window, base_config)

    # Since it robustly calculates slope via pairwise median, it successfully identifies the fast rise
    assert detection is not None
    assert detection.slope_mgdl_per_min >= 1.26


def test_detect_meal_rise_has_gap_suppresses(base_config):
    anchor_ts = datetime(2026, 5, 25, 8, 0, tzinfo=TZ)
    anchor = Anchor(anchor_ts, "live")
    timestamps = [anchor_ts - timedelta(minutes=30) + timedelta(minutes=5 * i) for i in range(7)]
    cgm_df = pd.DataFrame({"timestamp": timestamps, "bg_mgdl": [100, 110, 120, 130, 140, 150, 160]})
    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0))

    from core.detection.windowing import Window

    gapped = Window(
        anchor=window.anchor,
        start=window.start,
        end=window.end,
        samples=window.samples,
        coverage=window.coverage,
        has_gap=True,
    )
    assert detect_meal_rise(gapped, base_config) is None


def test_detect_meal_rise_low_coverage_suppresses(base_config):
    anchor_ts = datetime(2026, 5, 25, 8, 0, tzinfo=TZ)
    anchor = Anchor(anchor_ts, "live")
    timestamps = [
        anchor_ts - timedelta(minutes=30),
        anchor_ts - timedelta(minutes=20),
        anchor_ts - timedelta(minutes=10),
        anchor_ts,
    ]
    cgm_df = pd.DataFrame(
        {"timestamp": timestamps, "bg_mgdl": [100, 110, 120, 130]}
    )
    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0))
    assert window.n_samples == 4
    assert window.coverage == pytest.approx(4 / 7, rel=1e-6)
    assert detect_meal_rise(window, base_config) is None


def test_detect_meal_rise_slow_drift_below_threshold(base_config):
    anchor_ts = datetime(2026, 5, 25, 8, 0, tzinfo=TZ)
    anchor = Anchor(anchor_ts, "live")
    bg_values = [100, 102, 104, 106, 108, 110, 112]
    timestamps = [anchor_ts - timedelta(minutes=30) + timedelta(minutes=5 * i) for i in range(7)]
    cgm_df = pd.DataFrame({"timestamp": timestamps, "bg_mgdl": bg_values})
    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0))
    assert detect_meal_rise(window, base_config) is None
