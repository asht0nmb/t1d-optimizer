"""Tests for the OSS local Streamlit shell helpers (pure functions)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from apps.local.chart_prep import format_bolus_hover, slice_day_frames
from apps.local.charts.day_view import build_plotly_day_figure
from apps.local.charts.heatmap import build_plotly_heatmap_figure
from apps.local.charts.tir_trend import build_plotly_tir_trend_figure
from apps.local.dates import (
    MAX_HEATMAP_DAYS,
    clamp_heatmap_days,
    date_window_bounds,
    iter_dates_in_window,
)
from apps.local.metrics import compute_tir_percent, tir_summary_for_windows
from apps.local.navigation import list_cgm_dates, shift_day
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


def test_format_bolus_hover():
    cluster = {
        "time": datetime(2026, 4, 14, 12, 30, tzinfo=timezone.utc),
        "total_units": 2.5,
        "count": 2,
        "carbs": 45,
    }
    text = format_bolus_hover(cluster, "meal")
    assert "2.5" in text
    assert "45g" in text
    assert "meal" in text


def test_shift_day_boundaries():
    available = [date(2026, 4, 10), date(2026, 4, 11), date(2026, 4, 12)]
    assert shift_day(available[0], -1, available) == available[0]
    assert shift_day(available[-1], 1, available) == available[-1]
    assert shift_day(available[1], 1, available) == available[2]


def test_list_cgm_dates():
    tz = timezone.utc
    cgm = pd.DataFrame(
        [
            {"timestamp": datetime(2026, 4, 10, 8, 0, tzinfo=tz), "bg_mgdl": 100},
            {"timestamp": datetime(2026, 4, 12, 9, 0, tzinfo=tz), "bg_mgdl": 110},
        ]
    )
    assert list_cgm_dates(cgm) == [date(2026, 4, 10), date(2026, 4, 12)]


def _minimal_day_frames(target: date) -> dict[str, pd.DataFrame]:
    tz = timezone.utc
    ts = datetime(target.year, target.month, target.day, 10, 0, tzinfo=tz)
    cgm = pd.DataFrame(
        [
            {
                "timestamp": ts,
                "bg_mgdl": 120.0,
                "pump_serial": "p1",
                "seqnum": 1,
            },
            {
                "timestamp": ts.replace(hour=10, minute=5),
                "bg_mgdl": 125.0,
                "pump_serial": "p1",
                "seqnum": 2,
            },
        ]
    )
    return {
        "cgm": cgm,
        "bolus": pd.DataFrame(),
        "requests": pd.DataFrame(),
        "basal": pd.DataFrame(),
        "suspension": pd.DataFrame(),
        "events": pd.DataFrame(),
        "alarms": pd.DataFrame(),
        "site_issues": pd.DataFrame(),
        "cgm_gaps": pd.DataFrame(),
    }


def test_slice_day_frames_empty_cgm():
    frames = _minimal_day_frames(date(2026, 4, 14))
    frames["cgm"] = pd.DataFrame()
    assert slice_day_frames(frames, date(2026, 4, 14), view="original", low=70, high=180) is None


def test_build_plotly_day_figure_has_subplots():
    import plotly.io as pio

    target = date(2026, 4, 14)
    frames = _minimal_day_frames(target)
    fig = build_plotly_day_figure(
        frames, target.isoformat(), view="original", low=70, high=180
    )
    assert fig is not None
    assert len(fig.data) >= 1
    roundtrip = pio.from_json(fig.to_json())
    assert len(roundtrip.data) == len(fig.data)


def test_day_xlim_respects_cgm_timezone():
    from apps.local.chart_prep import day_xlim

    tz = timezone.utc
    cgm = pd.DataFrame(
        [{"timestamp": datetime(2026, 4, 14, 12, 0, tzinfo=tz), "bg_mgdl": 100.0}]
    )
    x0, x1 = day_xlim(date(2026, 4, 14), cgm)
    assert x0.tzinfo is not None


def test_build_plotly_day_figure_none_without_cgm():
    frames = _minimal_day_frames(date(2026, 4, 14))
    frames["cgm"] = pd.DataFrame()
    assert (
        build_plotly_day_figure(
            frames, "2026-04-14", view="original", low=70, high=180
        )
        is None
    )


def test_build_plotly_heatmap_figure():
    tz = timezone.utc
    rows = []
    for d in range(10, 13):
        for h in (8, 12):
            rows.append(
                {
                    "timestamp": datetime(2026, 4, d, h, 0, tzinfo=tz),
                    "bg_mgdl": 100.0 + h,
                }
            )
    cgm = pd.DataFrame(rows)
    fig = build_plotly_heatmap_figure(
        cgm, low=70, high=180, end_date=date(2026, 4, 12), days=3
    )
    assert len(fig.data) == 1


def test_build_plotly_tir_trend_figure():
    tz = timezone.utc
    cgm = pd.DataFrame(
        [
            {
                "timestamp": datetime(2026, 4, 14, 12, 0, tzinfo=tz),
                "bg_mgdl": 100.0,
            },
        ]
    )
    fig = build_plotly_tir_trend_figure(
        cgm, low=70, high=180, end_date=date(2026, 4, 14), days=7
    )
    assert len(fig.data) == 1


def test_app_helpers_import_without_streamlit():
    import apps.local.chart_prep  # noqa: F401
    import apps.local.metrics  # noqa: F401
    import apps.local.dates  # noqa: F401
