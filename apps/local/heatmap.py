"""BG heatmap (hour-of-day × date) for the local dashboard."""

from __future__ import annotations

from datetime import date, timedelta

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


def build_heatmap_figure(
    cgm: pd.DataFrame,
    *,
    low: float,
    high: float,
    end_date: date,
    days: int,
) -> plt.Figure:
    """Mean BG by hour and calendar day over the trailing ``days`` window."""
    if cgm.empty or "timestamp" not in cgm.columns or "bg_mgdl" not in cgm.columns:
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.text(0.5, 0.5, "No CGM data for heatmap", ha="center", va="center")
        ax.set_axis_off()
        return fig

    ts = pd.to_datetime(cgm["timestamp"])
    start_date = end_date - timedelta(days=days - 1)
    dates = ts.dt.date
    mask = (dates >= start_date) & (dates <= end_date)
    subset = cgm.loc[mask].copy()
    if subset.empty:
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.text(0.5, 0.5, "No CGM data in selected range", ha="center", va="center")
        ax.set_axis_off()
        return fig

    subset["_date"] = ts.loc[mask].dt.date
    subset["_hour"] = ts.loc[mask].dt.hour
    pivot = subset.pivot_table(
        index="_hour",
        columns="_date",
        values="bg_mgdl",
        aggfunc="mean",
    )
    pivot = pivot.sort_index()

    fig, ax = plt.subplots(figsize=(12, 5))
    sns.heatmap(
        pivot,
        ax=ax,
        cmap="RdYlGn_r",
        vmin=low - 20,
        vmax=high + 80,
        cbar_kws={"label": "BG (mg/dL)"},
    )
    ax.set_xlabel("Date")
    ax.set_ylabel("Hour of day")
    ax.set_title(f"BG heatmap — {days} days ending {end_date.isoformat()}")
    fig.tight_layout()
    return fig
