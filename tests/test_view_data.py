"""Tests for the shared view-mode helper (ingestion/view_data.py)."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from ingestion.view_data import (
    ENRICHED_COLUMNS,
    VIEW_MODES,
    ensure_enriched,
    load_frames,
    strip_enriched_columns,
)


# ─────────────────────────────────────────────────────────────────────────────
# strip_enriched_columns
# ─────────────────────────────────────────────────────────────────────────────

def test_strip_enriched_columns_removes_known_columns() -> None:
    df = pd.DataFrame({
        "timestamp": [pd.Timestamp("2026-03-19", tz="UTC")],
        "carbs_g": [30.0],
        "bolus_source": ["user"],
        "bolus_category": ["user_meal"],
        "override_delta": [0.0],
    })
    out = strip_enriched_columns("requests", df)
    assert "bolus_category" not in out.columns
    assert "override_delta" not in out.columns
    assert "bolus_source" in out.columns  # not an enrichment column


def test_strip_enriched_columns_noop_when_absent() -> None:
    df = pd.DataFrame({"timestamp": [pd.Timestamp("2026-03-19", tz="UTC")]})
    out = strip_enriched_columns("requests", df)
    assert list(out.columns) == ["timestamp"]


def test_strip_enriched_columns_handles_none() -> None:
    assert strip_enriched_columns("requests", None) is None


def test_enriched_column_catalog_covers_known_names() -> None:
    # Guards against accidental catalog shrinkage when refactoring.
    assert "bolus_category" in ENRICHED_COLUMNS["requests"]
    assert "override_delta" in ENRICHED_COLUMNS["requests"]
    assert "forced_by_alarm" in ENRICHED_COLUMNS["events"]


# ─────────────────────────────────────────────────────────────────────────────
# ensure_enriched
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def raw_frames() -> dict[str, pd.DataFrame]:
    """A minimal set of frames mimicking pre-enrichment parquets."""
    ts_req = pd.Timestamp("2026-03-19 12:00", tz="UTC")
    ts_ev = pd.Timestamp("2026-03-19 09:00", tz="UTC")
    ts_alarm = pd.Timestamp("2026-03-19 08:00", tz="UTC")

    requests = pd.DataFrame({
        "timestamp": [ts_req],
        "bolus_id": [1],
        "carbs_g": [30.0],
        "bg_mgdl": [140],
        "iob": [0.0],
        "bolus_source": ["user"],
        "food_insulin": [3.0],
        "correction_insulin": [0.0],
        "total_requested": [3.0],
        "pump_serial": ["p1"],
    })

    events = pd.DataFrame({
        "timestamp": [ts_ev],
        "event_type": ["site_change"],
        "event_subtype": ["tubing"],
        "previous_mode": [None],
        "details": [None],
        "seqnum": [10],
        "pump_serial": ["p1"],
    })

    alarms = pd.DataFrame({
        "timestamp": [ts_alarm, ts_alarm + pd.Timedelta(minutes=5)],
        "category": ["alarm", "alarm"],
        "action": ["activated", "activated"],
        "alarm_id": [1, 1],
        "alarm_name": ["BatteryShutdownAlarm", "BatteryShutdownAlarm"],
        "param1": [None, None],
        "param2": [None, None],
        "seqnum": [1, 2],
        "pump_serial": ["p1", "p1"],
    })

    return {"requests": requests, "events": events, "alarms": alarms}


def test_ensure_enriched_backfills_requests_columns(raw_frames, default_config) -> None:
    out = ensure_enriched(raw_frames, default_config)
    assert "bolus_category" in out["requests"].columns
    assert "override_delta" in out["requests"].columns
    assert out["requests"]["bolus_category"].iloc[0] == "user_meal"


def test_ensure_enriched_backfills_events_column(raw_frames, default_config) -> None:
    out = ensure_enriched(raw_frames, default_config)
    assert "forced_by_alarm" in out["events"].columns
    # site_change at 09:00 is within 120 min of BatteryShutdownAlarm at 08:00 → forced.
    assert out["events"]["forced_by_alarm"].iloc[0] is True


def test_ensure_enriched_builds_missing_tables(raw_frames, default_config) -> None:
    out = ensure_enriched(raw_frames, default_config)
    assert "site_issues" in out
    assert "cgm_gaps" in out
    assert isinstance(out["site_issues"], pd.DataFrame)
    assert isinstance(out["cgm_gaps"], pd.DataFrame)


def test_ensure_enriched_preserves_already_enriched_frames(default_config) -> None:
    requests = pd.DataFrame({
        "timestamp": [pd.Timestamp("2026-03-19", tz="UTC")],
        "bolus_id": [1],
        "carbs_g": [0.0],
        "bg_mgdl": [140],
        "iob": [0.0],
        "bolus_source": ["auto"],
        "food_insulin": [0.0],
        "correction_insulin": [1.0],
        "total_requested": [1.0],
        "pump_serial": ["p1"],
        "bolus_category": ["PRE_EXISTING"],
        "override_delta": [float("nan")],
    })
    frames = {"requests": requests}
    out = ensure_enriched(frames, default_config)
    assert out["requests"]["bolus_category"].iloc[0] == "PRE_EXISTING"


def test_ensure_enriched_does_not_mutate_input(raw_frames, default_config) -> None:
    before = set(raw_frames["requests"].columns)
    ensure_enriched(raw_frames, default_config)
    after = set(raw_frames["requests"].columns)
    assert before == after


def test_ensure_enriched_handles_empty_dict(default_config) -> None:
    out = ensure_enriched({}, default_config)
    # No alarms → empty site_issues / cgm_gaps, still emitted as DataFrames.
    assert isinstance(out.get("site_issues"), pd.DataFrame)
    assert out["site_issues"].empty
    assert isinstance(out.get("cgm_gaps"), pd.DataFrame)
    assert out["cgm_gaps"].empty


# ─────────────────────────────────────────────────────────────────────────────
# load_frames
# ─────────────────────────────────────────────────────────────────────────────

def test_load_frames_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError):
        load_frames(mode="bogus")  # type: ignore[arg-type]


def test_view_modes_exported() -> None:
    assert set(VIEW_MODES) == {"original", "enriched"}


def test_load_frames_original_strips_enriched_columns(default_config) -> None:
    pre_enriched_requests = pd.DataFrame({
        "timestamp": [pd.Timestamp("2026-03-19", tz="UTC")],
        "bolus_id": [1],
        "carbs_g": [30.0],
        "bg_mgdl": [140],
        "iob": [0.0],
        "bolus_source": ["user"],
        "food_insulin": [3.0],
        "correction_insulin": [0.0],
        "total_requested": [3.0],
        "pump_serial": ["p1"],
        "bolus_category": ["user_meal"],
        "override_delta": [float("nan")],
    })

    def fake_load(name: str):
        if name == "requests":
            return pre_enriched_requests
        return None

    with patch("ingestion.view_data.load_df", side_effect=fake_load):
        frames = load_frames(mode="original", config=default_config)

    assert "bolus_category" not in frames["requests"].columns
    assert "override_delta" not in frames["requests"].columns
    # Base columns preserved.
    assert "bolus_source" in frames["requests"].columns


def test_load_frames_enriched_backfills_when_disk_is_raw(default_config) -> None:
    raw_requests = pd.DataFrame({
        "timestamp": [pd.Timestamp("2026-03-19 12:00", tz="UTC")],
        "bolus_id": [1],
        "carbs_g": [30.0],
        "bg_mgdl": [140],
        "iob": [0.0],
        "bolus_source": ["user"],
        "food_insulin": [3.0],
        "correction_insulin": [0.0],
        "total_requested": [3.0],
        "pump_serial": ["p1"],
    })

    def fake_load(name: str):
        return raw_requests if name == "requests" else None

    with patch("ingestion.view_data.load_df", side_effect=fake_load):
        frames = load_frames(mode="enriched", config=default_config)

    assert "bolus_category" in frames["requests"].columns


def test_load_frames_returns_all_known_keys(default_config) -> None:
    with patch("ingestion.view_data.load_df", return_value=None):
        frames = load_frames(mode="enriched", config=default_config)
    for key in ("cgm", "bolus", "requests", "basal", "suspension",
                "events", "alarms", "site_issues", "cgm_gaps"):
        assert key in frames
        assert isinstance(frames[key], pd.DataFrame)
