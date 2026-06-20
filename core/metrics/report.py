"""``CgmReport`` — the single source of truth for a window's CGM analytics.

``compute_cgm_report`` windows the input via :mod:`core.metrics.windows`, drops
NaNs, computes the full panel (bands, central tendency, GMI/eA1c, LBGI/HBGI,
GRI), and applies the consensus data-sufficiency gate: ``gmi`` and ``gri`` are
``None`` unless the window meets ``>=14`` covered days and ``>=70%`` active CGM
time (and there are at least 2 readings). ``None`` means *undefined*; ``0.0``
means *legitimately zero*.

Config is consumed by duck typing — any object exposing ``bg_targets.low``,
``bg_targets.high`` and (optionally) ``timezone`` works, so this module never
imports the detection config and stays source-agnostic. Core import rules
apply: stdlib / numpy / pandas only.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

import numpy as np
import pandas as pd

from core.metrics import cgm_metrics, risk_indices, variability, windows

_MIN_DAYS = 14
_MIN_ACTIVE = 70.0


@dataclasses.dataclass(frozen=True)
class ReportWindow:
    """A trailing window of ``days`` local calendar dates ending on ``end_date``."""

    end_date: dt.date
    days: int
    tz: str


@dataclasses.dataclass(frozen=True)
class CgmReport:
    """Immutable result of :func:`compute_cgm_report`."""

    # Provenance
    end_date: dt.date
    days: int
    tz: str
    n_readings: int
    expected_readings: int
    active_pct: float
    days_covered: int
    meets_sufficiency: bool

    # Band panel (percentages; partition bins always defined, 0.0 when empty)
    tbr2: float
    tbr1: float
    tir: float
    tar1: float
    tar2: float
    tbr_total: float
    tar_total: float
    titr: float
    tir_config: float  # configurable-band TIR using bg_targets.low/high

    # Central tendency
    mean_bg: float | None
    median_bg: float | None
    sd_bg: float | None
    cv_pct: float | None
    cv_stable: bool | None

    # Estimated glycation
    gmi: float | None
    ea1c: float | None

    # Risk indices
    lbgi: float | None
    hbgi: float | None
    gri: float | None
    gri_hypo: float | None
    gri_hyper: float | None

    # Advanced variability (Task 7) — defaulted None here.
    j_index: float | None = None
    modd: float | None = None
    conga: float | None = None
    mage: float | None = None


def _bg_targets(config: Any) -> tuple[float, float]:
    targets = getattr(config, "bg_targets")
    return float(targets.low), float(targets.high)


def compute_cgm_report(
    cgm: pd.DataFrame, *, config: Any, window: ReportWindow
) -> CgmReport:
    """Compute the full CGM analytics panel for ``window`` from ``cgm``.

    ``cgm`` must expose ``timestamp`` (tz-aware; naive treated as UTC) and
    ``bg_mgdl`` columns. Returns a frozen :class:`CgmReport`. GMI/GRI are gated
    to ``None`` when the window is insufficient or has fewer than 2 readings.
    """
    since, until = windows.window_bounds(window.end_date, window.days, tz=window.tz)
    n_readings, expected, active_pct = windows.active_time(
        cgm, since, until, expected_interval_min=5
    )

    # Slice to window and pull valid BG values.
    low, high = _bg_targets(config)
    in_window = _slice_window(cgm, since, until)
    bg = _bg_values(in_window)
    days_covered = _distinct_local_days(in_window, window.tz)

    sufficient = windows.meets_sufficiency(
        days_covered, active_pct, min_days=_MIN_DAYS, min_active=_MIN_ACTIVE
    )
    n_valid = bg.size

    bands = cgm_metrics.time_in_bands(bg)
    mean = cgm_metrics.mean_bg(bg)
    sd = cgm_metrics.sd_bg(bg)
    cv = cgm_metrics.cv_pct(bg)

    # LBGI/HBGI defined for >=1 reading; None only when no valid readings.
    if n_valid == 0:
        lbgi_v: float | None = None
        hbgi_v: float | None = None
    else:
        lbgi_v = risk_indices.lbgi(bg)
        hbgi_v = risk_indices.hbgi(bg)

    # GMI/GRI gated on sufficiency AND N>=2.
    gate_ok = sufficient and n_valid >= 2
    gmi_v = cgm_metrics.gmi(mean) if gate_ok else None
    ea1c_v = cgm_metrics.ea1c(mean) if n_valid > 0 else None
    if gate_ok:
        gri_d = risk_indices.gri(
            tbr2=bands["tbr2"],
            tbr1=bands["tbr1"],
            tar1=bands["tar1"],
            tar2=bands["tar2"],
        )
        gri_v: float | None = gri_d["gri"]
        gri_hypo: float | None = gri_d["gri_hypo"]
        gri_hyper: float | None = gri_d["gri_hyper"]
    else:
        gri_v = gri_hypo = gri_hyper = None

    # Variability — each defined independently of the sufficiency gate; None
    # when its own preconditions aren't met (e.g. MODD needs >=2 days).
    j_v = variability.j_index(mean, sd)
    modd_v = variability.modd(in_window, tz=window.tz)
    conga_v = variability.conga(in_window, n_hours=1.0, tz=window.tz)
    mage_v = variability.mage(bg, sd=sd) if n_valid >= 3 else None

    return CgmReport(
        end_date=window.end_date,
        days=window.days,
        tz=window.tz,
        n_readings=n_valid,
        expected_readings=expected,
        active_pct=active_pct,
        days_covered=days_covered,
        meets_sufficiency=sufficient,
        tbr2=bands["tbr2"],
        tbr1=bands["tbr1"],
        tir=bands["tir"],
        tar1=bands["tar1"],
        tar2=bands["tar2"],
        tbr_total=bands["tbr_total"],
        tar_total=bands["tar_total"],
        titr=bands["titr"],
        tir_config=cgm_metrics.time_in_range(bg, low, high),
        mean_bg=mean,
        median_bg=cgm_metrics.median_bg(bg),
        sd_bg=sd,
        cv_pct=cv,
        cv_stable=cgm_metrics.cv_stable(cv),
        gmi=gmi_v,
        ea1c=ea1c_v,
        lbgi=lbgi_v,
        hbgi=hbgi_v,
        gri=gri_v,
        gri_hypo=gri_hypo,
        gri_hyper=gri_hyper,
        j_index=j_v,
        modd=modd_v,
        conga=conga_v,
        mage=mage_v,
    )


def _slice_window(
    cgm: pd.DataFrame, since: dt.datetime, until: dt.datetime
) -> pd.DataFrame:
    if cgm is None or cgm.empty or "timestamp" not in cgm.columns:
        return cgm if cgm is not None else pd.DataFrame(columns=["timestamp", "bg_mgdl"])
    ts = pd.to_datetime(cgm["timestamp"], utc=True)
    since_utc = pd.Timestamp(since).tz_convert("UTC")
    until_utc = pd.Timestamp(until).tz_convert("UTC")
    mask = (ts >= since_utc) & (ts < until_utc)
    return cgm.loc[mask]


def _bg_values(cgm: pd.DataFrame) -> np.ndarray:
    if cgm is None or cgm.empty or "bg_mgdl" not in cgm.columns:
        return np.array([], dtype=float)
    arr = cgm["bg_mgdl"].to_numpy(dtype=float)
    return arr[~np.isnan(arr)]


def _distinct_local_days(cgm: pd.DataFrame, tz: str) -> int:
    """Count distinct local calendar dates with at least one valid reading."""
    if cgm is None or cgm.empty or "timestamp" not in cgm.columns:
        return 0
    valid = cgm
    if "bg_mgdl" in cgm.columns:
        valid = cgm.loc[~pd.to_numeric(cgm["bg_mgdl"], errors="coerce").isna()]
    if valid.empty:
        return 0
    local = pd.to_datetime(valid["timestamp"], utc=True).dt.tz_convert(tz)
    return int(local.dt.date.nunique())
