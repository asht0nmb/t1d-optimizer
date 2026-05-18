"""Tests for the OSS local Streamlit shell helpers (pure functions)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from apps.local.dates import (
    MAX_HEATMAP_DAYS,
    clamp_heatmap_days,
    date_window_bounds,
    iter_dates_in_window,
)
from apps.local.metrics import compute_tir_percent, tir_summary_for_windows
from core.storage.memory import InMemoryStorage


def test_compute_tir_percent_basic():
    bg = pd.Series([70.0, 100.0, 180.0, 200.0])
    assert compute_tir_percent(bg, low=70, high=180) == 75.0


def test_compute_tir_percent_empty():
    assert compute_tir_percent(pd.Series(dtype=float), low=70, high=180) == 0.0


def test_compute_tir_percent_all_in_range():
    bg = pd.Series([70.0, 125.0, 180.0])
    assert compute_tir_percent(bg, low=70, high=180) == 100.0


def test_iter_dates_in_window():
    end = date(2026, 4, 14)
    days = iter_dates_in_window(end, 3)
    assert days == [date(2026, 4, 12), date(2026, 4, 13), date(2026, 4, 14)]


def test_date_window_bounds():
    end = date(2026, 4, 14)
    since, until = date_window_bounds(end, days=7)
    assert since.date() == date(2026, 4, 8)
    assert until.date() == date(2026, 4, 15)


def test_clamp_heatmap_days():
    assert clamp_heatmap_days(30) == 30
    assert clamp_heatmap_days(120) == MAX_HEATMAP_DAYS
    assert clamp_heatmap_days(0) == 1


def test_tir_summary_for_windows():
    tz = timezone.utc
    rows = []
    for d in range(10, 15):
        rows.append(
            {
                "timestamp": datetime(2026, 4, d, 12, 0, tzinfo=tz),
                "bg_mgdl": 100.0,
                "pump_serial": "p1",
                "seqnum": d,
            }
        )
    cgm = pd.DataFrame(rows)
    summary = tir_summary_for_windows(
        cgm,
        low=70,
        high=180,
        end_date=date(2026, 4, 14),
        windows=(7, 14),
    )
    assert summary[7] == 100.0
    assert summary[14] == 100.0


def test_tir_summary_missing_days_returns_none():
    cgm = pd.DataFrame(
        columns=["timestamp", "bg_mgdl", "pump_serial", "seqnum"]
    )
    summary = tir_summary_for_windows(
        cgm,
        low=70,
        high=180,
        end_date=date(2026, 4, 14),
        windows=(7,),
    )
    assert summary[7] is None


def test_load_frames_empty_storage():
    from apps.local.data import load_view_frames

    storage = InMemoryStorage()
    frames = load_view_frames(storage, view="original")
    assert "cgm" in frames
    assert frames["cgm"].empty


def test_doctor_status_empty_root(tmp_path: Path):
    from apps.local.doctor_status import collect_doctor_status

    status = collect_doctor_status(tmp_path)
    assert status["parquet_count"] == 0
    assert status["ok"] is False


def test_app_helpers_import_without_streamlit():
    """Pure helper modules must import without streamlit installed."""
    import apps.local.metrics  # noqa: F401
    import apps.local.dates  # noqa: F401
