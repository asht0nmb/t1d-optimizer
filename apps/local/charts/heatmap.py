"""Interactive Plotly BG heatmap (hour-of-day × calendar date)."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go

# Anchored colorscale: deep blue (hypo) → green (in-range) → orange/red (high).
# Stops correspond to z values in [40, 320]; computed dynamically from low/high.
def _colorscale(low: float, high: float) -> list[list]:
    z_min, z_max = 40.0, 320.0
    def n(x: float) -> float:
        return (x - z_min) / (z_max - z_min)
    return [
        [0.0, "#1565C0"],
        [n(low) - 0.001, "#42A5F5"],
        [n(low), "#81C784"],
        [n((low + high) / 2), "#43A047"],
        [n(high), "#FFB300"],
        [n(min(250, z_max - 1)), "#F4511E"],
        [1.0, "#B71C1C"],
    ]


def _placeholder(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=msg,
        xref="paper", yref="paper",
        x=0.5, y=0.5,
        showarrow=False,
        font=dict(size=14, color="#666"),
    )
    fig.update_layout(height=400, margin=dict(l=40, r=40, t=40, b=40))
    return fig


def build_plotly_heatmap_figure(
    cgm: pd.DataFrame,
    *,
    low: float,
    high: float,
    end_date: date,
    days: int,
) -> go.Figure:
    """Hour-of-day × date median BG heatmap with per-cell hover.

    Cells aggregate with the median (robust to outliers) to match the web
    shell (lib/queries/heatmap.ts uses PERCENTILE_CONT(0.5)).
    """
    if cgm.empty or "timestamp" not in cgm.columns or "bg_mgdl" not in cgm.columns:
        return _placeholder("No CGM data for heatmap.")

    ts = pd.to_datetime(cgm["timestamp"])
    start_date = end_date - timedelta(days=days - 1)
    mask = (ts.dt.date >= start_date) & (ts.dt.date <= end_date)
    subset = cgm.loc[mask].copy()
    if subset.empty:
        return _placeholder("No CGM data in selected range.")

    subset["_date"] = ts.loc[mask].dt.date
    subset["_hour"] = ts.loc[mask].dt.hour
    pivot_bg = subset.pivot_table(
        index="_hour", columns="_date", values="bg_mgdl", aggfunc="median"
    )
    pivot_count = subset.pivot_table(
        index="_hour", columns="_date", values="bg_mgdl", aggfunc="count"
    )
    # Fill missing hours (0..23) so the y-axis is consistent.
    pivot_bg = pivot_bg.reindex(range(24))
    pivot_count = pivot_count.reindex(range(24)).fillna(0)

    col_labels = [d.isoformat() for d in pivot_bg.columns]
    y_labels = [f"{h:02d}:00" for h in pivot_bg.index]

    # Per-cell customdata: [date_iso, hour, n_readings]
    customdata: list[list[list]] = []
    for hour in pivot_bg.index:
        row_cd = []
        for col in pivot_bg.columns:
            cnt = int(pivot_count.loc[hour, col])
            row_cd.append([col.isoformat(), int(hour), cnt])
        customdata.append(row_cd)

    fig = go.Figure(
        data=go.Heatmap(
            z=pivot_bg.values,
            x=col_labels,
            y=y_labels,
            customdata=customdata,
            hovertemplate=(
                "<b>%{customdata[0]}</b> · %{customdata[1]:02d}:00<br>"
                "Median BG <b>%{z:.0f}</b> mg/dL<br>"
                "n=%{customdata[2]}<extra></extra>"
            ),
            zauto=False,
            zmin=40,
            zmax=320,
            colorscale=_colorscale(low, high),
            colorbar=dict(
                title=dict(text="mg/dL", side="right"),
                tickvals=[60, low, (low + high) / 2, high, 250, 300],
                ticks="outside",
                len=0.9,
            ),
            xgap=1,
            ygap=1,
        )
    )

    # Annotate weekly breaks subtly with vertical separators (Mondays).
    weekly_x: list[str] = []
    for d in pivot_bg.columns:
        if d.weekday() == 0:
            weekly_x.append(d.isoformat())
    for x in weekly_x:
        fig.add_vline(
            x=x, line_color="rgba(0,0,0,0.18)", line_width=0.5, line_dash="dot"
        )

    height = max(420, min(720, 24 * len(pivot_bg.index)))
    fig.update_layout(
        height=height,
        margin=dict(l=70, r=60, t=20, b=70),
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    fig.update_xaxes(
        title_text="Date",
        tickangle=-45,
        type="category",
        showgrid=False,
    )
    fig.update_yaxes(
        title_text="Hour of day",
        autorange="reversed",  # 00:00 at top
        showgrid=False,
    )
    return fig


def parse_heatmap_selection(event: object) -> date | None:
    """Extract a calendar date from a Streamlit Plotly selection event."""
    if event is None:
        return None
    selection = (
        event.get("selection") if isinstance(event, dict) else getattr(event, "selection", None)
    )
    if selection is None:
        return None
    points = (
        selection.get("points")
        if isinstance(selection, dict)
        else getattr(selection, "points", None)
    ) or []
    if not points:
        return None
    pt = points[0]
    get = pt.get if isinstance(pt, dict) else lambda k, d=None: getattr(pt, k, d)
    custom = get("customdata")
    if custom and len(custom) >= 1:
        try:
            return date.fromisoformat(str(custom[0]))
        except ValueError:
            pass
    x_val = get("x")
    if x_val is not None:
        try:
            return date.fromisoformat(str(x_val))
        except ValueError:
            pass
    return None
