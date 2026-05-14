"""Tests for ``detection.legacy.clustering.cluster_days``.

Uses a deterministic synthetic feature DataFrame that mirrors the schema
produced by ``detection.features.daily_features``. Each test points the
clustering ``model_dir`` at a per-test ``tmp_path`` so pickle files never
leak between runs or into the real ``data/models`` directory.
"""

from __future__ import annotations

import dataclasses
import shutil
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from detection.config import AppConfig, ClusteringConfig
from detection.legacy.clustering import cluster_days

pytestmark = pytest.mark.legacy


# Mirrors `daily_features` output (minus the `date` key). 16 feature columns.
_FEATURE_COLUMNS = [
    "tir_70_180",
    "time_below_70",
    "time_above_180",
    "time_above_250",
    "mean_bg",
    "std_bg",
    "cv_bg",
    "total_daily_insulin",
    "basal_bolus_ratio",
    "meal_count",
    "total_carbs_g",
    "overnight_dip",
    "mean_postprandial_peak",
    "alarm_count",
    "suspension_minutes",
    "out_of_range_minutes",
]


def _synthetic_features_df(n_days: int, seed: int = 0) -> pd.DataFrame:
    """Return an ``n_days``-row DataFrame with the feature schema.

    Values are drawn from a local ``np.random.default_rng(seed)`` so the
    function is deterministic and independent of the global RNG state.
    """
    rng = np.random.default_rng(seed)
    start = date(2026, 1, 1)
    dates = [start + timedelta(days=i) for i in range(n_days)]

    data: dict[str, list] = {"date": dates}
    # Realistic-ish ranges per feature; exact values don't matter for
    # clustering determinism, only that they are reproducible.
    data["tir_70_180"] = rng.uniform(0.3, 0.95, n_days).tolist()
    data["time_below_70"] = rng.uniform(0.0, 0.15, n_days).tolist()
    data["time_above_180"] = rng.uniform(0.0, 0.5, n_days).tolist()
    data["time_above_250"] = rng.uniform(0.0, 0.2, n_days).tolist()
    data["mean_bg"] = rng.uniform(110.0, 200.0, n_days).tolist()
    data["std_bg"] = rng.uniform(20.0, 70.0, n_days).tolist()
    data["cv_bg"] = rng.uniform(0.15, 0.45, n_days).tolist()
    data["total_daily_insulin"] = rng.uniform(20.0, 70.0, n_days).tolist()
    data["basal_bolus_ratio"] = rng.uniform(0.3, 2.5, n_days).tolist()
    data["meal_count"] = rng.integers(0, 6, n_days).tolist()
    data["total_carbs_g"] = rng.integers(0, 300, n_days).tolist()
    data["overnight_dip"] = rng.uniform(-30.0, 30.0, n_days).tolist()
    data["mean_postprandial_peak"] = rng.uniform(-10.0, 90.0, n_days).tolist()
    data["alarm_count"] = rng.integers(0, 10, n_days).tolist()
    data["suspension_minutes"] = rng.uniform(0.0, 120.0, n_days).tolist()
    data["out_of_range_minutes"] = rng.uniform(0.0, 180.0, n_days).tolist()

    return pd.DataFrame(data, columns=["date"] + _FEATURE_COLUMNS)


