"""Smoke tests for scripts/daily_viz.py (viz CLI).

These do not validate rendering; they confirm:
* both view modes run end-to-end without exception given minimal synthetic data;
* the enriched view uses `cgm_gaps` spans instead of re-deriving OOR shading;
* a `--view` that isn't `original`/`enriched` raises.

`plt.show` is mocked to a no-op so tests don't open a window.
"""

from __future__ import annotations

from unittest.mock import patch

import matplotlib

matplotlib.use("Agg")  # headless backend — must set before importing pyplot.

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from scripts.daily_viz import daily_viz  # noqa: E402


TARGET_DATE = "2026-03-19"


@pytest.fixture(autouse=True)
def _close_figs():
    """Guarantee we never leak matplotlib figures between tests."""
    yield
    plt.close("all")


@pytest.fixture
def day_frames() -> dict[str, pd.DataFrame]:
    tz = "America/Los_Angeles"
    day = pd.Timestamp("2026-03-19 08:00", tz=tz)

    cgm = pd.DataFrame({
        "timestamp": pd.date_range(day, periods=12, freq="5min"),
        "bg_mgdl": [120, 130, 145, 160, 170, 155, 140, 135, 150, 165, 170, 180],
        "seqnum": list(range(12)),
        "pump_serial": ["p1"] * 12,
        "backfilled": [False] * 12,
    })
    bolus = pd.DataFrame({
        "timestamp": [day + pd.Timedelta(hours=4)],
        "insulin_units": [3.5],
        "bolus_id": [1],
        "pump_serial": ["p1"],
    })
    requests = pd.DataFrame({
        "timestamp": [day + pd.Timedelta(hours=4)],
        "bolus_id": [1],
        "carbs_g": [30.0],
        "bg_mgdl": [140],
        "iob": [0.0],
        "bolus_source": ["user"],
        "food_insulin": [3.0],
        "correction_insulin": [0.5],
        "total_requested": [3.5],
        "pump_serial": ["p1"],
    })
    basal = pd.DataFrame({
        "timestamp": pd.date_range(day, periods=24, freq="5min"),
        "commanded_rate": [1.0] * 24,
        "rate_source": ["profile"] * 24,
        "pump_serial": ["p1"] * 24,
    })
    # BatteryShutdown at 08:00 → site change at 09:00 (forced).
    # cgm_out_of_range pair at 12:00–12:30.
    events = pd.DataFrame({
        "timestamp": [day + pd.Timedelta(hours=1)],
        "event_type": ["site_change"],
        "event_subtype": ["tubing"],
        "previous_mode": [None],
        "details": [None],
        "seqnum": [100],
        "pump_serial": ["p1"],
    })
    alarms = pd.DataFrame({
        "timestamp": [
            day,
            day + pd.Timedelta(hours=4),
            day + pd.Timedelta(hours=4, minutes=30),
        ],
        "category": ["alarm", "alert", "alert"],
        "action": ["activated", "activated", "cleared"],
        "alarm_id": [1, 2, 2],
        "alarm_name": ["BatteryShutdownAlarm", "cgm_out_of_range", "cgm_out_of_range"],
        "param1": [None, None, None],
        "param2": [None, None, None],
        "seqnum": [200, 201, 202],
        "pump_serial": ["p1"] * 3,
    })
    suspension = pd.DataFrame(columns=[
        "suspend_timestamp", "resume_timestamp", "duration_minutes",
        "suspend_reason", "insulin_at_suspend", "pairing_suspect",
        "alarm_name", "pump_serial",
    ])
    return {
        "cgm": cgm, "bolus": bolus, "requests": requests, "basal": basal,
        "events": events, "alarms": alarms, "suspension": suspension,
    }


def _patch_load(frames: dict[str, pd.DataFrame]):
    def fake(name: str):
        return frames.get(name)
    return patch("scripts.daily_viz.load_df", side_effect=fake)


def test_daily_viz_original_runs(day_frames) -> None:
    with _patch_load(day_frames), patch("scripts.daily_viz.plt.show"):
        daily_viz(TARGET_DATE)


def test_daily_viz_enriched_runs(day_frames) -> None:
    with _patch_load(day_frames), patch("scripts.daily_viz.plt.show"):
        daily_viz(TARGET_DATE, view="enriched")


def test_daily_viz_rejects_invalid_view(day_frames) -> None:
    with _patch_load(day_frames), patch("scripts.daily_viz.plt.show"):
        with pytest.raises(ValueError):
            daily_viz(TARGET_DATE, view="bogus")


def test_daily_viz_enriched_handles_empty_day() -> None:
    """An empty day (no CGM) must return without rendering."""
    with _patch_load({}), patch("scripts.daily_viz.plt.show") as show:
        daily_viz(TARGET_DATE, view="enriched")
    show.assert_not_called()


def test_daily_viz_enriched_skips_raw_oor_shading(day_frames) -> None:
    """Enriched mode must draw CGM gap spans from `cgm_gaps`, not re-derive.

    We assert the *original* code path is not taken: the internal helper
    that shades from raw alarm pairs (`_shade_oor_from_alarms`) must not be
    invoked in enriched mode. The enriched helper (`_shade_oor_from_gaps`)
    must be.
    """
    with _patch_load(day_frames), patch("scripts.daily_viz.plt.show"), \
            patch("scripts.daily_viz._shade_oor_from_alarms") as from_alarms, \
            patch("scripts.daily_viz._shade_oor_from_gaps") as from_gaps:
        daily_viz(TARGET_DATE, view="enriched")
    from_alarms.assert_not_called()
    from_gaps.assert_called_once()


def test_daily_viz_original_uses_alarm_based_oor(day_frames) -> None:
    with _patch_load(day_frames), patch("scripts.daily_viz.plt.show"), \
            patch("scripts.daily_viz._shade_oor_from_alarms") as from_alarms, \
            patch("scripts.daily_viz._shade_oor_from_gaps") as from_gaps:
        daily_viz(TARGET_DATE, view="original")
    from_alarms.assert_called_once()
    from_gaps.assert_not_called()
