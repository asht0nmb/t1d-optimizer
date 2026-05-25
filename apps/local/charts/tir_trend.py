"""Interactive daily TIR trend chart."""

from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go

from apps.local.dates import iter_dates_in_window
from apps.local.metrics import compute_tir_percent

C_TIR_GOOD = "#2E7D32"
C_TIR_OK = "#FFA000"
C_TIR_POOR = "#C62828"
C_LINE = "#455A64"


def _band_color(tir: float) -> str:
    if tir >= 70:
        return C_TIR_GOOD
    if tir >= 50:
        return C_TIR_OK
    return C_TIR_POOR


def _placeholder(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=msg, xref="paper", yref="paper",
        x=0.5, y=0.5, showarrow=False,
        font=dict(size=14, color="#666"),
    )
    fig.update_layout(height=320, margin=dict(l=40, r=40, t=40, b=40))
    return fig


def build_plotly_tir_trend_figure(
    cgm: pd.DataFrame,
    *,
    low: float,
    high: float,
    end_date: date,
    days: int = 30,
) -> go.Figure:
    """One point per calendar day: TIR % with hover (date, TIR, reading count)."""
    dates = iter_dates_in_window(end_date, days)
    if cgm.empty or "timestamp" not in cgm.columns:
        return _placeholder("No CGM data for TIR trend.")

    ts = pd.to_datetime(cgm["timestamp"])
    xs: list[date] = []
    ys: list[float] = []
    counts: list[int] = []
    for d in dates:
        mask = ts.dt.date == d
        day_df = cgm.loc[mask]
        if day_df.empty:
            continue
        xs.append(d)
        ys.append(compute_tir_percent(day_df["bg_mgdl"], low=low, high=high))
        counts.append(len(day_df))

    if not xs:
        return _placeholder("No CGM days in selected range.")

    fig = go.Figure()
    # 70% goal band.
    fig.add_hrect(
        y0=70, y1=100,
        fillcolor="rgba(76, 175, 80, 0.06)",
        line_width=0, layer="below",
    )
    fig.add_hline(
        y=70, line_color=C_TIR_GOOD, line_width=1, line_dash="dot", opacity=0.8,
        annotation_text="70% goal", annotation_position="top right",
        annotation_font_size=10, annotation_font_color=C_TIR_GOOD,
    )

    fig.add_trace(
        go.Scatter(
            x=xs, y=ys,
            mode="lines+markers",
            line=dict(color=C_LINE, width=1.5),
            marker=dict(
                size=11,
                color=[_band_color(v) for v in ys],
                line=dict(color="white", width=1.5),
            ),
            customdata=list(zip([d.isoformat() for d in xs], counts)),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "TIR <b>%{y:.0f}%</b><br>"
                "n=%{customdata[1]}<extra></extra>"
            ),
            name="Daily TIR",
        )
    )
    fig.update_layout(
        height=380,
        margin=dict(l=55, r=30, t=30, b=50),
        hovermode="closest",
        showlegend=False,
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    fig.update_xaxes(
        title_text=f"Date · {low:.0f}–{high:.0f} mg/dL target",
        tickformat="%b %-d",
        showgrid=True, gridcolor="rgba(0,0,0,0.05)",
    )
    fig.update_yaxes(
        title_text="TIR %",
        range=[0, 100],
        ticksuffix="%",
        showgrid=True, gridcolor="rgba(0,0,0,0.05)",
    )
    return fig


def parse_tir_selection(event: object) -> date | None:
    """Extract date from TIR trend point selection."""
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
    if isinstance(x_val, date):
        return x_val
    if x_val is not None:
        try:
            return pd.Timestamp(x_val).date()
        except (TypeError, ValueError):
            pass
    return None
