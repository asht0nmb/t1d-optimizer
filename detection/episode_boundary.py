"""Deviation-trajectory tracking: a candidate episode-boundary primitive.

Workstream B of the 2026-08-16 algorithm-research phase
(``notes/algorithm-research.md``): "Read the UAM path in
determine-basal.js before touching the M1 detector again... The useful
extraction is the deviation trajectory logic: what oref assumes when
deviations are rising, peaking, or decaying too slowly. That decay model
is a candidate for episode boundary detection, not just triggering."

**This module is a standalone, tested prototype. It is NOT wired into
M1** (``core/detection/meal_rise.py``) or anything else that runs live or
in the nightly batch. M1 stays on the raw Theil-Sen slope per the notes
("read... before touching the M1 detector again" — reading and
prototyping, not replacing). Whether/how to actually use this for episode
boundaries (where a detected meal-rise "ends") is a design decision the
owner should make deliberately, informed by this prototype, not something
this research phase decides unilaterally.

PROVENANCE
----------
``track_deviation_trajectory`` is a **port**, with attribution, of oref's
maxDeviation/minDeviation slope-tracking loop:

    Source repo:   nightscout/trio-oref (MIT License)
    Source file:   lib/determine-basal/cob.js, function
                   ``detectCarbAbsorption`` (the deviation-tracking loop,
                   not the carb-absorption/COB accounting that surrounds
                   it — see "What was NOT ported" below)
    Pinned commit: 8282ce71a57d09a160e92ecd2baf28a70c89694d (dev, fetched
                   2026-08-16)

trio-oref's file-level license header applies (MIT — see
``detection/iob.py``'s module docstring for the full text, not repeated
here).

THE CONCEPT (read this before the code)
----------------------------------------
Walking backward in time from "now" over a window of already-computed
5-minute ``deviation`` values, oref tracks two running extremes:

* ``maxDeviation`` — the largest deviation seen so far walking backward.
  Every time a new max is set, oref records the slope from that peak back
  to "now" (``slopeFromMaxDeviation``, clamped to be non-positive — i.e.
  a decay rate). This is "how fast is the deviation coming down from its
  peak" — the core signal for "is this meal/episode winding down."
* ``minDeviation`` — the smallest (most negative) deviation seen so far.
  Symmetrically, ``slopeFromMinDeviation`` (clamped non-negative) tracks
  "how fast is the deviation climbing up from its trough."

oref combines both into a conservative single decay estimate
(``slopeFromDeviations = min(slopeFromMaxDeviation, -slopeFromMinDeviation
/ 3)`` in ``determine-basal.js``) used to predict how much longer an
unannounced-meal deviation should be trusted before assuming it has
decayed to nothing. We do not port that combination step (see below) —
only the two slope-tracking values themselves, since those are the
literal "episode boundary" signal the notes point at: a rising max with a
flat/positive slope reads as "still building"; a max with a strongly
negative slope reads as "past peak, winding down"; deviations pinned near
their min reads as "flat/baseline, no episode."

WHAT WAS NOT PORTED
--------------------
* The COB/carb-absorption accounting that surrounds the loop in
  ``cob.js`` (``carbsAbsorbed``, ``mealCOB`` decrement) — that is a
  dosing computation (how many carbs remain to be covered), out of scope
  per non-goal #1.
* ``determine-basal.js``'s ``slopeFromDeviations`` combination and its
  downstream use in ``predUCIslope``/``UAMduration`` — those feed a
  *prediction* (how long to keep dosing SMBs), which we do not do.
* The BG<39 skip-row / bucketing mechanics from ``cob.js`` are not
  re-ported here because this module consumes an already-bucketed
  ``deviation_5m`` series from ``detection.deviation`` — bucketing
  happens once, upstream, not per-consumer.

This module exposes the two raw slopes plus the running extremes as a
DataFrame so a future M1/M2 design can inspect them directly, rather than
hiding them behind oref's single blended heuristic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = ["track_deviation_trajectory"]


def track_deviation_trajectory(deviation_df: pd.DataFrame) -> pd.DataFrame:
    """Track running max/min deviation and their decay/rise slopes.

    ``deviation_df`` must have ``timestamp`` (tz-aware, ascending) and
    ``deviation_5m`` (see ``detection.deviation.compute_deviation_frame``).
    Rows with ``NaN`` deviation (unwarmed / no delta) are carried through
    unchanged (all outputs NaN for that row) — they cannot participate in
    or reset the running trajectory, matching oref's own skip-on-missing-
    data behavior in ``cob.js``.

    Returns a copy of ``deviation_df`` with four added columns:

    * ``running_max_deviation`` / ``running_min_deviation`` — the
      trajectory's extremes seen so far in this call's window (reset only
      at the start of the frame passed in — callers control the episode
      window by how much history they slice before calling this).
    * ``slope_from_max_mgdl_per_5m`` — non-positive; the rate the
      deviation has come down from its running max, in mg/dL per 5
      minutes elapsed (oref's units are mg/dL per 5-minute tick; see the
      scaling note below). A value near 0 means "still at/near peak, not
      decaying." A large negative value means "decaying fast, episode is
      winding down."
    * ``slope_from_min_mgdl_per_5m`` — non-negative; symmetric, the rate
      the deviation has climbed from its running min.

    Note on units: oref's ``deviationSlope`` is computed as
    ``(avgDeviation - currentDeviation) / (bgTime - ciTime) * 1000 * 60 *
    5`` — a millisecond-timestamp difference scaled to "per 5-minute
    tick." We use actual elapsed minutes directly (``/ elapsed_minutes *
    5``), which is equivalent but avoids oref's JS-millisecond-arithmetic
    idiom; the two are algebraically the same computation.
    """
    cols = [
        "timestamp",
        "deviation_5m",
        "running_max_deviation",
        "running_min_deviation",
        "slope_from_max_mgdl_per_5m",
        "slope_from_min_mgdl_per_5m",
    ]
    if deviation_df is None or deviation_df.empty:
        return pd.DataFrame(columns=cols)

    df = deviation_df.sort_values("timestamp").reset_index(drop=True)
    ts = df["timestamp"].to_numpy()
    dev = df["deviation_5m"].to_numpy(dtype=float)
    n = len(df)

    running_max = np.full(n, np.nan)
    running_min = np.full(n, np.nan)
    slope_from_max = np.full(n, np.nan)
    slope_from_min = np.full(n, np.nan)

    max_dev = -np.inf
    max_dev_ts = None
    min_dev = np.inf
    min_dev_ts = None

    for i in range(n):
        if np.isnan(dev[i]):
            continue

        if dev[i] > max_dev:
            max_dev = dev[i]
            max_dev_ts = ts[i]
        if dev[i] < min_dev:
            min_dev = dev[i]
            min_dev_ts = ts[i]

        running_max[i] = max_dev
        running_min[i] = min_dev

        if max_dev_ts is not None and ts[i] > max_dev_ts:
            elapsed_min = (ts[i] - max_dev_ts) / np.timedelta64(1, "m")
            raw_slope = (dev[i] - max_dev) / elapsed_min * 5.0
            slope_from_max[i] = min(0.0, raw_slope)
        elif max_dev_ts is not None:
            slope_from_max[i] = 0.0  # this row IS the new max: no decay yet

        if min_dev_ts is not None and ts[i] > min_dev_ts:
            elapsed_min = (ts[i] - min_dev_ts) / np.timedelta64(1, "m")
            raw_slope = (dev[i] - min_dev) / elapsed_min * 5.0
            slope_from_min[i] = max(0.0, raw_slope)
        elif min_dev_ts is not None:
            slope_from_min[i] = 0.0

    out = df.copy()
    out["running_max_deviation"] = running_max
    out["running_min_deviation"] = running_min
    out["slope_from_max_mgdl_per_5m"] = slope_from_max
    out["slope_from_min_mgdl_per_5m"] = slope_from_min
    return out
