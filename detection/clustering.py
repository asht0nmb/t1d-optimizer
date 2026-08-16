"""v2 daily-pattern clustering over `detection.features.daily_features` output.

*** RESEARCH / EXPLORATION MODULE. Not called by any production surface
(the live Telegram loop, the web dashboard, the nightly sync). See
`docs/ml-notes/clustering.md` for the full pedagogical write-up this module
is the code half of; this docstring and the inline comments below explain
the *why* at each decision point so the code stands on its own too. ***

--------------------------------------------------------------------------
WHY CLUSTERING, AND WHY K-MEANS
--------------------------------------------------------------------------
The question this module answers: "are there a handful of recurring *kinds*
of days in this person's data, distinguishable by their glucose/insulin
shape — not by date, but by behavior?" That's an unsupervised problem (no
labels exist for "day type") which is exactly what clustering is for, as
opposed to classification (needs labels) or regression (needs a numeric
target). Three families of clustering algorithm are relevant here:

* **Centroid-based (k-means).** Assumes clusters are roughly convex,
  similarly-sized blobs in feature space; every point is forced into
  exactly one cluster. Requires choosing k up front. Fast, and — this is
  the deciding factor for a *dashboard* feature, not just a research
  exercise — the output is a **centroid**: a single interpretable "typical
  day" vector per cluster that a human (or a future dashboard "day type"
  badge) can describe in one sentence.
* **Hierarchical (agglomerative / Ward linkage).** Builds a full tree of
  nested clusters bottom-up; doesn't require choosing k in advance, and the
  dendrogram is genuinely useful for *seeing* how many "natural" groups the
  data supports before committing to k. No centroid, though — a leaf
  cluster is defined only by "which points are in it," which is a weaker
  answer to "what does a typical day in this cluster look like."
* **Density/soft (Gaussian Mixture Models).** Like k-means but clusters can
  be elliptical (own covariance per cluster) and assignment is
  *probabilistic* (a day can be 60% "stable day," 40% "rough day") rather
  than forced hard membership. More flexible, more parameters to overfit
  with ~400-2000 days of data.

This module treats **k-means as the primary/production-facing algorithm**
(matches the config shape that already existed — `clustering.method:
kmeans` — and gives centroids), but implements Ward hierarchical and GMM
alongside it purely as **validation tools**: if k-means, Ward, and GMM
independently converge on similar groupings of the same days, that's much
stronger evidence the structure is real than any one method's internal
score (see `AGREEMENT VS GROUND TRUTH` below). See `hierarchical_labels`,
`gmm_labels`, and `cross_method_agreement`.

--------------------------------------------------------------------------
FEATURE SELECTION — why 14 of the 16 `daily_features` columns
--------------------------------------------------------------------------
K-means (and Ward, and GMM) all operate on Euclidean distance in feature
space. That distance implicitly treats every feature as an independent
axis contributing equally (after scaling) to "how different are these two
days." Two problems with `daily_features`'s raw 16 numeric columns break
that assumption, found by computing the correlation matrix on this user's
own 417 coverage-filtered days (see `docs/ml-notes/clustering.md` for the
full matrix; the two structural findings below are exact, not estimated):

1. `time_below_70 + tir_70_180 + time_above_180 + time_above_250 == 1.0`
   to floating-point precision, by construction (`detection/features.py`'s
   `_cgm_features` computes all four as fractions of the same partition of
   the day). Feeding all four into Euclidean distance double-counts this
   one axis of "how was my day distributed across BG bands" — geometrically,
   the four points live on a 3-dimensional hyperplane inside 4-D space, so
   the 4th dimension contributes only numerical noise, not information.
   **Fix: drop `time_above_180`** (the least clinically distinct of the
   four — "moderately high, not severely high" — the other three
   [hypoglycemia / in-range / severe hyperglycemia] are the clinically
   load-bearing edges and their sum plus the dropped one still fully
   determines the dropped one, so no information is lost).

2. `cv_bg = std_bg / mean_bg` is *exactly* determined by two features this
   module already keeps (`std_bg`, `mean_bg`), and empirically correlated
   0.87 with `std_bg` alone on this data — most of what `cv_bg` says is
   already said by `std_bg`. **Fix: drop `cv_bg`**, keep `std_bg` and
   `mean_bg` (both independently interpretable — "how variable" and "how
   high on average" are different clinical questions, worth keeping
   separately rather than collapsing into their ratio).

`mean_bg` itself correlates strongly with the TIR bands (~-0.8 with
`tir_70_180`, ~0.87 with `time_above_250` in this data) but *not* exactly —
two days can have the same mean with very different distributions (one
tight around 170, one bimodal between 90 and 250). That's a real,
non-redundant axis of variation, so `mean_bg` is kept despite the
correlation.

The result is `SELECTED_FEATURES` below: 14 columns, chosen by removing
exact/near-exact linear dependence, not by guessing. This is the single
biggest lever on cluster geometry in the whole pipeline — see the module
docstring's cross-reference to the notes file for a worked "what changes
if you don't do this" comparison.

--------------------------------------------------------------------------
THE COVERAGE TRAP
--------------------------------------------------------------------------
A day with a failed/missing CGM sensor for 20 of its 24 hours produces
`daily_features` values computed from ~70 readings instead of ~288. Those
values are not wrong, but they are a much noisier estimate of "what this
day was like" — and worse, low-coverage days share a *specific* feature
signature (near-zero `tir_70_180`/`time_below_70`/`time_above_250` because
there's so little data each band's count rounds toward 0, unusual
`mean_bg`/`std_bg` swings, etc.). K-means will happily group them into
their own "cluster" — which reads as a discovered behavioral pattern but
is actually just "sensor was disconnected." `filter_low_coverage_days`
removes days below `min_coverage_fraction` (default 0.9, i.e. at least
~260 of 288 expected 5-minute readings) *before* fitting. On this user's
548-day default window, that excluded 131 days (24%) — see the notes file
for the coverage histogram; it is not a rare edge case here, it's material.

--------------------------------------------------------------------------
VALIDATION — why "the code runs and produces 5 clusters" is not evidence
--------------------------------------------------------------------------
K-means will produce exactly `n_clusters` non-empty groups for *any* input,
including pure noise — the algorithm cannot report "there is no real
structure here," only "here is your data forced into k buckets." Believing
the clusters are meaningful requires evidence beyond "it ran":

* `elbow_curve` — inertia (within-cluster sum of squared distances) vs k.
  Necessary but weak: inertia is monotonically non-increasing in k by
  construction, so it can only rule out obviously-too-small k, never
  confirm a specific k.
* `silhouette_curve` — for each k, the mean silhouette coefficient (how much
  closer each point is to its own cluster than to the nearest other one,
  in [-1, 1]). Better than inertia because it penalizes over-splitting, but
  a single scalar per k still can't distinguish "genuine structure" from
  "k-means always finds *some* separation, even in a unimodal Gaussian
  blob whose only structure is where the centroids happened to land."
* `stability_ari` — refit k-means on resampled subsets of the *same* data
  and compare the resulting labelings pairwise via Adjusted Rand Index
  (ARI; 1.0 = identical up to cluster-id relabeling, ~0.0 = no better than
  chance agreement). This is the strongest test in this module: if the
  cluster assignments change every time you refit on a slightly different
  sample of the same underlying days, the "clusters" are an artifact of
  this particular random draw, not a property of the person's data.
  **Consecutive days are autocorrelated** (a bad week, a travel stretch, a
  pump-site problem spanning several days all create runs of similar days)
  — plain i.i.d. row resampling would make stability look artificially
  good by frequently keeping whole autocorrelated runs together across
  resamples. `stability_ari` resamples in contiguous **blocks** of days
  (default 14) instead of individual rows, which is the standard fix for
  time-series bootstrap and is deliberately more pessimistic.
* `cross_method_agreement` — ARI between k-means labels and Ward/GMM labels
  on the *same* data. Three different algorithms with different geometric
  assumptions converging on similar groupings is much better evidence of
  real structure than any one method's self-reported score, precisely
  because they can't all share the same failure mode.
* `cluster_profiles` — per-cluster mean z-score of every selected feature,
  plus a naming heuristic. This is the qualitative check: do the numeric
  clusters correspond to something a person would recognize ("the
  high-variability post-site-change days," "the tight, low-carb days")?
  If the profiles are not describable in one sentence each, the
  clustering — however statistically stable — is not yet *useful*.

--------------------------------------------------------------------------
WHAT THIS MODULE DELIBERATELY DOES NOT DO
--------------------------------------------------------------------------
* Does not write cluster assignments back to Supabase. Read-only research
  tooling; wiring cluster labels into a production surface (dashboard,
  Telegram) is a follow-up decision for the repo owner, not this module.
* Does not touch `config/user_config.yaml`. `n_clusters` here is an
  in-code-defaulted, optionally overridable value read from
  `AppConfig.raw.get("clustering_v2", {})` — a *new*, fully optional block
  — specifically so this exploration never edits the existing required
  `clustering:` block (still consumed only by `detection/legacy/*`, per
  `TECHNICAL_SPEC.md`) and so no model-recommended value can accidentally
  become a committed personal threshold. If a chosen k should become
  permanent, that is a human decision the owner makes explicitly, the same
  way `meal_rise_calibration` output is "advisory only."
"""

