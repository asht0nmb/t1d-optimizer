"""Supervised models over the M2 meal-rise-labeled corpus.

*** RESEARCH / EXPLORATION MODULE. Not called by the live meal-rise loop
(`apps/personal/cron/`) or any other production surface. See
`docs/ml-notes/supervised-models.md` for the full pedagogical write-up. ***

--------------------------------------------------------------------------
WHAT PROBLEM THIS IS SOLVING, AND WHY IT'S DIFFERENT FROM CLUSTERING
--------------------------------------------------------------------------
`detection/clustering.py` (priority 1) is *unsupervised* — it looks for
structure with no labels. This module is *supervised*: the M2 calibration
scorer (`detection/calibration/meal_rise_scoring.py`) already produces a
ground-truth label for every historical meal-rise detection — `pre_bolused`
/ `late_bolused` / `uncovered` — by comparing the detection's timing against
the pump's bolus log after the fact. That's a real target variable, so the
natural question becomes: can a model, using only information available
*at the moment the rise is detected* (no future bolus data), predict which
of those three outcomes is coming? If it can, that's a step toward a
"this meal-rise looks like it's heading toward uncovered — consider a
correction" live alert, instead of only a retrospective report card.

**This module is advisory/research only, same as `score_meal_rise.py`.**
Nothing here writes to `config/user_config.yaml`, and nothing here is wired
into the live Telegram loop. A model output becoming a live alert threshold
is a human decision for later, not something this exploration pass makes.

--------------------------------------------------------------------------
THE LEAKAGE BOUNDARY — the single most important design decision here
--------------------------------------------------------------------------
`ScoredInstance` (see `detection/calibration/meal_rise_scoring.py`) has 18
fields. Several of them are **definitionally derived from the label** and
must never be used as model inputs, or the "model" would just be
re-deriving the label from data that only exists *because* the label is
already known:

    matched_bolus_ts, matched_bolus_category, matched_bolus_carbs,
    bolus_delay_min, resolution, resolution_ts, resolution_delay_min

Every one of those fields comes from *searching forward from the
detection* through the bolus log — `score_instances` computes them only
after deciding the label. Training on `bolus_delay_min` to predict `label`
(which is a `sign(bolus_delay_min)` step function, by construction) would
report near-100% accuracy while learning nothing generalizable: at the
moment a live system would need this prediction — right when the rise is
detected — none of these fields exist yet, because the very bolus (or its
absence) they describe hasn't happened yet.

The only fields safe to use are the ones computable from the CGM window
that produced the detection, before any bolus context is consulted:

    start_level, end_level, delta, slope_mgdl_per_min, hour_of_day
    (+ day_of_week, derived here from rise_start_ts — a `MealRiseDetection`
    doesn't carry it directly, but it's timestamp arithmetic, not bolus
    data, so it's equally safe)

`LEAKY_FEATURES` and `SAFE_FEATURES` below encode this split as data, not
just prose, specifically so a future contributor extending this module
gets an explicit list to check against rather than having to rediscover
the reasoning by reading `meal_rise_scoring.py` closely.

--------------------------------------------------------------------------
TRAIN/TEST SPLIT — why chronological, not random
--------------------------------------------------------------------------
The standard `sklearn.model_selection.train_test_split` shuffles rows
uniformly at random before splitting. For i.i.d. data (e.g. rows are
independent photographs) that's the right call. Health time-series data
violates the i.i.d. assumption in two ways that make random splitting
optimistic (it overstates how well the model will do on truly new, future
days):

1. **Autocorrelation.** Meal-rise instances from the same day, or from a
   run of days with the same illness/travel/pump-site situation, share
   context a model can partially memorize (e.g. "this whole week ran a bit
   high") even from features that look person-agnostic. A random split
   scatters same-day/same-week instances across both train and test,
   letting the model implicitly "see" test-adjacent context during
   training.
2. **Regime drift.** Insulin sensitivity, carb ratios, and behavior all
   shift over months/years (site changes, life changes, endo-directed
   retuning). A model is only useful if it generalizes *forward in time* —
   predicting tomorrow from history, never predicting an already-past day
   from data recorded after it. A random split can put an instance from
   2025 in the training set and one from 2024 in the test set, which is a
   direction of "generalization" no live system will ever actually need
   and can make a model look better than it will perform once deployed.

`chronological_split` sorts by `rise_start_ts` and takes the trailing
`test_fraction` as the held-out set — the model only ever "sees" the past
when evaluated on the future, mirroring exactly how it would be used live.

--------------------------------------------------------------------------
BASELINES — why a bare accuracy number is not an honest result
--------------------------------------------------------------------------
With 3 imbalanced classes (uncovered is typically the minority — see the
real class distribution reported in `docs/ml-notes/supervised-models.md`),
a model can post a deceptively high accuracy just by mostly predicting the
majority class. Two baselines make any headline number interpretable:

* `MajorityClassBaseline` — always predicts the training set's most common
  label. The floor: if the real model can't beat this, it has learned
  nothing.
* `HourOfDayBaseline` — predicts the majority label observed *for that
  hour of day* in training (falls back to the overall majority for unseen
  hours). Meal-rise labels plausibly correlate with hour of day already
  (breakfast vs. a 2am correction bolus have very different typical
  behavior) — this baseline asks "is the model doing better than just
  knowing what time it is?", which is a much higher and more honest bar
  than the majority-class floor.

--------------------------------------------------------------------------
MODEL CHOICE
--------------------------------------------------------------------------
`RandomForestClassifier` (scikit-learn) is used for the "beyond baseline"
model: it handles the small feature count and modest sample size here
without heavy tuning, requires no feature scaling, gives interpretable
`feature_importances_`, and is a reasonable off-the-shelf choice before
reaching for gradient boosting on a dataset this size. `class_weight=
"balanced"` compensates for label imbalance rather than letting the model
default toward the majority class the way an unweighted fit would.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix

from detection.calibration.meal_rise_scoring import ScoredInstance

__all__ = [
    "SAFE_FEATURES",
    "LEAKY_FEATURES",
    "scored_instances_to_frame",
    "chronological_split",
    "MajorityClassBaseline",
    "HourOfDayBaseline",
    "train_random_forest",
    "evaluate",
]

#: Fields computable at detection time, before any bolus context exists.
#: Safe to use as model inputs. See module docstring "THE LEAKAGE BOUNDARY."
SAFE_FEATURES: tuple[str, ...] = (
    "start_level",
    "end_level",
    "delta",
    "slope_mgdl_per_min",
    "hour_of_day",
    "day_of_week",
)

#: Fields derived from searching the bolus log *after* the detection, i.e.
#: definitionally downstream of the label. Never use these as model inputs.
#: Kept here (not just documented in prose) so a future change can assert
#: against it — see `tests/test_detection_supervised.py`'s leakage-boundary
#: test, which fails loudly if `SAFE_FEATURES` and `LEAKY_FEATURES` ever
#: overlap.
LEAKY_FEATURES: tuple[str, ...] = (
    "matched_bolus_ts",
    "matched_bolus_category",
    "matched_bolus_carbs",
    "bolus_delay_min",
    "resolution",
    "resolution_ts",
    "resolution_delay_min",
)


def scored_instances_to_frame(scored: list[ScoredInstance]) -> pd.DataFrame:
    """`ScoredInstance` list -> one row per instance, `SAFE_FEATURES` + label + timestamp.

    Includes `label` (the target) and `rise_start_ts` (needed for
    `chronological_split`; not a feature — dropped before fitting). Does
    NOT include any `LEAKY_FEATURES` column, by construction — this is the
    only place a `ScoredInstance` is converted to a model-ready frame, so
    keeping leaky fields out here is what makes the leakage boundary
    actually enforced rather than just documented.
    """
    rows = []
    for s in scored:
        rows.append(
            {
                "rise_start_ts": s.rise_start_ts,
                "start_level": s.start_level,
                "end_level": s.end_level,
                "delta": s.delta,
                "slope_mgdl_per_min": s.slope_mgdl_per_min,
                "hour_of_day": s.hour_of_day,
                "day_of_week": s.rise_start_ts.weekday(),
                "label": s.label,
            }
        )
    df = pd.DataFrame(rows)
    assert set(LEAKY_FEATURES).isdisjoint(df.columns)
    return df


def chronological_split(
    df: pd.DataFrame, test_fraction: float = 0.2, *, time_col: str = "rise_start_ts"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sort by `time_col` and take the trailing `test_fraction` as test.

    See module docstring "TRAIN/TEST SPLIT" for why this is chronological
    rather than a random `sklearn.model_selection.train_test_split`.
    """
    if not (0 < test_fraction < 1):
        raise ValueError(f"test_fraction must be in (0, 1), got {test_fraction}")
    ordered = df.sort_values(time_col).reset_index(drop=True)
    n_test = max(1, int(len(ordered) * test_fraction))
    split_idx = len(ordered) - n_test
    return ordered.iloc[:split_idx].reset_index(drop=True), ordered.iloc[split_idx:].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

