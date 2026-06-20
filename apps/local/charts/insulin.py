"""Daily insulin totals chart — bolus + basal bars per calendar day.

Daily-total semantics mirror ``apps/web/lib/queries/insulin.ts`` so both
shells report the same numbers: bolus = SUM(insulin_units) per day; basal
= SUM(commanded_rate * 5 / 60) per day (the basal stream is 5-minute
commanded-rate rows, so each row contributes rate/12 units).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go

from apps.local.dates import iter_dates_in_window

C_BOLUS = "#1565C0"
C_BASAL = "#90A4AE"

_BASAL_ROW_HOURS = 5.0 / 60.0  # 5-minute commanded-rate rows


def _daily_totals(
    df: pd.DataFrame, value_col: str, dates: list[date], per_row_factor: float = 1.0
) -> list[float]:
    """Sum ``value_col * per_row_factor`` per calendar day, zero-filled."""
    if df.empty or "timestamp" not in df.columns or value_col not in df.columns:
        return [0.0] * len(dates)
    ts = pd.to_datetime(df["timestamp"])
    sums = (
        df[value_col].astype(float).mul(per_row_factor).groupby(ts.dt.date).sum()
    )
    return [float(sums.get(d, 0.0)) for d in dates]


def build_plotly_insulin_figure(
    bolus: pd.DataFrame,
    basal: pd.DataFrame,
    *,
    end_date: date,
    days: int = 30,
) -> go.Figure:
    """Grouped daily bolus/basal totals for the trailing ``days`` window."""
    dates = iter_dates_in_window(end_date, days)
    bolus_y = _daily_totals(bolus, "insulin_units", dates)
    basal_y = _daily_totals(basal, "commanded_rate", dates, _BASAL_ROW_HOURS)

    fig = go.Figure()
    for name, ys, color in (
        ("Bolus", bolus_y, C_BOLUS),
        ("Basal", basal_y, C_BASAL),
    ):
        fig.add_trace(
            go.Bar(
                x=[d.isoformat() for d in dates],
                y=ys,
                name=name,
                marker_color=color,
                hovertemplate=(
                    "<b>%{x}</b><br>" + name + " <b>%{y:.1f} U</b><extra></extra>"
                ),
            )
        )
    fig.update_layout(
        barmode="group",
        height=380,
        margin=dict(l=55, r=30, t=30, b=50),
        hovermode="closest",
        plot_bgcolor="white",
        paper_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    fig.update_xaxes(
        title_text="Date",
        showgrid=True, gridcolor="rgba(0,0,0,0.05)",
    )
    fig.update_yaxes(
        title_text="Insulin (U)",
        rangemode="tozero",
        showgrid=True, gridcolor="rgba(0,0,0,0.05)",
    )
    return fig
