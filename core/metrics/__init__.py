"""Clinical CGM analytics — the single source of truth for both shells.

Pure ``stdlib + numpy + pandas`` (core import boundary; no scipy/sklearn).
Every metric is an independently golden-tested pure function; the
``compute_cgm_report`` orchestrator assembles them into a frozen ``CgmReport``.
"""

from __future__ import annotations

from core.metrics.cgm_metrics import (
    cv_pct,
    cv_stable,
    ea1c,
    gmi,
    mean_bg,
    median_bg,
    sd_bg,
    time_in_bands,
    time_in_range,
)
from core.metrics.report import CgmReport, ReportWindow, compute_cgm_report
from core.metrics.risk_indices import gri, hbgi, lbgi
from core.metrics.variability import conga, j_index, mage, modd
from core.metrics.windows import (
    active_time,
    local_day_bounds,
    meets_sufficiency,
    window_bounds,
)

__all__ = [
    # windows
    "local_day_bounds",
    "window_bounds",
    "active_time",
    "meets_sufficiency",
    # cgm_metrics
    "time_in_bands",
    "time_in_range",
    "mean_bg",
    "median_bg",
    "sd_bg",
    "cv_pct",
    "cv_stable",
    "gmi",
    "ea1c",
    # risk_indices
    "lbgi",
    "hbgi",
    "gri",
    # variability
    "j_index",
    "modd",
    "conga",
    "mage",
    # report
    "CgmReport",
    "ReportWindow",
    "compute_cgm_report",
]
