"""Ambulatory Glucose Profile chart — hourly percentile ribbons.

Percentile math lives in ``core/metrics/agp.py`` (canonical definition:
5/25/50/75/95 by local hour). The web shell mirrors the same definition
in SQL; this module only renders.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go

from core.metrics.agp import agp_profile

C_MEDIAN = "#1565C0"
C_IQR = "rgba(21, 101, 192, 0.25)"
C_OUTER = "rgba(21, 101, 192, 0.10)"
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


def _band_trace(
    hours: list[int], upper: list[float], lower: list[float],
    name: str, fill: str,
) -> go.Scatter:
    """Closed-polygon band: upper path then reversed lower path."""
    return go.Scatter(
        x=hours + hours[::-1],
        y=upper + lower[::-1],
        fill="toself",
        fillcolor=fill,
        line=dict(width=0),
        name=name,
        hoverinfo="skip",
        showlegend=True,
    )


def build_plotly_agp_figure(
    cgm: pd.DataFrame,
    *,
    low: float,
    high: float,
    end_date: date,
    days: int = 30,
    tz: str = "UTC",
) -> go.Figure:
    """Median line + 25–75 and 5–95 percentile bands by hour of day."""
    profile = agp_profile(cgm, days=days, end_date=end_date, tz=tz)
    if profile.empty:
        return _placeholder("No CGM data in the selected window.")

    # ``hour`` is the fractional hour-of-day of each bucket start (e.g. 6.0,
    # 6.25, ... for 15-min buckets), so the smoothed percentile curves render
    # as continuous time-of-day ribbons rather than 24 coarse hourly steps.
    hours = [float(h) for h in profile["hour"]]
    fig = go.Figure()
    fig.add_hrect(
        y0=low, y1=high, fillcolor=C_TARGET, line_width=0, layer="below",
    )
    fig.add_trace(
        _band_trace(
            hours,
            [float(v) for v in profile["p95"]],
            [float(v) for v in profile["p05"]],
            "5–95%", C_OUTER,
        )
    )
    fig.add_trace(
        _band_trace(
            hours,
            [float(v) for v in profile["p75"]],
            [float(v) for v in profile["p25"]],
            "25–75%", C_IQR,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=hours,
            y=[float(v) for v in profile["p50"]],
            mode="lines+markers",
            line=dict(color=C_MEDIAN, width=2.5),
            marker=dict(size=6),
            name="Median",
            customdata=[int(n) for n in profile["n"]],
            hovertemplate=(
                "<b>%{x:.2f} h</b><br>median <b>%{y:.0f} mg/dL</b><br>"
                "n=%{customdata}<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        height=420,
        margin=dict(l=55, r=30, t=30, b=50),
        hovermode="closest",
        plot_bgcolor="white",
        paper_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    fig.update_xaxes(
        title_text=f"Hour of day · target {low:.0f}–{high:.0f} mg/dL",
        range=[-0.5, 23.5],
        dtick=2,
        showgrid=True, gridcolor="rgba(0,0,0,0.05)",
    )
    fig.update_yaxes(
        title_text="BG (mg/dL)",
        rangemode="tozero",
        showgrid=True, gridcolor="rgba(0,0,0,0.05)",
    )
    return fig
