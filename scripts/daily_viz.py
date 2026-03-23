"""Daily visualization: multi-panel chart modeled after the Tandem t:connect app.

Usage: uv run python main.py viz --date 2026-03-19
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd
import yaml

from ingestion.storage import load_df

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


def daily_viz(date_str: str) -> None:
    target = date.fromisoformat(date_str)
    config = _load_config()
    low = config["bg_targets"]["low"]
    high = config["bg_targets"]["high"]

    # Load data
    cgm = _filter_day(load_df("cgm"), target)
    bolus = _filter_day(load_df("bolus"), target)
    requests = _filter_day(load_df("requests"), target)
    basal = _filter_day(load_df("basal"), target)
    suspension = _filter_day(load_df("suspension"), target, ts_col="suspend_timestamp")
    events = _filter_day(load_df("events"), target)

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
    header = (
        f"{day_name} - {target.strftime('%b %d, %Y')}        "
        f"Time in Range: {tir:.0f}%    "
        f"Avg: {avg_bg:.0f}mg/dL    "
        f"SD: {sd_bg:.0f}mg/dL    "
        f"TDI: {tdd:.1f}units    "
        f"Carbs: {total_carbs:.0f}g"
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

    # Plot CGM with color segments
    times = cgm["_plot_time"].values
    bgs = cgm["bg_mgdl"].values

    for i in range(len(times) - 1):
        bg_val = bgs[i]
        if bg_val < low or bg_val > 250:
            color = C_RED
        elif bg_val > high:
            color = C_ORANGE
        else:
            color = C_GREEN

        # Line segment
        ax_cgm.plot(
            [times[i], times[i + 1]], [bgs[i], bgs[i + 1]],
            color=color, linewidth=1.5, solid_capstyle="round",
        )

    # Scatter dots on top
    colors = []
    for v in bgs:
        if v < low or v > 250:
            colors.append(C_RED)
        elif v > high:
            colors.append(C_ORANGE)
        else:
            colors.append(C_GREEN)

    ax_cgm.scatter(times, bgs, c=colors, s=12, zorder=5, edgecolors="none")

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
            ax_bolus.scatter(t, 0.1, marker="s", s=40, c="gray", zorder=5, alpha=0.6)

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

    # Suspension shading
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
    if len(sys.argv) != 2:
        print("Usage: python scripts/daily_viz.py YYYY-MM-DD")
        sys.exit(1)
    daily_viz(sys.argv[1])
