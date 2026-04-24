"""Shared view-mode loader for `check` / `viz` / `run_detection`.

Two modes — same helper so CLI commands and detection scripts never drift:

* ``original``: load parquets as-is, then strip known enrichment columns. The
  goal is a deterministic, pre-enrichment projection regardless of whether the
  on-disk parquets happen to already carry enriched columns. Helper-level
  tables that only exist post-enrichment (``site_issues``, ``cgm_gaps``) are
  **not** built in this mode — callers see whatever is on disk (or empty).

* ``enriched``: load parquets, then fill in any missing enrichment in memory
  (`bolus_category`, `override_delta`, `forced_by_alarm`, `site_issues`,
  `cgm_gaps`). Mirrors the backfill that `scripts/run_detection.py` uses when
  invoked against pre-enrichment parquets — the canonical implementation
  lives here and `run_detection._ensure_enriched` delegates.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from detection.config import AppConfig, get_config
from ingestion.enrich import (
    build_cgm_gaps_df,
    build_site_issues_df,
    enrich_events_df,
    enrich_requests_df,
)
from ingestion.storage import PARQUET_FILES, load_df

ViewMode = Literal["original", "enriched"]
VIEW_MODES: tuple[ViewMode, ...] = ("original", "enriched")

# Columns added by `ingestion/enrich.py` beyond what the raw builders emit.
# `strip_enriched_columns` drops these in "original" mode so the projection is
# stable regardless of what the parquet on disk happens to contain.
ENRICHED_COLUMNS: dict[str, tuple[str, ...]] = {
    "requests": ("bolus_category", "override_delta"),
    "events": ("forced_by_alarm",),
}

# Helper tables that only exist as a result of enrichment. In "original" mode
# we don't build them; in "enriched" mode we backfill if missing.
_ENRICHED_TABLES: tuple[str, ...] = ("site_issues", "cgm_gaps")


def strip_enriched_columns(
    name: str, df: pd.DataFrame | None
) -> pd.DataFrame | None:
    """Return a copy of ``df`` with known enrichment columns removed."""
    if df is None:
        return None
    cols_to_drop = [c for c in ENRICHED_COLUMNS.get(name, ()) if c in df.columns]
    if not cols_to_drop:
        return df
    return df.drop(columns=cols_to_drop)


def ensure_enriched(
    frames: dict[str, pd.DataFrame],
    config: AppConfig,
) -> dict[str, pd.DataFrame]:
    """Backfill enrichment columns and helper tables when they're missing.

    Safe to call on already-enriched frames — each check is guarded on
    the absence of the derived column/table, so repeat calls are no-ops.
    Never mutates the input dict or its frames.
    """
    out = dict(frames)
    site_cfg = config.raw.get("site_change_detection", {})

    requests = out.get("requests")
    if (
        requests is not None
        and not requests.empty
        and "bolus_category" not in requests.columns
    ):
        out["requests"] = enrich_requests_df(requests)

    events = out.get("events")
    alarms = out.get("alarms")
    if (
        events is not None
        and not events.empty
        and "forced_by_alarm" not in events.columns
    ):
        out["events"] = enrich_events_df(events, alarms, site_cfg)

    if out.get("site_issues") is None or out["site_issues"].empty:
        if alarms is not None and not alarms.empty:
            out["site_issues"] = build_site_issues_df(
                alarms, out.get("events"), site_cfg
            )
        else:
            out["site_issues"] = pd.DataFrame()

    if out.get("cgm_gaps") is None or out["cgm_gaps"].empty:
        out["cgm_gaps"] = build_cgm_gaps_df(alarms)

    return out


def load_frames(
    mode: ViewMode = "original",
    config: AppConfig | None = None,
) -> dict[str, pd.DataFrame]:
    """Load every known parquet and project it into the requested view.

    Returns a dict keyed by `PARQUET_FILES` names (cgm, bolus, requests, ...)
    plus the helper tables (site_issues, cgm_gaps). Missing files become
    empty DataFrames so downstream code can branch on `.empty` uniformly.
    """
    if mode not in VIEW_MODES:
        raise ValueError(
            f"Unknown view mode {mode!r}; expected one of {VIEW_MODES}"
        )

    frames: dict[str, pd.DataFrame] = {}
    for name in PARQUET_FILES:
        df = load_df(name)
        if df is None:
            df = pd.DataFrame()
        frames[name] = df

    if mode == "original":
        for name in list(frames):
            frames[name] = strip_enriched_columns(name, frames[name])
        return frames

    if config is None:
        config = get_config()
    return ensure_enriched(frames, config)
