"""Daily feature aggregation for clustering (plan §2.4).

`daily_features(frames, date, config)` slices the seven normalized/enriched
frames to a single day in the configured timezone and returns a dict of
14 features plus a ``date`` key (16 fields total) suitable for the Task 2.5
KMeans clustering pipeline.

Source-agnostic: no imports from ``ingestion/``; never references
tconnectsync. Every threshold comes from ``AppConfig``.

Documented choices (plan left these open):

* **Empty-frame defaults.** Counts/sums (``total_daily_insulin``,
  ``meal_count``, ``total_carbs_g``, ``alarm_count``,
  ``suspension_minutes``, ``out_of_range_minutes``) are ``0`` when their
  source frame is missing or empty. Ratios/means (``tir_*``,
  ``time_*``, ``mean_bg``, ``std_bg``, ``cv_bg``, ``basal_bolus_ratio``,
  ``overnight_dip``, ``mean_postprandial_peak``) are ``NaN`` — they are
  undefined, not zero.
* **``std_bg`` uses ``ddof=0``** (population std). Rationale: a single
  day is the whole population we care about for that day; ddof=1 would
  require ``n > 1`` and produce NaN on single-reading days. ``cv_bg``
  therefore matches the "pandas .std(ddof=0)" convention.
* **Ongoing CGM gaps** (``ongoing=True``, ``end_ts`` NaT) are treated as
  ending at the end of the slicing window for today's overlap
  calculation. This avoids dropping the last minutes of a live gap at
  run time.
* **Basal integration.** For each basal row inside the day window,
  duration = next row's timestamp − current. The final in-window row
  extends to ``day_end``. v1 intentionally ignores the contribution
  from a row starting before ``day_start`` that remained active into
  the day; a future revision may carry the last pre-day row forward.
* **Overnight dip windows** (04:00–06:00 minus 00:00–02:00) are
  hardcoded per plan v1 simplification. They could move to
  ``AppConfig`` when the dip definition itself evolves.
* **``mean_postprandial_peak`` anchor.** The baseline BG at the bolus
  timestamp comes from the nearest CGM reading at or before the bolus
  (10-minute tolerance) — *not* ``requests.bg_mgdl``, which is a
  finger-stick value and often 0/missing. Meal rows without a nearby
  CGM anchor are skipped; if no meal rows remain, the feature is NaN.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from detection.config import AppConfig

__all__ = ["daily_features"]


_MEAL_CATEGORIES = frozenset(
    {"user_meal", "user_meal_and_correction", "override_up"}
)

_BG_ANCHOR_TOLERANCE_MIN = 10.0
_POSTPRANDIAL_WINDOW_MIN = 120.0

# v1: hardcoded overnight dip windows (documented above).
_DIP_MORNING_START_HOUR = 4
_DIP_MORNING_END_HOUR = 6
_DIP_EARLY_START_HOUR = 0
_DIP_EARLY_END_HOUR = 2


def daily_features(
    frames: dict[str, pd.DataFrame],
    date: _date,
    config: AppConfig,
) -> dict:
    """Return one row of daily features for the given ``date``.

    See module docstring for semantics and documented defaults.
    """
    tz = ZoneInfo(config.timezone)
    day_start = pd.Timestamp(datetime(date.year, date.month, date.day, tzinfo=tz))
    day_end = day_start + pd.Timedelta(days=1)

    cgm = _slice(frames.get("cgm"), "timestamp", day_start, day_end)
    bolus = _slice(frames.get("bolus"), "timestamp", day_start, day_end)
    basal = _slice(frames.get("basal"), "timestamp", day_start, day_end)
    requests = _slice(frames.get("requests"), "timestamp", day_start, day_end)
    alarms = _slice(frames.get("alarms"), "timestamp", day_start, day_end)
    # Suspension + cgm_gaps use interval overlap; we keep the full frames
    # and compute overlap minutes against the day window.
    suspension = frames.get("suspension")
    cgm_gaps = frames.get("cgm_gaps")

    feats: dict = {"date": date}
    feats.update(_cgm_features(cgm, config))
    feats.update(_insulin_features(bolus, basal, day_start, day_end))
    feats.update(_meal_features(requests))
    feats["overnight_dip"] = _overnight_dip(cgm, day_start)
    feats["mean_postprandial_peak"] = _postprandial_peak(cgm, requests)
    feats["alarm_count"] = _alarm_count(alarms)
    feats["suspension_minutes"] = _suspension_minutes(
        suspension, day_start, day_end
    )
    feats["out_of_range_minutes"] = _out_of_range_minutes(
        cgm_gaps, day_start, day_end
    )
    return feats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slice(
    df: pd.DataFrame | None,
    ts_col: str,
    day_start: pd.Timestamp,
    day_end: pd.Timestamp,
) -> pd.DataFrame:
    """Return rows whose ``ts_col`` lies in ``[day_start, day_end)``.

    Tolerant of ``None`` or empty frames; returns empty copies in those
    cases. The comparison is tz-aware: ``day_start`` / ``day_end`` are
    tz-aware and the frames' tz-aware columns compare correctly even
    when zones differ (pandas aligns on instants).
    """
    if df is None or df.empty or ts_col not in df.columns:
        return df if df is not None else pd.DataFrame()
    ts = df[ts_col]
    mask = (ts >= day_start) & (ts < day_end)
    return df.loc[mask]


def _cgm_features(cgm: pd.DataFrame, config: AppConfig) -> dict:
    if cgm is None or cgm.empty or "bg_mgdl" not in cgm.columns:
        return {
            "tir_70_180": float("nan"),
            "time_below_70": float("nan"),
            "time_above_180": float("nan"),
            "time_above_250": float("nan"),
            "mean_bg": float("nan"),
            "std_bg": float("nan"),
            "cv_bg": float("nan"),
        }

    bg = cgm["bg_mgdl"].astype(float).to_numpy()
    if bg.size == 0:
        return _cgm_features(cgm.iloc[0:0], config)

    low = config.bg_targets.low
    high = config.bg_targets.high
    severe_high = 250

    total = float(bg.size)
    tir = float(((bg >= low) & (bg <= high)).sum()) / total
    below = float((bg < low).sum()) / total
    above = float(((bg > high) & (bg <= severe_high)).sum()) / total
    severe = float((bg > severe_high).sum()) / total

    mean_bg = float(bg.mean())
    std_bg = float(bg.std(ddof=0))
    cv_bg = std_bg / mean_bg if mean_bg != 0 else float("nan")

    return {
        "tir_70_180": tir,
        "time_below_70": below,
        "time_above_180": above,
        "time_above_250": severe,
        "mean_bg": mean_bg,
        "std_bg": std_bg,
        "cv_bg": cv_bg,
    }


def _insulin_features(
    bolus: pd.DataFrame,
    basal: pd.DataFrame,
    day_start: pd.Timestamp,
    day_end: pd.Timestamp,
) -> dict:
    bolus_total = 0.0
    if bolus is not None and not bolus.empty and "insulin_units" in bolus.columns:
        bolus_total = float(bolus["insulin_units"].astype(float).sum())

    basal_total = _integrate_basal(basal, day_start, day_end)

    tdi = bolus_total + basal_total
    if bolus_total == 0:
        ratio = float("nan")
    else:
        ratio = basal_total / bolus_total
    return {
        "total_daily_insulin": tdi,
        "basal_bolus_ratio": ratio,
    }


def _integrate_basal(
    basal: pd.DataFrame,
    day_start: pd.Timestamp,
    day_end: pd.Timestamp,
) -> float:
    """Integrate commanded_rate (u/hr) × duration (hours) across the day.

    Duration for each in-window row is ``(next_ts - this_ts)`` clipped
    to ``day_end``. The final row extends to ``day_end``.
    """
    if basal is None or basal.empty or "commanded_rate" not in basal.columns:
        return 0.0
    df = basal.sort_values("timestamp").reset_index(drop=True)
    ts = df["timestamp"]
    rates = df["commanded_rate"].astype(float).to_numpy()
    total = 0.0
    n = len(df)
    for i in range(n):
        row_ts = ts.iloc[i]
        next_ts = ts.iloc[i + 1] if i + 1 < n else day_end
        end_ts = min(next_ts, day_end)
        dur_hours = (end_ts - row_ts).total_seconds() / 3600.0
        if dur_hours > 0:
            total += rates[i] * dur_hours
    return float(total)


def _meal_features(requests: pd.DataFrame) -> dict:
    if (
        requests is None
        or requests.empty
        or "bolus_category" not in requests.columns
    ):
        return {"meal_count": 0, "total_carbs_g": 0}
    mask = requests["bolus_category"].isin(_MEAL_CATEGORIES)
    meal_rows = requests.loc[mask]
    carbs = 0
    if "carbs_g" in meal_rows.columns:
        carbs = int(
            pd.to_numeric(meal_rows["carbs_g"], errors="coerce").fillna(0).sum()
        )
    return {
        "meal_count": int(mask.sum()),
        "total_carbs_g": carbs,
    }


def _overnight_dip(cgm: pd.DataFrame, day_start: pd.Timestamp) -> float:
    if cgm is None or cgm.empty or "bg_mgdl" not in cgm.columns:
        return float("nan")
    early_lo = day_start + pd.Timedelta(hours=_DIP_EARLY_START_HOUR)
    early_hi = day_start + pd.Timedelta(hours=_DIP_EARLY_END_HOUR)
    morning_lo = day_start + pd.Timedelta(hours=_DIP_MORNING_START_HOUR)
    morning_hi = day_start + pd.Timedelta(hours=_DIP_MORNING_END_HOUR)

    ts = cgm["timestamp"]
    early = cgm.loc[(ts >= early_lo) & (ts < early_hi), "bg_mgdl"]
    morning = cgm.loc[(ts >= morning_lo) & (ts < morning_hi), "bg_mgdl"]
    if early.empty or morning.empty:
        return float("nan")
    return float(morning.astype(float).mean() - early.astype(float).mean())


def _postprandial_peak(
    cgm: pd.DataFrame, requests: pd.DataFrame
) -> float:
    if requests is None or requests.empty or "bolus_category" not in requests.columns:
        return float("nan")
    meal_rows = requests.loc[
        requests["bolus_category"].isin(_MEAL_CATEGORIES)
    ]
    if meal_rows.empty:
        return float("nan")
    if cgm is None or cgm.empty or "bg_mgdl" not in cgm.columns:
        return float("nan")

    cgm_sorted = cgm.sort_values("timestamp").reset_index(drop=True)
    ts = cgm_sorted["timestamp"]
    bg = cgm_sorted["bg_mgdl"].astype(float)

    tolerance = pd.Timedelta(minutes=_BG_ANCHOR_TOLERANCE_MIN)
    window = pd.Timedelta(minutes=_POSTPRANDIAL_WINDOW_MIN)

    deltas: list[float] = []
    for bolus_ts in meal_rows["timestamp"]:
        # Anchor: nearest CGM at or before bolus_ts within tolerance.
        prior_mask = (ts <= bolus_ts) & (ts >= bolus_ts - tolerance)
        if not prior_mask.any():
            continue
        anchor_bg = float(bg[prior_mask].iloc[-1])
        # Peak: max BG in [bolus_ts, bolus_ts + 2h].
        peak_mask = (ts >= bolus_ts) & (ts <= bolus_ts + window)
        if not peak_mask.any():
            continue
        peak_bg = float(bg[peak_mask].max())
        deltas.append(peak_bg - anchor_bg)

    if not deltas:
        return float("nan")
    return float(np.mean(deltas))


def _alarm_count(alarms: pd.DataFrame) -> int:
    if alarms is None or alarms.empty or "action" not in alarms.columns:
        return 0
    return int((alarms["action"] == "activated").sum())


def _suspension_minutes(
    suspension: pd.DataFrame,
    day_start: pd.Timestamp,
    day_end: pd.Timestamp,
) -> float:
    if (
        suspension is None
        or suspension.empty
        or "suspend_timestamp" not in suspension.columns
    ):
        return 0.0
    total = 0.0
    for _, row in suspension.iterrows():
        start = row["suspend_timestamp"]
        end = row.get("resume_timestamp")
        if pd.isna(start):
            continue
        # Unpaired suspend (no resume) → treat as ending at day_end for
        # today's contribution (mirrors the ongoing-gap decision).
        if pd.isna(end):
            end = day_end
        total += _overlap_minutes(start, end, day_start, day_end)
    return float(total)


def _out_of_range_minutes(
    cgm_gaps: pd.DataFrame,
    day_start: pd.Timestamp,
    day_end: pd.Timestamp,
) -> float:
    if (
        cgm_gaps is None
        or cgm_gaps.empty
        or "start_ts" not in cgm_gaps.columns
    ):
        return 0.0
    total = 0.0
    for _, row in cgm_gaps.iterrows():
        start = row["start_ts"]
        end = row.get("end_ts")
        ongoing = bool(row.get("ongoing", False))
        if pd.isna(start):
            continue
        # Ongoing (open-ended) gaps: treat end as day_end for today's slice.
        if pd.isna(end) or ongoing:
            end = day_end
        total += _overlap_minutes(start, end, day_start, day_end)
    return float(total)


def _overlap_minutes(
    start: pd.Timestamp,
    end: pd.Timestamp,
    day_start: pd.Timestamp,
    day_end: pd.Timestamp,
) -> float:
    lo = max(start, day_start)
    hi = min(end, day_end)
    if hi <= lo:
        return 0.0
    return (hi - lo).total_seconds() / 60.0
