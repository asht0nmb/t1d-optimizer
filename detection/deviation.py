"""BGI (blood-glucose impact) and deviation: the Workstream A derived column.

This is what ``notes/algorithm-research.md`` calls for under "Workstream A:
BGI and deviation primitive" — "Port BGI and deviation into the nightly
batch as a per-reading derived column on the CGM frame." BGI is the BG
change expected from insulin activity alone; deviation is the observed
change minus BGI, i.e. the part of a glucose move that insulin does not
explain (carbs, exercise, stress, a bad site, Control-IQ's own
algorithmic modulation, or noise).

This module is a **nightly-batch-only** primitive (see
``compute_deviation_frame``'s docstring): it requires ``basal_df`` /
``bolus_df`` insulin history via ``detection.iob``, which the live
5-minute path (``core/detection/meal_rise.py``, M1) does not have and is
not being changed to require. M1 stays on the raw Theil-Sen slope. M2's
scoring stays as-is (bolus-proximity labels). This module exists so a
*future* M2/M4 pass can use ``deviation`` as a better label than bolus
proximity — it is not wired into either yet.

PROVENANCE
----------
``compute_glucose_deltas`` is a **port**, with attribution, of oref's
delta-normalization scheme:

    Source repo:   nightscout/trio-oref (MIT License)
    Source file:   lib/glucose-get-last.js
    Pinned commit: 8282ce71a57d09a160e92ecd2baf28a70c89694d (dev, fetched
                   2026-08-16)

Upstream computes this only for the single latest reading (it is meant to
run once per live loop tick); we generalized the same per-reading formula
(``change / minutes_ago * 5``, i.e. normalize every gap to a "per 5
minutes" rate regardless of actual spacing) to run over an entire
historical CGM frame, which is what a nightly batch column needs. The
generalization (looping over every row instead of just the newest one)
and the elapsed-minutes bucket boundaries (2.5/17.5/42.5 min) are taken
directly from the upstream file; the vectorized/backward-looking search
implementation is original.

One piece of upstream behavior is deliberately NOT ported: oref merges
any neighbor within 2.5 minutes into "now" (``now.glucose = (now.glucose
+ then.glucose) / 2``) rather than treating it as a delta sample, which
matters for its live single-tick use (dedup near-duplicate polls). For a
historical batch column, a sub-2.5-minute neighbor simply falls into no
bucket here and the row's delta is left ``NaN`` rather than merged —
simpler, and never produces a spuriously huge normalized rate from a
near-zero elapsed time in the denominator.

``compute_bgi`` reproduces oref's ``determine-basal.js`` formula
(``bgi = -activity * sens * 5``, see that file's provenance note in
``detection/iob.py``).

DELIBERATE OMISSIONS (documented, not ported)
----------------------------------------------
oref's ``deviation`` (in ``determine-basal.js``) is not just
``delta - bgi``; it also:
  1. Scales by ``30/5`` to project a 30-minute-ahead deviation (used to
     predict ``eventualBG`` for dosing).
  2. Falls back from ``minDelta`` to ``minAvgDelta`` to
     ``long_avgdelta`` whenever the result is negative, to avoid
     *undertreating* a real negative deviation with noisy data.
  3. Clamps positive deviations to 0 when BG < 80 (a dosing safety rule
     from ``lib/autotune-prep/categorize.js``).
  4. Floors carb impact at ``profile.min_5m_carbimpact``.

All four exist to make *dosing decisions* safer, and all four bake a
directional (asymmetric) bias into the number — exactly what a
*descriptive* per-reading column must not do, since this system never
doses and non-goal #1 is "no dosing logic, no prediction curves for
therapy, no safety caps." ``deviation_5m`` here is therefore the plain,
un-adjusted ``delta - bgi``: it can be negative, it is not projected
forward, and it is not clamped. Anything that wants oref's dosing-safe
composite should build it from these primitives, not extend this module.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from detection.iob import IobCurveConfig, build_dose_events, compute_iob_activity

__all__ = [
    "DeviationConfig",
    "compute_glucose_deltas",
    "compute_bgi",
    "compute_deviation_frame",
]

MIN_VALID_BG = 39


@dataclass(frozen=True)
class DeviationConfig:
    """Bundles the IOB curve config with the ISF this run should use.

    ``isf_mgdl_per_unit`` has **no default** on purpose: BGI is linearly
    scaled by ISF, so silently guessing one would make every downstream
    deviation value wrong in a way that is not visible without re-deriving
    it. See the findings doc — ``LidBolusRequestedMsg2.ISF`` (mg/dL per
    unit) is observable per real bolus event today but is not currently
    extracted by ``ingestion.builders.build_request_df``; until that lands,
    callers must supply a value explicitly (e.g. from
    ``config/user_config.yaml``'s bolus-calculator ISF, read by the caller
    — this module does not read config itself, per the source-agnostic
    convention).
    """

    isf_mgdl_per_unit: float
    iob: IobCurveConfig = IobCurveConfig()


def _elapsed_minute_delta(
    ts: np.ndarray, bg: np.ndarray, i: int, lo_min: float, hi_min: float
) -> float | None:
    """Average, 5-minute-normalized delta from readings ``lo_min``-``hi_min``
    minutes before row ``i``. Mirrors oref's bucket boundaries exactly
    (``glucose-get-last.js``): short_avgdelta uses (2.5, 17.5], long_avgdelta
    uses (17.5, 42.5). Returns ``None`` if no reading falls in the window.
    """
    now_t = ts[i]
    now_bg = bg[i]
    contributions = []
    # Walk backward; readings are sorted ascending so we scan down from i-1.
    for j in range(i - 1, -1, -1):
        minutes_ago = (now_t - ts[j]) / np.timedelta64(1, "m")
        if minutes_ago > hi_min:
            break  # sorted ascending in time -> only gets older from here
        if lo_min < minutes_ago <= hi_min and bg[j] > MIN_VALID_BG:
            contributions.append((now_bg - bg[j]) / minutes_ago * 5.0)
    if not contributions:
        return None
    return float(np.mean(contributions))


def compute_glucose_deltas(cgm_df: pd.DataFrame) -> pd.DataFrame:
    """Add ``delta``, ``short_avgdelta``, ``long_avgdelta`` (mg/dL per 5 min).

    Requires a tz-aware, ascending-sorted ``timestamp`` column and
    ``bg_mgdl``. Does **not** assume fixed 5-minute spacing — every delta is
    computed as ``change_in_bg / actual_minutes_elapsed * 5``, exactly as
    oref does, which is why this survives contact with backfilled Dexcom
    data (see the findings doc's cadence open question: backfilled rows
    keep their real sensor-read spacing, which is frequently *tighter*
    than 5 minutes and never grid-aligned — a naive ``.diff()`` on row
    order would silently compute the wrong per-5-min rate for every
    backfilled stretch).

    Rows with fewer than ``MIN_VALID_BG`` mg/dL neighbors in a given
    window, or no neighbor in range at all, get ``NaN`` for that column —
    never 0, since 0 is a real (flat) delta value.
    """
    if cgm_df is None or cgm_df.empty:
        return cgm_df.assign(delta=pd.Series(dtype=float), short_avgdelta=pd.Series(dtype=float), long_avgdelta=pd.Series(dtype=float)) if cgm_df is not None else cgm_df

    df = cgm_df.sort_values("timestamp").reset_index(drop=True)
    ts = df["timestamp"].to_numpy()
    bg = df["bg_mgdl"].to_numpy(dtype=float)

    n = len(df)
    delta = np.full(n, np.nan)
    short_avgdelta = np.full(n, np.nan)
    long_avgdelta = np.full(n, np.nan)

    for i in range(n):
        if bg[i] <= MIN_VALID_BG:
            continue
        d = _elapsed_minute_delta(ts, bg, i, 2.5, 7.5)
        if d is not None:
            delta[i] = d
        s = _elapsed_minute_delta(ts, bg, i, 2.5, 17.5)
        if s is not None:
            short_avgdelta[i] = s
        long_ = _elapsed_minute_delta(ts, bg, i, 17.5, 42.5)
        if long_ is not None:
            long_avgdelta[i] = long_

    out = df.copy()
    out["delta"] = delta
    out["short_avgdelta"] = short_avgdelta
    out["long_avgdelta"] = long_avgdelta
    return out


def compute_bgi(activity_per_min: pd.Series, isf_mgdl_per_unit: float) -> pd.Series:
    """``bgi = -activity * isf * 5`` (mg/dL over 5 minutes).

    Ported formula from ``determine-basal.js`` (see module + ``iob.py``
    docstrings for full provenance). Negative sign: rising insulin
    activity should *lower* BG, so BGI is negative when insulin is
    actively working and near zero when it is not.
    """
    return -activity_per_min * isf_mgdl_per_unit * 5.0


def compute_deviation_frame(
    cgm_df: pd.DataFrame,
    bolus_df: pd.DataFrame,
    basal_df: pd.DataFrame,
    config: DeviationConfig,
    suspension_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """The Workstream A deliverable: one row per CGM reading with
    ``delta``, ``iob``, ``activity``, ``bgi``, ``deviation_5m``, and
    ``warmed_up``.

    ``deviation_5m = delta - bgi``. Positive means BG rose faster (or fell
    slower) than insulin activity alone predicts — carbs, algorithmic
    basal modulation, stress, or noise. Negative means BG fell faster (or
    rose slower) than insulin activity predicts — exercise, a stacked
    dose, or noise. See the module docstring for what this column
    deliberately does NOT do (no 30-min projection, no negative-value
    fallback, no BG<80 clamp, no carb-impact floor) — those are dosing
    adjustments, and this is a descriptive analytics column.

    **Requires warm-up history**: pass ``bolus_df``/``basal_df`` starting
    at least ``config.iob.dia_hours`` before the first CGM reading you
    actually care about, or the leading ``dia_hours`` of rows will
    correctly come back with ``warmed_up=False`` / ``NaN`` (see
    ``detection.iob.compute_iob_activity``'s docstring — this is the
    single most important correctness property of this module).

    **Degradation with gaps** (per the notes' explicit ask — "how it
    degrades when insulin history has gaps"):
      - A CGM gap: rows simply do not exist for that stretch; the delta
        computation naturally produces ``NaN`` for the reading immediately
        after a gap wider than 42.5 minutes (no neighbor in any window).
      - A bolus-history gap (a sync outage, a missed pump-history page):
        undercounts IOB/activity for any dose that occurred during the gap
        (silently — this module cannot detect "missing" events, only
        "zero" events). BGI comes out closer to 0 than reality, which
        pushes ``deviation_5m`` up (looks like an unexplained rise). There
        is no in-band signal for this failure mode today; cross-checking
        against ``ingestion.enrich``'s ``cgm_gaps``/sync-freshness metadata
        is a recommended follow-up, not implemented here.
      - A basal-history gap: the rolling-median baseline
        (``basal_baseline_rate``) still works as long as *some* basal
        history exists in the lookback window; if none does, that segment
        is dropped from ``dose_events`` entirely (treated as unknown, not
        zero) — see ``build_dose_events``.
    """
    deltas = compute_glucose_deltas(cgm_df)
    if deltas is None or deltas.empty:
        return deltas

    horizon_end = deltas["timestamp"].max()
    dose_events = build_dose_events(
        bolus_df,
        basal_df,
        horizon_end=horizon_end,
        suspension_df=suspension_df,
        baseline_lookback_days=config.iob.baseline_lookback_days,
    )

    candidate_starts = [deltas["timestamp"].min()]
    if bolus_df is not None and not bolus_df.empty:
        candidate_starts.append(bolus_df["timestamp"].min())
    if basal_df is not None and not basal_df.empty:
        candidate_starts.append(basal_df["timestamp"].min())
    data_start = min(candidate_starts)

    iob_activity = compute_iob_activity(
        deltas["timestamp"], dose_events, config.iob, data_start
    )

    out = deltas.merge(
        iob_activity[["timestamp", "iob", "activity", "warmed_up"]],
        on="timestamp",
        how="left",
    )
    out["bgi"] = compute_bgi(out["activity"], config.isf_mgdl_per_unit)
    out["deviation_5m"] = out["delta"] - out["bgi"]
    # Deviation is only meaningful where BGI is known; keep NaN propagation
    # explicit rather than letting arithmetic silently produce a number
    # from a NaN activity (pandas already NaNs this out, but we assert the
    # intent here for readers).
    out.loc[~out["warmed_up"].fillna(False), "deviation_5m"] = np.nan
    return out
