"""Insulin-on-board (IOB) and insulin-activity reconstruction.

This is the load-bearing primitive for Workstream A of the 2026-08-16
algorithm-research phase (see ``docs/algorithm-research-findings.md``):
the owner's research notes (``notes/algorithm-research.md``) asked for a
"BGI and deviation" derived column, computed nightly, that needs insulin
history (``basal_df`` / ``bolus_df``) the live 5-minute path does not have.
BGI (blood-glucose impact) requires an estimate of insulin *activity* —
how fast insulin is currently acting on blood glucose — which in turn
requires reconstructing net IOB from the pump's bolus and basal event
history. That reconstruction is what this module does.

PROVENANCE (binding — see notes/algorithm-research.md "Hard constraint:
license boundary")
--------------------------------------------------------------------------
``_activity_and_iob_fraction`` below is a direct, attributed **port** of
the exponential insulin-action-curve formula from the openaps/oref
algorithm family:

    Source repo:   nightscout/trio-oref (MIT License)
    Source file:   lib/iob/calculate.js, function ``iobCalcExponential``
    Pinned commit: 8282ce71a57d09a160e92ecd2baf28a70c89694d (dev branch,
                   fetched 2026-08-16 via the GitHub API)
    Upstream note: the formula itself originates from LoopKit/Loop issue
                   #388 (comment by @ps2 / Pete Schwamb), cited in the
                   oref source as:
                   https://github.com/LoopKit/Loop/issues/388#issuecomment-317938473

trio-oref's file-level license header (reproduced per the notes'
attribution requirement):

    Determine Basal
    Released under MIT license. See the accompanying LICENSE.txt file for
    full terms and conditions
    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
    OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
    MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
    NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
    LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
    OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
    WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

The curve math (``tau``, ``a``, ``S``, ``activityContrib``, ``iobContrib``)
is reproduced with the same variable names as the upstream JS so it can be
diffed against the source directly. We only ported the *exponential*
curve (oref also offers a legacy "bilinear" curve); the exponential model
is the modern default across oref0/trio/Loop/AAPS for rapid-acting
analogs and is what Control-IQ's insulin (Humalog/Novolog-class) most
resembles.

Everything else in this module — basal→dose-event decomposition, the
rolling-median "baseline rate" used as a profile-rate surrogate, the
warm-up/NaN policy, and suspension zeroing — is **original code written
for this repo**, not ported from oref. oref's own basal→IOB decomposition
(``lib/iob/history.js``) assumes a distinct "profile basal rate" is always
available (it is, in a normal oref/AAPS/Loop rig with an ingested pump
profile); we do not have that column today (see the "profile vs.
delivered" open question in the findings doc), so the decomposition here
is a documented approximation, not a port.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

__all__ = [
    "IobCurveConfig",
    "build_dose_events",
    "compute_iob_activity",
]


@dataclass(frozen=True)
class IobCurveConfig:
    """Exponential insulin-action-curve parameters.

    ``dia_hours`` and ``peak_minutes`` are **placeholders**, not calibrated
    values. See the "What DIA and peak time to assume" open question in
    docs/algorithm-research-findings.md: ``tconnectsync``'s API map exposes
    ``PumpProfile.insulinDuration`` (minutes), but ingestion does not fetch
    the pump profile today, and Control-IQ's *internal* model may not equal
    what the profile reports even once it is fetched. 5.0h / 75min are the
    common oref/Loop "rapid-acting" defaults, chosen only so this module is
    runnable and testable — NOT asserted as this user's true DIA. Treat any
    output of this module as sensitive to this assumption; a sensitivity
    analysis (± the DIA/peak the owner actually observes) is recommended
    before trusting absolute deviation values for anything beyond relative
    comparison.

    ``baseline_lookback_days`` controls the rolling-median "profile basal
    rate" surrogate (see ``basal_baseline_rate``) used because
    ``profileBasalRate``/``algorithmRate`` are not currently captured by
    ``ingestion.builders.build_basal_df`` even though the underlying
    ``LidBasalDelivery`` event carries them (confirmed against the
    installed v3 tconnectsync source — see findings doc Workstream A).
    """

    dia_hours: float = 5.0
    peak_minutes: float = 75.0
    baseline_lookback_days: int = 7


# Dexcom's documented display floor; also oref's own low-glucose guard
# (`data[i].glucose > 38` in glucose-get-last.js). Below this, a reading is
# noise/error, not a physiologically meaningful low.
MIN_VALID_BG = 39


def _activity_and_iob_fraction(
    minutes_ago: np.ndarray, dia_hours: float, peak_minutes: float
) -> tuple[np.ndarray, np.ndarray]:
    """Per-unit-dose activity/IOB remaining, ``minutes_ago`` after a dose.

    Ported from trio-oref's ``iobCalcExponential`` (see module docstring
    for full provenance). The upstream function multiplies by
    ``treatment.insulin`` inline; we factor that out so callers can scale
    by an arbitrary dose (real bolus or synthetic net-basal dose) without
    re-deriving the curve. Both formulas are linear in dose size, so this
    factoring is exact, not an approximation.

    Returns ``(activity_frac, iob_frac)``, each shaped like
    ``minutes_ago``. ``activity_frac`` is insulin-fraction acted on in the
    previous minute (oref's ``activityContrib`` convention — this is why
    ``bgi = -activity * isf * 5`` in ``deviation.py`` scales by 5, not 1).
    ``iob_frac`` is the fraction of the dose still "on board".  Both are
    zero for ``minutes_ago`` outside ``[0, dia_hours * 60)``.
    """
    end = dia_hours * 60.0
    peak = float(peak_minutes)

    # tau: time constant of exponential decay; a: rise-time factor;
    # S: scale factor so the activity curve integrates to the full dose
    # over [0, end]. Variable names match the upstream JS 1:1.
    tau = peak * (1 - peak / end) / (1 - 2 * peak / end)
    a = 2 * tau / end
    S = 1 / (1 - a + (1 + a) * np.exp(-end / tau))

    t = np.asarray(minutes_ago, dtype=float)
    in_range = (t >= 0) & (t < end)

    # Avoid divide/exp warnings on out-of-range entries by clamping the
    # input to the formula (results at those indices are discarded by the
    # `in_range` mask below).
    t_safe = np.clip(t, 0.0, end - 1e-9)

    activity = (S / tau**2) * t_safe * (1 - t_safe / end) * np.exp(-t_safe / tau)
    iob = 1 - S * (1 - a) * (
        (t_safe**2 / (tau * end * (1 - a)) - t_safe / tau - 1) * np.exp(-t_safe / tau)
        + 1
    )

    activity = np.where(in_range, activity, 0.0)
    iob = np.where(in_range, iob, 0.0)
    return activity, iob


def basal_baseline_rate(
    basal_df: pd.DataFrame, at: pd.Timestamp, lookback_days: int
) -> float | None:
    """Robust "profile basal rate" surrogate: trailing-median ``commanded_rate``.

    We do not have ``profileBasalRate`` (see module docstring), so we
    cannot net Control-IQ's algorithmic modulation against the user's
    actual programmed profile. Instead we estimate a local baseline as the
    median delivered rate over the trailing ``lookback_days`` window ending
    at ``at``. A median (not mean) is used because it is robust to
    short excursions — meal-time algorithm boosts, exercise suspensions —
    that would otherwise pull a mean baseline away from the "typical"
    rate. This is a documented approximation, not a port of any oref
    logic: oref assumes the real profile rate is always available.

    Returns ``None`` if there is no basal history in the lookback window
    (caller should treat downstream basal contribution as unknown, not
    zero).
    """
    if basal_df is None or basal_df.empty or "commanded_rate" not in basal_df.columns:
        return None
    window_start = at - timedelta(days=lookback_days)
    mask = (basal_df["timestamp"] > window_start) & (basal_df["timestamp"] <= at)
    windowed = basal_df.loc[mask, "commanded_rate"]
    if windowed.empty:
        return None
    return float(windowed.median())


def _zero_during_suspensions(
    segments: pd.DataFrame, suspension_df: pd.DataFrame | None
) -> pd.DataFrame:
    """Force ``commanded_rate`` to 0 for the portion of each segment that
    overlaps a pump-suspension episode.

    Belt-and-braces: ``LidBasalDelivery`` should already emit a
    ``rate_source="suspended"`` row (commanded_rate 0) when the pump
    suspends, but we have not verified that invariant holds for every
    suspension reason (alarm/malfunction/PLGS) against real data in this
    exploratory pass (no production DB credentials in this worktree — see
    findings doc). Splitting segments against ``suspension_df`` directly
    is a second, independent source of truth for "no insulin was actually
    delivered here" that does not depend on that invariant.

    Segments are split (not just filtered) at suspend/resume boundaries so
    a segment that starts before a suspension and ends after it is
    correctly divided into a pre-suspension delivering part, a
    zero-during-suspension part, and a post-suspension delivering part.
    """
    if suspension_df is None or suspension_df.empty:
        return segments
    out_rows: list[dict] = []
    for row in segments.itertuples(index=False):
        seg_start, seg_end, rate = row.start, row.end, row.commanded_rate
        cuts = [seg_start, seg_end]
        zero_intervals: list[tuple[pd.Timestamp, pd.Timestamp]] = []
        for sus in suspension_df.itertuples(index=False):
            s_start = sus.suspend_timestamp
            s_end = sus.resume_timestamp if pd.notna(sus.resume_timestamp) else seg_end
            ov_start, ov_end = max(seg_start, s_start), min(seg_end, s_end)
            if ov_start < ov_end:
                zero_intervals.append((ov_start, ov_end))
                cuts.extend([ov_start, ov_end])
        if not zero_intervals:
            out_rows.append({"start": seg_start, "end": seg_end, "commanded_rate": rate})
            continue
        cuts = sorted(set(cuts))
        for c_start, c_end in zip(cuts[:-1], cuts[1:]):
            if c_start >= c_end:
                continue
            mid = c_start + (c_end - c_start) / 2
            zeroed = any(s <= mid < e for s, e in zero_intervals)
            out_rows.append(
                {"start": c_start, "end": c_end, "commanded_rate": 0.0 if zeroed else rate}
            )
    return pd.DataFrame(out_rows, columns=["start", "end", "commanded_rate"])


def build_dose_events(
    bolus_df: pd.DataFrame,
    basal_df: pd.DataFrame,
    horizon_end: pd.Timestamp,
    suspension_df: pd.DataFrame | None = None,
    baseline_lookback_days: int = 7,
) -> pd.DataFrame:
    """Build a unified ``[timestamp, units, kind]`` dose-event stream.

    Real boluses pass through unchanged (``kind="bolus"``). Basal history
    is decomposed into one synthetic "net basal" dose per basal segment
    (event-to-next-event, mirroring the ``next_ts - this_ts`` / "final row
    extends to the horizon" convention already used by
    ``detection/features.py::_integrate_basal`` so the two stay
    consistent): ``units = (commanded_rate - baseline_rate) * duration_hours``,
    dated at the segment's start.

    Only the *excursion above/below the rolling baseline* is treated as an
    IOB-contributing dose. This mirrors oref's own convention (temp-basal
    history is decomposed into pseudo-boluses of ``rate - profile_rate``,
    see ``lib/iob/history.js``) but substitutes our rolling-median baseline
    for oref's ingested profile rate — see ``basal_baseline_rate`` and the
    module docstring for why. A segment whose baseline cannot be
    determined (no basal history in the lookback window) is dropped
    rather than assumed net-zero, so gaps degrade to "unknown" rather than
    silently under-counting insulin — see the findings doc's "how this
    degrades with gaps" section.

    ``horizon_end`` closes the final open-ended basal segment (basal
    events are event-driven / change-triggered, not fixed-cadence — see
    the findings doc's cadence open question).
    """
    events: list[dict] = []

    if bolus_df is not None and not bolus_df.empty:
        for row in bolus_df.itertuples(index=False):
            events.append({"timestamp": row.timestamp, "units": float(row.insulin_units), "kind": "bolus"})

    if basal_df is not None and not basal_df.empty and "commanded_rate" in basal_df.columns:
        bdf = basal_df.sort_values("timestamp").reset_index(drop=True)
        ts = bdf["timestamp"]
        rates = bdf["commanded_rate"].astype(float)
        n = len(bdf)
        segments = []
        for i in range(n):
            seg_start = ts.iloc[i]
            seg_end = ts.iloc[i + 1] if i + 1 < n else horizon_end
            if seg_end <= seg_start:
                continue
            segments.append({"start": seg_start, "end": seg_end, "commanded_rate": rates.iloc[i]})
        segments_df = pd.DataFrame(segments, columns=["start", "end", "commanded_rate"])
        segments_df = _zero_during_suspensions(segments_df, suspension_df)

        for seg in segments_df.itertuples(index=False):
            baseline = basal_baseline_rate(bdf, seg.start, baseline_lookback_days)
            if baseline is None:
                continue
            duration_hours = (seg.end - seg.start).total_seconds() / 3600.0
            net_units = (seg.commanded_rate - baseline) * duration_hours
            events.append({"timestamp": seg.start, "units": net_units, "kind": "basal_net"})

    df = pd.DataFrame(events, columns=["timestamp", "units", "kind"])
    if not df.empty:
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def compute_iob_activity(
    eval_timestamps: pd.DatetimeIndex | pd.Series,
    dose_events: pd.DataFrame,
    config: IobCurveConfig,
    data_start: pd.Timestamp,
) -> pd.DataFrame:
    """Sum per-dose IOB/activity contributions at each evaluation timestamp.

    Returns a DataFrame indexed like ``eval_timestamps`` with columns
    ``iob`` (units), ``activity`` (units/min — oref's convention; see
    ``_activity_and_iob_fraction``), and ``warmed_up`` (bool).

    **Warm-up policy (correctness-critical — see review discussion in the
    findings doc).** IOB at the first available CGM timestamp is not 0; it
    is whatever the prior ``dia_hours`` of insulin history left on board.
    If ``dose_events`` does not reach back a full DIA before an evaluation
    timestamp, the computed activity is *silently incomplete* and will read
    as spurious "unexplained rise" (an undercounted IOB looks like too
    little insulin is acting, so any real BG rise gets misattributed to
    carbs/UAM rather than correctly-still-decaying insulin). We do not
    guess a warm-up IOB; instead, any evaluation timestamp whose
    ``t - dia_hours`` falls before ``data_start`` gets ``NaN`` for both
    columns rather than a plausible-looking wrong number. Callers building
    a multi-day backfill should always request ``dose_events``/
    ``data_start`` starting at least ``dia_hours`` before their first
    real evaluation timestamp.
    """
    ts = pd.DatetimeIndex(eval_timestamps)
    dia_minutes = config.dia_hours * 60.0
    warmup_cutoff = data_start + timedelta(hours=config.dia_hours)
    warmed_up_mask = ts >= warmup_cutoff

    if dose_events is None or dose_events.empty:
        return pd.DataFrame(
            {
                "timestamp": ts,
                "iob": np.nan,
                "activity": np.nan,
                "warmed_up": warmed_up_mask,
            }
        )

    # Normalize to tz-naive UTC datetime64[ns] before touching numpy:
    # tz-aware pandas Timestamps come back from `.to_numpy()` as an
    # object-dtype array (numpy has no native tz-aware datetime64), which
    # `np.searchsorted` cannot compare against a `np.datetime64` value.
    # Converting to UTC first (not just dropping tz) is required for
    # correctness, not just dtype-compatibility — bare tz-drop would
    # compare wall-clock times across whatever offsets the inputs happen
    # to carry.
    ts64 = ts.tz_convert("UTC").tz_localize(None).to_numpy()
    dose_ts_series = pd.DatetimeIndex(dose_events["timestamp"])
    dose_ts = dose_ts_series.tz_convert("UTC").tz_localize(None).to_numpy()
    dose_units = dose_events["units"].to_numpy(dtype=float)
    order = np.argsort(dose_ts)
    dose_ts, dose_units = dose_ts[order], dose_units[order]

    iob_out = np.zeros(len(ts), dtype=float)
    activity_out = np.zeros(len(ts), dtype=float)
    warmed_up = warmed_up_mask.to_numpy() if hasattr(warmed_up_mask, "to_numpy") else np.asarray(warmed_up_mask)

    dia_ns = np.timedelta64(int(round(dia_minutes * 60)), "s")
    for i in range(len(ts64)):
        t64 = dose_ts.dtype.type(ts64[i])
        lo = np.searchsorted(dose_ts, t64 - dia_ns, side="left")
        hi = np.searchsorted(dose_ts, t64, side="right")
        if lo >= hi:
            continue
        window_ts = dose_ts[lo:hi]
        window_units = dose_units[lo:hi]
        minutes_ago = (t64 - window_ts) / np.timedelta64(1, "m")
        activity_frac, iob_frac = _activity_and_iob_fraction(
            minutes_ago, config.dia_hours, config.peak_minutes
        )
        iob_out[i] = float(np.sum(window_units * iob_frac))
        activity_out[i] = float(np.sum(window_units * activity_frac))

    iob_out = np.where(warmed_up, iob_out, np.nan)
    activity_out = np.where(warmed_up, activity_out, np.nan)

    return pd.DataFrame(
        {"timestamp": ts, "iob": iob_out, "activity": activity_out, "warmed_up": warmed_up}
    )
