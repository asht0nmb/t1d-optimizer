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

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
import yaml

from apps.local.data import load_view_frames
from apps.local.dates import MAX_HEATMAP_DAYS, clamp_heatmap_days
from apps.local.doctor_status import collect_doctor_status
from apps.local.heatmap import build_heatmap_figure
from apps.local.metrics import cgm_in_read_bounds, tir_summary_for_windows
from core.storage.parquet import PARQUET_FILES, ParquetStorage
from ingestion.view_data import VIEW_MODES, ViewMode
from scripts.daily_viz import build_daily_figure

_REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_ROOT = _REPO_ROOT / "data" / "processed"
CONFIG_PATH = _REPO_ROOT / "config" / "user_config.yaml"


@st.cache_resource
def get_storage() -> ParquetStorage:
    return ParquetStorage(root=PROCESSED_ROOT)


def _load_bg_targets() -> tuple[float, float]:
    with CONFIG_PATH.open() as f:
        cfg = yaml.safe_load(f)
    targets = cfg["bg_targets"]
    return float(targets["low"]), float(targets["high"])


def _latest_cgm_date(storage: ParquetStorage) -> date | None:
    cgm = storage.read_all_table("cgm")
    if cgm.empty:
        return None
    return pd.to_datetime(cgm["timestamp"]).max().date()


def _render_sidebar(view: ViewMode) -> tuple[ViewMode, date]:
    st.sidebar.header("Doctor")
    status = collect_doctor_status(PROCESSED_ROOT)
    st.sidebar.text(f"code pipeline: v{status['code_version']}")
    if status["on_disk_version"] is not None:
        st.sidebar.text(f"on-disk pipeline: v{status['on_disk_version']}")
    else:
        st.sidebar.text("on-disk pipeline: (none)")
    st.sidebar.text(
        f"parquet tables: {status['parquet_count']}/{len(PARQUET_FILES)}"
    )
    if status["staleness_message"]:
        st.sidebar.warning(status["staleness_message"])
    elif status["ok"]:
        st.sidebar.success("pipeline state: OK")
    else:
        st.sidebar.info("No processed data yet.")

    st.sidebar.divider()
    st.sidebar.header("View")
    view_label = st.sidebar.radio(
        "Data projection",
        options=list(VIEW_MODES),
        index=list(VIEW_MODES).index(view),
        horizontal=True,
    )

    st.sidebar.divider()
    st.sidebar.header("Data sync")
    st.sidebar.markdown(
        "Refresh local parquets from Tandem (requires tconnectsync credentials in "
        "your environment, not stored in this repo):\n\n"
        "```bash\n"
        "uv run python main.py update    # incremental\n"
        "uv run python main.py fetch --clean   # full rebuild\n"
        "```"
    )

    storage = get_storage()
    default_day = _latest_cgm_date(storage) or date.today()
    selected = st.sidebar.date_input("Day", value=default_day)
    return view_label, selected  # type: ignore[return-value]


def main() -> None:
    st.set_page_config(page_title="T1D Engine (local)", layout="wide")
    st.title("T1D Engine — local dashboard")
    st.caption(
        "Observations only — not medical advice. Do not change therapy based on this UI."
    )

    if "view_mode" not in st.session_state:
        st.session_state["view_mode"] = "original"

    view, selected_day = _render_sidebar(st.session_state["view_mode"])
    st.session_state["view_mode"] = view

    page = st.radio(
        "Page",
        ["Day view", "Heatmap", "Time in range"],
        horizontal=True,
        label_visibility="collapsed",
    )

    storage = get_storage()
    low, high = _load_bg_targets()

    if page == "Day view":
        with st.spinner("Loading day…"):
            frames = load_view_frames(storage, view=view)
        fig = build_daily_figure(
            selected_day.isoformat(),
            view=view,
            frames=frames,
        )
        if fig is None:
            st.warning(
                f"No CGM data for {selected_day}. "
                "Run `uv run python main.py fetch` or `fetch-day --date …`."
            )
        else:
            st.pyplot(fig)
            plt.close(fig)

    elif page == "Heatmap":
        heat_days = clamp_heatmap_days(
            st.slider(
                "Days",
                min_value=7,
                max_value=MAX_HEATMAP_DAYS,
                value=30,
                help=f"At most {MAX_HEATMAP_DAYS} calendar days of CGM.",
            )
        )
        cgm = cgm_in_read_bounds(
            storage.read_all_table("cgm"),
            end_date=selected_day,
            days=heat_days,
        )
        fig = build_heatmap_figure(
            cgm,
            low=low,
            high=high,
            end_date=selected_day,
            days=heat_days,
        )
        st.pyplot(fig)
        plt.close(fig)

    else:
        cgm = cgm_in_read_bounds(
            storage.read_all_table("cgm"),
            end_date=selected_day,
            days=30,
        )
        summary = tir_summary_for_windows(
            cgm,
            low=low,
            high=high,
            end_date=selected_day,
        )
        cols = st.columns(len(summary))
        for col, (window, pct) in zip(cols, summary.items(), strict=True):
            with col:
                st.metric(
                    label=f"{window}-day TIR",
                    value=f"{pct:.0f}%" if pct is not None else "—",
                )
        st.caption(f"Target range {low:.0f}–{high:.0f} mg/dL (from {CONFIG_PATH})")


if __name__ == "__main__":
    main()
