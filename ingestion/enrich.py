"""Source-agnostic enrichment layer.

Runs after `builders.build_all` to add derived columns and helper tables
that the detection engine depends on. Every function is a pure transform
over normalized DataFrames — no API calls, no I/O.

See `docs/operating_docs/DATA_NOTES.md` and
`docs/plans/2026-04-20-enrichment-and-detection-v1.md` for motivation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path("config/user_config.yaml")


def load_config(path: Path | None = None) -> dict:
    """Minimal YAML config loader.

    Task 2.1 introduces a typed, validated `AppConfig` in
    `detection/config.py` that supersedes this helper. Callers that only
    need the raw dict (fetch → enrich pipeline) can keep using it.
    """
    p = path or _CONFIG_PATH
    with open(p) as f:
        return yaml.safe_load(f) or {}

# Insulin-unit tolerance for override_delta sign classification.
# Covers float noise from Msg3 fractional unit reporting.
_OVERRIDE_EPSILON = 0.01


def enrich_requests_df(df: pd.DataFrame) -> pd.DataFrame:
    """Derive `bolus_category` and `override_delta` on a requests frame.

    Categories (see DATA_NOTES §3):
      - `auto_correction`: Control-IQ automated correction (no food)
      - `user_meal`: user bolus covering carbs only
      - `user_meal_and_correction`: user bolus with food + correction
      - `user_correction_only`: user correction bolus (no carbs)
      - `override_up` / `override_down`: user changed the calculated dose
      - `unknown`: undiscernible (empty row, unexpected bolus_source)

    `override_delta = total_requested - (food_insulin + correction_insulin)`
    when `bolus_source == "override"`, else NaN. Positive = dose increased.
    """
    new_columns = ["bolus_category", "override_delta"]

    if df.empty:
        out = df.copy()
        out["bolus_category"] = pd.Series(dtype="object")
        out["override_delta"] = pd.Series(dtype="float64")
        return out

    out = df.copy()
    food = out["food_insulin"].fillna(0.0)
    corr = out["correction_insulin"].fillna(0.0)
    total = out["total_requested"].fillna(0.0)
    source = out["bolus_source"]

    delta = total - (food + corr)

    categories: list[str] = []
    for src, c, f, k, d in zip(
        source.tolist(),
        out["carbs_g"].fillna(0).tolist(),
        food.tolist(),
        corr.tolist(),
        delta.tolist(),
    ):
        categories.append(_categorize(src, c, f, k, d))

    out["bolus_category"] = categories
    out["override_delta"] = delta.where(source == "override", other=float("nan"))

    # Preserve original column order + new derived columns at the end.
    return out[[*df.columns, *new_columns]]


def _categorize(src: str, carbs: float, food: float, correction: float, delta: float) -> str:
    if src == "auto":
        return "auto_correction"
    if src == "override":
        if delta > _OVERRIDE_EPSILON:
            return "override_up"
        if delta < -_OVERRIDE_EPSILON:
            return "override_down"
        # Override that net-zero'd out: fall back to the user-branch semantics.
        return _user_branch(carbs, food, correction)
    if src == "user":
        return _user_branch(carbs, food, correction)
    return "unknown"


def _user_branch(carbs: float, food: float, correction: float) -> str:
    has_carbs = carbs > 0
    has_food = food > 0
    has_correction = correction > 0
    if has_carbs and has_food and has_correction:
        return "user_meal_and_correction"
    if has_carbs and has_food:
        return "user_meal"
    if not has_carbs and has_correction:
        return "user_correction_only"
    return "unknown"


# ---------------------------------------------------------------------------
# Forced-site-change tagging (DATA_NOTES §2)
# ---------------------------------------------------------------------------

def enrich_events_df(
    events_df: pd.DataFrame,
    alarms_df: pd.DataFrame | None,
    config: dict,
) -> pd.DataFrame:
    """Tag firmware-forced site changes after a `BatteryShutdownAlarm`.

    Adds a `forced_by_alarm` column to `events_df`:
      - `True` if `event_type == "site_change"` falls inside
        `[shutdown_ts, shutdown_ts + forced_window_minutes]` of any activated
        `BatteryShutdownAlarm`, subject to the cartridge volume override below.
      - `False` for site_change rows outside that window (or when no such
        alarm exists).
      - `pd.NA` for non-site_change rows.

    Cartridge volume override (DATA_NOTES §2): a `cartridge` subtype inside
    the window whose `details.insulin_volume` is `>= cartridge_real_fill_threshold`
    is treated as a real site change (`forced_by_alarm = False`). Missing /
    malformed volumes default to forced. `tubing` and `cannula` subtypes carry
    no volume signal and are always forced when inside the window.
    """
    out = events_df.copy()
    out["forced_by_alarm"] = pd.Series(pd.NA, index=out.index, dtype="object")

    if out.empty:
        return out

    site_mask = out["event_type"] == "site_change"
    if not site_mask.any():
        return out

    out.loc[site_mask, "forced_by_alarm"] = False

    if alarms_df is None or alarms_df.empty:
        return out

    shutdowns = alarms_df.loc[
        (alarms_df["alarm_name"] == "BatteryShutdownAlarm")
        & (alarms_df["action"] == "activated"),
        "timestamp",
    ].tolist()
    if not shutdowns:
        return out

    window = pd.Timedelta(minutes=config["forced_window_minutes"])
    threshold = config.get("cartridge_real_fill_threshold")

    def _in_forced_window(event_ts) -> bool:
        return any(s <= event_ts <= s + window for s in shutdowns)

    for idx in out.index[site_mask]:
        event_ts = out.at[idx, "timestamp"]
        if not _in_forced_window(event_ts):
            continue

        subtype = out.at[idx, "event_subtype"]
        if subtype == "cartridge" and threshold is not None:
            volume = _parse_insulin_volume(out.at[idx, "details"])
            if volume is not None and volume >= threshold:
                # Large fill after pump-death → genuine site change.
                continue

        out.at[idx, "forced_by_alarm"] = True

    return out


def _parse_insulin_volume(details) -> float | None:
    """Extract `insulin_volume` from a site_change details JSON string.

    Returns None for missing keys, non-string inputs, malformed JSON, or
    values that can't be coerced to float. Callers treat None as "no signal"
    and fall back to the timestamp-only heuristic (i.e., forced).
    """
    if not isinstance(details, str):
        return None
    try:
        payload = json.loads(details)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("insulin_volume")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Occlusion clustering → site_issues (DATA_NOTES §1)
# ---------------------------------------------------------------------------

_SITE_ISSUES_COLUMNS = [
    "first_occlusion_ts",
    "last_occlusion_ts",
    "occlusion_count",
    "resolved_by_site_change_ts",
    "resolution_delay_minutes",
    "pump_serial",
]


def build_site_issues_df(
    alarms_df: pd.DataFrame,
    events_df: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    """Cluster `OcclusionAlarm` activations into suspected site-failure episodes.

    A cluster is a run of activated `OcclusionAlarm` rows within
    `occlusion_cluster_window_minutes` of the previous alarm. Only clusters
    with `occlusion_count >= min_occlusions_for_cluster` are emitted
    (per DATA_NOTES §1: a single isolated occlusion is not clinically
    significant; 2+ together indicate a failing site).

    Resolution is the first `site_change` event (any subtype) strictly after
    `last_occlusion_ts` whose `forced_by_alarm != True` — firmware-forced
    cartridge refills tagged by `enrich_events_df` do not count as real site
    rotations. If `events_df` lacks a `forced_by_alarm` column (e.g.
    enrichment hasn't run yet), all site_change rows are treated as
    non-forced; unresolved clusters leave NaT / NaN.
    """
    window = pd.Timedelta(minutes=config.get("occlusion_cluster_window_minutes", 180))
    min_count = config.get("min_occlusions_for_cluster", 2)

    empty = _empty_site_issues()
    if alarms_df is None or alarms_df.empty:
        return empty

    occl = alarms_df[
        (alarms_df["alarm_name"] == "OcclusionAlarm")
        & (alarms_df["action"] == "activated")
    ].sort_values("timestamp")
    if occl.empty:
        return empty

    real_site_changes = _real_site_changes(events_df).sort_values("timestamp")

    clusters: list[dict] = []
    current: list[pd.Series] | None = None
    prev_ts: pd.Timestamp | None = None

    for _, row in occl.iterrows():
        ts = row["timestamp"]
        if current is None or (ts - prev_ts) > window:
            if current is not None:
                clusters.append(_summarize_cluster(current, real_site_changes))
            current = [row]
        else:
            current.append(row)
        prev_ts = ts

    if current is not None:
        clusters.append(_summarize_cluster(current, real_site_changes))

    filtered = [c for c in clusters if c["occlusion_count"] >= min_count]
    if not filtered:
        return empty

    return pd.DataFrame(filtered, columns=_SITE_ISSUES_COLUMNS)


def _real_site_changes(events_df: pd.DataFrame) -> pd.DataFrame:
    """Return site_change rows eligible to resolve an occlusion cluster.

    Firmware-forced fills (`forced_by_alarm == True`) are excluded. If the
    column is absent, assume enrichment hasn't run and accept everything.
    """
    if events_df is None or events_df.empty:
        return pd.DataFrame(columns=["timestamp"])
    site = events_df[events_df["event_type"] == "site_change"]
    if site.empty:
        return site
    if "forced_by_alarm" in site.columns:
        site = site[site["forced_by_alarm"] != True]  # noqa: E712
    return site


def _summarize_cluster(rows: list[pd.Series], site_changes: pd.DataFrame) -> dict:
    first_ts = rows[0]["timestamp"]
    last_ts = rows[-1]["timestamp"]
    pump_serial = rows[0]["pump_serial"]

    resolver_ts = pd.NaT
    delay = float("nan")
    if not site_changes.empty:
        after = site_changes[site_changes["timestamp"] > last_ts]
        if not after.empty:
            resolver_ts = after.iloc[0]["timestamp"]
            delay = (resolver_ts - last_ts).total_seconds() / 60.0

    return {
        "first_occlusion_ts": first_ts,
        "last_occlusion_ts": last_ts,
        "occlusion_count": len(rows),
        "resolved_by_site_change_ts": resolver_ts,
        "resolution_delay_minutes": delay,
        "pump_serial": pump_serial,
    }


def _empty_site_issues() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "first_occlusion_ts": pd.Series(dtype="datetime64[ns, UTC]"),
            "last_occlusion_ts": pd.Series(dtype="datetime64[ns, UTC]"),
            "occlusion_count": pd.Series(dtype="int64"),
            "resolved_by_site_change_ts": pd.Series(dtype="datetime64[ns, UTC]"),
            "resolution_delay_minutes": pd.Series(dtype="float64"),
            "pump_serial": pd.Series(dtype="object"),
        },
        columns=_SITE_ISSUES_COLUMNS,
    )


# ---------------------------------------------------------------------------
# CGM out-of-range episodes → cgm_gaps (DATA_ISSUES #6)
# ---------------------------------------------------------------------------

_CGM_GAPS_COLUMNS = [
    "start_ts",
    "end_ts",
    "duration_minutes",
    "pump_serial",
    "ongoing",
]


def build_cgm_gaps_df(alarms_df: pd.DataFrame | None) -> pd.DataFrame:
    """Pair `cgm_out_of_range` activated/cleared rows into sensor-blind episodes.

    Detection code uses these windows to exclude periods where Control-IQ had
    no CGM signal (and therefore couldn't dose / adjust). The pairing mirrors
    `build_suspension_df`: maintain one open `activated` event; when a
    `cleared` arrives, pair them. A second `activated` without an intervening
    `cleared` force-closes the prior episode at the new activation timestamp
    and logs a warning. A trailing unpaired `activated` is emitted with
    `end_ts = NaT`, `duration_minutes = NaN`, `ongoing = True`.
    """
    empty = _empty_cgm_gaps()
    if alarms_df is None or alarms_df.empty:
        return empty

    gap_alarms = alarms_df[alarms_df["alarm_name"] == "cgm_out_of_range"]
    if gap_alarms.empty:
        return empty

    gap_alarms = gap_alarms.sort_values("timestamp")

    episodes: list[dict] = []
    current: pd.Series | None = None

    for _, row in gap_alarms.iterrows():
        action = row["action"]
        ts_ = row["timestamp"]
        if action == "activated":
            if current is not None:
                logger.warning(
                    "Double-activated cgm_out_of_range at %s; closing prior "
                    "unpaired episode started at %s",
                    ts_,
                    current["timestamp"],
                )
                episodes.append(_closed_gap(current, ts_))
            current = row
        elif action == "cleared":
            if current is None:
                logger.warning(
                    "Unpaired cgm_out_of_range cleared at %s; skipping", ts_
                )
                continue
            episodes.append(_closed_gap(current, ts_))
            current = None
        # Any other action (e.g. "ack") is ignored — sensor-blind state is
        # defined by activated/cleared transitions only.

    if current is not None:
        episodes.append({
            "start_ts": current["timestamp"],
            "end_ts": pd.NaT,
            "duration_minutes": float("nan"),
            "pump_serial": current["pump_serial"],
            "ongoing": True,
        })

    if not episodes:
        return empty

    return pd.DataFrame(episodes, columns=_CGM_GAPS_COLUMNS)


def _closed_gap(activated_row: pd.Series, end_ts: pd.Timestamp) -> dict:
    start_ts = activated_row["timestamp"]
    dur = (end_ts - start_ts).total_seconds() / 60.0
    return {
        "start_ts": start_ts,
        "end_ts": end_ts,
        "duration_minutes": dur,
        "pump_serial": activated_row["pump_serial"],
        "ongoing": False,
    }


def _empty_cgm_gaps() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "start_ts": pd.Series(dtype="datetime64[ns, UTC]"),
            "end_ts": pd.Series(dtype="datetime64[ns, UTC]"),
            "duration_minutes": pd.Series(dtype="float64"),
            "pump_serial": pd.Series(dtype="object"),
            "ongoing": pd.Series(dtype="bool"),
        },
        columns=_CGM_GAPS_COLUMNS,
    )


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------

def enrich_all(frames: dict[str, pd.DataFrame], config: dict) -> dict[str, pd.DataFrame]:
    """Apply all enrichment steps to a dict of normalized frames.

    Returns a new dict; does not mutate the input dict or its frames.
    Missing frames are tolerated — callers may pass partial dicts for testing.
    """
    out = dict(frames)
    site_cfg = config.get("site_change_detection", {})

    if "requests" in out:
        out["requests"] = enrich_requests_df(out["requests"])

    if "events" in out:
        out["events"] = enrich_events_df(
            out["events"],
            out.get("alarms"),
            site_cfg,
        )

    # Site issues depend on events already carrying `forced_by_alarm`, so this
    # must run after enrich_events_df.
    if "alarms" in out:
        out["site_issues"] = build_site_issues_df(
            out["alarms"],
            out.get("events", pd.DataFrame()),
            site_cfg,
        )
        out["cgm_gaps"] = build_cgm_gaps_df(out.get("alarms"))

    return out
