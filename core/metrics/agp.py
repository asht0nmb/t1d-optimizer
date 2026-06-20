"""Ambulatory Glucose Profile (AGP) percentile math.

Single source of truth for the AGP definition: for a window of N days
ending at ``end_date``, group CGM readings by local time-of-day bucket in
the given timezone and report percentiles 5/25/50/75/95 of ``bg_mgdl`` per
bucket, plus reading count ``n``. Buckets with no readings are omitted.

By default the profile uses 15-minute buckets (96 per day) and applies a
**circular weighted moving average** to each percentile curve, matching the
clinical AGP rendering (smooth ribbons that wrap around midnight). The legacy
hourly profile is reachable via ``bucket_minutes=60, smooth=False`` and
reproduces the original output byte-for-byte for pre-existing callers.

The ``hour`` column is the fractional hour-of-day of the bucket *start* (0.0,
0.25, ..., 23.75 for 15-min buckets; 0..23 for hourly), so existing hourly
callers keep integer-valued hours.

The web shell mirrors this definition in SQL (``PERCENTILE_CONT`` by
local time bucket); any change here must be reflected there.

Core import rules apply: stdlib / pandas / numpy only.
"""

from __future__ import annotations

import datetime

import numpy as np
import pandas as pd

DEFAULT_PERCENTILES: tuple[float, ...] = (5, 25, 50, 75, 95)


def _percentile_columns(percentiles: tuple[float, ...]) -> list[str]:
    return [f"p{int(p):02d}" for p in percentiles]


def _circular_smooth(values: np.ndarray, window_bins: int) -> np.ndarray:
    """Triangular-weighted circular (wrap-around) moving average.

    The series is treated as periodic over the day: the array is padded on
    both ends with its own opposite-end values, convolved with normalized
    triangular weights, then trimmed back to its original length. With a flat
    (zero-variance) input the output is unchanged; a spike near one edge bleeds
    into the opposite edge, which is the wrap-around property AGP needs.

    ``window_bins`` is the full window width (odd >= 3); even values are bumped
    to the next odd value so the window is symmetric about each bin.
    """
    n = values.size
    if n == 0 or window_bins <= 1:
        return values.astype(float, copy=True)
    if window_bins % 2 == 0:
        window_bins += 1
    half = window_bins // 2
    # Triangular weights: 1, 2, ..., half+1, ..., 2, 1 (peak in the center).
    ramp = np.arange(1, half + 2, dtype=float)
    weights = np.concatenate([ramp, ramp[-2::-1]])
    weights /= weights.sum()
    # Circular pad: wrap the opposite ends so smoothing crosses midnight.
    padded = np.concatenate([values[-half:], values, values[:half]])
    smoothed = np.convolve(padded, weights, mode="valid")
    return smoothed[:n]


def agp_profile(
    cgm_df: pd.DataFrame,
    *,
    days: int,
    end_date: datetime.date,
    tz: str,
    percentiles: tuple[float, ...] = DEFAULT_PERCENTILES,
    bucket_minutes: int = 15,
    smooth: bool = True,
    smooth_window_bins: int = 5,
) -> pd.DataFrame:
    """Time-of-day BG percentile profile over a trailing window of local days.

    Args:
        cgm_df: DataFrame with ``timestamp`` (tz-aware; naive treated as UTC)
            and ``bg_mgdl`` columns.
        days: Window length in calendar days (window is the ``days`` local
            dates ending on ``end_date``, inclusive).
        end_date: Last local calendar date of the window.
        tz: IANA timezone name used to localize timestamps and bucket.
        percentiles: Percentile levels (0-100) to report.
        bucket_minutes: Bucket width in minutes (must divide 1440). Default 15
            (96 buckets/day). Pass ``60`` for the legacy hourly profile.
        smooth: When True, apply a circular weighted moving average to each
            percentile curve (wrap-around at midnight). Default True. Smoothing
            only runs over the buckets present; if some buckets are empty the
            curve is smoothed over the populated buckets in order.
        smooth_window_bins: Full width (in buckets) of the smoothing window.

    Returns:
        DataFrame with columns ``["hour", "p05", ..., "p95", "n"]`` (one
        ``pNN`` column per requested percentile), one row per time-of-day
        bucket that has at least one reading in the window, sorted by bucket.
        ``hour`` is the fractional hour-of-day of the bucket start.
    """
    if days < 1:
        raise ValueError("days must be >= 1")
    if bucket_minutes < 1 or 1440 % bucket_minutes != 0:
        raise ValueError("bucket_minutes must be a positive divisor of 1440")
    columns = ["hour", *_percentile_columns(percentiles), "n"]
    if (
        cgm_df.empty
        or "timestamp" not in cgm_df.columns
        or "bg_mgdl" not in cgm_df.columns
    ):
        return pd.DataFrame(columns=columns)

    local_ts = pd.to_datetime(cgm_df["timestamp"], utc=True).dt.tz_convert(tz)
    start_date = end_date - datetime.timedelta(days=days - 1)
    local_dates = local_ts.dt.date
    mask = (local_dates >= start_date) & (local_dates <= end_date)
    if not mask.any():
        return pd.DataFrame(columns=columns)

    minute_of_day = local_ts.loc[mask].dt.hour * 60 + local_ts.loc[mask].dt.minute
    bucket_idx = (minute_of_day // bucket_minutes).astype(int)

    grouped = pd.DataFrame(
        {
            "bucket": bucket_idx.to_numpy(),
            "bg": cgm_df.loc[mask, "bg_mgdl"].astype(float).to_numpy(),
        }
    ).groupby("bucket")["bg"]

    pcols = _percentile_columns(percentiles)
    rows: list[dict] = []
    for bucket, values in grouped:
        arr = values.to_numpy()
        row: dict = {
            "hour": (int(bucket) * bucket_minutes) / 60.0,
        }
        for p, col in zip(percentiles, pcols):
            row[col] = float(np.percentile(arr, p, method="linear"))
        row["n"] = int(arr.size)
        rows.append(row)

    out = (
        pd.DataFrame(rows, columns=columns)
        .sort_values("hour")
        .reset_index(drop=True)
    )

    if smooth and len(out) > 1:
        for col in pcols:
            out[col] = _circular_smooth(out[col].to_numpy(dtype=float), smooth_window_bins)

    return out
