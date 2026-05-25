"""Interactive Plotly day view (CGM + bolus + basal).

Design notes:

* Hover mode is "closest" — events sit at arbitrary x; "x unified" makes the
  tooltip pile up multiple unrelated traces.
* Marker text labels are avoided on bolus / carb / event / alarm traces;
  hover carries the detail. This keeps the chart legible at full-day zoom.
* Range bands (low / high) are drawn as filled background rectangles, not
  just horizontal lines.
* Gap shading uses ``add_vrect`` with a label — no invisible hover markers.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from apps.local.chart_prep import (
    MAJOR_ALARM_LABELS,
    DaySlice,
    day_xlim,
    format_bolus_hover,
    slice_day_frames,
)
from ingestion.view_data import ViewMode

# Range / event palette.
C_GREEN = "#2E7D32"
C_ORANGE = "#EF6C00"
C_RED = "#C62828"
C_LOW_LINE = "#E53935"
C_HIGH_LINE = "#E65100"
C_TARGET_FILL = "rgba(76, 175, 80, 0.08)"
C_HIGH_FILL = "rgba(255, 152, 0, 0.06)"
C_LOW_FILL = "rgba(244, 67, 54, 0.08)"
C_BOLUS = "#1565C0"
C_CARB = "#F57C00"
C_BASAL_EDGE = "#1E88E5"
C_BASAL_FILL = "rgba(30, 136, 229, 0.18)"
C_SUSPEND = "rgba(244, 67, 54, 0.18)"
C_GAP = "rgba(120, 120, 120, 0.18)"
C_SITE = "rgba(255, 193, 7, 0.25)"


def _bg_band(val: float, low: float, high: float) -> tuple[str, str]:
    if val < low:
        return C_RED, "low"
    if val > 250:
        return C_RED, "very high"
    if val > high:
        return C_ORANGE, "high"
    return C_GREEN, "in range"


def _bolus_category(cluster: dict, requests: pd.DataFrame) -> str | None:
    if requests.empty or "bolus_category" not in requests.columns:
        return None
    cl_ts = pd.Timestamp(cluster["time"])
    req_ts = pd.to_datetime(requests["timestamp"])
    mask = (req_ts >= cl_ts - pd.Timedelta(minutes=5)) & (
        req_ts <= cl_ts + pd.Timedelta(minutes=5)
    )
    matched = requests[mask]
    if matched.empty:
        return None
    rep = matched.sort_values("total_requested", ascending=False).iloc[0]
    cat = rep.get("bolus_category")
    return cat if isinstance(cat, str) and cat else None


def build_plotly_day_figure(
    frames: dict[str, pd.DataFrame],
    target_date: str,
    *,
    view: ViewMode = "original",
    low: float,
    high: float,
) -> go.Figure | None:
    """Build the day figure, or ``None`` if no CGM data on that day."""
    from datetime import date as date_type

    target = date_type.fromisoformat(target_date)
    day = slice_day_frames(frames, target, view=view, low=low, high=high)
    if day is None:
        return None
    return _figure_from_slice(day)


def _figure_from_slice(day: DaySlice) -> go.Figure:
    x0, x1 = day_xlim(day.target, day.cgm)
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.6, 0.18, 0.22],
    )

    _add_range_bands(fig, day, row=1, x0=x0, x1=x1)
    _add_cgm_panel(fig, day, row=1, x1=x1)
    _add_bolus_panel(fig, day, row=2, x1=x1)
    _add_basal_panel(fig, day, row=3, x1=x1)

    fig.update_layout(
        height=720,
        hovermode="closest",
        margin=dict(l=60, r=30, t=20, b=40),
        showlegend=False,
        dragmode="zoom",
        plot_bgcolor="white",
        paper_bgcolor="white",
    )

    hour_tick_kwargs = dict(
        tickformat="%-I %p",
        dtick=60 * 60 * 1000 * 2,
        showgrid=True,
        gridcolor="rgba(0,0,0,0.05)",
    )
    fig.update_xaxes(range=[x0, x1], row=1, col=1, **hour_tick_kwargs)
    fig.update_xaxes(range=[x0, x1], row=2, col=1, **hour_tick_kwargs)
    fig.update_xaxes(range=[x0, x1], row=3, col=1, **hour_tick_kwargs)

    fig.update_yaxes(
        title_text="CGM (mg/dL)",
        range=[40, 360],
        row=1,
        col=1,
        showgrid=True,
        gridcolor="rgba(0,0,0,0.05)",
    )
    fig.update_yaxes(
        title_text="Bolus",
        range=[0, 3],
        showticklabels=False,
        row=2,
        col=1,
        showgrid=False,
    )
    fig.update_yaxes(
        title_text="Basal (U/hr)",
        rangemode="tozero",
        row=3,
        col=1,
        showgrid=True,
        gridcolor="rgba(0,0,0,0.05)",
    )
    return fig


def _add_range_bands(
    fig: go.Figure, day: DaySlice, *, row: int, x0: datetime, x1: datetime
) -> None:
    fig.add_hrect(
        y0=day.low, y1=day.high,
        fillcolor=C_TARGET_FILL, line_width=0,
        layer="below", row=row, col=1,
    )
    fig.add_hrect(
        y0=day.high, y1=360,
        fillcolor=C_HIGH_FILL, line_width=0,
        layer="below", row=row, col=1,
    )
    fig.add_hrect(
        y0=40, y1=day.low,
        fillcolor=C_LOW_FILL, line_width=0,
        layer="below", row=row, col=1,
    )
    fig.add_hline(
        y=day.high, line_color=C_HIGH_LINE, line_width=1, opacity=0.7,
        row=row, col=1,
        annotation_text=f"{int(day.high)}", annotation_position="right",
        annotation_font_color=C_HIGH_LINE, annotation_font_size=10,
    )
    fig.add_hline(
        y=day.low, line_color=C_LOW_LINE, line_width=1, opacity=0.7,
        row=row, col=1,
        annotation_text=f"{int(day.low)}", annotation_position="right",
        annotation_font_color=C_LOW_LINE, annotation_font_size=10,
    )


def _add_cgm_panel(fig: go.Figure, day: DaySlice, *, row: int, x1: datetime) -> None:
    cgm = day.cgm.sort_values("timestamp").copy()
    cgm["_ts"] = pd.to_datetime(cgm["timestamp"])
    n = len(cgm)
    backfilled = (
        cgm["backfilled"].astype(bool).tolist()
        if "backfilled" in cgm.columns
        else [False] * n
    )

    # Gap shading first (drawn under markers).
    if day.view == "enriched":
        _add_gap_vrects(fig, day.cgm_gaps_day, row=row, label="CGM gap")
    else:
        _add_alarm_gap_vrects(fig, day.alarms, x1=x1, row=row)

    # Single line with gap breaks.
    line_x: list = []
    line_y: list = []
    ts_vals = cgm["_ts"].to_list()
    bg_vals = cgm["bg_mgdl"].to_list()
    for i in range(n):
        if i > 0 and (ts_vals[i] - ts_vals[i - 1]).total_seconds() > 900:
            line_x.append(None)
            line_y.append(None)
        line_x.append(ts_vals[i])
        line_y.append(float(bg_vals[i]))
    fig.add_trace(
        go.Scatter(
            x=line_x, y=line_y,
            mode="lines",
            line=dict(color="#9E9E9E", width=1.4),
            connectgaps=False,
            hoverinfo="skip",
            showlegend=False,
        ),
        row=row, col=1,
    )

    # Range-colored markers with rich hover.
    colors: list[str] = []
    bands: list[str] = []
    for v in bg_vals:
        c, b = _bg_band(float(v), day.low, day.high)
        colors.append(c)
        bands.append(b)
    hover = [
        f"<b>{t.strftime('%H:%M')}</b><br>"
        f"{int(round(float(v)))} mg/dL · {b}"
        + ("<br><i>backfilled</i>" if bf else "")
        for t, v, b, bf in zip(ts_vals, bg_vals, bands, backfilled)
    ]
    fig.add_trace(
        go.Scatter(
            x=ts_vals, y=bg_vals,
            mode="markers",
            marker=dict(
                color=colors,
                size=5,
                line=dict(width=0),
            ),
            hovertext=hover,
            hoverinfo="text",
            name="CGM",
            showlegend=False,
        ),
        row=row, col=1,
    )

    # Major alarms — top of panel, hover-only (no permanent labels).
    if not day.alarms.empty:
        major = day.alarms[
            (day.alarms["category"] == "alarm")
            & (day.alarms["action"] == "activated")
            & (day.alarms["alarm_name"].isin(MAJOR_ALARM_LABELS))
        ]
        if not major.empty:
            ts_list = pd.to_datetime(major["timestamp"]).to_list()
            labels = [
                MAJOR_ALARM_LABELS.get(n, str(n)) for n in major["alarm_name"]
            ]
            full_names = list(major["alarm_name"])
            hover_text = [
                f"<b>{label}</b><br>{t.strftime('%H:%M')}<br>{name}"
                for label, name, t in zip(labels, full_names, ts_list)
            ]
            fig.add_trace(
                go.Scatter(
                    x=ts_list,
                    y=[340] * len(ts_list),
                    mode="markers",
                    marker=dict(
                        color=C_RED, size=10, symbol="triangle-down",
                        line=dict(color="white", width=1),
                    ),
                    hovertext=hover_text,
                    hoverinfo="text",
                    showlegend=False,
                ),
                row=row, col=1,
            )


def _add_gap_vrects(
    fig: go.Figure, gaps: pd.DataFrame, *, row: int, label: str
) -> None:
    if gaps.empty:
        return
    for _, g in gaps.iterrows():
        start = pd.to_datetime(g["start_ts"])
        end_raw = g.get("end_ts")
        end = pd.to_datetime(end_raw) if pd.notna(end_raw) else start + timedelta(hours=2)
        fig.add_vrect(
            x0=start, x1=end,
            fillcolor=C_GAP, opacity=1.0, line_width=0,
            layer="below",
            annotation_text=label,
            annotation_position="top left",
            annotation_font_size=9,
            annotation_font_color="#616161",
            row=row, col=1,
        )


def _add_alarm_gap_vrects(
    fig: go.Figure, alarms: pd.DataFrame, *, x1: datetime, row: int
) -> None:
    if alarms.empty:
        return
    oor_act = alarms[
        (alarms["alarm_name"] == "cgm_out_of_range")
        & (alarms["action"] == "activated")
    ].sort_values("timestamp")
    oor_clr = alarms[
        (alarms["alarm_name"] == "cgm_out_of_range")
        & (alarms["action"] == "cleared")
    ].sort_values("timestamp")
    for _, act_row in oor_act.iterrows():
        start = pd.to_datetime(act_row["timestamp"])
        cleared = oor_clr[pd.to_datetime(oor_clr["timestamp"]) > start]
        end = (
            pd.to_datetime(cleared.iloc[0]["timestamp"])
            if not cleared.empty
            else x1
        )
        fig.add_vrect(
            x0=start, x1=end,
            fillcolor=C_GAP, opacity=1.0, line_width=0,
            layer="below",
            row=row, col=1,
        )


def _add_bolus_panel(fig: go.Figure, day: DaySlice, *, row: int, x1: datetime) -> None:
    fig.add_hline(
        y=1.0, line_color="rgba(0,0,0,0.12)", line_width=1, layer="below",
        row=row, col=1,
    )

    if day.view == "enriched" and not day.site_issues_day.empty:
        for _, issue in day.site_issues_day.iterrows():
            start = pd.to_datetime(issue["first_occlusion_ts"])
            end_raw = issue.get("last_occlusion_ts")
            end = (
                pd.to_datetime(end_raw)
                if pd.notna(end_raw)
                else start + timedelta(hours=1)
            )
            fig.add_vrect(
                x0=start, x1=end,
                fillcolor=C_SITE, opacity=1.0, line_width=0,
                layer="below",
                annotation_text="site issue",
                annotation_position="bottom left",
                annotation_font_size=9,
                annotation_font_color="#8D6E00",
                row=row, col=1,
            )

    # Bolus clusters — one marker, hover-only details, sized by units.
    if day.clusters:
        xs: list = []
        ys: list[float] = []
        sizes: list[float] = []
        hover: list[str] = []
        for cl in day.clusters:
            cat = _bolus_category(cl, day.requests) if day.view == "enriched" else None
            xs.append(pd.Timestamp(cl["time"]))
            ys.append(2.0)
            sizes.append(min(30.0, max(12.0, cl["total_units"] * 4)))
            hover.append(format_bolus_hover(cl, cat))
        fig.add_trace(
            go.Scatter(
                x=xs, y=ys,
                mode="markers",
                marker=dict(
                    symbol="diamond", color=C_BOLUS,
                    size=sizes,
                    line=dict(color="white", width=1),
                ),
                hovertext=hover,
                hoverinfo="text",
                showlegend=False,
            ),
            row=row, col=1,
        )

        # Carbs row (only clusters that carry carbs).
        carb_xs: list = []
        carb_hover: list[str] = []
        for cl in day.clusters:
            if cl.get("carbs", 0) > 0:
                carb_xs.append(pd.Timestamp(cl["time"]))
                carb_hover.append(
                    f"<b>Carbs</b> {pd.Timestamp(cl['time']).strftime('%H:%M')}<br>"
                    f"{int(cl['carbs'])} g"
                )
        if carb_xs:
            fig.add_trace(
                go.Scatter(
                    x=carb_xs, y=[0.5] * len(carb_xs),
                    mode="markers",
                    marker=dict(color=C_CARB, size=11,
                                line=dict(color="white", width=1)),
                    hovertext=carb_hover,
                    hoverinfo="text",
                    showlegend=False,
                ),
                row=row, col=1,
            )

    # Events.
    if not day.events.empty:
        ev = day.events.copy()
        ev["_ts"] = pd.to_datetime(ev["timestamp"])

        sites = ev[ev["event_type"] == "site_change"]
        if not sites.empty:
            forced_col = sites.get("forced_by_alarm")
            xs_list = sites["_ts"].to_list()
            forced_list = (
                forced_col.to_list() if forced_col is not None else [None] * len(sites)
            )
            hover = []
            colors = []
            for t, forced in zip(xs_list, forced_list):
                is_forced = forced is True and day.view == "enriched"
                hover.append(
                    f"<b>Site change</b> {t.strftime('%H:%M')}"
                    + ("<br><i>forced by alarm</i>" if is_forced else "")
                )
                colors.append("rgba(0,0,0,0)" if is_forced else "#9E9E9E")
            fig.add_trace(
                go.Scatter(
                    x=xs_list, y=[0.1] * len(xs_list),
                    mode="markers",
                    marker=dict(
                        symbol="square",
                        size=11,
                        color=colors,
                        line=dict(color="#616161", width=1.2),
                    ),
                    hovertext=hover,
                    hoverinfo="text",
                    showlegend=False,
                ),
                row=row, col=1,
            )

        modes = ev[ev["event_type"] == "mode_change"]
        if not modes.empty:
            xs_list = modes["_ts"].to_list()
            subtypes = [str(s or "mode") for s in modes.get("event_subtype", [""] * len(modes))]
            hover = [
                f"<b>Mode</b> {t.strftime('%H:%M')}<br>{s}"
                for t, s in zip(xs_list, subtypes)
            ]
            fig.add_trace(
                go.Scatter(
                    x=xs_list, y=[0.1] * len(xs_list),
                    mode="markers",
                    marker=dict(symbol="circle", size=8, color="#9C27B0",
                                line=dict(color="white", width=1)),
                    hovertext=hover,
                    hoverinfo="text",
                    showlegend=False,
                ),
                row=row, col=1,
            )


def _add_basal_panel(
    fig: go.Figure, day: DaySlice, *, row: int, x1: datetime
) -> None:
    if not day.basal.empty:
        basal = day.basal.sort_values("timestamp").copy()
        basal["_ts"] = pd.to_datetime(basal["timestamp"])
        ts_list = basal["_ts"].to_list()
        rate_list = basal["commanded_rate"].to_list()
        sources = (
            basal["rate_source"].to_list()
            if "rate_source" in basal.columns
            else ["?"] * len(basal)
        )
        hover = [
            f"<b>Basal</b> {t.strftime('%H:%M')}<br>"
            f"{float(r):.3f} U/hr<br>{s}"
            for t, r, s in zip(ts_list, rate_list, sources)
        ]
        fig.add_trace(
            go.Scatter(
                x=ts_list, y=rate_list,
                mode="lines",
                line=dict(color=C_BASAL_EDGE, width=1.2, shape="hv"),
                fill="tozeroy",
                fillcolor=C_BASAL_FILL,
                hovertext=hover,
                hoverinfo="text",
                showlegend=False,
            ),
            row=row, col=1,
        )

    if not day.suspension.empty:
        for _, sus in day.suspension.iterrows():
            s0 = pd.to_datetime(sus["suspend_timestamp"])
            resume_raw = sus.get("resume_timestamp")
            s1 = pd.to_datetime(resume_raw) if pd.notna(resume_raw) else x1
            alarm = sus.get("alarm_name")
            if alarm is not None and pd.notna(alarm):
                reason = str(alarm).replace("Alarm", "").strip()
            else:
                reason = str(sus.get("suspend_reason", ""))
            fig.add_vrect(
                x0=s0, x1=s1,
                fillcolor=C_SUSPEND, opacity=1.0, line_width=0,
                layer="below",
                annotation_text=f"suspend · {reason}" if reason else "suspend",
                annotation_position="top left",
                annotation_font_size=9,
                annotation_font_color="#C62828",
                row=row, col=1,
            )
