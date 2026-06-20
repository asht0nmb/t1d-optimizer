"""Tests for core/metrics/agp.py — pure AGP hourly percentile profile."""

from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from core.metrics.agp import agp_profile

EXPECTED_COLUMNS = ["hour", "p05", "p25", "p50", "p75", "p95", "n"]


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["timestamp", "bg_mgdl"])


def _three_day_frame() -> pd.DataFrame:
    """3 days (2026-04-12..14). Hour 6 has exactly one reading per day:
    100, 110, 120 — so percentiles over [100, 110, 120] are known.
    Hour 12 has one reading per day at 150."""
    tz = timezone.utc
    rows = []
    for offset, val in zip(range(3), (100.0, 110.0, 120.0)):
        rows.append(
            {
                "timestamp": datetime(2026, 4, 12 + offset, 6, 0, tzinfo=tz),
                "bg_mgdl": val,
            }
        )
        rows.append(
            {
                "timestamp": datetime(2026, 4, 12 + offset, 12, 30, tzinfo=tz),
                "bg_mgdl": 150.0,
            }
        )
    return _frame(rows)


def test_agp_profile_columns_and_shape():
    cgm = _three_day_frame()
    out = agp_profile(
        cgm, days=3, end_date=date(2026, 4, 14), tz="UTC",
        bucket_minutes=60, smooth=False,
    )
    assert list(out.columns) == EXPECTED_COLUMNS
    # Only hours 6 and 12 have data; hours without readings are omitted.
    assert out["hour"].tolist() == [6, 12]
    assert out["n"].tolist() == [3, 3]


def test_agp_profile_percentiles_match_numpy_linear():
    cgm = _three_day_frame()
    out = agp_profile(
        cgm, days=3, end_date=date(2026, 4, 14), tz="UTC",
        bucket_minutes=60, smooth=False,
    )
    row = out.loc[out["hour"] == 6].iloc[0]
    vals = np.array([100.0, 110.0, 120.0])
    assert row["p50"] == pytest.approx(110.0)
    assert row["p05"] == pytest.approx(101.0)  # linear interpolation
    for col, q in (("p05", 5), ("p25", 25), ("p50", 50), ("p75", 75), ("p95", 95)):
        assert row[col] == pytest.approx(
            float(np.percentile(vals, q, method="linear"))
        )


def test_agp_profile_sorted_by_hour():
    tz = timezone.utc
    cgm = _frame(
        [
            {"timestamp": datetime(2026, 4, 14, 22, 0, tzinfo=tz), "bg_mgdl": 90.0},
            {"timestamp": datetime(2026, 4, 14, 3, 0, tzinfo=tz), "bg_mgdl": 95.0},
        ]
    )
    out = agp_profile(
        cgm, days=1, end_date=date(2026, 4, 14), tz="UTC",
        bucket_minutes=60, smooth=False,
    )
    assert out["hour"].tolist() == [3, 22]


def test_agp_profile_honors_timezone():
    # 14:00 UTC on a June day is 07:00 in America/Los_Angeles (PDT, UTC-7).
    cgm = _frame(
        [
            {
                "timestamp": datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc),
                "bg_mgdl": 130.0,
            }
        ]
    )
    out = agp_profile(
        cgm, days=1, end_date=date(2026, 6, 10), tz="America/Los_Angeles",
        bucket_minutes=60, smooth=False,
    )
    assert out["hour"].tolist() == [7]
    assert out.iloc[0]["n"] == 1


def test_agp_profile_excludes_readings_outside_window():
    tz = timezone.utc
    cgm = _frame(
        [
            # Inside 3-day window ending 2026-04-14 (i.e. 04-12..04-14):
            {"timestamp": datetime(2026, 4, 12, 6, 0, tzinfo=tz), "bg_mgdl": 100.0},
            # Before the window:
            {"timestamp": datetime(2026, 4, 11, 6, 0, tzinfo=tz), "bg_mgdl": 999.0},
            # After the window:
            {"timestamp": datetime(2026, 4, 15, 6, 0, tzinfo=tz), "bg_mgdl": 999.0},
        ]
    )
    out = agp_profile(
        cgm, days=3, end_date=date(2026, 4, 14), tz="UTC",
        bucket_minutes=60, smooth=False,
    )
    assert out["hour"].tolist() == [6]
    assert out.iloc[0]["n"] == 1
    assert out.iloc[0]["p50"] == pytest.approx(100.0)


def test_agp_profile_empty_frame():
    out = agp_profile(pd.DataFrame(), days=14, end_date=date(2026, 4, 14), tz="UTC")
    assert list(out.columns) == EXPECTED_COLUMNS
    assert out.empty


# --- Task 6: 15-min buckets + circular smoothing -------------------------


