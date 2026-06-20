"""Core CGM analytics panel — time-in-bands, central tendency, GMI, eA1c, CV.

Every function is pure and operates on a numeric BG array (mg/dL); NaNs are
dropped internally so callers can pass raw column values. The clinical band
cut points (54 / 70 / 140 / 180 / 250) are **fixed constants, independent of
``bg_targets``** — the configurable-band TIR lives in :func:`time_in_range`.

The six-bin partition is **half-open** so it sums to exactly 100% of valid
readings:

    TBR2 = g < 54
    TBR1 = 54 <= g < 70
    TIR  = 70 <= g <= 180   (consensus range, inclusive both ends)
    TAR1 = 180 < g <= 250
    TAR2 = g > 250

TITR (70 <= g <= 140) overlaps TIR and is reported separately — it is NOT part
of the partition.

``None`` means *undefined* (e.g. SD with N<2); ``0.0`` means *legitimately
zero*. Core import rules apply: stdlib / numpy / pandas only.
"""

from __future__ import annotations

import numpy as np

# Fixed clinical cut points (mg/dL). Independent of bg_targets.
_VLOW = 54.0
_LOW = 70.0
_TITR_HIGH = 140.0
_HIGH = 180.0
_VHIGH = 250.0

# Glycemic variability stability threshold (ADA consensus): CV <= 36%.
_CV_STABLE_THRESHOLD = 36.0


def _clean(bg) -> np.ndarray:
    """Coerce to a 1-D float array with NaNs dropped."""
    arr = np.asarray(bg, dtype=float).ravel()
    return arr[~np.isnan(arr)]


def time_in_bands(bg) -> dict[str, float]:
    """Return the half-open six-bin partition plus TITR/aggregates as percents.

    Keys: ``tbr2``, ``tbr1``, ``tir``, ``tar1``, ``tar2``, ``tar_total``,
    ``tbr_total``, ``titr``. Each value is a percentage in ``[0, 100]``. With no
    valid readings, every value is ``0.0``.
    """
    arr = _clean(bg)
    n = arr.size
    if n == 0:
        return {
            k: 0.0
            for k in (
                "tbr2",
                "tbr1",
                "tir",
                "tar1",
                "tar2",
                "tar_total",
                "tbr_total",
                "titr",
            )
        }

    scale = 100.0 / n
    tbr2 = float(np.count_nonzero(arr < _VLOW)) * scale
    tbr1 = float(np.count_nonzero((arr >= _VLOW) & (arr < _LOW))) * scale
    tir = float(np.count_nonzero((arr >= _LOW) & (arr <= _HIGH))) * scale
    tar1 = float(np.count_nonzero((arr > _HIGH) & (arr <= _VHIGH))) * scale
    tar2 = float(np.count_nonzero(arr > _VHIGH)) * scale
    titr = float(np.count_nonzero((arr >= _LOW) & (arr <= _TITR_HIGH))) * scale

    return {
        "tbr2": tbr2,
        "tbr1": tbr1,
        "tir": tir,
        "tar1": tar1,
        "tar2": tar2,
        "tar_total": tar1 + tar2,
        "tbr_total": tbr2 + tbr1,
        "titr": titr,
    }


def time_in_range(bg, low: float, high: float) -> float:
    """Configurable-band TIR: percent of readings with ``low <= g <= high``.

    This is the single shared TIR that local metrics, detection features, the
    Telegram digest, and the web shell converge on. Empty input → ``0.0``.
    """
    arr = _clean(bg)
    if arr.size == 0:
        return 0.0
    in_band = np.count_nonzero((arr >= low) & (arr <= high))
    return 100.0 * in_band / arr.size


def mean_bg(bg) -> float | None:
    """Mean BG (mg/dL); ``None`` when there are no valid readings."""
    arr = _clean(bg)
    if arr.size == 0:
        return None
    return float(np.mean(arr))


def median_bg(bg) -> float | None:
    """Median BG (mg/dL); ``None`` when there are no valid readings."""
    arr = _clean(bg)
    if arr.size == 0:
        return None
    return float(np.median(arr))


def sd_bg(bg) -> float | None:
    """Sample SD (ddof=1) of BG; ``None`` when fewer than 2 valid readings."""
    arr = _clean(bg)
    if arr.size < 2:
        return None
    return float(np.std(arr, ddof=1))


def cv_pct(bg) -> float | None:
    """Coefficient of variation (%) = 100 * sd(ddof=1) / mean.

    ``None`` when fewer than 2 valid readings or the mean is 0.
    """
    arr = _clean(bg)
    if arr.size < 2:
        return None
    mean = float(np.mean(arr))
    if mean == 0:
        return None
    return 100.0 * float(np.std(arr, ddof=1)) / mean


def cv_stable(cv: float | None) -> bool | None:
    """True when CV <= 36% (ADA stability threshold). ``None`` passes through."""
    if cv is None:
        return None
    return cv <= _CV_STABLE_THRESHOLD


def gmi(mean_mgdl: float | None) -> float | None:
    """Glucose Management Indicator: ``3.31 + 0.02392 * mean``. ``None`` passes."""
    if mean_mgdl is None:
        return None
    return 3.31 + 0.02392 * mean_mgdl


def ea1c(mean_mgdl: float | None) -> float | None:
    """Estimated A1c (%): ``(mean + 46.7) / 28.7``. ``None`` passes through."""
    if mean_mgdl is None:
        return None
    return (mean_mgdl + 46.7) / 28.7
