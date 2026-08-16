"""Tests for detection/deviation_categorize.py (Workstream C: M4 design work,
algorithm-research phase)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from detection.deviation_categorize import (
    CAT_ALGORITHM,
    CAT_AUTO_CORRECTION,
    CAT_BASELINE,
    CAT_MEAL,
    CAT_UNEXPLAINED_FALL,
    CAT_UNEXPLAINED_RISE,
    CAT_UNKNOWN,
    CAT_USER_CORRECTION,
    CategorizeConfig,
    categorize_deviations,
)

TZ = timezone(timedelta(hours=-7), name="PDT")


def _ts(m):
    return datetime(2026, 8, 1, 12, 0, tzinfo=TZ) + timedelta(minutes=m)


def _dev_frame(minutes, devs):
    return pd.DataFrame({"timestamp": [_ts(m) for m in minutes], "deviation_5m": devs})


class TestCategorizeDeviations:
    def test_empty_frame(self):
        out = categorize_deviations(
            pd.DataFrame(columns=["timestamp", "deviation_5m"]), pd.DataFrame(), pd.DataFrame()
        )
        assert out.empty

    def test_nan_deviation_is_unknown(self):
        df = _dev_frame([0], [np.nan])
        out = categorize_deviations(df, pd.DataFrame(), pd.DataFrame())
        assert out.iloc[0]["deviation_category"] == CAT_UNKNOWN

    def test_small_deviation_is_baseline_even_with_bolus_nearby(self):
        df = _dev_frame([0], [2.0])  # within default noise_band_mgdl=5.0
        requests_df = pd.DataFrame(
            {"timestamp": [_ts(0)], "bolus_category": ["user_meal"], "carbs_g": [30]}
        )
        out = categorize_deviations(df, requests_df, pd.DataFrame())
        assert out.iloc[0]["deviation_category"] == CAT_BASELINE

    def test_large_deviation_near_food_bolus_is_meal_explained(self):
        df = _dev_frame([30], [40.0])
        requests_df = pd.DataFrame(
            {"timestamp": [_ts(0)], "bolus_category": ["user_meal_and_correction"], "carbs_g": [45]}
        )
        out = categorize_deviations(df, requests_df, pd.DataFrame())
        assert out.iloc[0]["deviation_category"] == CAT_MEAL

    def test_auto_correction_nearby_is_labeled_distinctly_from_user_correction(self):
        df = _dev_frame([10, 10], [30.0, 30.0])
        auto = pd.DataFrame({"timestamp": [_ts(0)], "bolus_category": ["auto_correction"], "carbs_g": [0]})
        user = pd.DataFrame({"timestamp": [_ts(0)], "bolus_category": ["user_correction_only"], "carbs_g": [0]})
        out_auto = categorize_deviations(df.iloc[[0]], auto, pd.DataFrame())
        out_user = categorize_deviations(df.iloc[[1]], user, pd.DataFrame())
        assert out_auto.iloc[0]["deviation_category"] == CAT_AUTO_CORRECTION
        assert out_user.iloc[0]["deviation_category"] == CAT_USER_CORRECTION

    def test_algorithm_modulation_carve_out_not_lumped_into_unexplained(self):
        """The AAPS-pitfall fix: a large deviation with no bolus nearby but
        with Control-IQ actively modulating basal should be tagged
        algorithm_modulated, not unexplained_rise and never a basal-tuning
        bucket (this repo doesn't have one)."""
        df = _dev_frame([0], [50.0])
        basal_df = pd.DataFrame(
            {"timestamp": [_ts(-5)], "commanded_rate": [2.5], "rate_source": ["algorithm"]}
        )
        out = categorize_deviations(df, pd.DataFrame(), basal_df)
        assert out.iloc[0]["deviation_category"] == CAT_ALGORITHM

    def test_no_explanation_positive_deviation_is_unexplained_rise(self):
        df = _dev_frame([0], [50.0])
        out = categorize_deviations(df, pd.DataFrame(), pd.DataFrame())
        assert out.iloc[0]["deviation_category"] == CAT_UNEXPLAINED_RISE

    def test_no_explanation_negative_deviation_is_unexplained_fall(self):
        df = _dev_frame([0], [-50.0])
        out = categorize_deviations(df, pd.DataFrame(), pd.DataFrame())
        assert out.iloc[0]["deviation_category"] == CAT_UNEXPLAINED_FALL

    def test_bolus_outside_window_does_not_explain(self):
        df = _dev_frame([0], [50.0])
        requests_df = pd.DataFrame(
            {
                "timestamp": [_ts(0) - timedelta(minutes=500)],
                "bolus_category": ["user_meal"],
                "carbs_g": [40],
            }
        )
        out = categorize_deviations(df, requests_df, pd.DataFrame())
        assert out.iloc[0]["deviation_category"] == CAT_UNEXPLAINED_RISE

    def test_profile_rate_source_does_not_trigger_algorithm_category(self):
        df = _dev_frame([0], [50.0])
        basal_df = pd.DataFrame(
            {"timestamp": [_ts(-5)], "commanded_rate": [1.0], "rate_source": ["profile"]}
        )
        out = categorize_deviations(df, pd.DataFrame(), basal_df)
        assert out.iloc[0]["deviation_category"] == CAT_UNEXPLAINED_RISE

    def test_precedence_meal_over_algorithm(self):
        """When both a food bolus and algorithm modulation are nearby, the
        bolus explanation wins — a logged carb entry is stronger evidence
        than "Control-IQ happened to be active.\""""
        df = _dev_frame([0], [50.0])
        requests_df = pd.DataFrame(
            {"timestamp": [_ts(0)], "bolus_category": ["user_meal"], "carbs_g": [40]}
        )
        basal_df = pd.DataFrame(
            {"timestamp": [_ts(-5)], "commanded_rate": [2.0], "rate_source": ["algorithm"]}
        )
        out = categorize_deviations(df, requests_df, basal_df)
        assert out.iloc[0]["deviation_category"] == CAT_MEAL

    def test_custom_noise_band(self):
        df = _dev_frame([0], [8.0])
        out_default = categorize_deviations(df, pd.DataFrame(), pd.DataFrame())
        assert out_default.iloc[0]["deviation_category"] != CAT_BASELINE
        out_wide = categorize_deviations(
            df, pd.DataFrame(), pd.DataFrame(), CategorizeConfig(noise_band_mgdl=10.0)
        )
        assert out_wide.iloc[0]["deviation_category"] == CAT_BASELINE
