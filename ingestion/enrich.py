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
# Top-level orchestrator
# ---------------------------------------------------------------------------

def enrich_all(frames: dict[str, pd.DataFrame], config: dict) -> dict[str, pd.DataFrame]:
    """Apply all enrichment steps to a dict of normalized frames.

    Returns a new dict; does not mutate the input dict or its frames.
    Missing frames are tolerated — callers may pass partial dicts for testing.
    """
    out = dict(frames)

    if "requests" in out:
        out["requests"] = enrich_requests_df(out["requests"])

    if "events" in out:
        out["events"] = enrich_events_df(
            out["events"],
            out.get("alarms"),
            config.get("site_change_detection", {}),
        )

    return out
