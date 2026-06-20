"""Score meal-rise detections against pump bolus/carb context (M2 calibration).

Pure and source-agnostic: DataFrame-in / dataclass-out, no I/O, no ``ingestion``
import. The orchestration script (``scripts/score_meal_rise.py``) reads frames
from storage, backfills ``bolus_category`` if needed, calls these functions, and
persists the results.

Two stages:

* :func:`find_meal_rise_instances` slides the production detector across a CGM
  frame to surface candidate fast-rise events (retrospective equivalent of the
  live cron, which only evaluates the latest anchor), then applies the same
  refractory de-duplication so a single sustained rise yields one instance.
* :func:`score_instances` labels each instance against the bolus context as
  ``pre_bolused`` / ``late_bolused`` / ``uncovered`` (keeping the signed bolus
  delay), and for uncovered ones records how the miss resolved (a later user
  correction or a Control-IQ auto-correction).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from core.bolus_categories import CORRECTION_CATEGORIES as _CORRECTION_CATEGORIES
from core.bolus_categories import FOOD_CARRYING
from core.detection.meal_rise import MealRiseDetection, detect_meal_rise
from core.detection.windowing import Anchor, make_window
from detection.config import AppConfig, MealRiseCalibrationConfig

__all__ = [
    "FOOD_CARRYING",
    "LABEL_PRE",
    "LABEL_LATE",
    "LABEL_UNCOVERED",
    "ScoredInstance",
    "find_meal_rise_instances",
    "score_instances",
    "summarize",
]

# Food-carrying bolus categories (``FOOD_CARRYING``) and correction-only
# categories (``_CORRECTION_CATEGORIES``) are imported from
# ``core.bolus_categories`` — the canonical vocabulary. Corrections are NOT
# coverage; they are evidence a meal was missed (see resolution below).

LABEL_PRE = "pre_bolused"
LABEL_LATE = "late_bolused"
LABEL_UNCOVERED = "uncovered"

_RESOLUTION_BY_CATEGORY = {
    "user_correction_only": "user_correction",
    "auto_correction": "auto_correction",
}


@dataclass(frozen=True)
class ScoredInstance:
    """One labeled meal-rise detection. Fields map 1:1 to ``meal_rise_scores``."""

    event_ref: str
    pump_serial: str | None
    label: str
    anchor_ts: datetime
    rise_start_ts: datetime
    rise_end_ts: datetime
    start_level: int
    end_level: int
    delta: int
    slope_mgdl_per_min: float
    hour_of_day: int
    matched_bolus_ts: datetime | None
    matched_bolus_category: str | None
    matched_bolus_carbs: int | None
    bolus_delay_min: float | None  # signed: matched_bolus_ts - rise_start
    resolution: str | None  # uncovered only: 'user_correction' | 'auto_correction' | 'none'
    resolution_ts: datetime | None
    resolution_delay_min: float | None


def _to_dt(value) -> datetime:
    """Normalize a pandas Timestamp / datetime to a plain tz-aware datetime."""
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def find_meal_rise_instances(
    cgm_df: pd.DataFrame, config: AppConfig
) -> list[MealRiseDetection]:
    """Slide the production detector over ``cgm_df`` and dedupe by refractory.

    ``cgm_df`` needs tz-aware ``timestamp`` + ``bg_mgdl`` columns. Timestamps are
    converted to ``config.timezone`` so the detector's time-of-day multiplier
    sees the local hour (mirrors the live path).
    """
    if cgm_df is None or cgm_df.empty or "timestamp" not in cgm_df.columns:
        return []

    tz = ZoneInfo(config.timezone)
    local = cgm_df.copy()
    local["timestamp"] = local["timestamp"].dt.tz_convert(tz)
    local = local.sort_values("timestamp").reset_index(drop=True)

    pre = timedelta(minutes=config.meal_rise.window_minutes)
    detections: list[MealRiseDetection] = []
    for ts in local["timestamp"]:
        anchor = Anchor(timestamp=_to_dt(ts), kind="sliding")
        window = make_window(local, anchor, pre=pre, post=timedelta(0))
        det = detect_meal_rise(window, config.meal_rise)
        if det is not None:
            detections.append(det)

    return _apply_refractory(detections, config.meal_rise.refractory_minutes)


def _apply_refractory(
    detections: list[MealRiseDetection], refractory_minutes: int
) -> list[MealRiseDetection]:
    """Greedily keep the earliest detection per refractory window."""
    if not detections:
        return []
    ordered = sorted(detections, key=lambda d: d.anchor_timestamp)
    refractory = timedelta(minutes=refractory_minutes)
    kept: list[MealRiseDetection] = [ordered[0]]
    for det in ordered[1:]:
        if det.anchor_timestamp - kept[-1].anchor_timestamp >= refractory:
            kept.append(det)
    return kept


def score_instances(
    detections: list[MealRiseDetection],
    requests_df: pd.DataFrame | None,
    calib: MealRiseCalibrationConfig,
    *,
    pump_serial: str | None = None,
) -> list[ScoredInstance]:
    """Label each detection against bolus context. See module docstring."""
    food = _category_frame(requests_df, FOOD_CARRYING)
    corrections = _category_frame(requests_df, _CORRECTION_CATEGORIES)

    pre_lookback = timedelta(minutes=calib.pre_bolus_lookback_minutes)
    late_lookahead = timedelta(minutes=calib.late_bolus_lookahead_minutes)
    corr_lookahead = timedelta(minutes=calib.correction_lookahead_minutes)

    out: list[ScoredInstance] = []
    for det in detections:
        rise_start = _to_dt(det.window_start)
        anchor = _to_dt(det.anchor_timestamp)
        # All windows are measured from rise_start (the meal-onset proxy), so the
        # config knobs mean exactly what they say (e.g. "...after the rise start").
        match = _nearest_in_window(
            food, rise_start - pre_lookback, rise_start + late_lookahead, rise_start
        )

        if match is not None:
            bolus_ts, row = match
            delay = (bolus_ts - rise_start).total_seconds() / 60.0
            label = LABEL_PRE if delay < 0 else LABEL_LATE
            out.append(
                _build(
                    det, rise_start, anchor, pump_serial,
                    label=label,
                    matched_bolus_ts=bolus_ts,
                    matched_bolus_category=str(row["bolus_category"]),
                    matched_bolus_carbs=_int_or_none(row.get("carbs_g")),
                    bolus_delay_min=delay,
                    resolution=None,
                    resolution_ts=None,
                    resolution_delay_min=None,
                )
            )
            continue

        # Uncovered → record how (if) the miss resolved.
        resolver = _earliest_in_window(
            corrections, rise_start, rise_start + corr_lookahead
        )
        if resolver is not None:
            res_ts, res_row = resolver
            resolution = _RESOLUTION_BY_CATEGORY[str(res_row["bolus_category"])]
            res_delay = (res_ts - rise_start).total_seconds() / 60.0
        else:
            resolution, res_ts, res_delay = "none", None, None

        out.append(
            _build(
                det, rise_start, anchor, pump_serial,
                label=LABEL_UNCOVERED,
                matched_bolus_ts=None,
                matched_bolus_category=None,
                matched_bolus_carbs=None,
                bolus_delay_min=None,
                resolution=resolution,
                resolution_ts=res_ts,
                resolution_delay_min=res_delay,
            )
        )
    return out


def _build(
    det: MealRiseDetection,
    rise_start: datetime,
    anchor: datetime,
    pump_serial: str | None,
    **kw,
) -> ScoredInstance:
    return ScoredInstance(
        event_ref=f"meal_rise:{anchor.isoformat(timespec='minutes')}",
        pump_serial=pump_serial,
        anchor_ts=anchor,
        rise_start_ts=rise_start,
        rise_end_ts=_to_dt(det.window_end),
        start_level=int(det.start_level),
        end_level=int(det.end_level),
        delta=int(det.delta),
        slope_mgdl_per_min=float(det.slope_mgdl_per_min),
        hour_of_day=int(det.hour_of_day),
        **kw,
    )


def _category_frame(
    requests_df: pd.DataFrame | None, categories: frozenset[str]
) -> pd.DataFrame:
    cols = ["timestamp", "bolus_category", "carbs_g"]
    if (
        requests_df is None
        or requests_df.empty
        or "bolus_category" not in requests_df.columns
        or "timestamp" not in requests_df.columns
    ):
        return pd.DataFrame(columns=cols)
    sub = requests_df[requests_df["bolus_category"].isin(categories)]
    if sub.empty:
        return pd.DataFrame(columns=cols)
    return sub.sort_values("timestamp").reset_index(drop=True)


def _nearest_in_window(
    frame: pd.DataFrame, lo: datetime, hi: datetime, ref: datetime
):
    """Return (ts, row) of the row nearest to ``ref`` within [lo, hi], or None."""
    if frame.empty:
        return None
    in_win = frame[(frame["timestamp"] >= lo) & (frame["timestamp"] <= hi)]
    if in_win.empty:
        return None
    deltas = (in_win["timestamp"] - ref).abs()
    idx = deltas.idxmin()
    row = in_win.loc[idx]
    return _to_dt(row["timestamp"]), row


def _earliest_in_window(frame: pd.DataFrame, lo: datetime, hi: datetime):
    """Return (ts, row) of the earliest row within [lo, hi], or None."""
    if frame.empty:
        return None
    in_win = frame[(frame["timestamp"] >= lo) & (frame["timestamp"] <= hi)]
    if in_win.empty:
        return None
    row = in_win.iloc[0]  # frame is sorted ascending by timestamp
    return _to_dt(row["timestamp"]), row


def _int_or_none(value) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or pd.isna(value):
        return None
    return int(value)


def summarize(scored: list[ScoredInstance]) -> dict:
    """Label distribution + uncovered-rate headline + resolution breakdown."""
    total = len(scored)
    counts = Counter(s.label for s in scored)
    label_counts = {
        LABEL_PRE: counts.get(LABEL_PRE, 0),
        LABEL_LATE: counts.get(LABEL_LATE, 0),
        LABEL_UNCOVERED: counts.get(LABEL_UNCOVERED, 0),
    }
    resolutions = Counter(
        s.resolution for s in scored if s.label == LABEL_UNCOVERED
    )
    return {
        "total": total,
        "counts": label_counts,
        "uncovered_rate": (label_counts[LABEL_UNCOVERED] / total) if total else 0.0,
        "uncovered_resolutions": dict(resolutions),
    }
