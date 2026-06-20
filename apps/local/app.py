"""Streamlit local dashboard — OSS shell over ParquetStorage.

Run:
    uv sync --group local
    uv run streamlit run apps/local/app.py

Or:
    uv run python main.py dashboard
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yaml

from apps.local.chart_prep import DayStats, slice_day_frames
from apps.local.charts.day_view import _figure_from_slice
from apps.local.charts.heatmap import (
    build_plotly_heatmap_figure,
    parse_heatmap_selection,
)
from apps.local.charts.agp import build_plotly_agp_figure
from apps.local.charts.compare import build_plotly_compare_figure
from apps.local.charts.insulin import build_plotly_insulin_figure
from apps.local.charts.report import build_time_in_bands_bar
from core.metrics import ReportWindow, compute_cgm_report
from detection.config import get_config
from apps.local.charts.tir_trend import (
    build_plotly_tir_trend_figure,
    parse_tir_selection,
)
from apps.local.data import load_view_frames
from apps.local.dates import MAX_HEATMAP_DAYS, clamp_heatmap_days
from apps.local.doctor_status import collect_doctor_status
from apps.local.metrics import cgm_in_read_bounds, tir_summary_for_windows
from apps.local.navigation import (
    list_cgm_dates_from_storage,
    nearest_cgm_date,
    shift_day,
)
from core.storage.parquet import PARQUET_FILES, ParquetStorage
from ingestion.view_data import VIEW_MODES, ViewMode

_REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_ROOT = _REPO_ROOT / "data" / "processed"
CONFIG_PATH = _REPO_ROOT / "config" / "user_config.yaml"

_PLOTLY_CONFIG = {
    "scrollZoom": True,
    "displayModeBar": True,
    "displaylogo": False,
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
    "toImageButtonOptions": {"format": "png", "scale": 2},
}

PAGES = ("Day view", "Heatmap", "Time in range", "Insulin", "AGP", "Compare", "Report")


# ─────────────────────────────────────────────────────────────────────────
# Cached resources / loaders
# ─────────────────────────────────────────────────────────────────────────


@st.cache_resource
def get_storage() -> ParquetStorage:
    return ParquetStorage(root=PROCESSED_ROOT)


@st.cache_data(show_spinner=False, ttl=300)
def _list_available_dates(version_key: str) -> list[str]:
    del version_key
    return [d.isoformat() for d in list_cgm_dates_from_storage(get_storage())]


def _load_bg_targets() -> tuple[float, float]:
    with CONFIG_PATH.open() as f:
        cfg = yaml.safe_load(f)
    targets = cfg["bg_targets"]
    return float(targets["low"]), float(targets["high"])


def _load_timezone() -> str:
    with CONFIG_PATH.open() as f:
        cfg = yaml.safe_load(f)
    return str(cfg["ingestion"]["timezone"])


def _on_disk_version_key() -> str:
    status = collect_doctor_status(PROCESSED_ROOT)
    v = status["on_disk_version"]
    return "none" if v is None else str(v)


# Frames are expensive (multiple parquet reads + enrichment). Cache them per
# (view, version_key). Rebuilding the matplotlib/Plotly figure itself is fast
# (~30ms) so it is NOT cached — that keeps Streamlit free of pickled Plotly
# state that has bitten us before.
@st.cache_data(show_spinner="Loading data…", ttl=300)
def _cached_frames(view: str, version_key: str) -> dict[str, pd.DataFrame]:
    del version_key
    return load_view_frames(get_storage(), view=view)  # type: ignore[arg-type]


@st.cache_data(show_spinner=False, ttl=300)
def _cached_cgm_window(end_iso: str, days: int, version_key: str) -> pd.DataFrame:
    del version_key
    return cgm_in_read_bounds(
        get_storage().read_all_table("cgm"),
        end_date=date.fromisoformat(end_iso),
        days=days,
        tz=ZoneInfo(_load_timezone()),
    )


# ─────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────


def _render_sidebar() -> ViewMode:
    st.sidebar.header("Status")
    status = collect_doctor_status(PROCESSED_ROOT)
    if status["staleness_message"]:
        st.sidebar.warning(status["staleness_message"])
    elif status["ok"]:
        st.sidebar.success(
            f"OK · pipeline v{status['code_version']} · "
            f"{status['parquet_count']}/{len(PARQUET_FILES)} tables"
        )
    else:
        st.sidebar.info("No processed data yet.")

    available = _list_available_dates(_on_disk_version_key())
    if available:
        st.sidebar.caption(
            f"{len(available)} days with CGM data · "
            f"{available[0]} → {available[-1]}"
        )

    st.sidebar.divider()
    st.sidebar.subheader("View")
    current_view = st.session_state.get("view_mode", "original")
    view_label = st.sidebar.radio(
        "Data projection",
        options=list(VIEW_MODES),
        index=list(VIEW_MODES).index(current_view),
        horizontal=True,
        key="view_mode",
    )

    st.sidebar.divider()
    with st.sidebar.expander("Data sync", expanded=False):
        st.markdown(
            "Refresh local parquets from Tandem (needs tconnectsync creds in env):\n\n"
            "```bash\n"
            "uv run python main.py update          # incremental\n"
            "uv run python main.py fetch --clean   # full rebuild\n"
            "```"
        )
    return view_label  # type: ignore[return-value]


# ─────────────────────────────────────────────────────────────────────────
# Navigation
# ─────────────────────────────────────────────────────────────────────────


_DAY_PICKER_KEY = "main_day_picker"


def _init_session_state(available: list[date]) -> None:
    """Seed widget-backed state. ``main_day_picker`` is the canonical day."""
    if _DAY_PICKER_KEY not in st.session_state:
        st.session_state[_DAY_PICKER_KEY] = (
            available[-1] if available else date.today()
        )
    if available:
        # If saved state isn't in the available list, snap to the nearest day.
        snapped = nearest_cgm_date(st.session_state[_DAY_PICKER_KEY], available)
        if snapped != st.session_state[_DAY_PICKER_KEY]:
            st.session_state[_DAY_PICKER_KEY] = snapped


def _set_selected_day(target: date) -> None:
    """Programmatically change the canonical selected day.

    Must be called *before* ``st.rerun()``. Setting the widget key directly is
    the only way to make ``st.date_input`` reflect the new value on the next
    render — otherwise widget state silently overrides session updates.
    """
    st.session_state[_DAY_PICKER_KEY] = target


def _render_day_toolbar(available: list[date]) -> date:
    """Prev | date picker | Next. The date picker owns canonical state."""
    current = st.session_state[_DAY_PICKER_KEY]

    c_prev, c_date, c_next, c_label = st.columns(
        [1, 2, 1, 4], vertical_alignment="center"
    )
    with c_prev:
        if st.button(
            "◀ Prev day",
            width="stretch",
            disabled=not available,
            key="btn_prev_day",
        ):
            _set_selected_day(shift_day(current, -1, available))
            st.rerun()
    with c_date:
        st.date_input(
            "Day",
            key=_DAY_PICKER_KEY,
            label_visibility="collapsed",
        )
    with c_next:
        if st.button(
            "Next day ▶",
            width="stretch",
            disabled=not available,
            key="btn_next_day",
        ):
            _set_selected_day(shift_day(current, 1, available))
            st.rerun()
    with c_label:
        # Read after the widget so it reflects any same-run picker change.
        live_day = st.session_state[_DAY_PICKER_KEY]
        st.markdown(
            f"<div style='padding-top:0.4rem;color:#555;'>"
            f"<b>{live_day.strftime('%A, %B %-d, %Y')}</b></div>",
            unsafe_allow_html=True,
        )

    return st.session_state[_DAY_PICKER_KEY]


def _render_day_stats(stats: DayStats, view: ViewMode) -> None:
    cols = st.columns(5)
    cols[0].metric("TIR", f"{stats.tir_pct:.0f}%")
    cols[1].metric("Avg BG", f"{stats.avg_bg:.0f}")
    cols[2].metric("SD", f"{stats.sd_bg:.0f}")
    cols[3].metric("TDI", f"{stats.tdd:.1f} U")
    cols[4].metric("Carbs", f"{stats.total_carbs:.0f} g")
    if view == "enriched":
        st.caption("View: enriched (gap shading from `cgm_gaps`, bolus categories, forced site flags)")


# ─────────────────────────────────────────────────────────────────────────
# Page bodies
# ─────────────────────────────────────────────────────────────────────────


def _page_day_view(selected_day: date, view: ViewMode, low: float, high: float) -> None:
    frames = _cached_frames(view, _on_disk_version_key())
    day_slice = slice_day_frames(
        frames, selected_day, view=view, low=low, high=high  # type: ignore[arg-type]
    )
    if day_slice is None:
        st.warning(
            f"No CGM data for **{selected_day}**. "
            "Try a different day, or run `uv run python main.py fetch` / "
            "`fetch-day --date YYYY-MM-DD`."
        )
        return

    _render_day_stats(day_slice.stats, view)
    fig = _figure_from_slice(day_slice)
    st.plotly_chart(
        fig,
        width="stretch",
        config=_PLOTLY_CONFIG,
        key="day_chart",
    )
    with st.expander("Legend & hover guide", expanded=False):
        st.markdown(
            "- **CGM** markers are color-coded: green = in range, "
            "orange = high (>target), red = low or very high (>250).\n"
            "- **Bolus** diamond size is proportional to units; carbs (orange dot) "
            "are matched to the bolus cluster.\n"
            "- **Gap** shading (gray): CGM signal-loss windows. In enriched view "
            "these come from `cgm_gaps`; original view derives from raw alarm "
            "pairs.\n"
            "- **Site issue** band (yellow, bolus row): clustered occlusions in "
            "enriched view.\n"
            "- **Suspend** band (red, basal row): pump-suspend windows.\n"
            "- Use the toolbar (top-right of chart) to zoom, pan, autoscale, and "
            "download a PNG."
        )


def _page_heatmap(
    selected_day: date,
    view: ViewMode,
    low: float,
    high: float,
    available: list[date],
) -> None:
    heat_days = clamp_heatmap_days(
        st.slider(
            "Days in heatmap",
            min_value=7,
            max_value=MAX_HEATMAP_DAYS,
            value=30,
            step=1,
            help=f"At most {MAX_HEATMAP_DAYS} calendar days.",
            key="heatmap_days",
        )
    )
    cgm = _cached_cgm_window(
        selected_day.isoformat(), heat_days, _on_disk_version_key()
    )
    fig = build_plotly_heatmap_figure(
        cgm, low=low, high=high, end_date=selected_day, days=heat_days
    )
    event = st.plotly_chart(
        fig,
        width="stretch",
        config=_PLOTLY_CONFIG,
        on_select="rerun",
        selection_mode="points",
        key="heatmap_chart",
    )
    picked = parse_heatmap_selection(event)
    c1, c2 = st.columns([3, 1])
    with c1:
        st.caption(
            "Tip: click any cell to jump to that day's Day view, or use the "
            "picker below if click selection isn't available."
        )
    with c2:
        if available:
            jump = st.selectbox(
                "Jump to date",
                options=available,
                index=available.index(nearest_cgm_date(selected_day, available)),
                format_func=lambda d: d.strftime("%Y-%m-%d"),
                key="heatmap_jump_picker",
                label_visibility="collapsed",
            )
            if st.button("Open Day view", key="heatmap_jump_btn", width="stretch"):
                _jump_to_day_view(jump)
    if picked is not None and picked != st.session_state.get("_last_heatmap_pick"):
        st.session_state._last_heatmap_pick = picked
        _jump_to_day_view(picked)


def _page_tir(
    selected_day: date,
    low: float,
    high: float,
    available: list[date],
) -> None:
    cgm = _cached_cgm_window(
        selected_day.isoformat(), 30, _on_disk_version_key()
    )
    summary = tir_summary_for_windows(
        cgm, low=low, high=high, end_date=selected_day
    )
    cols = st.columns(len(summary))
    for col, (window, pct) in zip(cols, summary.items(), strict=True):
        with col:
            st.metric(
                label=f"{window}-day TIR",
                value=f"{pct:.0f}%" if pct is not None else "—",
            )
    st.caption(
        f"Target range {low:.0f}–{high:.0f} mg/dL (from `{CONFIG_PATH.name}`)"
    )

    trend = build_plotly_tir_trend_figure(
        cgm, low=low, high=high, end_date=selected_day, days=30
    )
    tir_event = st.plotly_chart(
        trend,
        width="stretch",
        config=_PLOTLY_CONFIG,
        on_select="rerun",
        selection_mode="points",
        key="tir_trend_chart",
    )
    tir_day = parse_tir_selection(tir_event)
    if tir_day is not None and tir_day != st.session_state.get("_last_tir_pick"):
        st.session_state._last_tir_pick = tir_day
        _jump_to_day_view(tir_day)


def _page_insulin(selected_day: date, view: ViewMode) -> None:
    days = st.radio(
        "Window",
        (14, 30, 90),
        index=1,
        horizontal=True,
        format_func=lambda d: f"{d} days",
        key="insulin_days",
    )
    frames = _cached_frames(view, _on_disk_version_key())
    fig = build_plotly_insulin_figure(
        frames.get("bolus", pd.DataFrame()),
        frames.get("basal", pd.DataFrame()),
        end_date=selected_day,
        days=int(days),
    )
    st.plotly_chart(fig, width="stretch", config=_PLOTLY_CONFIG, key="insulin_chart")
    st.caption(
        "Daily totals: bolus = sum of delivered boluses; basal = commanded "
        "rate integrated over 5-minute intervals. Matches the web insulin panel."
    )


def _page_agp(selected_day: date, low: float, high: float) -> None:
    days = st.radio(
        "Window",
        (14, 30, 90),
        index=1,
        horizontal=True,
        format_func=lambda d: f"{d} days",
        key="agp_days",
    )
    cgm = _cached_cgm_window(
        selected_day.isoformat(), int(days), _on_disk_version_key()
    )
    fig = build_plotly_agp_figure(
        cgm,
        low=low,
        high=high,
        end_date=selected_day,
        days=int(days),
        tz=_load_timezone(),
    )
    st.plotly_chart(fig, width="stretch", config=_PLOTLY_CONFIG, key="agp_chart")
    st.caption(
        "Ambulatory Glucose Profile: median with 25–75% and 5–95% bands by "
        "hour of day. Percentiles computed by core/metrics/agp.py; the web "
        "AGP page uses the same definition."
    )


def _page_compare(
    selected_day: date, low: float, high: float, available: list[date]
) -> None:
    if not available:
        st.info("No CGM days available to compare.")
        return
    default_a = nearest_cgm_date(selected_day, available)
    default_b = shift_day(default_a, -1, available)
    c1, c2 = st.columns(2)
    with c1:
        day_a = st.selectbox(
            "Day A",
            options=available,
            index=available.index(default_a),
            format_func=lambda d: d.strftime("%Y-%m-%d"),
            key="compare_day_a",
        )
    with c2:
        day_b = st.selectbox(
            "Day B",
            options=available,
            index=available.index(default_b),
            format_func=lambda d: d.strftime("%Y-%m-%d"),
            key="compare_day_b",
        )
    version = _on_disk_version_key()
    cgm_a = _cached_cgm_window(day_a.isoformat(), 1, version)
    cgm_b = _cached_cgm_window(day_b.isoformat(), 1, version)
    fig = build_plotly_compare_figure(
        cgm_a, cgm_b, date_a=day_a, date_b=day_b, low=low, high=high
    )
    st.plotly_chart(fig, width="stretch", config=_PLOTLY_CONFIG, key="compare_chart")
    st.caption("Two days overlaid on a shared time-of-day axis.")


def _fmt(value, suffix="", *, digits=1):
    return f"{value:.{digits}f}{suffix}" if value is not None else "—"


def _page_report(selected_day: date) -> None:
    days = st.radio(
        "Window",
        (14, 30, 90),
        index=0,
        horizontal=True,
        format_func=lambda d: f"{d} days",
        key="report_days",
    )
    config = get_config()
    cgm = _cached_cgm_window(
        selected_day.isoformat(),
        int(days),
        _on_disk_version_key(),
    )
    report = compute_cgm_report(
        cgm,
        config=config,
        window=ReportWindow(end_date=selected_day, days=int(days), tz=config.timezone),
    )

    if not report.meets_sufficiency:
        st.warning(
            f"Data sufficiency not met (need ≥14 days and ≥70% active CGM time; "
            f"have {report.days_covered} days, {report.active_pct:.0f}% active). "
            "GMI and GRI are withheld until the window is sufficient."
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("GRI", _fmt(report.gri, digits=0))
    c2.metric("GMI", _fmt(report.gmi, "%"))
    c3.metric("Mean BG", _fmt(report.mean_bg, " mg/dL", digits=0))
    c4.metric(
        "CV",
        _fmt(report.cv_pct, "%", digits=0),
        delta="stable" if report.cv_stable else "high" if report.cv_stable is not None else None,
        delta_color="off",
    )
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("TIR (70–180)", _fmt(report.tir, "%", digits=0))
    c6.metric("TITR (70–140)", _fmt(report.titr, "%", digits=0))
    c7.metric("Below 70", _fmt(report.tbr_total, "%", digits=0))
    c8.metric("Above 180", _fmt(report.tar_total, "%", digits=0))

    st.plotly_chart(
        build_time_in_bands_bar(
            tbr2=report.tbr2,
            tbr1=report.tbr1,
            tir=report.tir,
            tar1=report.tar1,
            tar2=report.tar2,
        ),
        width="stretch",
        config=_PLOTLY_CONFIG,
        key="report_bands_bar",
    )

    with st.expander("Risk & variability detail"):
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("LBGI", _fmt(report.lbgi, digits=1))
        d2.metric("HBGI", _fmt(report.hbgi, digits=1))
        d3.metric("eA1c", _fmt(report.ea1c, "%"))
        d4.metric("MAGE", _fmt(report.mage, " mg/dL", digits=0))
        e1, e2, e3, _ = st.columns(4)
        e1.metric("MODD", _fmt(report.modd, digits=1))
        e2.metric("CONGA", _fmt(report.conga, digits=1))
        e3.metric("J-index", _fmt(report.j_index, digits=1))
    st.caption(
        "Computed by core/metrics (single source of truth) over the "
        f"{int(days)}-day window. Observations only — not medical advice."
    )


def _jump_to_day_view(target: date) -> None:
    """Switch tabs to Day view and force the picker to show ``target``."""
    _set_selected_day(target)
    st.session_state.page_radio = "Day view"
    st.rerun()


# ─────────────────────────────────────────────────────────────────────────
# Top-level
# ─────────────────────────────────────────────────────────────────────────


def main() -> None:
    st.set_page_config(
        page_title="T1D Engine (local)",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.title("T1D Engine — local dashboard")
    st.caption(
        "Observations only — not medical advice. Do not change therapy based on this UI."
    )

    available_iso = _list_available_dates(_on_disk_version_key())
    available = [date.fromisoformat(d) for d in available_iso]
    _init_session_state(available)

    view = _render_sidebar()

    # ``page_radio`` is fully owned by the widget. Programmatic jumps from
    # heatmap / TIR write the key before ``st.rerun`` so the next render
    # picks it up. Do NOT re-assign here — that would overwrite user clicks.
    if "page_radio" not in st.session_state:
        st.session_state.page_radio = "Day view"
    page = st.radio(
        "Page",
        list(PAGES),
        horizontal=True,
        label_visibility="collapsed",
        key="page_radio",
    )

    low, high = _load_bg_targets()
    selected_day = _render_day_toolbar(available)

    st.divider()

    if page == "Day view":
        _page_day_view(selected_day, view, low, high)
    elif page == "Heatmap":
        _page_heatmap(selected_day, view, low, high, available)
    elif page == "Insulin":
        _page_insulin(selected_day, view)
    elif page == "AGP":
        _page_agp(selected_day, low, high)
    elif page == "Compare":
        _page_compare(selected_day, low, high, available)
    elif page == "Report":
        _page_report(selected_day)
    else:
        _page_tir(selected_day, low, high, available)


if __name__ == "__main__":
    main()
