"""Anomaly detection on normalized CGM series.

Source-agnostic: `detect_anomalies` consumes a DataFrame shaped like
`ingestion.builders.build_cgm_df` output and an `AppConfig`; it does not
import from `ingestion/` and must never gain a tconnectsync reference.

Three anomaly classes are emitted, one row per event:

- **spike**: first reading crossing above `spike_threshold` (previous
  reading was at/below). Repeat emissions while the series stays elevated
  are suppressed.
- **drop**: mirror image against `drop_threshold`.
- **flatline**: rolling window of `flatline_consecutive_intervals`
  readings whose sample variance is below `flatline_tolerance` and whose
  inter-sample gaps are all <= 7 min (contiguous sensor cadence). After
  flagging the last index of the window, scanning advances by K to avoid
  emitting overlapping flatline events.

Backfilled readings keep their sensor-time `timestamp` from
`build_cgm_df`; they are treated as valid signal. The output row's
`is_backfilled_context` mirrors the source reading's `backfilled` flag so
downstream surfaces may segregate historical-only events.

Confidence values are v1 arithmetic placeholders (see plan §2.2). They
are intended for ordering, not calibration — Phase 3 will replace them.
"""

from __future__ import annotations

import pandas as pd

from detection.config import AppConfig

__all__ = ["detect_anomalies"]

# Maximum allowed inter-sample gap (minutes) inside a flatline window.
# Dexcom G7 cadence is 5 min; 7 min tolerates mild jitter but rejects any
# real gap (dropped reading, sensor restart, etc.).
_FLATLINE_MAX_GAP_MIN = 7.0

_OUTPUT_COLUMNS = [
    "timestamp",
    "anomaly_type",
    "bg_at_event",
    "rate_mgdl_per_min",
    "confidence",
    "is_backfilled_context",
]


def _empty_output() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
            "anomaly_type": pd.Series(dtype="object"),
            "bg_at_event": pd.Series(dtype="int64"),
            "rate_mgdl_per_min": pd.Series(dtype="float64"),
            "confidence": pd.Series(dtype="float64"),
            "is_backfilled_context": pd.Series(dtype="bool"),
        }
    )[_OUTPUT_COLUMNS]


def detect_anomalies(cgm_df: pd.DataFrame, config: AppConfig) -> pd.DataFrame:
    """Emit one row per anomalous CGM event.

    See module docstring for detection rules and the output schema.
    """
    if cgm_df is None or cgm_df.empty or len(cgm_df) < 2:
        return _empty_output()

    ac = config.anomaly_detection
    spike_threshold = ac.spike_threshold
    drop_threshold = ac.drop_threshold
    flatline_tolerance = ac.flatline_tolerance
    K = ac.flatline_consecutive_intervals

    df = cgm_df.sort_values("timestamp").reset_index(drop=True)

    bg = df["bg_mgdl"].to_numpy()
    ts = df["timestamp"]
    backfilled = df["backfilled"].to_numpy()

    prev_bg = df["bg_mgdl"].shift(1).to_numpy()
    delta_minutes = (ts.diff().dt.total_seconds() / 60.0).to_numpy()

    rows: list[dict] = []

    # --- Spike / drop: one event per threshold crossing --------------------
    for i in range(1, len(df)):
        p, b = prev_bg[i], bg[i]
        if pd.isna(p):
            continue
        dt_min = delta_minutes[i]
        if not (dt_min and dt_min > 0):
            # Identical/non-advancing timestamps: skip rate-based events.
            continue
        rate = float((b - p) / dt_min)

        if b > spike_threshold and p <= spike_threshold:
            rows.append(
                {
                    "timestamp": ts.iloc[i],
                    "anomaly_type": "spike",
                    "bg_at_event": int(b),
                    "rate_mgdl_per_min": rate,
                    "confidence": min(
                        1.0, (float(b) - spike_threshold) / spike_threshold
                    ),
                    "is_backfilled_context": bool(backfilled[i]),
                }
            )
        elif b < drop_threshold and p >= drop_threshold:
            rows.append(
                {
                    "timestamp": ts.iloc[i],
                    "anomaly_type": "drop",
                    "bg_at_event": int(b),
                    "rate_mgdl_per_min": rate,
                    "confidence": min(
                        1.0, (drop_threshold - float(b)) / drop_threshold
                    ),
                    "is_backfilled_context": bool(backfilled[i]),
                }
            )

    # --- Flatline: rolling variance + contiguity ---------------------------
    # Sample variance (ddof=1) matches pandas' default; we use it as a
    # relative heuristic, not a statistical estimate, so ddof choice is
    # arbitrary.
    if len(df) >= K and K >= 2:
        var_series = df["bg_mgdl"].rolling(window=K).var(ddof=1).to_numpy()
        # Max intra-window gap (minutes). We compute rolling max over a
        # window of size K-1 deltas ending at index i, which corresponds to
        # gaps *inside* the K-reading window ending at i.
        gap_series = (
            pd.Series(delta_minutes).rolling(window=K - 1).max().to_numpy()
        )

        i = K - 1
        n = len(df)
        while i < n:
            var_i = var_series[i]
            max_gap = gap_series[i]
            if (
                not pd.isna(var_i)
                and not pd.isna(max_gap)
                and var_i < flatline_tolerance
                and max_gap <= _FLATLINE_MAX_GAP_MIN
            ):
                rows.append(
                    {
                        "timestamp": ts.iloc[i],
                        "anomaly_type": "flatline",
                        "bg_at_event": int(bg[i]),
                        "rate_mgdl_per_min": 0.0,
                        "confidence": max(
                            0.0, 1.0 - (float(var_i) / flatline_tolerance)
                        ),
                        "is_backfilled_context": bool(backfilled[i]),
                    }
                )
                i += K  # Skip forward so windows don't overlap.
            else:
                i += 1

    if not rows:
        return _empty_output()

    out = pd.DataFrame(rows, columns=_OUTPUT_COLUMNS)
    out = out.sort_values("timestamp").reset_index(drop=True)
    return out
