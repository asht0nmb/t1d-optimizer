"""Clinical report visuals — the time-in-bands stacked bar.

Pure chart builder (no Streamlit import) so it stays unit-testable. The page
itself (apps/local/app.py) renders the scalar tiles via ``st.metric`` from a
``core.metrics.CgmReport``.
"""

from __future__ import annotations

import plotly.graph_objects as go

# Band colors — align with the day-view BG palette (low→red, in-range→green,
# high→orange, very-high→deep orange).
_BANDS = [
    ("tbr2", "Very low", "#B71C1C"),
    ("tbr1", "Low", "#E53935"),
    ("tir", "In range", "#2E7D32"),
    ("tar1", "High", "#FB8C00"),
    ("tar2", "Very high", "#E65100"),
]


def build_time_in_bands_bar(
    *,
    tbr2: float,
    tbr1: float,
    tir: float,
    tar1: float,
    tar2: float,
) -> go.Figure:
    """Single horizontal stacked bar of the five consensus glucose bands."""
    values = {"tbr2": tbr2, "tbr1": tbr1, "tir": tir, "tar1": tar1, "tar2": tar2}
    fig = go.Figure()
    for key, label, color in _BANDS:
        pct = float(values[key])
        fig.add_trace(
            go.Bar(
                x=[pct],
                y=["Time in bands"],
                name=label,
                orientation="h",
                marker_color=color,
                hovertemplate=f"{label}: <b>{pct:.1f}%</b><extra></extra>",
            )
        )
    fig.update_layout(
        barmode="stack",
        height=120,
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.05, x=0, font=dict(size=10)),
        xaxis=dict(range=[0, 100], ticksuffix="%", showgrid=False),
        yaxis=dict(showticklabels=False),
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    return fig
