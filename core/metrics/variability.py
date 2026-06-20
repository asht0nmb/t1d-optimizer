"""Glycemic variability metrics — J-index, MODD, CONGA, MAGE.

All pure; stdlib / numpy / pandas only (core import rules). ``None`` means the
metric is undefined for the input (too few readings / days / no matched pairs);
``0.0`` means a legitimately flat result.

- **J-index** (Wojcicki): ``0.001 * (mean + sd)^2`` in mg/dL units.
- **MODD** (Mean Of Daily Differences): mean ``|g(t) - g(t-24h)|`` over points
  matched by time-of-day across consecutive local days. Needs >= 2 days.
- **CONGA(n)** (Continuous Overlapping Net Glycemic Action): sample SD of the
  differences ``g(t) - g(t - n hours)`` over all readings that have a partner
  ``n`` hours earlier (within a tolerance).
- **MAGE** (Mean Amplitude of Glycemic Excursions): mean amplitude of the
  excursions between consecutive turning points whose amplitude exceeds 1 SD of
  the series. Deterministic Baghurst-style variant: turning points are found by
  tracking the sign of successive deltas; the amplitude of each
  peak-to-nadir (or nadir-to-peak) leg is kept when it exceeds 1 SD, and MAGE is
  the mean of those qualifying amplitudes. Direction is not pre-selected — both
  ascending and descending qualifying legs contribute — which keeps the result
  reproducible and order-independent of an arbitrary first-excursion rule.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def j_index(mean: float | None, sd: float | None) -> float | None:
    """``0.001 * (mean + sd)^2``; ``None`` if either input is ``None``."""
    if mean is None or sd is None:
        return None
    return 0.001 * (mean + sd) ** 2


def _local_frame(cgm: pd.DataFrame, tz: str) -> pd.DataFrame | None:
    if cgm is None or cgm.empty or "timestamp" not in cgm.columns or "bg_mgdl" not in cgm.columns:
        return None
    out = pd.DataFrame(
        {
            "ts": pd.to_datetime(cgm["timestamp"], utc=True).dt.tz_convert(tz),
            "bg": pd.to_numeric(cgm["bg_mgdl"], errors="coerce"),
        }
    ).dropna(subset=["bg"])
    if out.empty:
        return None
    return out.sort_values("ts").reset_index(drop=True)


def modd(cgm: pd.DataFrame, tz: str = "UTC") -> float | None:
    """Mean of |g(t) - g(t-24h)| over time-of-day-matched consecutive days."""
    frame = _local_frame(cgm, tz)
    if frame is None:
        return None
    frame = frame.assign(
        date=frame["ts"].dt.date,
        tod=frame["ts"].dt.hour * 3600 + frame["ts"].dt.minute * 60 + frame["ts"].dt.second,
    )
    if frame["date"].nunique() < 2:
        return None
    diffs: list[float] = []
    for _, group in frame.groupby("tod"):
        if len(group) < 2:
            continue
        ordered = group.sort_values("date")["bg"].to_numpy(dtype=float)
        diffs.extend(np.abs(np.diff(ordered)).tolist())
    if not diffs:
        return None
    return float(np.mean(diffs))


def conga(cgm: pd.DataFrame, n_hours: float = 1.0, tz: str = "UTC") -> float | None:
    """Sample SD of ``g(t) - g(t - n_hours)`` over all matched readings."""
    frame = _local_frame(cgm, tz)
    if frame is None:
        return None
    left = frame.rename(columns={"bg": "bg_now"})
    right = frame.rename(columns={"bg": "bg_past", "ts": "ts_past"}).copy()
    right["ts"] = right["ts_past"] + pd.Timedelta(hours=n_hours)
    tol = pd.Timedelta(minutes=2, seconds=30)
    matched = pd.merge_asof(
        left, right[["ts", "bg_past"]], on="ts", tolerance=tol, direction="nearest"
    ).dropna(subset=["bg_past"])
    if len(matched) < 2:
        return None
    diffs = matched["bg_now"].to_numpy(dtype=float) - matched["bg_past"].to_numpy(dtype=float)
    return float(np.std(diffs, ddof=1))


def mage(bg, sd: float | None = None) -> float | None:
    """Mean amplitude of excursions exceeding 1 SD of the series.

    ``sd`` may be supplied (e.g. the report's ddof=1 SD); otherwise it is
    computed with ddof=1. Returns ``0.0`` for a flat series and ``None`` when
    there are too few readings to form an excursion.
    """
    arr = np.asarray(bg, dtype=float).ravel()
    arr = arr[~np.isnan(arr)]
    if arr.size < 3:
        return None
    threshold = float(np.std(arr, ddof=1)) if sd is None else float(sd)
    if threshold == 0.0:
        return 0.0

    # Turning points: indices where the sign of the successive delta flips,
    # plus the two endpoints.
    deltas = np.diff(arr)
    nonzero = deltas[deltas != 0]
    if nonzero.size == 0:
        return 0.0
    turning_idx = [0]
    last_sign = 0
    for i, d in enumerate(deltas):
        if d == 0:
            continue
        sign = 1 if d > 0 else -1
        if last_sign != 0 and sign != last_sign:
            turning_idx.append(i)  # the point before the reversal is the extremum
        last_sign = sign
    turning_idx.append(arr.size - 1)
    turning_vals = arr[np.array(sorted(set(turning_idx)))]

    amplitudes = np.abs(np.diff(turning_vals))
    qualifying = amplitudes[amplitudes > threshold]
    if qualifying.size == 0:
        return 0.0
    return float(np.mean(qualifying))
