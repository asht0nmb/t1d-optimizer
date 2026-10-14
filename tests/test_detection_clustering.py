"""Tests for `detection.clustering` (v2 pattern layer, research/exploration).

Uses deterministic synthetic feature frames — either mirroring
`daily_features`'s schema for the "does the plumbing work" tests, or
constructed as clearly-separated Gaussian blobs for the "does validation
actually detect structure vs. noise" tests. No network, no Supabase; every
test is fast and hermetic. Persisted-artifact tests point `model_dir` at
`tmp_path` so pickles never leak into the real `data/models/v2`.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from detection.clustering import (
    DROPPED_REDUNDANT_FEATURES,
    SELECTED_FEATURES,
    ClusteringV2Config,
    cluster_profiles,
    cross_method_agreement,
    elbow_curve,
    filter_low_coverage_days,
    fit_kmeans,
    get_clustering_v2_config,
    gmm_labels,
    hierarchical_labels,
    impute_median,
    load_model,
    name_clusters,
    predict_kmeans,
    save_model,
    select_features,
    silhouette_curve,
    stability_ari,
)
from detection.config import get_config


def _synthetic_daily_features(n_days: int, seed: int = 0) -> pd.DataFrame:
    """A frame with `daily_features`'s full 16-column schema + `date` +
    `cgm_reading_count`, filled with plausible-range random values.
    """
    rng = np.random.default_rng(seed)
    start = date(2025, 1, 1)
    dates = [start + timedelta(days=i) for i in range(n_days)]
    below = rng.uniform(0, 0.2, n_days)
    tir = rng.uniform(0.4, 0.9, n_days)
    above250 = rng.uniform(0, 0.2, n_days)
    above180 = np.clip(1 - below - tir - above250, 0, None)
    df = pd.DataFrame(
        {
            "date": dates,
            "tir_70_180": tir,
            "time_below_70": below,
            "time_above_180": above180,
            "time_above_250": above250,
            "mean_bg": rng.uniform(120, 200, n_days),
            "std_bg": rng.uniform(20, 70, n_days),
            "cv_bg": rng.uniform(0.2, 0.5, n_days),
            "total_daily_insulin": rng.uniform(30, 100, n_days),
            "basal_bolus_ratio": rng.uniform(0.3, 2.0, n_days),
            "meal_count": rng.integers(1, 6, n_days).astype(float),
            "total_carbs_g": rng.uniform(50, 250, n_days),
            "overnight_dip": rng.uniform(-50, 20, n_days),
            "mean_postprandial_peak": rng.uniform(10, 80, n_days),
            "alarm_count": rng.integers(0, 30, n_days).astype(float),
            "suspension_minutes": rng.uniform(0, 200, n_days),
            "out_of_range_minutes": rng.uniform(0, 150, n_days),
            "cgm_reading_count": rng.integers(260, 289, n_days),
        }
    )
    return df


def _separated_blobs(n_per_blob: int = 60, seed: int = 0) -> pd.DataFrame:
    """Two well-separated Gaussian blobs in the 14 `SELECTED_FEATURES`, so
    validation functions have unambiguous ground truth to detect.
    """
    rng = np.random.default_rng(seed)
    n = n_per_blob * 2
    start = date(2025, 1, 1)
    dates = [start + timedelta(days=i) for i in range(n)]
    data = {}
    for i, col in enumerate(SELECTED_FEATURES):
        # Blob A centered at 0, blob B centered at +20 (in raw units this
        # is a large separation relative to the small within-blob sigma=1,
        # so k-means/Ward/GMM should agree almost perfectly on the split).
        a = rng.normal(0, 1, n_per_blob)
        b = rng.normal(20, 1, n_per_blob)
        data[col] = np.concatenate([a, b])
    data["date"] = dates
    data["cgm_reading_count"] = rng.integers(280, 289, n)
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# Feature selection
# ---------------------------------------------------------------------------

def test_selected_features_excludes_documented_drops():
    assert set(SELECTED_FEATURES).isdisjoint(DROPPED_REDUNDANT_FEATURES)
    assert "time_above_180" in DROPPED_REDUNDANT_FEATURES
    assert "cv_bg" in DROPPED_REDUNDANT_FEATURES


def test_select_features_raises_on_missing_column():
    df = pd.DataFrame({"date": [date(2025, 1, 1)]})
    with pytest.raises(ValueError, match="missing columns"):
        select_features(df)


def test_select_features_projects_and_orders():
    df = _synthetic_daily_features(5)
    out = select_features(df)
    assert list(out.columns) == list(SELECTED_FEATURES)
    assert len(out) == 5


# ---------------------------------------------------------------------------
# Coverage filter
# ---------------------------------------------------------------------------

def test_filter_low_coverage_days_drops_below_threshold():
    df = pd.DataFrame({"date": [date(2025, 1, 1), date(2025, 1, 2)], "cgm_reading_count": [288, 50]})
    out = filter_low_coverage_days(df, min_coverage_fraction=0.9)
    assert len(out) == 1
    assert out.iloc[0]["date"] == date(2025, 1, 1)


def test_filter_low_coverage_days_requires_reading_count_column():
    df = pd.DataFrame({"date": [date(2025, 1, 1)]})
    with pytest.raises(ValueError, match="cgm_reading_count"):
        filter_low_coverage_days(df)


def test_filter_low_coverage_days_keeps_over_full_coverage():
    # Real data shows coverage fraction can exceed 1.0 (documented quirk).
    df = pd.DataFrame({"date": [date(2025, 1, 1)], "cgm_reading_count": [350]})
    out = filter_low_coverage_days(df, min_coverage_fraction=0.9)
    assert len(out) == 1


# ---------------------------------------------------------------------------
# Imputation
# ---------------------------------------------------------------------------

def test_impute_median_fits_and_fills():
    df = pd.DataFrame({"a": [1.0, np.nan, 3.0], "b": [10.0, 20.0, 30.0]})
    filled, medians = impute_median(df)
    assert filled["a"].tolist() == [1.0, 2.0, 3.0]
    assert medians["a"] == 2.0
    assert not filled.isna().any().any()


def test_impute_median_all_nan_column_falls_back_to_zero():
    df = pd.DataFrame({"a": [np.nan, np.nan]})
    filled, medians = impute_median(df)
    assert medians["a"] == 0.0
    assert filled["a"].tolist() == [0.0, 0.0]


def test_impute_median_reuses_persisted_medians_for_single_row_batch():
    """The legacy per-batch-median approach degenerates on a lone NaN row
    (median of one NaN is NaN). This module's explicit `medians` argument
    fixes that — verify a single-row batch with a NaN gets the *persisted*
    fit-time median, not NaN.
    """
    train = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
    _, medians = impute_median(train)
    new_day = pd.DataFrame({"a": [np.nan]})
    filled, _ = impute_median(new_day, medians=medians)
    assert filled["a"].iloc[0] == medians["a"]
    assert not filled.isna().any().any()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_clustering_v2_config_defaults_when_block_absent():
    app_config = get_config()
    cfg = get_clustering_v2_config(app_config)
    assert cfg == ClusteringV2Config()


def test_clustering_v2_config_overrides_from_raw():
    app_config = get_config()
    import dataclasses
    patched = dataclasses.replace(
        app_config, raw={**app_config.raw, "clustering_v2": {"n_clusters": 3}}
    )
    cfg = get_clustering_v2_config(patched)
    assert cfg.n_clusters == 3
    assert cfg.random_seed == ClusteringV2Config().random_seed  # untouched default


# ---------------------------------------------------------------------------
# Fit / predict / persistence
# ---------------------------------------------------------------------------

def test_fit_kmeans_produces_one_row_per_input_day():
    df = _synthetic_daily_features(40)
    cfg = ClusteringV2Config(n_clusters=3)
    model, result = fit_kmeans(df, cfg)
    assert len(result) == 40
    assert set(result.columns) == {"date", "cluster_id", "distance_to_centroid"}
    assert result["cluster_id"].nunique() <= 3
    assert (result["distance_to_centroid"] >= 0).all()


def test_fit_kmeans_deterministic_given_seed():
    df = _synthetic_daily_features(40)
    cfg = ClusteringV2Config(n_clusters=3, random_seed=7)
    _, r1 = fit_kmeans(df, cfg)
    _, r2 = fit_kmeans(df, cfg)
    pd.testing.assert_frame_equal(r1, r2)


def test_fit_kmeans_raises_without_date_column():
    df = _synthetic_daily_features(10).drop(columns=["date"])
    with pytest.raises(ValueError, match="date"):
        fit_kmeans(df, ClusteringV2Config())


def test_save_and_load_model_roundtrip_matches_predict(tmp_path):
    df = _synthetic_daily_features(40)
    cfg = ClusteringV2Config(n_clusters=3, model_dir=str(tmp_path))
    model, fit_result = fit_kmeans(df, cfg)
    save_model(model, cfg.model_dir)

    loaded = load_model(cfg.model_dir)
    predicted = predict_kmeans(df, loaded)

    pd.testing.assert_frame_equal(
        fit_result.reset_index(drop=True), predicted.reset_index(drop=True)
    )


def test_predict_kmeans_raises_on_missing_training_columns():
    df = _synthetic_daily_features(40)
    model, _ = fit_kmeans(df, ClusteringV2Config(n_clusters=2))
    truncated = df.drop(columns=["mean_bg"])
    with pytest.raises(ValueError, match="missing columns"):
        predict_kmeans(truncated, model)


def test_predict_kmeans_single_row_batch_does_not_crash_on_nan():
    """Regression test for the exact failure mode `impute_median`'s
    docstring calls out: a lone new day with a missing feature must not
    propagate NaN into the scaler.
    """
    df = _synthetic_daily_features(40)
    model, _ = fit_kmeans(df, ClusteringV2Config(n_clusters=2))
    one_day = df.iloc[[0]].copy()
    one_day.loc[:, "mean_bg"] = np.nan
    result = predict_kmeans(one_day, model)
    assert len(result) == 1
    assert np.isfinite(result["distance_to_centroid"].iloc[0])


# ---------------------------------------------------------------------------
# Validation: elbow / silhouette
# ---------------------------------------------------------------------------

def test_elbow_curve_inertia_non_increasing_in_k():
    df = _synthetic_daily_features(60, seed=1)
    curve = elbow_curve(df, range(2, 8))
    inertias = curve.sort_values("k")["inertia"].to_numpy()
    assert (np.diff(inertias) <= 1e-9).all()  # non-increasing, allow fp noise


def test_silhouette_curve_scores_in_valid_range():
    df = _synthetic_daily_features(60, seed=1)
    curve = silhouette_curve(df, range(2, 6))
    assert not curve.empty
    assert (curve["silhouette"] >= -1).all() and (curve["silhouette"] <= 1).all()


def test_silhouette_curve_skips_k_too_large_for_n_samples():
    df = _synthetic_daily_features(5, seed=1)
    curve = silhouette_curve(df, range(2, 10))
    assert (curve["k"] < 5).all()


# ---------------------------------------------------------------------------
# Validation: stability + cross-method agreement — separated blobs vs. noise
# ---------------------------------------------------------------------------

def test_stability_ari_high_on_well_separated_blobs():
    df = _separated_blobs(n_per_blob=40, seed=2)
    result = stability_ari(df, k=2, n_resamples=10, block_size=8, random_seed=2)
    assert result["mean_ari"] > 0.9


def test_stability_ari_low_on_pure_noise():
    df = _synthetic_daily_features(120, seed=3)  # i.i.d. random, no real cluster structure
    result = stability_ari(df, k=4, n_resamples=10, block_size=8, random_seed=3)
    # Noise won't be perfectly 0 (k-means always finds *some* split) but
    # should be well below the near-1.0 seen on genuinely separated data.
    assert result["mean_ari"] < 0.7


def test_stability_ari_requires_minimum_rows():
    df = _synthetic_daily_features(5, seed=1)
    with pytest.raises(ValueError, match="at least"):
        stability_ari(df, k=2, block_size=14)


def test_cross_method_agreement_high_on_well_separated_blobs():
    df = _separated_blobs(n_per_blob=40, seed=4)
    agreement = cross_method_agreement(df, k=2, random_seed=4)
    assert agreement["kmeans_vs_ward"] > 0.9
    assert agreement["kmeans_vs_gmm"] > 0.9
    assert agreement["ward_vs_gmm"] > 0.9


def test_hierarchical_and_gmm_labels_shapes():
    df = _separated_blobs(n_per_blob=20, seed=5)
    ward = hierarchical_labels(df, k=2)
    assert len(ward) == 40
    assert len(set(ward)) == 2

    labels, probs = gmm_labels(df, k=2, random_seed=5)
    assert len(labels) == 40
    assert probs.shape == (40, 2)
    np.testing.assert_allclose(probs.sum(axis=1), 1.0, atol=1e-6)


# ---------------------------------------------------------------------------
# Interpretation
# ---------------------------------------------------------------------------

def test_cluster_profiles_and_naming_separate_blobs_cleanly():
    df = _separated_blobs(n_per_blob=30, seed=6)
    model, assignments = fit_kmeans(df, ClusteringV2Config(n_clusters=2, random_seed=6))
    profiles = cluster_profiles(df, assignments)

    assert len(profiles) == 2
    assert "n_days" in profiles.columns
    assert profiles["n_days"].sum() == 60
    # The two blobs are centered 20 apart with sigma=1 on every feature, so
    # every feature's z-score should differ sharply (large magnitude) by
    # cluster, and the higher-mean blob's cluster should show positive z
    # on (at least) most features.
    row0, row1 = profiles.iloc[0], profiles.iloc[1]
    diffs = (row0[list(SELECTED_FEATURES)] - row1[list(SELECTED_FEATURES)]).abs()
    assert (diffs > 1.0).all()

    names = name_clusters(profiles)
    assert set(names) == {0, 1}
    assert all(isinstance(v, str) and v for v in names.values())


def test_name_clusters_baseline_when_no_feature_stands_out():
    profiles = pd.DataFrame(
        {col: [0.01, -0.01] for col in SELECTED_FEATURES}, index=[0, 1]
    )
    profiles["n_days"] = [10, 10]
    names = name_clusters(profiles, z_threshold=0.5)
    assert names[0] == "baseline"
    assert names[1] == "baseline"
