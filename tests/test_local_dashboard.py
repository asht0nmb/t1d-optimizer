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


def test_cgm_in_read_bounds_windows_by_config_timezone():
    """Day window must slice the config-local day, not the UTC day.

    Readings at 22:00-23:55 America/Los_Angeles on Apr 14 land on Apr 15 in
    UTC. A 1-day window ending Apr 14 must include them (local day), and must
    agree with the TIR slice (`_cgm_for_calendar_days` via tir_summary).
    """
    from zoneinfo import ZoneInfo

    from apps.local.metrics import cgm_in_read_bounds

    la = ZoneInfo("America/Los_Angeles")
    rows = []
    # 22:00, 22:30, 23:00, 23:30, 23:55 local on Apr 14 (= Apr 15 05:00+ UTC).
    for hh, mm in ((22, 0), (22, 30), (23, 0), (23, 30), (23, 55)):
        rows.append(
            {
                "timestamp": datetime(2026, 4, 14, hh, mm, tzinfo=la),
                "bg_mgdl": 120.0,
                "pump_serial": "p1",
                "seqnum": hh * 60 + mm,
            }
        )
    cgm = pd.DataFrame(rows)

    subset = cgm_in_read_bounds(
        cgm, end_date=date(2026, 4, 14), days=1, tz=la
    )
    # All 5 local-day readings must be in the 1-day window.
    assert len(subset) == 5

    # Agreement with the calendar-day (TIR) slice for the same local day.
    from apps.local.metrics import _cgm_for_calendar_days

    cal = _cgm_for_calendar_days(cgm, dates=[date(2026, 4, 14)])
    assert len(cal) == len(subset) == 5


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


def test_build_plotly_heatmap_figure_uses_median():
    """Heatmap cell aggregates with median (robust) to match the web shell."""
    tz = timezone.utc
    # One hour-cell (2026-04-12 08:00) with values where mean != median:
    # [100, 100, 250] -> mean = 150, median = 100.
    cgm = pd.DataFrame(
        [
            {"timestamp": datetime(2026, 4, 12, 8, 0, tzinfo=tz), "bg_mgdl": 100.0},
            {"timestamp": datetime(2026, 4, 12, 8, 20, tzinfo=tz), "bg_mgdl": 100.0},
            {"timestamp": datetime(2026, 4, 12, 8, 40, tzinfo=tz), "bg_mgdl": 250.0},
        ]
    )
    fig = build_plotly_heatmap_figure(
        cgm, low=70, high=180, end_date=date(2026, 4, 12), days=1
    )
    z = fig.data[0].z
    # Hour 8 is index 8 in the reindexed 0..23 y-axis; single date column.
    cell = z[8][0]
    assert cell == pytest.approx(100.0)  # median, not mean (150)
    # Hover label should say "Median", not "Mean".
    assert "Median" in fig.data[0].hovertemplate
    assert "Mean" not in fig.data[0].hovertemplate


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
    import apps.local.charts.insulin  # noqa: F401
    import apps.local.charts.agp  # noqa: F401
    import apps.local.charts.compare  # noqa: F401


# ─────────────────────────────────────────────────────────────────────────
# Insulin page chart builder
# ─────────────────────────────────────────────────────────────────────────


def _insulin_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    tz = timezone.utc
    bolus = pd.DataFrame(
        [
            {
                "timestamp": datetime(2026, 4, 13, 8, 0, tzinfo=tz),
                "insulin_units": 2.5,
                "bolus_id": 1,
                "pump_serial": "p1",
            },
            {
                "timestamp": datetime(2026, 4, 13, 18, 0, tzinfo=tz),
                "insulin_units": 3.0,
                "bolus_id": 2,
                "pump_serial": "p1",
            },
            {
                "timestamp": datetime(2026, 4, 14, 12, 0, tzinfo=tz),
                "insulin_units": 4.0,
                "bolus_id": 3,
                "pump_serial": "p1",
            },
        ]
    )
    # 12 five-minute basal rows at 1.0 U/hr on Apr 13 → 12 * 1.0 * 5/60 = 1.0 U.
    basal_rows = [
        {
            "timestamp": datetime(2026, 4, 13, 0, 5 * i, tzinfo=tz),
            "commanded_rate": 1.0,
            "rate_source": "profile",
            "pump_serial": "p1",
        }
        for i in range(12)
    ]
    basal = pd.DataFrame(basal_rows)
    return bolus, basal


def test_build_plotly_insulin_figure_traces_and_totals():
    from apps.local.charts.insulin import build_plotly_insulin_figure

    bolus, basal = _insulin_frames()
    fig = build_plotly_insulin_figure(
        bolus, basal, end_date=date(2026, 4, 14), days=3
    )
    bars = [t for t in fig.data if t.type == "bar"]
    assert len(bars) == 2
    by_name = {t.name: t for t in bars}
    assert set(by_name) == {"Bolus", "Basal"}
    # One bar per day in window (zero-filled days included).
    for trace in bars:
        assert len(trace.x) == 3
        assert len(trace.y) == 3
    # Apr 13: bolus 2.5 + 3.0 = 5.5 U; basal 12 rows @ 1.0 U/hr * 5min = 1.0 U.
    bolus_y = dict(zip([str(x) for x in by_name["Bolus"].x], by_name["Bolus"].y))
    basal_y = dict(zip([str(x) for x in by_name["Basal"].x], by_name["Basal"].y))
    assert bolus_y["2026-04-13"] == pytest.approx(5.5)
    assert bolus_y["2026-04-14"] == pytest.approx(4.0)
    assert bolus_y["2026-04-12"] == pytest.approx(0.0)
    assert basal_y["2026-04-13"] == pytest.approx(1.0)
    assert basal_y["2026-04-12"] == pytest.approx(0.0)


