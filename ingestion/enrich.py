"""Source-agnostic enrichment layer.

Runs after `builders.build_all` to add derived columns and helper tables
that the detection engine depends on. Every function is a pure transform
over normalized DataFrames — no API calls, no I/O.

See `docs/operating_docs/DATA_NOTES.md` and
`docs/plans/2026-04-20-enrichment-and-detection-v1.md` for motivation.
"""

from __future__ import annotations

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

    return out