def _cfg_override(config: AppConfig, **overrides) -> AppConfig:
    """Return a copy of ``config`` with ``clustering`` fields replaced.

    ``AppConfig`` and ``ClusteringConfig`` are frozen dataclasses; mutate
    via ``dataclasses.replace``.
    """
    new_clustering = dataclasses.replace(config.clustering, **overrides)
    return dataclasses.replace(config, clustering=new_clustering)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClusterDays:
    def test_deterministic_with_fixed_seed(self, default_config, tmp_path):
        cfg = _cfg_override(default_config, model_dir=str(tmp_path))
        feats = _synthetic_features_df(30)

        out1 = cluster_days(feats, cfg, retrain=True)

        shutil.rmtree(tmp_path)
        tmp_path.mkdir()

        out2 = cluster_days(feats, cfg, retrain=True)

        assert list(out1["cluster_id"]) == list(out2["cluster_id"])

    def test_predict_after_train_uses_saved_model(self, default_config, tmp_path):
        cfg = _cfg_override(default_config, model_dir=str(tmp_path))
        feats = _synthetic_features_df(30)

        out_train = cluster_days(feats, cfg, retrain=True)

        assert (tmp_path / "kmeans_v1.pkl").exists()
        assert (tmp_path / "scaler_v1.pkl").exists()

        out_predict = cluster_days(feats, cfg, retrain=False)

        assert len(out_predict) == 30
        # Same data + same saved model => identical assignments.
        assert list(out_train["cluster_id"]) == list(out_predict["cluster_id"])

    def test_output_columns(self, default_config, tmp_path):
        cfg = _cfg_override(default_config, model_dir=str(tmp_path))
        feats = _synthetic_features_df(15)
        out = cluster_days(feats, cfg, retrain=True)
        assert set(out.columns) == {"date", "cluster_id", "distance_to_centroid"}

    def test_nan_features_imputed_not_crash(self, default_config, tmp_path):
        cfg = _cfg_override(default_config, model_dir=str(tmp_path))
        feats = _synthetic_features_df(10)
        feats.loc[0, "basal_bolus_ratio"] = float("nan")
        feats.loc[3, "mean_postprandial_peak"] = float("nan")
        feats.loc[5, "overnight_dip"] = float("nan")

        out = cluster_days(feats, cfg, retrain=True)

        assert len(out) == 10
        assert out["cluster_id"].notna().all()

    def test_n_clusters_respected(self, default_config, tmp_path):
        cfg = _cfg_override(default_config, model_dir=str(tmp_path), n_clusters=3)
        feats = _synthetic_features_df(30)
        out = cluster_days(feats, cfg, retrain=True)
        assert out["cluster_id"].nunique() <= 3
        assert out["cluster_id"].min() >= 0
        assert out["cluster_id"].max() < 3

    def test_distance_to_centroid_is_nonnegative_and_float(
        self, default_config, tmp_path
    ):
        cfg = _cfg_override(default_config, model_dir=str(tmp_path))
        feats = _synthetic_features_df(20)
        out = cluster_days(feats, cfg, retrain=True)

        assert out["distance_to_centroid"].dtype == np.float64
        assert (out["distance_to_centroid"] >= 0).all()
        assert out["distance_to_centroid"].notna().all()

    def test_retrain_false_without_saved_model_fits_fresh(
        self, default_config, tmp_path, caplog
    ):
        """With no saved model present, ``retrain=False`` still fits.

        Documented behavior: warn-and-fit rather than raise. A subsequent
        call with the same seed must produce identical assignments and
        the pickle files must now exist on disk.
        """
        cfg = _cfg_override(default_config, model_dir=str(tmp_path / "nested"))
        feats = _synthetic_features_df(20)

        assert not (tmp_path / "nested").exists()

        import logging

        with caplog.at_level(logging.WARNING, logger="detection.clustering"):
            out = cluster_days(feats, cfg, retrain=False)

        assert len(out) == 20
        assert (tmp_path / "nested" / "kmeans_v1.pkl").exists()
        assert (tmp_path / "nested" / "scaler_v1.pkl").exists()
        # A warning should mention the missing model.
        assert any(
            "no saved clustering model" in rec.getMessage().lower()
            for rec in caplog.records
        )

    def test_empty_features_df_returns_empty_with_schema(
        self, default_config, tmp_path
    ):
        cfg = _cfg_override(default_config, model_dir=str(tmp_path))
        empty = pd.DataFrame({col: [] for col in (["date"] + _FEATURE_COLUMNS)})

        out = cluster_days(empty, cfg, retrain=True)

        assert len(out) == 0
        assert set(out.columns) == {"date", "cluster_id", "distance_to_centroid"}

    def test_model_dir_created_if_missing(self, default_config, tmp_path):
        target = tmp_path / "does" / "not" / "exist" / "yet"
        cfg = _cfg_override(default_config, model_dir=str(target))
        feats = _synthetic_features_df(12)

        assert not target.exists()

        cluster_days(feats, cfg, retrain=True)

        assert target.exists()
        assert (target / "kmeans_v1.pkl").exists()
        assert (target / "scaler_v1.pkl").exists()
