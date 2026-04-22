"""Daily visualization: multi-panel chart modeled after the Tandem t:connect app.

Usage:
    uv run python main.py viz --date 2026-03-19
    uv run python main.py viz --date 2026-03-19 --view enriched

View modes:

* ``original`` (default) — historical panels, CGM OOR shading derived from
  raw `cgm_out_of_range` alarm pairs. Matches pre-enrichment visual intent
  for regression comparisons.
* ``enriched`` — draws the same panels plus these overlays:
  - CGM gap shading comes from the ``cgm_gaps`` frame (single source of
    truth); the raw alarm-pair shading is skipped so we never double-draw.
  - Site-change markers distinguish forced (`forced_by_alarm=True`, hollow
    gray square with "(forced)" label) from real site rotations.
  - Bolus clusters annotated with the dominant `bolus_category` when
    available.
  - `site_issues` episodes drawn as a subtle hatched band on the bolus
    panel at `[first_occlusion_ts, last_occlusion_ts]`.

The underlying parquets are never modified by `viz`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd
import yaml

from detection.config import get_config
from ingestion.storage import load_df
from ingestion.view_data import (
    VIEW_MODES,
    ViewMode,
    ensure_enriched,
    strip_enriched_columns,
)

# ── Colors ──────────────────────────────────────────────────────────
C_GREEN = "#4CAF50"
C_ORANGE = "#FF9800"
C_RED = "#F44336"
C_LOW_LINE = "#E53935"
C_HIGH_LINE = "#E65100"
C_BOLUS = "#1565C0"
C_BOLUS_LIGHT = "#64B5F6"
C_CARB = "#FFA726"
C_BASAL_FILL = "#BBDEFB"
C_BASAL_EDGE = "#1E88E5"
C_SUSPEND = "#FFCDD2"
C_BG = "#FAFAFA"


def _load_config():
    with open("config/user_config.yaml") as f:
        return yaml.safe_load(f)


def _filter_day(df: pd.DataFrame | None, target: date, ts_col: str = "timestamp") -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    mask = pd.to_datetime(df[ts_col]).dt.date == target
    return df[mask].copy()


def _to_plot_time(series: pd.Series) -> pd.Series:
    """Convert timestamps to matplotlib-friendly datetime with consistent date."""
    dt = pd.to_datetime(series)
    # Normalize all to same arbitrary date so x-axis is time-of-day only
    return dt.apply(lambda x: datetime(2000, 1, 1, x.hour, x.minute, x.second))


def _cluster_boluses(bolus: pd.DataFrame, requests: pd.DataFrame, window_minutes: int = 30) -> list[dict]:
    """Group boluses within a time window into clusters (like the t:connect app)."""
    if bolus.empty:
        return []

    bolus = bolus.sort_values("timestamp").copy()
    bolus["_ts"] = pd.to_datetime(bolus["timestamp"])

    clusters = []
    current = {"timestamps": [], "units": [], "ids": []}

    for _, row in bolus.iterrows():
        ts = row["_ts"]
        if current["timestamps"] and (ts - current["timestamps"][-1]).total_seconds() > window_minutes * 60:
            clusters.append(current)
            current = {"timestamps": [], "units": [], "ids": []}
        current["timestamps"].append(ts)
        current["units"].append(row["insulin_units"])
        current["ids"].append(row["bolus_id"])

    if current["timestamps"]:
        clusters.append(current)

    result = []
    for c in clusters:
        total = sum(c["units"])
        count = len(c["units"])
        center_ts = c["timestamps"][len(c["timestamps"]) // 2]
        # Find matching carbs
        carbs = 0
        if not requests.empty:
            req_ts = pd.to_datetime(requests["timestamp"])
            for t in c["timestamps"]:
                window_mask = (req_ts >= t - pd.Timedelta(minutes=5)) & (req_ts <= t + pd.Timedelta(minutes=5))
                matched = requests[window_mask]
                carbs += matched["carbs_g"].sum()
        result.append({
            "time": center_ts,
            "total_units": total,
            "count": count,
            "carbs": carbs,
        })

    return result


def _find_peaks(cgm: pd.DataFrame, min_prominence: int = 30) -> pd.DataFrame:
    """Find local peaks and valleys worth labeling."""
    if len(cgm) < 5:
        return pd.DataFrame()

    bg = cgm["bg_mgdl"].values
    times = cgm["_plot_time"].values
    labels = []

    # Label any reading >250 that's a local max (within ±3 readings)
    for i in range(2, len(bg) - 2):
        window = bg[max(0, i - 3):min(len(bg), i + 4)]
        if bg[i] == window.max() and bg[i] > 250:
            labels.append({"time": times[i], "bg": bg[i], "color": C_RED})
        elif bg[i] == window.max() and bg[i] > 180:
            labels.append({"time": times[i], "bg": bg[i], "color": C_ORANGE})

    # Also label transition points: first reading crossing back into range
    for i in range(1, len(bg)):
        if bg[i - 1] > 180 and bg[i] <= 180:
            labels.append({"time": times[i], "bg": bg[i], "color": C_GREEN})
        elif bg[i - 1] > 250 and bg[i] <= 250 and bg[i] > 180:
            labels.append({"time": times[i], "bg": bg[i], "color": C_ORANGE})

    # Deduplicate labels that are too close together (within 20 min)
    if not labels:
        return pd.DataFrame()

    result = [labels[0]]
    for lbl in labels[1:]:
        if isinstance(lbl["time"], (pd.Timestamp, datetime)):
            prev_time = result[-1]["time"]
            if hasattr(prev_time, 'timestamp') and hasattr(lbl["time"], 'timestamp'):
                diff = abs((lbl["time"] - prev_time).total_seconds())
                if diff < 1200:  # 20 minutes
                    # Keep the more extreme value
                    if lbl["bg"] > result[-1]["bg"]:
                        result[-1] = lbl
                    continue
        result.append(lbl)

    return pd.DataFrame(result)


def _to_plot_datetime(ts: pd.Timestamp) -> datetime:
    """Project a timestamp onto the arbitrary x-axis day (2000-01-01)."""
    ts = pd.to_datetime(ts)
    return datetime(2000, 1, 1, ts.hour, ts.minute, ts.second)


def _shade_oor_from_alarms(ax, alarms: pd.DataFrame, day_end: datetime) -> None:
    """Original-view strategy: derive OOR spans from raw alarm pairs."""
    if alarms.empty:
        return
    oor_act = alarms[
        (alarms["alarm_name"] == "cgm_out_of_range") & (alarms["action"] == "activated")
    ].sort_values("timestamp")
    oor_clr = alarms[
        (alarms["alarm_name"] == "cgm_out_of_range") & (alarms["action"] == "cleared")
    ].sort_values("timestamp")
    for _, act_row in oor_act.iterrows():
        act_ts = pd.to_datetime(act_row["timestamp"])
        act_t = _to_plot_datetime(act_ts)
        cleared_after = oor_clr[pd.to_datetime(oor_clr["timestamp"]) > act_ts]
        if not cleared_after.empty:
            clr_ts = pd.to_datetime(cleared_after.iloc[0]["timestamp"])
            clr_t = _to_plot_datetime(clr_ts)
        else:
            clr_t = day_end
        ax.axvspan(act_t, clr_t, alpha=0.06, color="gray")


def _shade_oor_from_gaps(ax, cgm_gaps: pd.DataFrame, day_end: datetime) -> None:
    """Enriched-view strategy: draw CGM-blind windows from the `cgm_gaps` frame.

    `cgm_gaps` already represents paired activated/cleared transitions, so this
    is a one-pass loop with no re-derivation. Single source of truth — when
    this is called we intentionally *skip* `_shade_oor_from_alarms` to avoid
    double-drawing the same windows in two colors.
    """
    if cgm_gaps is None or cgm_gaps.empty:
        return
    for _, row in cgm_gaps.iterrows():
        start_ts = pd.to_datetime(row["start_ts"])
        start_t = _to_plot_datetime(start_ts)
        end_ts = row.get("end_ts")
        if pd.notna(end_ts):
            end_t = _to_plot_datetime(pd.to_datetime(end_ts))
        else:
            end_t = day_end
        ax.axvspan(start_t, end_t, alpha=0.10, color="gray", hatch="...")


def _draw_site_issue_band(ax, site_issues_day: pd.DataFrame, day_end: datetime) -> None:
    """Hatch a subtle band on the bolus panel for occlusion clusters on the day."""
    if site_issues_day is None or site_issues_day.empty:
        return
    for _, row in site_issues_day.iterrows():
        start_t = _to_plot_datetime(pd.to_datetime(row["first_occlusion_ts"]))
        last_ts = row.get("last_occlusion_ts")
        end_t = (
            _to_plot_datetime(pd.to_datetime(last_ts))
            if pd.notna(last_ts) else day_end
        )
        ax.axvspan(
            start_t, end_t, ymin=0.02, ymax=0.22,
            alpha=0.25, facecolor="#FFD54F", edgecolor="#F57F17",
            hatch="xxx", linewidth=0.5,
        )


def _annotate_bolus_categories(
    ax,
    clusters: list[dict],
    requests: pd.DataFrame,
) -> None:
    """Label each bolus cluster with the bolus_category of its matched request.

    When multiple requests fall inside the ±5 min window, pick the one with
    the largest `total_requested` as the representative. Silently no-op when
    the requests frame doesn't carry `bolus_category` (caller is responsible
    for the `view="enriched"` guard).
    """
    if requests is None or requests.empty or "bolus_category" not in requests.columns:
        return
    req_ts = pd.to_datetime(requests["timestamp"])
    for cl in clusters:
        cl_ts = cl["time"]
        window_mask = (
            (req_ts >= cl_ts - pd.Timedelta(minutes=5))
            & (req_ts <= cl_ts + pd.Timedelta(minutes=5))
        )
        matched = requests[window_mask]
        if matched.empty:
            continue
        rep = matched.sort_values("total_requested", ascending=False).iloc[0]
        category = rep.get("bolus_category")
        if not isinstance(category, str) or not category:
            continue
        t = _to_plot_datetime(cl_ts)
        ax.annotate(
            category, xy=(t, 2.0), xytext=(0, -14),
            textcoords="offset points", fontsize=7,
            color="#455A64", ha="center", style="italic",
            bbox=dict(boxstyle="round,pad=0.15", facecolor="white",
                      edgecolor="#90A4AE", alpha=0.85, linewidth=0.5),
        )


def _overlap_with_day(
    df: pd.DataFrame | None,
    target: date,
    start_col: str,
    end_col: str,
) -> pd.DataFrame:
    """Return rows in ``df`` whose [start, end] window touches ``target``."""
    if df is None or df.empty:
        return pd.DataFrame()
    starts = pd.to_datetime(df[start_col])
    ends = pd.to_datetime(df[end_col]) if end_col in df.columns else pd.Series(pd.NaT, index=df.index)
    tz = getattr(starts.dt, "tz", None)
    day_start = pd.Timestamp(target)
    day_end = day_start + pd.Timedelta(days=1)
    if tz is not None:
        day_start = day_start.tz_localize(tz)
        day_end = day_end.tz_localize(tz)
    ongoing = ends.isna()
    mask = ((starts < day_end) & (ends >= day_start)) | (ongoing & (starts < day_end))
    return df[mask]


def _prepare_frames(
    target: date, view: ViewMode
) -> dict[str, pd.DataFrame]:
    """Load + project every frame the visualization uses for ``target``.

    Central to keep original vs enriched projection consistent with
    `sanity_check`. Only the day-slice happens here; overlap tables
    (site_issues / cgm_gaps) are returned whole because their rows span days.
    """
    names = ("cgm", "bolus", "requests", "basal", "suspension",
             "events", "alarms", "site_issues", "cgm_gaps")
    raw: dict[str, pd.DataFrame] = {}
    for name in names:
        df = load_df(name)
        raw[name] = df if df is not None else pd.DataFrame()

    if view == "enriched":
        config = get_config()
        raw = ensure_enriched(raw, config)
    else:
        for name in list(raw):
            raw[name] = strip_enriched_columns(name, raw[name])

    return raw


def daily_viz(date_str: str, view: ViewMode = "original") -> None:
    if view not in VIEW_MODES:
        raise ValueError(
            f"Unknown view mode {view!r}; expected one of {VIEW_MODES}"
        )

    target = date.fromisoformat(date_str)
    config = _load_config()
    low = config["bg_targets"]["low"]
    high = config["bg_targets"]["high"]

    frames = _prepare_frames(target, view)

    cgm = _filter_day(frames["cgm"], target)
    bolus = _filter_day(frames["bolus"], target)
    requests = _filter_day(frames["requests"], target)
    basal = _filter_day(frames["basal"], target)
    suspension = _filter_day(frames["suspension"], target, ts_col="suspend_timestamp")
    events = _filter_day(frames["events"], target)
    alarms = _filter_day(frames["alarms"], target)
    site_issues_day = _overlap_with_day(
        frames.get("site_issues"), target,
        start_col="first_occlusion_ts", end_col="last_occlusion_ts",
    )
    cgm_gaps_day = _overlap_with_day(
        frames.get("cgm_gaps"), target,
        start_col="start_ts", end_col="end_ts",
    )

    if cgm.empty:
        print(f"No CGM data for {target}. Run: uv run python main.py fetch-day --date {date_str}")
        return

    # Compute stats
    bg = cgm["bg_mgdl"]
    tir = ((bg >= low) & (bg <= high)).mean() * 100
    avg_bg = bg.mean()
    sd_bg = bg.std()
    tdd_bolus = bolus["insulin_units"].sum() if not bolus.empty else 0
    tdd_basal = (basal["commanded_rate"] * 5 / 60).sum() if not basal.empty else 0
    tdd = tdd_bolus + tdd_basal
    meals = requests[requests["carbs_g"] > 0] if not requests.empty else pd.DataFrame()
    total_carbs = meals["carbs_g"].sum() if not meals.empty else 0

    # Prepare plot times
    cgm["_plot_time"] = _to_plot_time(cgm["timestamp"])
    cgm = cgm.sort_values("_plot_time")

    day_start = datetime(2000, 1, 1, 0, 0, 0)
    day_end = datetime(2000, 1, 1, 23, 59, 59)

    # ── Figure setup ────────────────────────────────────────────────
    fig, (ax_cgm, ax_bolus, ax_basal) = plt.subplots(
        3, 1,
        figsize=(16, 8),
        gridspec_kw={"height_ratios": [5, 2, 2], "hspace": 0.08},
        sharex=True,
    )
    fig.patch.set_facecolor("white")

    # ── Header ──────────────────────────────────────────────────────
    day_name = target.strftime("%A")
    view_suffix = "    [view: enriched]" if view == "enriched" else ""
    header = (
        f"{day_name} - {target.strftime('%b %d, %Y')}        "
        f"Time in Range: {tir:.0f}%    "
        f"Avg: {avg_bg:.0f}mg/dL    "
        f"SD: {sd_bg:.0f}mg/dL    "
        f"TDI: {tdd:.1f}units    "
        f"Carbs: {total_carbs:.0f}g{view_suffix}"
    )
    fig.suptitle(header, fontsize=12, fontweight="bold", x=0.02, ha="left", y=0.97)

    # ══════════════════════════════════════════════════════════════════
    # Panel 1: CGM Trace
    # ══════════════════════════════════════════════════════════════════
    ax_cgm.set_facecolor(C_BG)
    ax_cgm.set_ylim(40, 420)
    ax_cgm.set_ylabel("Glucose", fontsize=10)

    # Reference lines
    ax_cgm.axhline(y=high, color=C_HIGH_LINE, linewidth=1.2, alpha=0.7)
    ax_cgm.axhline(y=low, color=C_LOW_LINE, linewidth=1.2, alpha=0.7)

    # Add range labels on right side
    ax_cgm.text(day_end, high, f" {high}", va="center", fontsize=8, color=C_HIGH_LINE, fontweight="bold")
    ax_cgm.text(day_end, low, f" {low}", va="center", fontsize=8, color=C_LOW_LINE, fontweight="bold")

    # Plot CGM with color segments (gap-aware, backfill-styled)
    times = cgm["_plot_time"].values
    bgs = cgm["bg_mgdl"].values
    backfilled = cgm["backfilled"].astype(bool).values if "backfilled" in cgm.columns else np.zeros(len(bgs), dtype=bool)

    def _bg_color(val):
        if val < low or val > 250:
            return C_RED
        elif val > high:
            return C_ORANGE
        return C_GREEN

    for i in range(len(times) - 1):
        t0, t1 = times[i], times[i + 1]
        # Don't draw lines across gaps > 15 min
        if isinstance(t0, datetime) and isinstance(t1, datetime):
            if (t1 - t0).total_seconds() > 900:
                continue

        color = _bg_color(bgs[i])
        is_bf = bool(backfilled[i]) or bool(backfilled[i + 1])
        ax_cgm.plot(
            [t0, t1], [bgs[i], bgs[i + 1]],
            color=color,
            linewidth=1.0 if is_bf else 1.5,
            linestyle="--" if is_bf else "-",
            alpha=0.4 if is_bf else 1.0,
            solid_capstyle="round",
        )

    # Scatter dots — live vs backfilled
    colors = np.array([_bg_color(v) for v in bgs])
    live_mask = ~backfilled
    bf_mask = backfilled

    if live_mask.any():
        ax_cgm.scatter(
            times[live_mask], bgs[live_mask],
            c=colors[live_mask],
            s=12, zorder=5, edgecolors="none",
        )
    if bf_mask.any():
        ax_cgm.scatter(
            times[bf_mask], bgs[bf_mask],
            c=colors[bf_mask],
            s=8, zorder=4, edgecolors="none", alpha=0.5,
        )

    # Peak labels
    peaks = _find_peaks(cgm)
    if not peaks.empty:
        for _, p in peaks.iterrows():
            ax_cgm.annotate(
                f"{int(p['bg'])}",
                xy=(p["time"], p["bg"]),
                xytext=(0, 10),
                textcoords="offset points",
                fontsize=9,
                fontweight="bold",
                color=p["color"],
                ha="center",
            )

    # Alarm markers on CGM panel
    if not alarms.empty:
        # Major alarms: vertical lines
        _MAJOR_ALARM_LABELS = {
            "BatteryShutdownAlarm": "Battery",
            "OcclusionAlarm": "Occlusion",
            "PumpResetAlarm": "Reset",
            "EmptyCartridgeAlarm": "Empty",
            "CartridgeAlarm": "Cartridge",
        }
        major = alarms[
            (alarms["category"] == "alarm")
            & (alarms["action"] == "activated")
            & (alarms["alarm_name"].isin(_MAJOR_ALARM_LABELS))
        ]
        for _, row in major.iterrows():
            ts = pd.to_datetime(row["timestamp"])
            t = datetime(2000, 1, 1, ts.hour, ts.minute, ts.second)
            label = _MAJOR_ALARM_LABELS.get(row["alarm_name"], row["alarm_name"])
            ax_cgm.axvline(x=t, color=C_RED, linewidth=0.8, linestyle="--", alpha=0.6)
            ax_cgm.annotate(
                label, xy=(t, 400), fontsize=7, color=C_RED,
                ha="center", fontweight="bold", alpha=0.8,
                bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor=C_RED, alpha=0.7),
            )

    # CGM signal-loss shading — one strategy per view; never both.
    if view == "enriched":
        _shade_oor_from_gaps(ax_cgm, cgm_gaps_day, day_end)
    else:
        _shade_oor_from_alarms(ax_cgm, alarms, day_end)

    # ══════════════════════════════════════════════════════════════════
    # Panel 2: Bolus + Carbs + Events
    # ══════════════════════════════════════════════════════════════════
    ax_bolus.set_facecolor(C_BG)
    ax_bolus.set_ylabel("Bolus", fontsize=10)
    ax_bolus.set_ylim(0, 3)
    ax_bolus.set_yticks([])

    # Horizontal divider
    ax_bolus.axhline(y=1.0, color=C_BOLUS, linewidth=1.5, alpha=0.3)

    # Cluster boluses
    clusters = _cluster_boluses(bolus, requests)
    for cl in clusters:
        t = datetime(2000, 1, 1, cl["time"].hour, cl["time"].minute, cl["time"].second)

        # Bolus marker (diamond)
        marker_size = min(200, max(80, cl["total_units"] * 15))
        ax_bolus.scatter(
            t, 2.0, marker="D", s=marker_size, c=C_BOLUS, zorder=5, edgecolors="white", linewidths=0.5,
        )

        # Label
        label = f"{cl['total_units']:.1f}"
        if cl["count"] > 1:
            label += f" ({cl['count']})"
        ax_bolus.annotate(
            label, xy=(t, 2.0), xytext=(0, 14),
            textcoords="offset points", fontsize=9, fontweight="bold",
            color=C_BOLUS, ha="center",
        )

        # Carb marker if present
        if cl["carbs"] > 0:
            ax_bolus.scatter(
                t, 0.5, marker="o", s=100, c=C_CARB, zorder=5, edgecolors="white", linewidths=0.5,
            )
            ax_bolus.annotate(
                f"{int(cl['carbs'])}g", xy=(t, 0.5), xytext=(8, -2),
                textcoords="offset points", fontsize=9, fontweight="bold",
                color=C_CARB, ha="left",
            )

    # Standalone carb entries not matched to a cluster
    if not meals.empty:
        for _, row in meals.iterrows():
            ts = pd.to_datetime(row["timestamp"])
            t = datetime(2000, 1, 1, ts.hour, ts.minute, ts.second)
            # Check if already plotted via cluster
            already = False
            for cl in clusters:
                cl_t = datetime(2000, 1, 1, cl["time"].hour, cl["time"].minute, cl["time"].second)
                if abs((t - cl_t).total_seconds()) < 600 and cl["carbs"] > 0:
                    already = True
                    break
            if not already:
                ax_bolus.scatter(t, 0.5, marker="o", s=100, c=C_CARB, zorder=5, edgecolors="white", linewidths=0.5)
                ax_bolus.annotate(
                    f"{int(row['carbs_g'])}g", xy=(t, 0.5), xytext=(8, -2),
                    textcoords="offset points", fontsize=9, fontweight="bold",
                    color=C_CARB, ha="left",
                )

    # Events (mode changes, site changes)
    if not events.empty:
        mode_changes = events[events["event_type"] == "mode_change"]
        site_changes = events[events["event_type"] == "site_change"]

        for _, row in mode_changes.iterrows():
            ts = pd.to_datetime(row["timestamp"])
            t = datetime(2000, 1, 1, ts.hour, ts.minute, ts.second)
            subtype = row.get("event_subtype", "")
            if "sleep" in str(subtype).lower():
                label = "Zzz"
            elif "exercise" in str(subtype).lower():
                label = "Ex"
            else:
                label = subtype[:6] if subtype else "mode"
            ax_bolus.annotate(
                label, xy=(t, 0.1), fontsize=7, color="gray",
                ha="center", style="italic",
                bbox=dict(boxstyle="round,pad=0.2", facecolor="lightgray", alpha=0.5),
            )

        for _, row in site_changes.iterrows():
            ts = pd.to_datetime(row["timestamp"])
            t = datetime(2000, 1, 1, ts.hour, ts.minute, ts.second)
            # Enriched view differentiates forced (firmware-triggered refill after a
            # BatteryShutdownAlarm) from real site rotations; original view draws
            # them identically for visual-regression stability.
            forced = row.get("forced_by_alarm") if view == "enriched" else None
            if forced is True:
                ax_bolus.scatter(
                    t, 0.1, marker="s", s=48, facecolors="none",
                    edgecolors="gray", linewidths=1.2, zorder=5, alpha=0.7,
                )
                ax_bolus.annotate(
                    "site (forced)", xy=(t, 0.1), xytext=(0, -12),
                    textcoords="offset points", fontsize=7, color="gray",
                    ha="center", style="italic", alpha=0.8,
                )
            else:
                ax_bolus.scatter(t, 0.1, marker="s", s=40, c="gray", zorder=5, alpha=0.6)
                if view == "enriched" and forced is False:
                    ax_bolus.annotate(
                        "site", xy=(t, 0.1), xytext=(0, -12),
                        textcoords="offset points", fontsize=7, color="dimgray",
                        ha="center", alpha=0.8,
                    )

    # Enriched-only overlays: site_issue band + bolus_category labels.
    if view == "enriched":
        _draw_site_issue_band(ax_bolus, site_issues_day, day_end)
        _annotate_bolus_categories(ax_bolus, clusters, requests)

    # ══════════════════════════════════════════════════════════════════
    # Panel 3: Basal Rate
    # ══════════════════════════════════════════════════════════════════
    ax_basal.set_facecolor(C_BG)
    ax_basal.set_ylabel("Basal", fontsize=10)

    if not basal.empty:
        basal["_plot_time"] = _to_plot_time(basal["timestamp"])
        basal = basal.sort_values("_plot_time")

        b_times = basal["_plot_time"].values
        b_rates = basal["commanded_rate"].values

        max_rate = b_rates.max() if len(b_rates) > 0 else 2.0
        ax_basal.set_ylim(0, max(max_rate * 1.5, 2.0))

        # Fill step chart
        ax_basal.fill_between(
            b_times, b_rates, step="post",
            color=C_BASAL_FILL, alpha=0.7,
        )
        ax_basal.step(
            b_times, b_rates, where="post",
            color=C_BASAL_EDGE, linewidth=0.8, alpha=0.6,
        )

        # Label profile rate changes
        profile_mask = basal["rate_source"] == "profile"
        profile = basal[profile_mask]
        if not profile.empty:
            prev_rate = None
            for _, row in profile.iterrows():
                rate = row["commanded_rate"]
                if rate != prev_rate:
                    t = row["_plot_time"]
                    ax_basal.annotate(
                        f"{rate:.3f}", xy=(t, rate), xytext=(0, 8),
                        textcoords="offset points", fontsize=7,
                        color=C_BASAL_EDGE, fontweight="bold", ha="center",
                    )
                    prev_rate = rate

    # Suspension shading with alarm labels
    if not suspension.empty:
        for _, row in suspension.iterrows():
            s_start = pd.to_datetime(row["suspend_timestamp"])
            s_start_t = datetime(2000, 1, 1, s_start.hour, s_start.minute, s_start.second)
            if pd.notna(row["resume_timestamp"]):
                s_end = pd.to_datetime(row["resume_timestamp"])
                s_end_t = datetime(2000, 1, 1, s_end.hour, s_end.minute, s_end.second)
            else:
                s_end_t = day_end
            ax_basal.axvspan(s_start_t, s_end_t, alpha=0.3, color=C_SUSPEND, hatch="//")

            # Label with alarm name or suspend reason
            alarm_name = row.get("alarm_name", None)
            if alarm_name and pd.notna(alarm_name):
                label = alarm_name.replace("Alarm", "").replace("alarm", "").strip()
            else:
                label = row.get("suspend_reason", "")
            if label:
                label_t = s_start_t + timedelta(minutes=2)
                ax_basal.annotate(
                    label, xy=(label_t, ax_basal.get_ylim()[1] * 0.85),
                    fontsize=7, color=C_RED, ha="left", fontweight="bold", alpha=0.8,
                )

    # ── X-axis formatting ───────────────────────────────────────────
    ax_basal.set_xlim(day_start, day_end)
    ax_basal.xaxis.set_major_locator(mdates.HourLocator(interval=2))
    ax_basal.xaxis.set_major_formatter(mdates.DateFormatter("%-I %p"))
    ax_basal.tick_params(axis="x", rotation=0, labelsize=8)

    # Remove x labels from upper panels
    ax_cgm.tick_params(axis="x", labelbottom=False)
    ax_bolus.tick_params(axis="x", labelbottom=False)

    # Grid on CGM panel only
    ax_cgm.grid(axis="y", alpha=0.2, linewidth=0.5)

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    plt.show()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python scripts/daily_viz.py YYYY-MM-DD [--view original|enriched]")
        sys.exit(1)
    args = list(sys.argv[1:])
    view: ViewMode = "original"
    if "--view" in args:
        i = args.index("--view")
        view = args[i + 1]  # type: ignore[assignment]
        del args[i:i + 2]
    daily_viz(args[0], view=view)