from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import AgglomerativeClustering, KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

from detection.config import AppConfig

__all__ = [
    "SELECTED_FEATURES",
    "DROPPED_REDUNDANT_FEATURES",
    "ClusteringV2Config",
    "get_clustering_v2_config",
    "filter_low_coverage_days",
    "select_features",
    "impute_median",
    "ClusterModel",
    "fit_kmeans",
    "predict_kmeans",
    "elbow_curve",
    "silhouette_curve",
    "stability_ari",
    "hierarchical_labels",
    "gmm_labels",
    "cross_method_agreement",
    "cluster_profiles",
    "name_clusters",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature selection (see module docstring "FEATURE SELECTION" for the why)
# ---------------------------------------------------------------------------

#: Columns dropped from `daily_features`'s 16 numeric outputs before
#: clustering, and why. Kept as a public constant (not just a comment) so
#: a caller/notebook can introspect the decision, and so a future change to
#: `daily_features`'s schema makes this list visibly stale rather than
#: silently wrong.
DROPPED_REDUNDANT_FEATURES: dict[str, str] = {
    "time_above_180": (
        "Exactly determined by the other 3 TIR bands "
        "(time_below_70 + tir_70_180 + time_above_180 + time_above_250 == 1.0); "
        "dropping it removes the resulting linear dependency without losing "
        "information, since the other three still sum-constrain it."
    ),
    "cv_bg": (
        "Exactly determined by std_bg / mean_bg, both of which are kept "
        "independently; empirically correlated ~0.87 with std_bg alone on "
        "this user's data."
    ),
}

#: The 14 features actually fed to the scaler + clustering algorithms.
#: Order matters only for artifact reproducibility (persisted alongside the
#: fitted model so `predict_kmeans` can realign an arbitrary caller's
#: column order).
SELECTED_FEATURES: tuple[str, ...] = (
    "tir_70_180",
    "time_below_70",
    "time_above_250",
    "mean_bg",
    "std_bg",
    "total_daily_insulin",
    "basal_bolus_ratio",
    "meal_count",
    "total_carbs_g",
    "overnight_dip",
    "mean_postprandial_peak",
    "alarm_count",
    "suspension_minutes",
    "out_of_range_minutes",
)

_DATE_COL = "date"
_EXPECTED_READINGS_PER_DAY = 288  # 5-minute cadence; see build_daily_features_dataset.py

_KMEANS_FILENAME = "kmeans_v2.pkl"
_SCALER_FILENAME = "scaler_v2.pkl"
_META_FILENAME = "clustering_v2_meta.json"
_N_INIT = 10


# ---------------------------------------------------------------------------
# Config (new optional block; see module docstring "WHAT THIS MODULE
# DELIBERATELY DOES NOT DO")
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClusteringV2Config:
    """In-code-defaulted config for the v2 clustering pipeline.

    Deliberately *not* part of `detection.config.AppConfig`'s required
    schema — this is exploration tooling, not a production detection
    threshold. Read from the optional `clustering_v2:` block in
    `config/user_config.yaml` if present (all keys optional; any subset may
    be overridden), falling back to these defaults otherwise. The existing
    `clustering:` block is untouched and keeps meaning "config for
    `detection/legacy/clustering.py`" per `TECHNICAL_SPEC.md`.
    """

    n_clusters: int = 5
    random_seed: int = 42
    min_coverage_fraction: float = 0.9
    model_dir: str = "data/models/v2"


def get_clustering_v2_config(app_config: AppConfig) -> ClusteringV2Config:
    """Read the optional `clustering_v2:` block, defaulting missing keys."""
    block = app_config.raw.get("clustering_v2", {}) or {}
    defaults = ClusteringV2Config()
    return ClusteringV2Config(
        n_clusters=int(block.get("n_clusters", defaults.n_clusters)),
        random_seed=int(block.get("random_seed", defaults.random_seed)),
        min_coverage_fraction=float(
            block.get("min_coverage_fraction", defaults.min_coverage_fraction)
        ),
        model_dir=str(block.get("model_dir", defaults.model_dir)),
    )


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------

def filter_low_coverage_days(
    df: pd.DataFrame,
    min_coverage_fraction: float = 0.9,
    *,
    reading_count_col: str = "cgm_reading_count",
) -> pd.DataFrame:
    """Drop days whose CGM sensor coverage is below threshold.

    See module docstring "THE COVERAGE TRAP." Expects `reading_count_col`
    (raw CGM row count for the day — added by
    `scripts/build_daily_features_dataset.py`, NOT part of
    `daily_features`'s own output) to already be present; callers computing
    features another way must add it themselves before filtering.

    Coverage fraction above 1.0 is possible in this data (observed values
    up to ~1.2 on the owner's real history) — almost certainly duplicate or
    overlapping-sensor-session readings rather than a real >288-reading
    day; the threshold check is a lower bound only (`>=`), so those rows
    pass through unaffected. Flagged here as a known real-data quirk, not
    silently normalized away, so a reader of `docs/ml-notes/clustering.md`
    knows it was seen and not ignored.
    """
    if reading_count_col not in df.columns:
        raise ValueError(
            f"{reading_count_col!r} not found; expected the caller to have "
            f"attached a raw CGM reading count before filtering "
            f"(see scripts/build_daily_features_dataset.py)."
        )
    coverage = df[reading_count_col].astype(float) / _EXPECTED_READINGS_PER_DAY
    kept = df.loc[coverage >= min_coverage_fraction].reset_index(drop=True)
    dropped_n = len(df) - len(kept)
    if dropped_n:
        logger.info(
            "filter_low_coverage_days: dropped %d/%d days below %.0f%% CGM coverage",
            dropped_n, len(df), min_coverage_fraction * 100,
        )
    return kept


def select_features(df: pd.DataFrame) -> pd.DataFrame:
    """Project to `SELECTED_FEATURES`, in canonical order. Raises on missing columns."""
    missing = [c for c in SELECTED_FEATURES if c not in df.columns]
    if missing:
        raise ValueError(f"daily-features frame missing columns: {missing}")
    return df.loc[:, list(SELECTED_FEATURES)].reset_index(drop=True)


def impute_median(
    df: pd.DataFrame, medians: dict[str, float] | None = None
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Fill NaNs with per-column median; return the (possibly newly-fit) medians.

    Difference from `detection/legacy/clustering.py`'s approach (documented
    there as "per-batch column median, computed fresh at every call"): that
    approach silently degenerates on a single-row predict batch — the
    median of one NaN value is NaN, so a lone new day with a missing
    feature gets no imputation at all and NaN reaches the scaler. Here,
    `medians` is an explicit optional argument: pass `None` when fitting
    (computes and returns fresh medians from `df`, meant to be persisted
    alongside the model) and pass the *persisted, training-time* medians
    when scoring new days later, so a batch of 1 still gets sensible
    imputation. An all-NaN column at fit time falls back to 0.0, same as
    legacy.
    """
    out = df.copy()
    fitted: dict[str, float] = {}
    for col in out.columns:
        series = pd.to_numeric(out[col], errors="coerce")
        if medians is None:
            median = series.median()
            if pd.isna(median):
                median = 0.0
            fitted[col] = float(median)
        else:
            fitted[col] = medians.get(col, 0.0)
        if series.isna().any():
            out[col] = series.fillna(fitted[col])
        else:
            out[col] = series
    return out, fitted


# ---------------------------------------------------------------------------
# K-means: fit / predict, with persisted artifacts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClusterModel:
    """Fitted pipeline: scaler + kmeans + the medians/columns used to fit it."""

    scaler: StandardScaler
    kmeans: KMeans
    feature_columns: tuple[str, ...]
    impute_medians: dict[str, float]


def fit_kmeans(
    features_df: pd.DataFrame, config: ClusteringV2Config
) -> tuple[ClusterModel, pd.DataFrame]:
    """Fit StandardScaler + KMeans on `features_df` (already coverage-filtered).

    `features_df` must contain a `date` column plus (at least)
    `SELECTED_FEATURES`; extra columns are ignored. Returns the fitted
    `ClusterModel` and a DataFrame of `date`, `cluster_id`,
    `distance_to_centroid` for the *training* rows (equivalent to calling
    `predict_kmeans` on the same data with the just-fitted model, but
    avoids double work).
    """
    if _DATE_COL not in features_df.columns:
        raise ValueError(f"features_df missing required column {_DATE_COL!r}")
    dates = features_df[_DATE_COL].reset_index(drop=True)
    X = select_features(features_df)
    X_filled, medians = impute_median(X)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_filled.to_numpy())

    kmeans = KMeans(
        n_clusters=config.n_clusters,
        random_state=config.random_seed,
        n_init=_N_INIT,
    )
    labels = kmeans.fit_predict(X_scaled)
    centroids = kmeans.cluster_centers_[labels]
    distances = np.linalg.norm(X_scaled - centroids, axis=1)

    model = ClusterModel(
        scaler=scaler,
        kmeans=kmeans,
        feature_columns=SELECTED_FEATURES,
        impute_medians=medians,
    )
    result = pd.DataFrame(
        {
            "date": dates,
            "cluster_id": labels.astype(np.int64),
            "distance_to_centroid": distances.astype(np.float64),
        }
    )
    return model, result


def save_model(model: ClusterModel, model_dir: str | Path) -> None:
    """Persist scaler + kmeans + metadata to `model_dir` (gitignored).

    Security note: this pickles sklearn objects, mirroring the same
    trusted-local-artifact pattern `detection/legacy/clustering.py` already
    uses. `model_dir` is gitignored, local-only, and never fetched from a
    network location or any other untrusted source — `load_model` below
    only ever reads back a file this process (or an earlier run by the
    same user, on the same machine) wrote. Do not repurpose this pair for
    loading pickles from anywhere else without re-evaluating that
    assumption.
    """
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    with open(model_dir / _SCALER_FILENAME, "wb") as f:
        pickle.dump(model.scaler, f)
    with open(model_dir / _KMEANS_FILENAME, "wb") as f:
        pickle.dump(model.kmeans, f)
    with open(model_dir / _META_FILENAME, "w") as f:
        json.dump(
            {
                "feature_columns": list(model.feature_columns),
                "impute_medians": model.impute_medians,
            },
            f,
            indent=2,
        )


def load_model(model_dir: str | Path) -> ClusterModel:
    """Load a previously `save_model`-ed pipeline."""
    model_dir = Path(model_dir)
    with open(model_dir / _SCALER_FILENAME, "rb") as f:
        scaler = pickle.load(f)
    with open(model_dir / _KMEANS_FILENAME, "rb") as f:
        kmeans = pickle.load(f)
    with open(model_dir / _META_FILENAME) as f:
        meta = json.load(f)
    return ClusterModel(
        scaler=scaler,
        kmeans=kmeans,
        feature_columns=tuple(meta["feature_columns"]),
        impute_medians=meta["impute_medians"],
    )


def predict_kmeans(features_df: pd.DataFrame, model: ClusterModel) -> pd.DataFrame:
    """Assign each row of `features_df` to a cluster of an already-fit `model`.

    Uses the model's *persisted* imputation medians (not medians of
    `features_df` itself) — see `impute_median`'s docstring for why this
    matters for small/single-row batches.
    """
    if _DATE_COL not in features_df.columns:
        raise ValueError(f"features_df missing required column {_DATE_COL!r}")
    dates = features_df[_DATE_COL].reset_index(drop=True)
    missing = [c for c in model.feature_columns if c not in features_df.columns]
    if missing:
        raise ValueError(f"features_df missing columns used at fit time: {missing}")
    X = features_df.loc[:, list(model.feature_columns)].reset_index(drop=True)
    X_filled, _ = impute_median(X, medians=model.impute_medians)
    X_scaled = model.scaler.transform(X_filled.to_numpy())
    labels = model.kmeans.predict(X_scaled)
    centroids = model.kmeans.cluster_centers_[labels]
    distances = np.linalg.norm(X_scaled - centroids, axis=1)
    return pd.DataFrame(
        {
            "date": dates,
            "cluster_id": labels.astype(np.int64),
            "distance_to_centroid": distances.astype(np.float64),
        }
    )


# ---------------------------------------------------------------------------
# Validation (see module docstring "VALIDATION")
# ---------------------------------------------------------------------------

def _scaled_matrix(features_df: pd.DataFrame) -> np.ndarray:
    X = select_features(features_df)
    X_filled, _ = impute_median(X)
    return StandardScaler().fit_transform(X_filled.to_numpy())


def elbow_curve(features_df: pd.DataFrame, k_range: range, random_seed: int = 42) -> pd.DataFrame:
    """Inertia (within-cluster SSE) for each k in `k_range`. See module docstring caveat."""
    X_scaled = _scaled_matrix(features_df)
    rows = []
    for k in k_range:
        km = KMeans(n_clusters=k, random_state=random_seed, n_init=_N_INIT).fit(X_scaled)
        rows.append({"k": k, "inertia": float(km.inertia_)})
    return pd.DataFrame(rows)


def silhouette_curve(features_df: pd.DataFrame, k_range: range, random_seed: int = 42) -> pd.DataFrame:
    """Mean silhouette coefficient for each k in `k_range`. Requires k >= 2 and k < n_samples."""
    X_scaled = _scaled_matrix(features_df)
    rows = []
    for k in k_range:
        if k < 2 or k >= len(X_scaled):
            continue
        km = KMeans(n_clusters=k, random_state=random_seed, n_init=_N_INIT).fit(X_scaled)
        score = silhouette_score(X_scaled, km.labels_)
        rows.append({"k": k, "silhouette": float(score)})
    return pd.DataFrame(rows)


def stability_ari(
    features_df: pd.DataFrame,
    k: int,
    *,
    n_resamples: int = 20,
    block_size: int = 14,
    sample_fraction: float = 0.8,
    random_seed: int = 42,
) -> dict:
    """Block-bootstrap stability of k-means labelings, via pairwise ARI.

    See module docstring's VALIDATION section for why block (not i.i.d. row)
    resampling matters for autocorrelated daily health data. Assumes
    `features_df` is already sorted by date (the caller's responsibility —
    `fit_kmeans`/callers of this module always pass date-sorted frames since
    that's how `build_daily_features_dataset.py` emits them).

    Returns `{"mean_ari": float, "min_ari": float, "pairwise_aris": list[float]}`.
    Adjusted Rand Index: 1.0 = identical clusterings (up to relabeling),
    ~0.0 = agreement no better than random, negative = worse than random.
    """
    X_scaled = _scaled_matrix(features_df)
    n = len(X_scaled)
    if n < block_size * 2:
        raise ValueError(
            f"stability_ari needs at least {block_size * 2} rows for "
            f"block_size={block_size}, got {n}"
        )
    rng = np.random.default_rng(random_seed)
    n_blocks_total = n // block_size
    n_blocks_sample = max(2, int(n_blocks_total * sample_fraction))

    # Each resample draws a *different* (and differently-ordered, differently
    # sized) set of day-blocks, so the fitted labels from two resamples are
    # not directly comparable position-by-position — cluster 0 in resample A
    # and cluster 0 in resample B don't refer to the same subset of days,
    # and even if they did, the two label arrays wouldn't line up row-for-row.
    # The fix: fit k-means on each resample, then use that fitted model to
    # *predict* labels for the one thing every resample has in common — the
    # full original (fixed) dataset. Comparing those full-length prediction
    # vectors index-for-index is a valid apples-to-apples ARI comparison.
    # (This is standard practice for bootstrap cluster-stability analysis —
    # e.g. Fang & Wang 2012 "Selection of the number of clusters via the
    # bootstrap method" uses the same fit-on-resample / predict-on-fixed-set
    # pattern.)
    full_predictions: list[np.ndarray] = []
    for i in range(n_resamples):
        block_starts = rng.choice(n_blocks_total, size=n_blocks_sample, replace=True)
        idx = np.concatenate(
            [np.arange(b * block_size, min((b + 1) * block_size, n)) for b in block_starts]
        )
        X_sample = X_scaled[idx]
        km = KMeans(n_clusters=k, random_state=random_seed + i, n_init=_N_INIT).fit(X_sample)
        full_predictions.append(km.predict(X_scaled))

    pairwise = []
    for i in range(len(full_predictions)):
        for j in range(i + 1, len(full_predictions)):
            pairwise.append(
                float(adjusted_rand_score(full_predictions[i], full_predictions[j]))
            )
    return {
        "mean_ari": float(np.mean(pairwise)),
        "min_ari": float(np.min(pairwise)),
        "pairwise_aris": pairwise,
    }


def hierarchical_labels(features_df: pd.DataFrame, k: int) -> np.ndarray:
    """Ward-linkage agglomerative clustering, forced to k clusters."""
    X_scaled = _scaled_matrix(features_df)
    model = AgglomerativeClustering(n_clusters=k, linkage="ward")
    return model.fit_predict(X_scaled)


def gmm_labels(
    features_df: pd.DataFrame, k: int, random_seed: int = 42
) -> tuple[np.ndarray, np.ndarray]:
    """Gaussian Mixture Model clustering. Returns (hard labels, soft probabilities [n, k])."""
    X_scaled = _scaled_matrix(features_df)
    model = GaussianMixture(n_components=k, random_state=random_seed)
    model.fit(X_scaled)
    labels = model.predict(X_scaled)
    probs = model.predict_proba(X_scaled)
    return labels, probs


def cross_method_agreement(features_df: pd.DataFrame, k: int, random_seed: int = 42) -> dict:
    """Pairwise ARI between k-means, Ward, and GMM labelings of the *same* rows.

    Unlike `stability_ari`, all three methods see the identical (full)
    dataset, so labels are directly comparable index-for-index — this
    measures algorithm-to-algorithm agreement, not resample-to-resample
    agreement.
    """
    X_scaled = _scaled_matrix(features_df)
    km = KMeans(n_clusters=k, random_state=random_seed, n_init=_N_INIT).fit(X_scaled)
    ward = AgglomerativeClustering(n_clusters=k, linkage="ward").fit(X_scaled)
    gmm = GaussianMixture(n_components=k, random_state=random_seed).fit(X_scaled)
    gmm_lab = gmm.predict(X_scaled)
    return {
        "kmeans_vs_ward": float(adjusted_rand_score(km.labels_, ward.labels_)),
        "kmeans_vs_gmm": float(adjusted_rand_score(km.labels_, gmm_lab)),
        "ward_vs_gmm": float(adjusted_rand_score(ward.labels_, gmm_lab)),
    }


# ---------------------------------------------------------------------------
# Interpretation: profiles + naming
# ---------------------------------------------------------------------------

def cluster_profiles(features_df: pd.DataFrame, assignments: pd.DataFrame) -> pd.DataFrame:
    """Per-cluster mean z-score of each selected feature, plus cluster size.

    `assignments` is the output of `fit_kmeans`/`predict_kmeans` (needs
    `date`, `cluster_id`); `features_df` is the same frame `fit_kmeans` was
    given (needs `date` + `SELECTED_FEATURES`). Z-scoring against the whole
    dataset's mean/std (not per-cluster) is what makes the profile
    readable: "cluster 2 runs +1.3 SD above the population's average
    `time_above_250`" is a directly interpretable magnitude; a raw mean in
    each cluster's native units is not comparable across features with
    very different scales (minutes vs. fractions vs. mg/dL).
    """
    X = select_features(features_df)
    dates = features_df[_DATE_COL].reset_index(drop=True)
    X_filled, _ = impute_median(X)
    z = (X_filled - X_filled.mean()) / X_filled.std(ddof=0).replace(0, 1.0)
    z[_DATE_COL] = dates
    merged = z.merge(assignments[[_DATE_COL, "cluster_id"]], on=_DATE_COL)
    profile = merged.groupby("cluster_id")[list(SELECTED_FEATURES)].mean()
    profile["n_days"] = merged.groupby("cluster_id").size()
    return profile


def name_clusters(profiles: pd.DataFrame, z_threshold: float = 0.5) -> dict[int, str]:
    """Heuristic human-readable name per cluster from its top z-score features.

    Not a substitute for a human reading `cluster_profiles` directly — this
    is a starting point/shorthand (e.g. for a dashboard label), built by
    taking each cluster's 1-2 features with |z| >= `z_threshold` of largest
    magnitude and phrasing them as "high X" / "low X". Clusters with no
    feature clearing the threshold are named "baseline" (unremarkable
    relative to the population, on every selected feature).
    """
    _READABLE = {
        "tir_70_180": "time-in-range",
        "time_below_70": "hypoglycemia",
        "time_above_250": "severe hyperglycemia",
        "mean_bg": "average glucose",
        "std_bg": "glucose variability",
        "total_daily_insulin": "total insulin",
        "basal_bolus_ratio": "basal/bolus ratio",
        "meal_count": "meal frequency",
        "total_carbs_g": "carb intake",
        "overnight_dip": "overnight dip",
        "mean_postprandial_peak": "post-meal rise",
        "alarm_count": "pump alarms",
        "suspension_minutes": "insulin suspension",
        "out_of_range_minutes": "CGM signal loss",
    }
    names: dict[int, str] = {}
    for cluster_id, row in profiles.iterrows():
        scores = row[list(SELECTED_FEATURES)]
        top = scores.reindex(scores.abs().sort_values(ascending=False).index)
        top = top[top.abs() >= z_threshold].head(2)
        if top.empty:
            names[int(cluster_id)] = "baseline"
            continue
        parts = [
            f"{'high' if v > 0 else 'low'} {_READABLE.get(f, f)}"
            for f, v in top.items()
        ]
        names[int(cluster_id)] = ", ".join(parts)
    return names
