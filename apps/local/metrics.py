"""TIR and rolling-window metrics for the local dashboard."""

from __future__ import annotations

from datetime import date, tzinfo

import pandas as pd

from apps.local.dates import date_window_bounds, iter_dates_in_window
from core.metrics.cgm_metrics import time_in_range


def compute_tir_percent(bg: pd.Series, *, low: float, high: float) -> float:
    """Percent of readings in ``[low, high]`` (0–100). Empty series → 0.

    Delegates to the shared :func:`core.metrics.cgm_metrics.time_in_range`
    (the single source of truth for TIR); both return ``0.0`` on empty input.
    """
    return time_in_range(bg, low, high)


def _cgm_for_calendar_days(
    cgm: pd.DataFrame,
    *,
    dates: list[date],
) -> pd.DataFrame:
    if cgm.empty or "timestamp" not in cgm.columns:
        return cgm.iloc[0:0]
    day_set = set(dates)
    ts = pd.to_datetime(cgm["timestamp"])
    mask = ts.dt.date.isin(day_set)
    return cgm.loc[mask]


def tir_summary_for_windows(
    cgm: pd.DataFrame,
    *,
    low: float,
    high: float,
    end_date: date,
    windows: tuple[int, ...] = (7, 14, 30),
) -> dict[int, float | None]:
    """TIR percent for each rolling window ending on ``end_date``.

    Returns ``None`` for a window when there is no CGM data in that span.
    """
    if "bg_mgdl" not in cgm.columns:
        return {w: None for w in windows}

    summary: dict[int, float | None] = {}
    for window in windows:
        dates = iter_dates_in_window(end_date, window)
        subset = _cgm_for_calendar_days(cgm, dates=dates)
        if subset.empty:
            summary[window] = None
        else:
            summary[window] = compute_tir_percent(subset["bg_mgdl"], low=low, high=high)
    return summary


def cgm_in_read_bounds(
    cgm: pd.DataFrame,
    *,
    end_date: date,
    days: int,
    tz: tzinfo | None = None,
) -> pd.DataFrame:
    """Filter CGM to the half-open datetime window used by ``read_table``.

    ``tz`` defines the day boundaries (midnight). It MUST be the config
    timezone so the day-view/AGP slice agrees with the calendar-day TIR slice
    (``_cgm_for_calendar_days``); otherwise the window spans a UTC day while
    TIR spans the local day. Defaults to UTC for backward compatibility.
    """
    since, until = date_window_bounds(end_date, days, tz=tz)
    if cgm.empty:
        return cgm
    ts = pd.to_datetime(cgm["timestamp"])
    mask = (ts >= since) & (ts < until)
    return cgm.loc[mask].copy()
