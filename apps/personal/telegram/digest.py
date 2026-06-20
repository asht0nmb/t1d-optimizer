"""Pure digest builders for the Telegram command surface.

Each function takes already-sliced DataFrames / scalars and returns a
reply string. No storage, no network, no config loading — the handler
does the reads and passes the pieces in. Observations only; replies never
recommend a dose.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from core.bolus_categories import FOOD_CARRYING as _FOOD_CARRYING
from core.metrics.cgm_metrics import time_in_range

DISCLAIMER = "Observations only — not medical advice."


def compute_tir(bg: pd.Series, *, low: float, high: float) -> float | None:
    """Percent of readings in ``[low, high]``; ``None`` when empty.

    Delegates the in-band arithmetic to the shared
    :func:`core.metrics.cgm_metrics.time_in_range`, but preserves this
    caller's ``None``-on-empty contract (the shared function returns ``0.0``).
    """
    if bg is None or bg.empty:
        return None
    return time_in_range(bg, low, high)


def _fmt_pct(value: float | None) -> str:
    return f"{value:.0f}%" if value is not None else "—"


def _fmt_num(value: float | None, suffix: str = "") -> str:
    return f"{value:.0f}{suffix}" if value is not None else "—"


def build_day_digest(
    *,
    label: str,
    day: date,
    cgm: pd.DataFrame,
    bolus: pd.DataFrame,
    requests: pd.DataFrame,
    alert_count: int,
    low: float,
    high: float,
) -> str:
    """One-day digest: TIR, mean BG, readings, bolus, carbs, alerts."""
    bg = cgm["bg_mgdl"] if "bg_mgdl" in cgm.columns else pd.Series(dtype=float)
    n_readings = int(len(bg))
    tir = compute_tir(bg, low=low, high=high)
    mean_bg = float(bg.mean()) if n_readings else None

    bolus_units = (
        float(bolus["insulin_units"].sum())
        if "insulin_units" in bolus.columns and not bolus.empty
        else 0.0
    )
    if (
        not requests.empty
        and "carbs_g" in requests.columns
        and "bolus_category" in requests.columns
    ):
        meals = requests[requests["bolus_category"].isin(_FOOD_CARRYING)]
        carbs = float(meals["carbs_g"].fillna(0).sum())
    else:
        carbs = 0.0

    lines = [
        f"<b>{label}</b> · {day.isoformat()}",
        f"TIR ({low:.0f}–{high:.0f}): <b>{_fmt_pct(tir)}</b>",
        f"Mean BG: {_fmt_num(mean_bg, ' mg/dL')} ({n_readings} readings)",
        f"Bolus: {bolus_units:.1f} U · Carbs: {carbs:.0f} g",
        f"Meal-rise alerts: {alert_count}",
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines)


def build_trends_digest(tir_by_window: dict[int, float | None]) -> str:
    """Trailing-window TIR digest (7/14/30 days)."""
    lines = ["<b>TIR trends</b>"]
    for window in sorted(tir_by_window):
        lines.append(f"{window}-day: <b>{_fmt_pct(tir_by_window[window])}</b>")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def _fmt_ts(ts: datetime | None) -> str:
    if ts is None:
        return "—"
    return ts.strftime("%Y-%m-%d %H:%M %Z").strip()


def _age_label(ts: datetime | None, now: datetime) -> str:
    if ts is None:
        return "none recorded"
    minutes = (now - ts).total_seconds() / 60.0
    if minutes < 0:
        return "just now"
    if minutes < 90:
        return f"{minutes:.0f} min ago"
    hours = minutes / 60.0
    if hours < 48:
        return f"{hours:.0f} h ago"
    return f"{hours / 24:.0f} d ago"


def build_status_digest(
    *,
    latest_cgm_ts: datetime | None,
    latest_detection_ts: datetime | None,
    latest_alert_ts: datetime | None,
    latest_alert_delivery: str | None,
    now: datetime,
) -> str:
    """Automation-health digest mirroring the web /status semantics."""
    alert_line = (
        f"Last alert: {_fmt_ts(latest_alert_ts)} ({latest_alert_delivery})"
        if latest_alert_ts is not None
        else "Last alert: none recorded"
    )
    lines = [
        "<b>Status</b>",
        f"Latest CGM: {_fmt_ts(latest_cgm_ts)} ({_age_label(latest_cgm_ts, now)})",
        f"Last detection: {_fmt_ts(latest_detection_ts)} "
        f"({_age_label(latest_detection_ts, now)})",
        alert_line,
    ]
    return "\n".join(lines)


def help_text() -> str:
    """Fixed help reply (also used for unknown commands)."""
    return (
        "<b>T1D Engine</b> commands:\n"
        "/today — today's digest\n"
        "/yesterday — yesterday's digest\n"
        "/trends — 7/14/30-day TIR\n"
        "/status — data freshness\n"
        "/help — this message"
    )
