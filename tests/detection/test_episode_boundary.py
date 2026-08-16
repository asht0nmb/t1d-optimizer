"""Tests for detection/episode_boundary.py (Workstream B: algorithm-research
phase). This module is a standalone prototype, not wired into M1 — these
tests exercise it in isolation against synthetic deviation series."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from detection.episode_boundary import track_deviation_trajectory

TZ = timezone(timedelta(hours=-7), name="PDT")


def _ts(m):
    return datetime(2026, 8, 1, 12, 0, tzinfo=TZ) + timedelta(minutes=m)


def _frame(minutes, deviations):
    return pd.DataFrame(
        {"timestamp": [_ts(m) for m in minutes], "deviation_5m": deviations}
    )


class TestTrackDeviationTrajectory:
    def test_empty_frame(self):
        out = track_deviation_trajectory(pd.DataFrame(columns=["timestamp", "deviation_5m"]))
        assert out.empty

    def test_first_row_is_its_own_max_and_min_with_zero_slope(self):
        df = _frame([0], [10.0])
        out = track_deviation_trajectory(df)
        assert out.iloc[0]["running_max_deviation"] == pytest.approx(10.0)
        assert out.iloc[0]["running_min_deviation"] == pytest.approx(10.0)
        assert out.iloc[0]["slope_from_max_mgdl_per_5m"] == pytest.approx(0.0)
        assert out.iloc[0]["slope_from_min_mgdl_per_5m"] == pytest.approx(0.0)

    def test_rising_then_decaying_episode(self):
        """A classic meal-shaped deviation trajectory: rises to a peak,
        then decays. slope_from_max should stay ~0 while still rising
        (each new row IS the new max) and go negative once decay starts."""
        # 0,5,10,...,25 min: deviations rise to a peak of 40 at t=15, then
        # decay back down.
        minutes = [0, 5, 10, 15, 20, 25]
        deviations = [5.0, 15.0, 30.0, 40.0, 20.0, 5.0]
        df = _frame(minutes, deviations)
        out = track_deviation_trajectory(df)

        # While rising (t=0..15), each row sets a new max -> slope_from_max
        # is 0 at every rising point.
        for i in range(4):
            assert out.iloc[i]["slope_from_max_mgdl_per_5m"] == pytest.approx(0.0)

        # After the peak (t=20, t=25), slope_from_max is negative (decaying).
        assert out.iloc[4]["slope_from_max_mgdl_per_5m"] < 0
        assert out.iloc[5]["slope_from_max_mgdl_per_5m"] < 0
        # Decaying further (t=25 is 10 min past peak vs t=20 at 5 min past)
        # should show at least as much cumulative decline captured.
        assert out.iloc[5]["running_max_deviation"] == pytest.approx(40.0)

    def test_slope_from_max_matches_hand_computed_value(self):
        # Peak of 40 at t=0, drop to 10 at t=10 (10 minutes later).
        # raw_slope = (10 - 40) / 10 * 5 = -15 mg/dL per 5-min-tick.
        df = _frame([0, 10], [40.0, 10.0])
        out = track_deviation_trajectory(df)
        assert out.iloc[1]["slope_from_max_mgdl_per_5m"] == pytest.approx(-15.0)

    def test_slope_from_max_never_positive(self):
        df = _frame([0, 5, 10, 15], [40.0, 45.0, 42.0, 50.0])
        out = track_deviation_trajectory(df)
        assert (out["slope_from_max_mgdl_per_5m"].dropna() <= 0).all()

    def test_slope_from_min_never_negative(self):
        df = _frame([0, 5, 10, 15], [-40.0, -45.0, -30.0, -50.0])
        out = track_deviation_trajectory(df)
        assert (out["slope_from_min_mgdl_per_5m"].dropna() >= 0).all()

    def test_nan_rows_pass_through_without_resetting_trajectory(self):
        df = _frame([0, 5, 10, 15], [10.0, np.nan, 5.0, 20.0])
        out = track_deviation_trajectory(df)
        assert np.isnan(out.iloc[1]["running_max_deviation"])
        assert np.isnan(out.iloc[1]["slope_from_max_mgdl_per_5m"])
        # Row 2 (t=10) should still see the max set at row 0 (t=0), skipping
        # over the NaN row rather than treating it as a reset.
        assert out.iloc[2]["running_max_deviation"] == pytest.approx(10.0)

    def test_flat_baseline_has_zero_slopes(self):
        df = _frame([0, 5, 10, 15], [2.0, 2.0, 2.0, 2.0])
        out = track_deviation_trajectory(df)
        assert (out["slope_from_max_mgdl_per_5m"].abs() < 1e-9).all()
        assert (out["slope_from_min_mgdl_per_5m"].abs() < 1e-9).all()
