"""Prepare single-day slices and summary stats for interactive charts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from core.metrics.cgm_metrics import time_in_range
from ingestion.view_data import ViewMode
from scripts.daily_viz import (
    _cluster_boluses,
    _filter_day,
    _overlap_with_day,
)

MAJOR_ALARM_LABELS: dict[str, str] = {
    "BatteryShutdownAlarm": "Battery",
    "OcclusionAlarm": "Occlusion",
    "PumpResetAlarm": "Reset",
    "EmptyCartridgeAlarm": "Empty",
    "CartridgeAlarm": "Cartridge",
}


@dataclass(frozen=True)
class DayStats:
    tir_pct: float
    avg_bg: float
    sd_bg: float
    tdd_bolus: float
    tdd_basal: float
    tdd: float
    total_carbs: float


@dataclass
class DaySlice:
    target: date
    view: ViewMode
    low: float
    high: float
    cgm: pd.DataFrame
    bolus: pd.DataFrame
    requests: pd.DataFrame
    basal: pd.DataFrame
    suspension: pd.DataFrame
    events: pd.DataFrame
    alarms: pd.DataFrame
    site_issues_day: pd.DataFrame
    cgm_gaps_day: pd.DataFrame
    clusters: list[dict]
    stats: DayStats


def slice_day_frames(
    frames: dict[str, pd.DataFrame],
    target: date,
    *,
    view: ViewMode,
    low: float,
    high: float,
) -> DaySlice | None:
    """Slice loaded frames to one calendar day. Returns ``None`` if no CGM."""
    cgm = _filter_day(frames.get("cgm"), target)
    if cgm.empty:
        return None

    bolus = _filter_day(frames.get("bolus"), target)
    requests = _filter_day(frames.get("requests"), target)
    basal = _filter_day(frames.get("basal"), target)
    suspension = _filter_day(
        frames.get("suspension"), target, ts_col="suspend_timestamp"
    )
    events = _filter_day(frames.get("events"), target)
    alarms = _filter_day(frames.get("alarms"), target)
    site_issues_day = _overlap_with_day(
        frames.get("site_issues"),
        target,
        start_col="first_occlusion_ts",
        end_col="last_occlusion_ts",
    )
    cgm_gaps_day = _overlap_with_day(
        frames.get("cgm_gaps"),
        target,
        start_col="start_ts",
        end_col="end_ts",
    )
    clusters = _cluster_boluses(bolus, requests)
    stats = _compute_stats(cgm, bolus, basal, requests, low=low, high=high)
    return DaySlice(
        target=target,
        view=view,
        low=low,
        high=high,
        cgm=cgm,
        bolus=bolus,
        requests=requests,
        basal=basal,
        suspension=suspension,
        events=events,
        alarms=alarms,
        site_issues_day=site_issues_day,
        cgm_gaps_day=cgm_gaps_day,
        clusters=clusters,
        stats=stats,
    )


def _compute_stats(
    cgm: pd.DataFrame,
    bolus: pd.DataFrame,
    basal: pd.DataFrame,
    requests: pd.DataFrame,
    *,
    low: float,
    high: float,
) -> DayStats:
    bg = cgm["bg_mgdl"]
    # cgm is guaranteed non-empty here (slice_day_frames returns None on empty).
    tir = time_in_range(bg, low, high)
    tdd_bolus = bolus["insulin_units"].sum() if not bolus.empty else 0.0
    tdd_basal = (
        (basal["commanded_rate"] * 5 / 60).sum() if not basal.empty else 0.0
    )
    meals = requests[requests["carbs_g"] > 0] if not requests.empty else pd.DataFrame()
    total_carbs = meals["carbs_g"].sum() if not meals.empty else 0.0
    sd = float(bg.std()) if len(bg) > 1 else 0.0
    if pd.isna(sd):
        sd = 0.0
    return DayStats(
        tir_pct=float(tir),
        avg_bg=float(bg.mean()),
        sd_bg=sd,
        tdd_bolus=float(tdd_bolus),
        tdd_basal=float(tdd_basal),
        tdd=float(tdd_bolus + tdd_basal),
        total_carbs=float(total_carbs),
    )


def day_xlim(target: date, cgm: pd.DataFrame | None = None) -> tuple[datetime, datetime]:
    """Wall-clock x-axis bounds for Plotly subplots on ``target``.

    When ``cgm`` is provided, match its timezone so Plotly axes align with traces.
    """
    if cgm is not None and not cgm.empty and "timestamp" in cgm.columns:
        ts = pd.to_datetime(cgm["timestamp"])
        tz = ts.dt.tz
        if tz is not None:
            start = pd.Timestamp(target, tz=tz)
            end = start + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            return start.to_pydatetime(), end.to_pydatetime()
    start = datetime(target.year, target.month, target.day)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    return start, end


def format_bolus_hover(cluster: dict, category: str | None = None) -> str:
    """Plain-text hover line for a bolus cluster."""
    ts = cluster["time"]
    time_s = pd.Timestamp(ts).strftime("%H:%M")
    parts = [
        f"<b>Bolus</b> {time_s}",
        f"{cluster['total_units']:.1f} U",
    ]
    if cluster["count"] > 1:
        parts.append(f"×{cluster['count']}")
    if cluster.get("carbs", 0) > 0:
        parts.append(f"{int(cluster['carbs'])}g carbs")
    if category:
        parts.append(category)
    return "<br>".join(parts)