def _dense_frame(end_date: date, days: int, interval_min: int = 5) -> pd.DataFrame:
    """One reading every ``interval_min`` minutes over ``days`` UTC days.

    BG follows a smooth diurnal sinusoid plus a small high-frequency jitter,
    so smoothing has something to reduce and bins are densely populated.
    """
    tz = timezone.utc
    start = pd.Timestamp(
        datetime(end_date.year, end_date.month, end_date.day, tzinfo=tz)
    ) - pd.Timedelta(days=days - 1)
    n = days * 24 * 60 // interval_min
    rows = []
    for i in range(n):
        ts = start + pd.Timedelta(minutes=i * interval_min)
        minute_of_day = ts.hour * 60 + ts.minute
        frac = minute_of_day / 1440.0
        base = 140.0 + 40.0 * np.sin(2 * np.pi * frac)
        jitter = 15.0 * ((i % 2) * 2 - 1)  # +/-15 alternating, bin-to-bin noise
        rows.append({"timestamp": ts.to_pydatetime(), "bg_mgdl": base + jitter})
    return _frame(rows)


def test_agp_profile_15min_has_96_buckets():
    cgm = _dense_frame(date(2026, 4, 14), days=3)
    out = agp_profile(
        cgm, days=3, end_date=date(2026, 4, 14), tz="UTC",
        bucket_minutes=15, smooth=False,
    )
    assert len(out) == 96
    # hour column is the fractional hour-of-day of the bucket start.
    assert out["hour"].iloc[0] == pytest.approx(0.0)
    assert out["hour"].iloc[1] == pytest.approx(0.25)
    assert out["hour"].iloc[-1] == pytest.approx(23.75)
    assert list(out.columns) == EXPECTED_COLUMNS


def test_agp_profile_default_is_15min_smoothed():
    cgm = _dense_frame(date(2026, 4, 14), days=3)
    out = agp_profile(cgm, days=3, end_date=date(2026, 4, 14), tz="UTC")
    # Defaults: 15-min buckets (96) + smoothing on.
    assert len(out) == 96


def test_agp_profile_smoothing_reduces_bin_to_bin_variance():
    cgm = _dense_frame(date(2026, 4, 14), days=3)
    raw = agp_profile(
        cgm, days=3, end_date=date(2026, 4, 14), tz="UTC",
        bucket_minutes=15, smooth=False,
    )
    sm = agp_profile(
        cgm, days=3, end_date=date(2026, 4, 14), tz="UTC",
        bucket_minutes=15, smooth=True,
    )

    def bin_to_bin_var(df: pd.DataFrame, col: str) -> float:
        return float(np.var(np.diff(df[col].to_numpy())))

    for col in ("p05", "p25", "p50", "p75", "p95"):
        assert bin_to_bin_var(sm, col) < bin_to_bin_var(raw, col)


def test_agp_profile_smoothing_is_circular():
    """The smoothed first bin must depend on the last bins (wrap-around).

    Build a profile that is flat everywhere except a spike in the final bin;
    circular smoothing must bleed that spike into the first bin.
    """
    tz = timezone.utc
    start = pd.Timestamp(datetime(2026, 4, 12, tzinfo=tz))
    rows = []
    # Flat baseline across all 96 bins for 3 days.
    n = 3 * 24 * 60 // 5
    for i in range(n):
        ts = start + pd.Timedelta(minutes=i * 5)
        rows.append({"timestamp": ts.to_pydatetime(), "bg_mgdl": 100.0})
    # Add a tall spike only in the last 15-min bin (23:45-00:00) each day.
    for d in range(3):
        for m in (45, 50, 55):
            ts = datetime(2026, 4, 12 + d, 23, m, tzinfo=tz)
            rows.append({"timestamp": ts, "bg_mgdl": 400.0})
    cgm = _frame(rows)
    sm = agp_profile(
        cgm, days=3, end_date=date(2026, 4, 14), tz="UTC",
        bucket_minutes=15, smooth=True, smooth_window_bins=5,
    )
    first = sm.loc[np.isclose(sm["hour"], 0.0)].iloc[0]
    # The first bin's median should be lifted above the flat 100 baseline
    # because the wrap-around window reaches the 23:45 spike.
    assert first["p50"] > 100.0


def test_agp_profile_legacy_path_unchanged_golden():
    """bucket_minutes=60, smooth=False reproduces the original output exactly."""
    cgm = _three_day_frame()
    out = agp_profile(
        cgm, days=3, end_date=date(2026, 4, 14), tz="UTC",
        bucket_minutes=60, smooth=False,
    )
    assert out["hour"].tolist() == [6, 12]
    assert out["n"].tolist() == [3, 3]
    row6 = out.loc[out["hour"] == 6].iloc[0]
    assert row6["p05"] == pytest.approx(101.0)
    assert row6["p50"] == pytest.approx(110.0)
    assert row6["p95"] == pytest.approx(119.0)
    row12 = out.loc[out["hour"] == 12].iloc[0]
    assert row12["p50"] == pytest.approx(150.0)
