"""Deviation categorization for M4 (Workstream C: algorithm-research phase).

``notes/algorithm-research.md``, Workstream C:

    "Autotune splits into prep and core. Prep computes BGI and deviation
    per reading and categorizes each as attributable to carb sensitivity,
    ISF, or basal. Core does the fitting. Port the categorization. Do not
    port the fitting... AAPS also defaults to categorizing UAM data as
    basal, which would poison basal attribution for a user whose pump
    fires auto-corrections continuously. The categorization rules need
    rewriting against the existing bolus_category enrichment rather than a
    straight port. That rewrite is the actual M4 design work."

M4 (per ``docs/plans/2026-05-23-v2-development-roadmap.md``) is "Effective
insulin sensitivity tracking and an Autotune-style settings report...
surfaced as observation rather than auto-applied" — i.e. this categorizer
feeds a *report*, never a dosing decision, consistent with non-goal #1.

WHY THIS IS A REWRITE, NOT A PORT (read before extending)
------------------------------------------------------------
oref's ``lib/autotune-prep/categorize.js`` (MIT-licensed; read in full for
this phase, see ``docs/algorithm-research-findings.md``) splits every
5-minute reading into ``CSF`` / ``ISF`` / ``basal`` / ``UAM`` buckets so
Autotune's *fitting* step (not ported — non-goal) can propose new profile
numbers. Its basal/ISF split hinges on comparing ``BGI`` against
``basalBGI = currentBasal * sens / 60 * 5`` — "the BG impact the
*programmed profile rate alone* would have" — and treats "BGI close to
what profile-only delivery would produce" as basal-tunable data.

That comparison assumes a rig where basal is either the profile rate or a
user-set temp rate — i.e. a system where "not much insulin activity right
now" implies "the profile rate might be miscalibrated." Under Control-IQ,
basal is *continuously, algorithmically* modulated (``rate_source`` in
``ingestion.builders.build_basal_df`` is regularly ``"algorithm"`` or
``"temp_rate_and_algorithm"``, not just ``"profile"``). A period of low
insulin activity is just as likely to be Control-IQ *choosing* to
suppress basal (predicting/reacting to a low) as it is to be "the profile
rate is set correctly and nothing else is happening." Porting oref's
basal/ISF split as-is would attribute Control-IQ's own moment-to-moment
decisions to "the user's basal profile," which is exactly the AAPS
pitfall the notes name — and doubly meaningless here since this system
has no profile-fitting step to feed anyway (non-goal #1, no autotune
core).

So this module does not attempt oref's CSF/ISF/basal/UAM split at all.
Instead it categorizes each deviation against data this repo actually
has and actually trusts: ``bolus_category`` (the canonical vocabulary in
``core.bolus_categories`` — user_meal / user_meal_and_correction /
user_correction_only / auto_correction / override_up / override_down) and
``rate_source`` (the ``LidBasalDelivery``-derived flag on ``basal_df``:
suspended / profile / temp_rate / algorithm / temp_rate_and_algorithm).
The categories below answer "what does the data-we-trust say might
explain this deviation," not "which pump-profile knob should change."

CATEGORIES
----------
* ``meal_explained``    — a food-carrying bolus (``FOOD_CARRYING``) is
                           active nearby; the deviation is plausibly carb
                           absorption already known to the system.
* ``user_correction_explained`` / ``auto_correction_explained`` — a
                           correction-only bolus (user- or
                           Control-IQ-initiated) is active nearby.
* ``algorithm_modulated`` — no explaining bolus, but ``rate_source``
                           indicates Control-IQ was actively driving basal
                           away from baseline at this time. This is the
                           carve-out that avoids the AAPS pitfall: instead
                           of lumping "insulin activity doesn't match a
                           static assumption" into a basal-tuning bucket,
                           it is labeled as what it almost certainly is —
                           the algorithm doing its job — and kept
                           separate from genuinely unexplained signal.
* ``unexplained_rise`` / ``unexplained_fall`` — no bolus and no active
                           algorithm modulation nearby, deviation still
                           large. This is the closest analog to oref's
                           UAM concept ("evidence of *something*, cause
                           not classified") but named for what it is
                           (unexplained) rather than what oref guesses it
                           might be (an unannounced meal) — see the
                           module docstring's non-goal note: this system
                           does not infer intent, only flags the gap.
* ``baseline``           — deviation within ``noise_band_mgdl`` of zero;
                           insulin activity adequately explains the
                           observed glucose change.

None of this is a straight port: the category *names*, the *thresholds*,
and the *join logic against bolus_category/rate_source* are original
design work for this repo. Only the general shape of "walk the deviation
series and bucket each reading" carries a family resemblance to
``categorize.js`` — the actual bucketing rule is different.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

from core.bolus_categories import CORRECTION_CATEGORIES, FOOD_CARRYING

__all__ = ["CategorizeConfig", "categorize_deviations"]

_ALGORITHM_RATE_SOURCES = frozenset({"algorithm", "temp_rate_and_algorithm"})

CAT_MEAL = "meal_explained"
CAT_USER_CORRECTION = "user_correction_explained"
CAT_AUTO_CORRECTION = "auto_correction_explained"
CAT_ALGORITHM = "algorithm_modulated"
CAT_UNEXPLAINED_RISE = "unexplained_rise"
CAT_UNEXPLAINED_FALL = "unexplained_fall"
CAT_BASELINE = "baseline"
CAT_UNKNOWN = "unknown"  # deviation itself is NaN (unwarmed/no-delta row)


@dataclass(frozen=True)
class CategorizeConfig:
    """Thresholds for ``categorize_deviations``.

    ``noise_band_mgdl``: deviations within ``±noise_band_mgdl`` of zero are
    "baseline" regardless of nearby bolus/algorithm activity — a
    perfectly-explained reading needs no further categorization even if a
    bolus happens to be nearby.

    ``bolus_lookback_minutes`` / ``bolus_lookahead_minutes``: a bolus
    "explains" a deviation if it falls within
    ``[reading_ts - lookback, reading_ts + lookahead]``. ``lookback`` is
    the *carb/insulin absorption window* — how far in the past a bolus can
    be and still plausibly be acting now (default 180 min, roughly a
    typical meal-bolus absorption tail). ``lookahead`` is a small
    late-bolus grace period — a bolus taken shortly *after* a rise started
    still explains it (default 20 min). This is intentionally asymmetric
    and mirrors the shape (not the values) of
    ``detection.calibration.meal_rise_scoring``'s pre/late bolus windows —
    deliberately not importing those constants, since this module answers
    a different question: "is a bolus active here," not "did a bolus
    cover this specific rise event."

    ``algorithm_lookback_minutes``: how recently ``rate_source`` must have
    shown algorithm-driven modulation for a reading to be tagged
    ``algorithm_modulated``.
    """

    noise_band_mgdl: float = 5.0
    bolus_lookback_minutes: float = 180.0
    bolus_lookahead_minutes: float = 20.0
    algorithm_lookback_minutes: float = 15.0


def _bolus_category_nearby(
    ts: pd.Timestamp,
    requests_df: pd.DataFrame,
    categories: frozenset[str],
    lookback: timedelta,
    lookahead: timedelta,
) -> bool:
    if requests_df is None or requests_df.empty or "bolus_category" not in requests_df.columns:
        return False
    window = requests_df[
        (requests_df["timestamp"] >= ts - lookback)
        & (requests_df["timestamp"] <= ts + lookahead)
        & (requests_df["bolus_category"].isin(categories))
    ]
    return not window.empty


def _algorithm_active_nearby(
    ts: pd.Timestamp, basal_df: pd.DataFrame, lookback: timedelta
) -> bool:
    if basal_df is None or basal_df.empty or "rate_source" not in basal_df.columns:
        return False
    window = basal_df[
        (basal_df["timestamp"] >= ts - lookback)
        & (basal_df["timestamp"] <= ts)
        & (basal_df["rate_source"].isin(_ALGORITHM_RATE_SOURCES))
    ]
    return not window.empty


def categorize_deviations(
    deviation_df: pd.DataFrame,
    requests_df: pd.DataFrame,
    basal_df: pd.DataFrame,
    config: CategorizeConfig = CategorizeConfig(),
) -> pd.DataFrame:
    """Add a ``deviation_category`` column to ``deviation_df``.

    ``deviation_df`` needs ``timestamp`` and ``deviation_5m`` (from
    ``detection.deviation.compute_deviation_frame``). ``requests_df`` needs
    ``timestamp`` and ``bolus_category`` (from ``ingestion.enrich`` — the
    ``bolus_category`` enrichment, NOT the raw ``bolus``/``requests``
    frames). ``basal_df`` needs ``timestamp`` and ``rate_source``.

    Category precedence (first match wins): baseline (noise band) →
    meal_explained → user/auto_correction_explained → algorithm_modulated
    → unexplained_{rise,fall}. Baseline is checked first deliberately: a
    reading with a food bolus nearby but a near-zero deviation is not
    "meal explained," it is "nothing happened yet" — the category should
    describe the deviation, not just what's nearby in time.
    """
    if deviation_df is None or deviation_df.empty:
        return deviation_df.assign(deviation_category=pd.Series(dtype="object")) if deviation_df is not None else deviation_df

    bolus_lookback = timedelta(minutes=config.bolus_lookback_minutes)
    bolus_lookahead = timedelta(minutes=config.bolus_lookahead_minutes)
    algo_lookback = timedelta(minutes=config.algorithm_lookback_minutes)

    categories: list[str] = []
    for row in deviation_df.itertuples(index=False):
        dev = getattr(row, "deviation_5m")
        ts = getattr(row, "timestamp")

        if dev is None or (isinstance(dev, float) and np.isnan(dev)):
            categories.append(CAT_UNKNOWN)
            continue
        if abs(dev) <= config.noise_band_mgdl:
            categories.append(CAT_BASELINE)
            continue
        if _bolus_category_nearby(ts, requests_df, FOOD_CARRYING, bolus_lookback, bolus_lookahead):
            categories.append(CAT_MEAL)
            continue
        if _bolus_category_nearby(ts, requests_df, {"auto_correction"}, bolus_lookback, bolus_lookahead):
            categories.append(CAT_AUTO_CORRECTION)
            continue
        if _bolus_category_nearby(
            ts, requests_df, CORRECTION_CATEGORIES - {"auto_correction"}, bolus_lookback, bolus_lookahead
        ):
            categories.append(CAT_USER_CORRECTION)
            continue
        if _algorithm_active_nearby(ts, basal_df, algo_lookback):
            categories.append(CAT_ALGORITHM)
            continue
        categories.append(CAT_UNEXPLAINED_RISE if dev > 0 else CAT_UNEXPLAINED_FALL)

    out = deviation_df.copy()
    out["deviation_category"] = categories
    return out
