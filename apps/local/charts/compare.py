"""Two-day CGM overlay chart for the Compare page.

Both days are projected onto a shared minutes-since-midnight x-axis (each
day's own local clock), so curve shapes line up regardless of date.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go

C_DAY_A = "#1565C0"
C_DAY_B = "#E65100"
C_TARGET = "rgba(76, 175, 80, 0.10)"


def _placeholder(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=msg, xref="paper", yref="paper",
        x=0.5, y=0.5, showarrow=False,
        font=dict(size=14, color="#666"),
    )
    fig.update_layout(height=380, margin=dict(l=40, r=40, t=40, b=40))
    return fig


def _minutes_since_midnight(ts: pd.Series) -> pd.Series:
    ts = pd.to_datetime(ts)
    return ts.dt.hour * 60 + ts.dt.minute + ts.dt.second / 60.0


def _add_day_trace(
    fig: go.Figure, cgm: pd.DataFrame, day: date, color: str
) -> bool:
    """Add one day's CGM line; returns False when the frame is unusable."""
    if cgm.empty or "timestamp" not in cgm.columns or "bg_mgdl" not in cgm.columns:
        return False
    df = cgm.sort_values("timestamp")
    fig.add_trace(
        go.Scatter(
            x=_minutes_since_midnight(df["timestamp"]),
            y=df["bg_mgdl"].astype(float),
            mode="lines+markers",
            line=dict(color=color, width=2),
            marker=dict(size=4),
            name=day.isoformat(),
            hovertemplate=(
                f"<b>{day.isoformat()}</b><br>"
                "%{customdata}<br><b>%{y:.0f} mg/dL</b><extra></extra>"
            ),
            customdata=[
                t.strftime("%H:%M") for t in pd.to_datetime(df["timestamp"])
            ],
        )
    )
    return True


def build_plotly_compare_figure(
    cgm_a: pd.DataFrame,
    cgm_b: pd.DataFrame,
    *,
    date_a: date,
    date_b: date,
    low: float,
    high: float,
) -> go.Figure:
    """Overlay two days' CGM curves on a shared time-of-day axis."""
    fig = go.Figure()
    fig.add_hrect(
        y0=low, y1=high, fillcolor=C_TARGET, line_width=0, layer="below",
    )
    added_a = _add_day_trace(fig, cgm_a, date_a, C_DAY_A)
    added_b = _add_day_trace(fig, cgm_b, date_b, C_DAY_B)
    if not added_a and not added_b:
        return _placeholder("No CGM data on either selected day.")

    fig.update_layout(
        height=420,
        margin=dict(l=55, r=30, t=30, b=50),
        hovermode="closest",
        plot_bgcolor="white",
        paper_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    fig.update_xaxes(
        title_text=f"Time of day · target {low:.0f}–{high:.0f} mg/dL",
        range=[0, 24 * 60],
        tickvals=[h * 60 for h in range(0, 25, 2)],
        ticktext=[f"{h:02d}:00" for h in range(0, 25, 2)],
        showgrid=True, gridcolor="rgba(0,0,0,0.05)",
    )
    fig.update_yaxes(
        title_text="BG (mg/dL)",
        rangemode="tozero",
        showgrid=True, gridcolor="rgba(0,0,0,0.05)",
    )
    return fig
