"""Glycemic risk indices — LBGI / HBGI (Kovatchev) and GRI (Klonoff).

LBGI/HBGI use the Kovatchev symmetrizing transform; BG is clamped to
``[20, 600]`` mg/dL before the logarithm to keep the transform numerically
stable at extreme values:

    f(g)  = 1.509 * ((ln g)**1.084 - 5.381)
    rl    = 10 * f**2 if f < 0 else 0
    rh    = 10 * f**2 if f > 0 else 0
    LBGI  = mean(rl) ;  HBGI = mean(rh)

GRI (Glycemia Risk Index, Klonoff 2022) is derived from the band percentages:

    hypo  = tbr2 + 0.8 * tbr1
    hyper = tar2 + 0.5 * tar1
    GRI   = clamp(3 * hypo + 1.6 * hyper, 0, 100)

Core import rules apply: stdlib / numpy / pandas only.
"""

from __future__ import annotations

import numpy as np

_CLAMP_LOW = 20.0
_CLAMP_HIGH = 600.0


def _clean(bg) -> np.ndarray:
    arr = np.asarray(bg, dtype=float).ravel()
    return arr[~np.isnan(arr)]


def _f_transform(arr: np.ndarray) -> np.ndarray:
    clamped = np.clip(arr, _CLAMP_LOW, _CLAMP_HIGH)
    return 1.509 * (np.power(np.log(clamped), 1.084) - 5.381)


def lbgi(bg) -> float:
    """Low Blood Glucose Index. Empty input → ``0.0``."""
    arr = _clean(bg)
    if arr.size == 0:
        return 0.0
    f = _f_transform(arr)
    rl = np.where(f < 0, 10.0 * f * f, 0.0)
    return float(np.mean(rl))


def hbgi(bg) -> float:
    """High Blood Glucose Index. Empty input → ``0.0``."""
    arr = _clean(bg)
    if arr.size == 0:
        return 0.0
    f = _f_transform(arr)
    rh = np.where(f > 0, 10.0 * f * f, 0.0)
    return float(np.mean(rh))


def gri(*, tbr2: float, tbr1: float, tar1: float, tar2: float) -> dict[str, float]:
    """Glycemia Risk Index from band percentages.

    Returns ``{"gri", "gri_hypo", "gri_hyper"}``. ``gri`` is clamped to
    ``[0, 100]``; the hypo/hyper components are reported pre-clamp.
    """
    hypo = tbr2 + 0.8 * tbr1
    hyper = tar2 + 0.5 * tar1
    raw = 3.0 * hypo + 1.6 * hyper
    return {
        "gri": float(min(max(raw, 0.0), 100.0)),
        "gri_hypo": float(hypo),
        "gri_hyper": float(hyper),
    }
