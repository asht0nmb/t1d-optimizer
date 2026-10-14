"""Tests for `detection.supervised` (M2 corpus supervised modeling, research).

Synthetic `ScoredInstance` corpora only — no Supabase, no network. The
leakage-boundary and chronological-split tests are the load-bearing ones:
everything else is standard sklearn-pipeline plumbing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from detection.calibration.meal_rise_scoring import LABEL_LATE, LABEL_PRE, LABEL_UNCOVERED, ScoredInstance
from detection.supervised import (
    LEAKY_FEATURES,
    SAFE_FEATURES,
    HourOfDayBaseline,
    MajorityClassBaseline,
    chronological_split,
    evaluate,
    scored_instances_to_frame,
    train_random_forest,
)

_TZ = timezone.utc


def _instance(
    *, rise_start, label, start_level=110, delta=40, slope=2.0, hour=8
) -> ScoredInstance:
    return ScoredInstance(
        event_ref=f"meal_rise:{rise_start.isoformat()}",
        pump_serial="12345",
        label=label,
        anchor_ts=rise_start + timedelta(minutes=15),
        rise_start_ts=rise_start,
        rise_end_ts=rise_start + timedelta(minutes=15),
        start_level=start_level,
        end_level=start_level + delta,
        delta=delta,
        slope_mgdl_per_min=slope,
        hour_of_day=hour,
        matched_bolus_ts=rise_start if label != LABEL_UNCOVERED else None,
        matched_bolus_category="user_meal" if label != LABEL_UNCOVERED else None,
        matched_bolus_carbs=30 if label != LABEL_UNCOVERED else None,
        bolus_delay_min=-5.0 if label != LABEL_UNCOVERED else None,
        resolution=None if label != LABEL_UNCOVERED else "none",
        resolution_ts=None,
        resolution_delay_min=None,
    )


def _synthetic_corpus(n: int, seed: int = 0) -> list[ScoredInstance]:
    """A corpus with a genuine (if simple) signal: high slope + low start
    level strongly predicts `uncovered` (the model should be able to beat
    baselines here); everything else is closer to the label distribution
    at random, so the model isn't handed a trivial 100%-separable problem.
    """
    rng = np.random.default_rng(seed)
    start = datetime(2025, 1, 1, tzinfo=_TZ)
    out = []
    for i in range(n):
        rise_start = start + timedelta(hours=6 * i)
        hour = rise_start.hour
        slope = float(rng.uniform(0.5, 4.0))
        start_level = int(rng.uniform(80, 160))
        if slope > 2.5 and start_level < 110:
            label = LABEL_UNCOVERED if rng.random() < 0.8 else rng.choice([LABEL_PRE, LABEL_LATE])
        else:
            label = rng.choice([LABEL_PRE, LABEL_LATE, LABEL_UNCOVERED], p=[0.5, 0.3, 0.2])
        out.append(
            _instance(rise_start=rise_start, label=label, start_level=start_level, slope=slope, hour=hour)
        )
    return out


# ---------------------------------------------------------------------------
# Leakage boundary
# ---------------------------------------------------------------------------

def test_safe_and_leaky_features_are_disjoint():
    assert set(SAFE_FEATURES).isdisjoint(LEAKY_FEATURES)


def test_scored_instances_to_frame_excludes_leaky_fields():
    scored = _synthetic_corpus(10)
    df = scored_instances_to_frame(scored)
    assert set(LEAKY_FEATURES).isdisjoint(df.columns)
    assert set(SAFE_FEATURES).issubset(df.columns)
    assert "label" in df.columns
    assert "rise_start_ts" in df.columns


def test_scored_instances_to_frame_derives_day_of_week():
    monday = datetime(2025, 1, 6, 8, 0, tzinfo=_TZ)  # a known Monday
    scored = [_instance(rise_start=monday, label=LABEL_PRE)]
    df = scored_instances_to_frame(scored)
    assert df.iloc[0]["day_of_week"] == 0  # Monday == 0 in datetime.weekday()


# ---------------------------------------------------------------------------
# Chronological split
# ---------------------------------------------------------------------------

def test_chronological_split_orders_by_time_not_shuffled():
    scored = _synthetic_corpus(20)
    df = scored_instances_to_frame(scored)
    train, test = chronological_split(df, test_fraction=0.25)
    assert len(train) + len(test) == len(df)
    assert train["rise_start_ts"].max() <= test["rise_start_ts"].min()


def test_chronological_split_rejects_bad_fraction():
    df = scored_instances_to_frame(_synthetic_corpus(5))
    with pytest.raises(ValueError, match="test_fraction"):
        chronological_split(df, test_fraction=1.5)
    with pytest.raises(ValueError, match="test_fraction"):
        chronological_split(df, test_fraction=0)


def test_chronological_split_test_set_always_at_least_one_row():
    df = scored_instances_to_frame(_synthetic_corpus(3))
    train, test = chronological_split(df, test_fraction=0.01)
    assert len(test) >= 1


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def test_majority_class_baseline_predicts_constant():
    y = pd.Series([LABEL_PRE, LABEL_PRE, LABEL_UNCOVERED])
    baseline = MajorityClassBaseline().fit(y)
    preds = baseline.predict(4)
    assert (preds == LABEL_PRE).all()


def test_hour_of_day_baseline_falls_back_for_unseen_hour():
    hours = pd.Series([8, 8, 20])
    y = pd.Series([LABEL_PRE, LABEL_PRE, LABEL_UNCOVERED])
    baseline = HourOfDayBaseline().fit(hours, y)
    preds = baseline.predict(pd.Series([8, 3]))  # hour 3 unseen in training
    assert preds[0] == LABEL_PRE
    assert preds[1] == baseline.overall_majority_


# ---------------------------------------------------------------------------
# Model + evaluation
# ---------------------------------------------------------------------------

def test_train_random_forest_beats_majority_baseline_on_separable_signal():
    scored = _synthetic_corpus(400, seed=1)
    df = scored_instances_to_frame(scored)
    train, test = chronological_split(df, test_fraction=0.3)

    baseline = MajorityClassBaseline().fit(train["label"])
    baseline_preds = baseline.predict(len(test))
    baseline_acc = float((baseline_preds == test["label"].to_numpy()).mean())

    model = train_random_forest(train, random_seed=1)
    model_preds = model.predict(test[list(SAFE_FEATURES)])
    model_acc = float((model_preds == test["label"].to_numpy()).mean())

    assert model_acc >= baseline_acc


def test_evaluate_returns_report_and_confusion_matrix_shape():
    scored = _synthetic_corpus(100, seed=2)
    df = scored_instances_to_frame(scored)
    train, test = chronological_split(df, test_fraction=0.3)
    model = train_random_forest(train, random_seed=2)
    preds = model.predict(test[list(SAFE_FEATURES)])
    result = evaluate(test["label"], preds)
    assert "report" in result and "confusion_matrix" in result and "labels" in result
    n_labels = len(result["labels"])
    assert len(result["confusion_matrix"]) == n_labels
    assert all(len(row) == n_labels for row in result["confusion_matrix"])
    for label in result["labels"]:
        assert label in result["report"]


def test_train_random_forest_uses_only_safe_features():
    scored = _synthetic_corpus(50, seed=3)
    df = scored_instances_to_frame(scored)
    model = train_random_forest(df, random_seed=3)
    assert list(model.feature_names_in_) == list(SAFE_FEATURES)
