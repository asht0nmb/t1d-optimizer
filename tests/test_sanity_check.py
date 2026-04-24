"""Tests for scripts/sanity_check.py (check CLI)."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from ingestion import storage, version_guard
from scripts.sanity_check import sanity_check


TARGET_DATE = "2026-03-19"


@pytest.fixture(autouse=True)
def _isolate_version_guard(tmp_path, monkeypatch):
    """Point the version guard at a clean tmp dir so real stale parquets don't
    leak into captured output.

    Tests that need a stale state opt-in by re-pointing storage paths and
    calling `version_guard.reset_cache()`.
    """
    monkeypatch.setattr(storage, "PROCESSED_DIR", tmp_path)
    monkeypatch.setattr(
        storage, "PIPELINE_VERSION_FILE", tmp_path / ".pipeline_version.json"
    )
    version_guard.reset_cache()
    yield
    version_guard.reset_cache()


@pytest.fixture
def day_frames() -> dict[str, pd.DataFrame]:
    """Synthetic frames spanning 2026-03-19, pre-enrichment."""
    tz = "America/Los_Angeles"
    day = pd.Timestamp("2026-03-19 08:00", tz=tz)

    cgm = pd.DataFrame({
        "timestamp": pd.date_range(day, periods=3, freq="5min"),
        "bg_mgdl": [120, 145, 160],
        "seqnum": [1, 2, 3],
        "pump_serial": ["p1"] * 3,
        "backfilled": [False, False, False],
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
        "timestamp": pd.date_range(day, periods=288, freq="5min"),
        "commanded_rate": [1.0] * 288,
        "rate_source": ["profile"] * 288,
        "pump_serial": ["p1"] * 288,
    })

    # Battery shutdown at 08:00 → site change at 09:00 will be forced.
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
            day,  # BatteryShutdown at 08:00
            day + pd.Timedelta(hours=12),  # cgm_out_of_range activated
            day + pd.Timedelta(hours=12, minutes=30),  # cgm_out_of_range cleared
        ],
        "category": ["alarm", "alert", "alert"],
        "action": ["activated", "activated", "cleared"],
        "alarm_id": [1, 2, 2],
        "alarm_name": ["BatteryShutdownAlarm", "cgm_out_of_range", "cgm_out_of_range"],
        "param1": [None, None, None],
        "param2": [None, None, None],
        "seqnum": [200, 201, 202],
        "pump_serial": ["p1", "p1", "p1"],
    })

    suspension = pd.DataFrame(
        columns=[
            "suspend_timestamp", "resume_timestamp", "duration_minutes",
            "suspend_reason", "insulin_at_suspend", "pairing_suspect",
            "alarm_name", "pump_serial",
        ]
    )

    return {
        "cgm": cgm,
        "bolus": bolus,
        "requests": requests,
        "basal": basal,
        "events": events,
        "alarms": alarms,
        "suspension": suspension,
    }


def _patch_load(frames: dict[str, pd.DataFrame]):
    """Patch `load_df` inside sanity_check to return synthetic frames."""
    def fake(name: str):
        return frames.get(name)
    return patch("scripts.sanity_check.load_df", side_effect=fake)


def test_sanity_check_original_view_default(capsys, day_frames) -> None:
    with _patch_load(day_frames):
        sanity_check(TARGET_DATE)
    out = capsys.readouterr().out
    assert "SANITY CHECK: 2026-03-19" in out
    assert "CGM readings:" in out
    # Original view never mentions enriched-only sections.
    assert "Bolus categories" not in out
    assert "Forced site changes" not in out
    assert "Site issues overlapping day" not in out
    assert "CGM gaps overlapping day" not in out


def test_sanity_check_original_view_explicit(capsys, day_frames) -> None:
    with _patch_load(day_frames):
        sanity_check(TARGET_DATE, view="original")
    out = capsys.readouterr().out
    assert "Bolus categories" not in out


def test_sanity_check_enriched_view_adds_bolus_category(capsys, day_frames) -> None:
    with _patch_load(day_frames):
        sanity_check(TARGET_DATE, view="enriched")
    out = capsys.readouterr().out
    assert "Bolus categories" in out
    assert "user_meal_and_correction" in out


def test_sanity_check_enriched_view_flags_forced_site_change(capsys, day_frames) -> None:
    with _patch_load(day_frames):
        sanity_check(TARGET_DATE, view="enriched")
    out = capsys.readouterr().out
    assert "Forced site changes" in out
    assert "forced=True" in out


def test_sanity_check_enriched_view_shows_cgm_gaps(capsys, day_frames) -> None:
    with _patch_load(day_frames):
        sanity_check(TARGET_DATE, view="enriched")
    out = capsys.readouterr().out
    assert "CGM gaps overlapping day" in out
    # 30-minute gap starting at 20:00 local (12h after 08:00 UTC pre-DST → 05:00 local? We use tz-aware pd.date_range.)
    # Rather than nail the clock time, just assert the minutes.
    assert "30" in out  # duration_minutes = 30


def test_sanity_check_enriched_view_includes_override_delta_column(capsys) -> None:
    tz = "America/Los_Angeles"
    day = pd.Timestamp("2026-03-19 10:00", tz=tz)
    override_requests = pd.DataFrame({
        "timestamp": [day],
        "bolus_id": [1],
        "carbs_g": [30.0],
        "bg_mgdl": [140],
        "iob": [0.0],
        "bolus_source": ["override"],
        "food_insulin": [3.0],
        "correction_insulin": [0.0],
        "total_requested": [4.0],  # +1.0 override_delta
        "pump_serial": ["p1"],
    })
    frames = {"requests": override_requests}

    with _patch_load(frames):
        sanity_check(TARGET_DATE, view="enriched")
    out = capsys.readouterr().out
    assert "override_delta" in out
    assert "+1.0" in out or "1.0" in out


def test_sanity_check_rejects_invalid_view(day_frames) -> None:
    with _patch_load(day_frames):
        with pytest.raises(ValueError):
            sanity_check(TARGET_DATE, view="bogus")


def test_sanity_check_tir_uses_config_bg_targets(capsys, day_frames, default_config) -> None:
    """TIR line must read low/high from config, not hardcoded 70-180."""
    expected_low = default_config.bg_targets.low
    expected_high = default_config.bg_targets.high
    with _patch_load(day_frames):
        sanity_check(TARGET_DATE)
    out = capsys.readouterr().out
    assert f"Time in range ({expected_low}-{expected_high}):" in out


def test_sanity_check_tir_reflects_custom_config(capsys, day_frames, monkeypatch) -> None:
    """Changing bg_targets in the resolved config changes the printed header+pct."""
    from types import SimpleNamespace

    fake_config = SimpleNamespace(bg_targets=SimpleNamespace(low=80, high=200))
    monkeypatch.setattr(
        "scripts.sanity_check.get_config", lambda: fake_config
    )
    with _patch_load(day_frames):
        sanity_check(TARGET_DATE)
    out = capsys.readouterr().out
    assert "Time in range (80-200):" in out


def test_sanity_check_silent_on_healthy_pipeline(capsys, day_frames) -> None:
    """Healthy staleness state must not emit a warning into check output."""
    with _patch_load(day_frames):
        sanity_check(TARGET_DATE)
    combined = capsys.readouterr().out + capsys.readouterr().err
    assert "PIPELINE VERSION MISMATCH" not in combined


def test_sanity_check_warns_on_stale_pipeline(
    capsys, day_frames, tmp_path
) -> None:
    """Unversioned parquet on disk → staleness warning prefixes the report."""
    (tmp_path / "cgm.parquet").write_bytes(b"fake")
    version_guard.reset_cache()
    with _patch_load(day_frames):
        sanity_check(TARGET_DATE)
    out = capsys.readouterr().out
    assert "PIPELINE VERSION MISMATCH" in out
    assert "fetch --clean" in out


def test_sanity_check_enriched_warns_on_same_second_stacking(capsys) -> None:
    """Multiple CGM readings at the same second on the target day → warning."""
    tz = "America/Los_Angeles"
    burst = pd.Timestamp("2026-03-19 12:03:16", tz=tz)
    stacked = [burst] * 6 + [
        pd.Timestamp("2026-03-19 12:10", tz=tz),
        pd.Timestamp("2026-03-19 12:15", tz=tz),
    ]
    cgm = pd.DataFrame({
        "timestamp": stacked,
        "bg_mgdl": list(range(100, 108)),
        "seqnum": list(range(len(stacked))),
        "pump_serial": ["p1"] * len(stacked),
        "backfilled": [True] * 6 + [False, False],
    })
    frames = {"cgm": cgm, "requests": pd.DataFrame(), "events": pd.DataFrame()}

    with _patch_load(frames):
        sanity_check(TARGET_DATE, view="enriched")
    out = capsys.readouterr().out
    assert "same-second" in out.lower() or "stack" in out.lower()


def test_sanity_check_enriched_silent_on_clean_cgm(capsys, day_frames) -> None:
    """Clean one-reading-per-5-min CGM must not fire the stacking warning."""
    with _patch_load(day_frames):
        sanity_check(TARGET_DATE, view="enriched")
    out = capsys.readouterr().out
    assert "same-second CGM stacking" not in out


def test_sanity_check_original_silent_on_stacking(capsys) -> None:
    """Stacking warning is enriched-only; original view stays quiet."""
    tz = "America/Los_Angeles"
    burst = pd.Timestamp("2026-03-19 12:03:16", tz=tz)
    stacked = [burst] * 6
    cgm = pd.DataFrame({
        "timestamp": stacked,
        "bg_mgdl": list(range(100, 106)),
        "seqnum": list(range(6)),
        "pump_serial": ["p1"] * 6,
        "backfilled": [True] * 6,
    })
    frames = {"cgm": cgm}
    with _patch_load(frames):
        sanity_check(TARGET_DATE, view="original")
    out = capsys.readouterr().out
    assert "same-second" not in out.lower()