@dataclass
class MajorityClassBaseline:
    """Always predicts the training set's most common label."""

    majority_label_: str | None = None

    def fit(self, y_train: pd.Series) -> "MajorityClassBaseline":
        self.majority_label_ = y_train.mode().iloc[0]
        return self

    def predict(self, n: int) -> np.ndarray:
        if self.majority_label_ is None:
            raise RuntimeError("call fit() first")
        return np.full(n, self.majority_label_, dtype=object)


@dataclass
class HourOfDayBaseline:
    """Predicts the majority label observed for that hour-of-day in training."""

    by_hour_: dict[int, str] | None = None
    overall_majority_: str | None = None

    def fit(self, hours_train: pd.Series, y_train: pd.Series) -> "HourOfDayBaseline":
        self.overall_majority_ = y_train.mode().iloc[0]
        table = {}
        for hour, group in y_train.groupby(hours_train):
            table[int(hour)] = group.mode().iloc[0]
        self.by_hour_ = table
        return self

    def predict(self, hours_test: pd.Series) -> np.ndarray:
        if self.by_hour_ is None:
            raise RuntimeError("call fit() first")
        return np.array(
            [self.by_hour_.get(int(h), self.overall_majority_) for h in hours_test],
            dtype=object,
        )


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def train_random_forest(
    train_df: pd.DataFrame, *, random_seed: int = 42, n_estimators: int = 200
) -> RandomForestClassifier:
    """Fit a `RandomForestClassifier` on `SAFE_FEATURES` -> `label`.

    `class_weight="balanced"` — see module docstring "BASELINES" for why
    label imbalance matters here.
    """
    X = train_df[list(SAFE_FEATURES)]
    y = train_df["label"]
    model = RandomForestClassifier(
        n_estimators=n_estimators,
        random_state=random_seed,
        class_weight="balanced",
    )
    model.fit(X, y)
    return model


def evaluate(y_true: pd.Series, y_pred: np.ndarray) -> dict:
    """`classification_report` (as a dict) + confusion matrix + label order."""
    labels = sorted(pd.unique(pd.concat([y_true, pd.Series(y_pred)])))
    report = classification_report(y_true, y_pred, labels=labels, output_dict=True, zero_division=0)
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    return {"report": report, "confusion_matrix": cm.tolist(), "labels": labels}