def test_build_plotly_insulin_figure_empty_frames():
    from apps.local.charts.insulin import build_plotly_insulin_figure

    fig = build_plotly_insulin_figure(
        pd.DataFrame(), pd.DataFrame(), end_date=date(2026, 4, 14), days=7
    )
    bars = [t for t in fig.data if t.type == "bar"]
    assert len(bars) == 2
    for trace in bars:
        assert len(trace.x) == 7
        assert all(v == 0.0 for v in trace.y)


# ─────────────────────────────────────────────────────────────────────────
# AGP page chart builder
# ─────────────────────────────────────────────────────────────────────────


def _agp_cgm_frame() -> pd.DataFrame:
    # Three days, readings at hours 6 and 12 (UTC) with known spreads.
    rows = []
    for day in (12, 13, 14):
        for hour, values in ((6, (100, 110, 120)), (12, (150, 160, 170))):
            for i, bg in enumerate(values):
                rows.append(
                    {
                        "timestamp": datetime(
                            2026, 4, day, hour, 5 * i, tzinfo=timezone.utc
                        ),
                        "bg_mgdl": bg,
                        "pump_serial": "p1",
                    }
                )
    return pd.DataFrame(rows)


def test_build_plotly_agp_figure_bands_and_median():
    from apps.local.charts.agp import build_plotly_agp_figure

    fig = build_plotly_agp_figure(
        _agp_cgm_frame(),
        low=70,
        high=180,
        end_date=date(2026, 4, 14),
        days=3,
        tz="UTC",
    )
    names = [t.name for t in fig.data]
    assert "Median" in names
    assert any("25" in (n or "") and "75" in (n or "") for n in names)
    assert any("5" in (n or "") and "95" in (n or "") for n in names)
    median = next(t for t in fig.data if t.name == "Median")
    # The chart now renders the 15-min smoothed profile on a continuous
    # fractional-hour axis (exact percentile values are covered by
    # tests/core/test_agp.py). The fixture only populates hours 6 and 12, so
    # assert both time-of-day regions appear and the smoothed medians stay
    # within the data's BG span (100–170).
    xs = list(median.x)
    ys = list(median.y)
    assert len(xs) >= 2
    assert all(95.0 <= y <= 175.0 for y in ys)
    assert any(abs(x - 6.0) < 0.01 for x in xs)
    assert any(abs(x - 12.0) < 0.01 for x in xs)


def test_build_plotly_agp_figure_empty_cgm():
    from apps.local.charts.agp import build_plotly_agp_figure

    fig = build_plotly_agp_figure(
        pd.DataFrame(),
        low=70,
        high=180,
        end_date=date(2026, 4, 14),
        days=14,
        tz="UTC",
    )
    assert isinstance(fig.data, tuple)  # placeholder figure, no crash


# ─────────────────────────────────────────────────────────────────────────
# Compare page chart builder
# ─────────────────────────────────────────────────────────────────────────


def _compare_day(day: int, base: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "timestamp": datetime(2026, 4, day, 8, 5 * i, tzinfo=timezone.utc),
                "bg_mgdl": base + i,
                "pump_serial": "p1",
            }
            for i in range(6)
        ]
    )


def test_build_plotly_compare_figure_two_traces():
    from apps.local.charts.compare import build_plotly_compare_figure

    fig = build_plotly_compare_figure(
        _compare_day(13, 100),
        _compare_day(14, 140),
        date_a=date(2026, 4, 13),
        date_b=date(2026, 4, 14),
        low=70,
        high=180,
    )
    lines = [t for t in fig.data if t.mode and "lines" in t.mode]
    assert len(lines) == 2
    names = {t.name for t in lines}
    assert names == {"2026-04-13", "2026-04-14"}
    # Both traces share a minutes-since-midnight x-axis (same day overlay).
    for t in lines:
        assert min(t.x) >= 0 and max(t.x) < 24 * 60


def test_build_plotly_compare_figure_one_empty_day():
    from apps.local.charts.compare import build_plotly_compare_figure

    fig = build_plotly_compare_figure(
        _compare_day(13, 100),
        pd.DataFrame(),
        date_a=date(2026, 4, 13),
        date_b=date(2026, 4, 14),
        low=70,
        high=180,
    )
    lines = [t for t in fig.data if t.mode and "lines" in t.mode]
    assert len(lines) == 1


# ─────────────────────────────────────────────────────────────────────────
# Clinical report page — time-in-bands bar
# ─────────────────────────────────────────────────────────────────────────


def test_build_time_in_bands_bar_segments():
    from apps.local.charts.report import build_time_in_bands_bar

    # 5 bins as percentages summing to 100.
    fig = build_time_in_bands_bar(
        tbr2=2.0, tbr1=8.0, tir=70.0, tar1=15.0, tar2=5.0
    )
    bars = [t for t in fig.data if t.type == "bar"]
    # one stacked segment per band
    assert len(bars) == 5
    names = {t.name for t in bars}
    assert {"Very low", "Low", "In range", "High", "Very high"} <= names
    # values match inputs
    by_name = {t.name: float(t.x[0]) for t in bars}
    assert by_name["In range"] == pytest.approx(70.0)
    assert by_name["Very low"] == pytest.approx(2.0)


def test_report_chart_imports_without_streamlit():
    import apps.local.charts.report  # noqa: F401
