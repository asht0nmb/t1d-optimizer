"""Missed-meal detection on normalized CGM + requests frames.

Source-agnostic: `detect_meals` consumes a CGM DataFrame shaped like
`ingestion.builders.build_cgm_df` output and an enriched requests frame
(shaped like `ingestion.builders.build_requests_df` + `enrich_requests_df`).
It does not import from `ingestion/` and never references tconnectsync.

Detection logic (see plan §2.3):

1. Sort CGM by timestamp. For each interval, compute the 5-min delta
   and the gap in minutes. Only intervals whose gap is in ``[4, 7]``
   minutes (normal Dexcom cadence) are considered — gaps outside that
   window are neither rising nor non-rising; they simply break runs so
   sensor dropouts can't mint phantom meals (plan §2.3, DATA_NOTES §4).
2. A *run* is exactly ``config.meal_detection.sustained_intervals``
   consecutive valid-cadence intervals whose delta is at least
   ``rise_threshold_per_5min``. On a hit we emit one detection and
   advance past the run's final index so detections don't overlap.
   Using a fixed-size window (vs. greedy extension) keeps
   ``rise_rate_per_5min`` comparable across events.
3. A run is **suppressed** if any row in ``requests_df`` within
   ``[run_start - no_bolus_window_minutes, run_start]`` has
   ``bolus_category`` in the food-carrying set
   (``user_meal``, ``user_meal_and_correction``, ``override_up``).
   Auto corrections (``auto_correction`` / ``bolus_source == "auto"``)
   and ``user_correction_only`` explicitly do NOT suppress — per
   DATA_NOTES §3, Control-IQ auto corrections never contain food, and
   a user correction without carbs doesn't cover a meal either.

Meal-window labeling: windows are keyed by position in the YAML
(``window_0``, ``window_1``, ...) rather than hardcoded "breakfast"
etc. This keeps the code config-driven — users can reorder or rename
windows in YAML without touching detection code. Off-hours rises are
labeled ``"off_window"``.

Confidence is a v1 heuristic placeholder (plan §2.3). Use it for
ordering, not calibration — Phase 3 will replace it.
"""

from __future__ import annotations

import pandas as pd

from detection.config import AppConfig

__all__ = ["detect_meals"]

_FOOD_CARRYING_CATEGORIES = frozenset(
    {"user_meal", "user_meal_and_correction", "override_up"}
)

_CADENCE_MIN_GAP = 4.0
_CADENCE_MAX_GAP = 7.0

_PEAK_WINDOW_MINUTES = 120

_OUTPUT_COLUMNS = [
    "timestamp",
    "bg_start",
    "bg_peak",
    "rise_rate_per_5min",
    "meal_window",
    "confidence",
]


def _empty_output() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.Series(dtype="datetime64[ns]"),
            "bg_start": pd.Series(dtype="int64"),
            "bg_peak": pd.Series(dtype="int64"),
            "rise_rate_per_5min": pd.Series(dtype="float64"),
            "meal_window": pd.Series(dtype="object"),
            "confidence": pd.Series(dtype="float64"),
        }
    )[_OUTPUT_COLUMNS]


def _meal_window_label(
    hour: int, windows: tuple[tuple[int, int], ...]
) -> str:
    for idx, (start, end) in enumerate(windows):
        if start <= hour < end:
            return f"window_{idx}"
    return "off_window"


def detect_meals(
    cgm_df: pd.DataFrame,
    requests_df: pd.DataFrame,
    config: AppConfig,
) -> pd.DataFrame:
    """Emit one row per sustained BG rise not covered by a food bolus.

    See module docstring for detection rules and the output schema.
    """
    if cgm_df is None or cgm_df.empty or len(cgm_df) < 2:
        return _empty_output()

    mc = config.meal_detection
    rise_threshold = mc.rise_threshold_per_5min
    sustained = mc.sustained_intervals
    no_bolus_window = pd.Timedelta(minutes=mc.no_bolus_window_minutes)
    windows = mc.meal_windows
    bg_high = config.bg_targets.high

    df = cgm_df.sort_values("timestamp").reset_index(drop=True)
    ts = df["timestamp"]
    bg = df["bg_mgdl"].to_numpy()
    delta = df["bg_mgdl"].diff().to_numpy()
    gap_min = (ts.diff().dt.total_seconds() / 60.0).to_numpy()

    # Bolus lookup: only food-carrying user/override categories can suppress.
    suppressing_ts: pd.Series
    if requests_df is None or requests_df.empty:
        suppressing_ts = pd.Series([], dtype="datetime64[ns, UTC]")
    else:
        mask = requests_df["bolus_category"].isin(_FOOD_CARRYING_CATEGORIES)
        suppressing_ts = pd.to_datetime(
            requests_df.loc[mask, "timestamp"]
        ).sort_values().reset_index(drop=True)

    def _valid_rise(k: int) -> bool:
        if k < 1 or k >= n:
            return False
        g = gap_min[k]
        d = delta[k]
        return (
            not pd.isna(g)
            and not pd.isna(d)
            and _CADENCE_MIN_GAP <= g <= _CADENCE_MAX_GAP
            and d >= rise_threshold
        )

    rows: list[dict] = []
    n = len(df)
    i = 1
    while i < n:
        # A run is exactly `sustained` consecutive valid rising intervals
        # starting at interval i. If any interval in the window fails the
        # cadence/threshold check, slide forward by one.
        run_end_idx = i + sustained - 1
        if run_end_idx >= n:
            break
        if not all(_valid_rise(k) for k in range(i, run_end_idx + 1)):
            i += 1
            continue

        run_start_idx = i
        run_deltas = [float(delta[k]) for k in range(i, run_end_idx + 1)]
        run_start_ts = ts.iloc[run_start_idx]

        # Suppress if any food-carrying bolus lies in the lookback window.
        if not suppressing_ts.empty:
            window_lo = run_start_ts - no_bolus_window
            in_window = (suppressing_ts >= window_lo) & (
                suppressing_ts <= run_start_ts
            )
            if bool(in_window.any()):
                i = run_end_idx + 1
                continue

        # bg_peak: max bg within 2h of run_start_ts, clipped to available data.
        peak_hi = run_start_ts + pd.Timedelta(minutes=_PEAK_WINDOW_MINUTES)
        peak_mask = (ts >= run_start_ts) & (ts <= peak_hi)
        bg_peak = int(df.loc[peak_mask, "bg_mgdl"].max())

        rise_rate = sum(run_deltas) / len(run_deltas)
        label = _meal_window_label(int(run_start_ts.hour), windows)

        base = min(1.0, rise_rate / (2.0 * rise_threshold))
        if label != "off_window":
            base += 0.1
        if bg_peak > bg_high:
            base += 0.1
        confidence = max(0.0, min(1.0, base))

        rows.append(
            {
                "timestamp": run_start_ts,
                "bg_start": int(bg[run_start_idx - 1]),
                "bg_peak": bg_peak,
                "rise_rate_per_5min": float(rise_rate),
                "meal_window": label,
                "confidence": float(confidence),
            }
        )

        i = run_end_idx + 1

    if not rows:
        return _empty_output()

    out = pd.DataFrame(rows, columns=_OUTPUT_COLUMNS)
    out = out.sort_values("timestamp").reset_index(drop=True)
    return out
