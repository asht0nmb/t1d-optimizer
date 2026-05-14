"""KMeans clustering over daily features (plan §2.5).

``cluster_days(features_df, config, retrain=False)`` fits or loads a
``StandardScaler`` + ``KMeans`` pipeline against the one-row-per-day
feature matrix produced by :mod:`detection.features` and returns a
DataFrame of ``date``, ``cluster_id``, and ``distance_to_centroid``.

Deterministic via ``config.clustering.random_seed``. The fitted scaler
and kmeans are pickled to ``config.clustering.model_dir`` so subsequent
runs can ``transform`` + ``predict`` without reshuffling cluster ids.

Documented choices (plan left these open):

* **No saved model, ``retrain=False``.** We fit-and-warn rather than
  raise: the first time the detection engine runs on a user's machine
  there is nothing to load, and silently falling back to a fit keeps
  the CLI usable. The warning names the model_dir so callers can tell
  they're implicitly training.
* **NaN imputation.** Per-batch column median, computed fresh at every
  call (train and predict). Columns actually imputed are logged at
  WARNING level so a degraded feature frame is visible. An all-NaN
  column falls back to ``0.0`` to avoid propagating NaNs into the
  scaler.
* **Feature column ordering.** The training column list is persisted
  as ``features_v1.json`` alongside the two pickles. Predict reorders
  the caller's DataFrame to match the training order, so callers can
  pass columns in any order as long as the set is a superset.
* **Empty input.** An empty ``features_df`` returns an empty DataFrame
  with the output schema instead of raising; the CLI can safely call
  this on a date range that happens to contain no days.
"""

from __future__ import annotations

import json
import logging
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from detection.config import AppConfig

__all__ = ["cluster_days"]

_KMEANS_FILENAME = "kmeans_v1.pkl"
_SCALER_FILENAME = "scaler_v1.pkl"
_FEATURES_FILENAME = "features_v1.json"
_N_INIT = 10
_DATE_COL = "date"
_OUTPUT_COLUMNS = ("date", "cluster_id", "distance_to_centroid")

logger = logging.getLogger(__name__)


def cluster_days(
    features_df: pd.DataFrame,
    config: AppConfig,
    retrain: bool = False,
) -> pd.DataFrame:
    """Assign each row of ``features_df`` to a KMeans cluster.

    Args:
        features_df: One row per day; must contain a ``date`` column and
            at least one numeric feature column.
        config: Application config; ``clustering.n_clusters``,
            ``clustering.random_seed``, and ``clustering.model_dir`` are
            consumed.
        retrain: When ``True``, always fit a fresh pipeline (overwriting
            any saved pickles). When ``False`` and a saved pipeline
            exists, load and predict. When ``False`` and no pipeline
            exists, fit-and-warn.

    Returns:
        DataFrame with columns ``date``, ``cluster_id`` (int), and
        ``distance_to_centroid`` (float, Euclidean distance in the
        scaled feature space to the assigned centroid).
    """
    if _DATE_COL not in features_df.columns:
        raise ValueError(f"features_df missing required column {_DATE_COL!r}")

    model_dir = Path(config.clustering.model_dir)
    kmeans_path = model_dir / _KMEANS_FILENAME
    scaler_path = model_dir / _SCALER_FILENAME
    features_path = model_dir / _FEATURES_FILENAME

    if features_df.empty:
        return _empty_output(features_df[_DATE_COL])

    dates = features_df[_DATE_COL].reset_index(drop=True)
    feature_matrix = features_df.drop(columns=[_DATE_COL]).reset_index(drop=True)

    have_saved = (
        kmeans_path.exists() and scaler_path.exists() and features_path.exists()
    )
    should_fit = retrain or not have_saved

    if not retrain and not have_saved:
        logger.warning(
            "No saved clustering model found at %s; fitting a fresh model "
            "instead of loading a saved one.",
            model_dir,
        )

    if should_fit:
        columns = list(feature_matrix.columns)
        X_filled = _impute_median(feature_matrix)
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X_filled.to_numpy())
        kmeans = KMeans(
            n_clusters=config.clustering.n_clusters,
            random_state=config.clustering.random_seed,
            n_init=_N_INIT,
        )
        labels = kmeans.fit_predict(X_scaled)

        model_dir.mkdir(parents=True, exist_ok=True)
        with open(scaler_path, "wb") as f:
            pickle.dump(scaler, f)
        with open(kmeans_path, "wb") as f:
            pickle.dump(kmeans, f)
        with open(features_path, "w") as f:
            json.dump(columns, f)
    else:
        with open(scaler_path, "rb") as f:
            scaler = pickle.load(f)
        with open(kmeans_path, "rb") as f:
            kmeans = pickle.load(f)
        with open(features_path) as f:
            columns = json.load(f)

        missing = [c for c in columns if c not in feature_matrix.columns]
        if missing:
            raise ValueError(
                f"features_df missing columns used at train time: {missing}"
            )
        feature_matrix = feature_matrix.loc[:, columns]
        X_filled = _impute_median(feature_matrix)
        X_scaled = scaler.transform(X_filled.to_numpy())
        labels = kmeans.predict(X_scaled)

    centroids = kmeans.cluster_centers_[labels]
    distances = np.linalg.norm(X_scaled - centroids, axis=1)

    return pd.DataFrame(
        {
            "date": dates,
            "cluster_id": labels.astype(np.int64),
            "distance_to_centroid": distances.astype(np.float64),
        }
    )


def _impute_median(df: pd.DataFrame) -> pd.DataFrame:
    """Fill NaNs with the per-column median; log columns touched.

    Non-numeric columns are coerced via ``pd.to_numeric(errors='coerce')``.
    An all-NaN column falls back to ``0.0`` so downstream sklearn never
    sees NaN.
    """
    out = df.copy()
    imputed: list[str] = []
    for col in out.columns:
        series = pd.to_numeric(out[col], errors="coerce")
        if series.isna().any():
            median = series.median()
            if pd.isna(median):
                median = 0.0
            series = series.fillna(median)
            imputed.append(col)
        out[col] = series
    if imputed:
        logger.warning(
            "Imputed NaN values with per-batch median for columns: %s",
            imputed,
        )
    return out


def _empty_output(dates: pd.Series) -> pd.DataFrame:
    """Return an empty DataFrame with the canonical output schema."""
    return pd.DataFrame(
        {
            "date": pd.Series([], dtype=dates.dtype if dates.size else "object"),
            "cluster_id": pd.Series([], dtype=np.int64),
            "distance_to_centroid": pd.Series([], dtype=np.float64),
        }
    )
