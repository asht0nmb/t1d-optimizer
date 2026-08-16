# Clustering daily glucose/insulin patterns — a learning write-up

**Status:** exploratory, branch `research/ml-clustering-and-models`, not merged. Code lives in `detection/clustering.py`; the loader is `scripts/build_daily_features_dataset.py`. Read those files' docstrings alongside this — the code has the same explanations inline, this file walks through the *process* and the *real numbers* from running it against the owner's own data.

## The question

"Are there a handful of recurring *kinds* of days in this person's glucose/insulin data — not by date, but by behavioral/physiological shape?" That's an unsupervised-learning question: no one has labeled any day as "type A" or "type B," so the task is to look for structure, not to predict a known answer.

## Step 1 — get the data into a shape that can be clustered

Clustering algorithms want **one row per thing you're clustering, with numeric columns**. `detection/features.py`'s `daily_features()` already computes exactly that shape for a single day — it existed before this exploration, as part of the v2 detection foundation (see `TECHNICAL_SPEC.md`). What didn't exist was a way to run it over *hundreds* of days and pull the underlying CGM/insulin/pump-event data from Supabase (the only place the data lives now — no local parquet files in this environment). `scripts/build_daily_features_dataset.py` is that loader: query Supabase for a date range, loop every calendar day, call `daily_features()` on each, stack into a DataFrame.

Running it against the full available window gave real numbers immediately:

```
uv run python scripts/build_daily_features_dataset.py
# 548 days, 2025-02-14 .. 2026-08-16
```

**Why 18 months, not "all 5 years"?** The owner's Supabase CGM history goes back to late 2021. Therapy settings (basal rates, carb ratios, correction factors) get retuned by an endocrinologist over years — a 2022 day and a 2026 day can look different in the feature space for reasons that have nothing to do with recurring *behavioral* patterns, which is what we're trying to find. Mixing regimes would blur real structure with regime drift. Trailing 18 months is a bias/variance call: long enough to give even a 5-cluster split ~80+ days per cluster on average, short enough to plausibly reflect one regimen era. `--start`/`--end` override this if you want to explore the tradeoff yourself.

## Step 2 — a real, unglamorous data-quality finding

Before touching an algorithm, look at the data. Two things fell out immediately:

**Coverage varies a lot.** `cgm_reading_count` per day (added by the loader, not part of `daily_features`'s own 16-feature output) ranged from 0 to 347 against an expected ~288 (5-minute cadence × 24h). Bucketed:

| coverage | days |
|---|---|
| 0–10% | 3 |
| 10–50% | 22 |
| 50–80% | 30 |
| 80–95% | 31 |
| 95%+ | 371 (missing the rest to 548 due to `>1.0` bucket) |

That's 24% of days below 95% coverage — not a rare edge case. **Why this matters for clustering specifically**: a day with 20% sensor coverage produces `daily_features` values computed from a tiny, noisy sample. Worse, low-coverage days share a *specific* numeric signature (near-zero time-in-range/below-70/above-250 fractions, because with so few readings each band's count rounds toward zero) — which k-means will happily group into its own "cluster." That reads as a discovered pattern but is actually "the sensor was disconnected that day." `detection/clustering.py`'s `filter_low_coverage_days()` drops anything below 90% coverage before fitting — on this window, that's 131 of 548 days (24%) excluded. That's a real, material filter, not a formality.

**Coverage can exceed 100%.** A handful of days showed `cgm_coverage_fraction` up to ~1.20 — more than 288 readings in 24 hours. That shouldn't happen at a fixed 5-minute cadence and is almost certainly duplicate/overlapping sensor-session rows rather than a real physical reading rate. I flagged this in the code (`filter_low_coverage_days`'s docstring) rather than silently clipping it — it's a genuine data-quality quirk worth knowing about, not something to paper over. It doesn't affect the coverage filter (which only checks a lower bound), but it's the kind of thing worth investigating later (a query for `cgm_reading_count > 288` narrowed to specific dates would find the exact duplicate rows).

## Step 3 — feature selection: the correlation matrix decides, not intuition

`daily_features()` emits 16 numeric columns. Feeding all 16 into a distance-based algorithm (k-means measures similarity as literal Euclidean distance in feature space, after scaling) implicitly treats every column as an independent axis of "how different are two days." Two groups of columns break that assumption — and I found this by actually computing the correlation matrix on the 417 coverage-filtered real days, not by guessing:

**1. The four TIR bands sum to exactly 1.0.** `time_below_70 + tir_70_180 + time_above_180 + time_above_250` — computed check on real data:

```
count    417.0
mean     1.000000
std      6.44e-17     ← floating-point zero
```

That's not a correlation, it's an identity: `_cgm_features()` in `detection/features.py` computes all four as fractions of the same day, so any three fully determine the fourth. Feeding all four into Euclidean distance double-counts one axis of "how was this day's BG distributed." **Fix: drop `time_above_180`** (the least clinically distinct of the four — "moderately high, not severely high" — dropping it keeps the three clinically load-bearing edges: hypoglycemia, in-range, severe hyperglycemia; no information is lost since the three remaining still constrain the fourth).

**2. `cv_bg` is derived from two other kept features.** `cv_bg = std_bg / mean_bg`, and empirically `cv_bg` correlates 0.87 with `std_bg` alone on this data (0.26 with `mean_bg`). Keeping all three means the "variability" axis is represented twice. **Fix: drop `cv_bg`**, keep `std_bg` and `mean_bg` independently — they answer different clinical questions ("how variable" vs. "how high on average") and shouldn't be collapsed into their ratio.

**What I decided to keep despite correlation**: `mean_bg` correlates strongly with the TIR bands (-0.81 with `tir_70_180`, 0.87 with `time_above_250`) but *not* exactly — two days can share a mean with very different distributions (tight around 170 vs. bimodal between 90 and 250). That's real, non-redundant information, so `mean_bg` stays.

Result: **14 of the 16 features survive**, encoded as `SELECTED_FEATURES` in `detection/clustering.py`, with the rationale as a public constant (`DROPPED_REDUNDANT_FEATURES`) — not just a code comment — so it's introspectable and so a future change to `daily_features`'s schema makes the decision visibly stale instead of silently wrong.

**Takeaway for anyone learning this**: this correlation-matrix step is the single biggest lever on the whole pipeline's output. Skipping it doesn't crash anything — k-means runs fine on 16 collinear features — it just produces a subtly wrong geometry (the compositional TIR bands and the variability trio get systematically overweighted relative to insulin/meal/pump-event features), and nothing about the code would tell you that. This is exactly the kind of bug that "the code runs and produces clusters" hides.

## Step 4 — fit, and immediately be skeptical of the result

`fit_kmeans()` in `detection/clustering.py` is StandardScaler + `sklearn.cluster.KMeans`, seeded via `random_seed` for reproducibility — the same basic recipe as the quarantined `detection/legacy/clustering.py` (whose persisted-artifact pattern was worth keeping; its `n_clusters`/algorithm choice is what this exploration re-derives from data instead of inheriting).

**Why k-means as the primary algorithm** (over hierarchical/Ward or a Gaussian Mixture Model, both of which this module also implements): k-means gives a **centroid** — a single numeric "typical day" vector per cluster — which is what makes a cluster describable in one sentence ("high glucose day," "low glucose day") and is what a future dashboard "day type" badge would need. Ward clustering doesn't require choosing k up front and produces a genuinely useful dendrogram, but a leaf cluster there is defined only by "which points are in it," with no analogous centroid. GMM adds soft/probabilistic assignment (a day can be 60% one type, 40% another) at the cost of more parameters to overfit with a dataset this size (~400 days). This module implements all three but uses Ward and GMM as **validation tools**, not competing production candidates — see below.

**The key mistake to avoid: believing the output because the code ran.** K-means will produce exactly `n_clusters` non-empty groups for *any* input, including pure random noise. It has no way to say "there's no real structure here" — only "here's your data forced into k buckets." So before believing any clustering is meaningful, I ran four separate checks against the real, coverage-filtered 417-day dataset:

### Elbow curve (weak evidence, but a floor)

```
k=2  inertia=4594.7
k=3  inertia=4197.3
k=4  inertia=3880.4
k=5  inertia=3609.9
k=6  inertia=3358.9
k=7  inertia=3177.4
k=8  inertia=3025.4
k=9  inertia=2863.8
```

Monotonically decreasing, as it must be by construction (adding a cluster can only reduce or preserve total within-cluster distance). No sharp "elbow" is visible — the curve is smoothly declining, which by itself already suggests there isn't one dominant natural k. Elbow alone can only rule out an obviously-too-small k, never confirm a specific one — it's necessary, not sufficient.

### Silhouette curve (better, still not sufficient)

```
k=2  silhouette=0.205   ← best
k=3  silhouette=0.129
k=4  silhouette=0.148
k=5  silhouette=0.145
k=6  silhouette=0.128
k=7  silhouette=0.128
k=8  silhouette=0.129
k=9  silhouette=0.130
```

**k=2 is the clear winner by silhouette**, and every k≥3 sits in a flat, mediocre 0.13–0.15 band — none of them separate meaningfully better than k=2, and 0.13–0.20 is itself a modest silhouette (1.0 = perfectly separated clusters; 0 = no better than random split; this data never gets much above 0.2). This is already an important, humbling finding on its own: **the data does not statistically support the old `detection/legacy/clustering.py` config default of `n_clusters: 5`.** That default was never data-derived — it was a placeholder. Silhouette says the honest answer here is closer to "two broad regimes" than "five."

### Stability (the strongest test in this module, and where it gets even more honest)

`stability_ari()` refits k-means on repeated **block-bootstrap** resamples of the same data (contiguous runs of 14 days, not individual rows — see the code docstring for why: individual-day resampling would be optimistic, because consecutive days are autocorrelated — a bad week, a travel stretch, a pump-site problem all create runs of similar days that a naive row-shuffle would keep together too often), then compares the resulting labelings via **Adjusted Rand Index** (1.0 = identical up to relabeling, ~0.0 = no better than chance).

```
k=2  mean ARI=0.791  (min 0.541, 100 pairwise comparisons over 15 resamples)
k=3  mean ARI=0.521  (min 0.223)
k=4  mean ARI=0.398  (min 0.224)
k=5  mean ARI=0.389  (min 0.197)
```

k=2 is reasonably stable — refitting on different day-blocks mostly recovers the same two-way split (mean ARI 0.79). **k=3 through k=5 are not** — mean ARI drops to 0.39–0.52, meaning a meaningful fraction of days flip cluster membership just from resampling the same underlying data. If I had picked k=5 (the old legacy default) without running this check, I would have shipped a "5-pattern" clustering that isn't actually a stable property of the data — it would just be *a* particular partition k-means happened to find on one fit, not something you'd get again on a slightly different sample.

### Cross-method agreement (an honest ceiling on how much to trust even k=2)

`cross_method_agreement()` fits k-means, Ward, and GMM on the **same** 417 days and compares all three labelings pairwise:

```
k=2:  kmeans_vs_ward=0.352   kmeans_vs_gmm=0.599   ward_vs_gmm=0.349
```

This is the most humbling number in the whole exercise. Even at k=2 — the *best-supported* k by every other metric — three different algorithms only moderately agree with each other (0.35–0.60 ARI, well short of the near-1.0 you'd see on a synthetic dataset with two truly separated blobs — see `tests/test_detection_clustering.py`'s synthetic-blob tests, which do hit >0.9 on data engineered to have obvious structure). **Honest conclusion: this data does contain a real, weak, single-axis split — but it is a soft gradient, not two sharply distinct day-archetypes.** k-means, Ward, and GMM each carve that gradient slightly differently because there isn't a hard boundary between "clusters" to agree on.

## Step 5 — what the k=2 clusters actually are

`cluster_profiles()` computes each cluster's mean z-score per feature (relative to the whole dataset's mean/std, which is what makes "cluster 0 runs +0.87 SD above average on `time_above_250`" a directly comparable magnitude across features with very different native units):

| feature | cluster 0 (n=174) | cluster 1 (n=243) |
|---|---:|---:|
| tir_70_180 | −0.82 | +0.59 |
| time_below_70 | −0.05 | +0.04 |
| time_above_250 | +0.87 | −0.62 |
| mean_bg | +0.84 | −0.60 |
| std_bg | +0.82 | −0.59 |
| total_daily_insulin | +0.78 | −0.56 |
| basal_bolus_ratio | −0.29 | +0.21 |
| meal_count | +0.49 | −0.35 |
| total_carbs_g | +0.38 | −0.28 |
| alarm_count | +0.23 | −0.16 |
| suspension_minutes | +0.41 | −0.30 |

`name_clusters()`'s heuristic (top-2-|z-score| features, phrased as "high/low X") calls these: **cluster 0 = "high severe hyperglycemia, high average glucose"; cluster 1 = "low severe hyperglycemia, low average glucose."** That's an honest, if unglamorous, read: this dataset's dominant recurring axis of day-to-day variation is essentially **overall glucose control that day** — higher-control days and lower-control days — correlated with total insulin, meal count, and pump-alarm/suspension activity (all consistent: rougher days involve more corrections, more alarms, more suspensions). It is *not* multiple sharply distinct "day archetypes" (e.g. a clean "dawn-phenomenon cluster" separate from a clean "post-exercise cluster") — the stability and cross-method-agreement numbers above say the data doesn't support splitting further than this with confidence.

## What I'd do with more time / what a next pass should try

- **Test whether behavioral features not yet in `daily_features()`** (day-of-week, whether it was a work day, exercise proxies if any exist in the pump event log) explain more of the remaining variance than the current 14 features do — the current split reads as "control quality," which is somewhat circular (it's restating TIR in cluster form); a genuinely new *behavioral* pattern axis might need features `daily_features()` doesn't compute yet.
- **If Phase 7's BGI/deviation-primitive work lands** (mentioned as running in parallel this session), it's worth testing as an additional feature — it's a different lens on the same CGM data (insulin-adjusted deviation) and might carry information orthogonal to the raw TIR/insulin summary used here.
- **A longer window with an explicit regime marker** (e.g. flag days after a known basal-rate change) could distinguish "this cluster is really about therapy-era, not behavior" from genuine behavioral clustering — worth checking before trusting any future higher-k result.

## What this module deliberately does not do

Per `CLAUDE.md`'s "never change a threshold based on model output" rule: nothing here writes `n_clusters` (or anything else) back into `config/user_config.yaml`. The `clustering:` block there is untouched and still means "config for `detection/legacy/clustering.py`," per `TECHNICAL_SPEC.md`. This module's own config (`ClusteringV2Config`) lives entirely in-code with sane defaults, optionally overridden by a new, separate `clustering_v2:` YAML block — so a future run with a different recommended k never silently becomes a committed personal setting. If k=2 (or any other value) should become permanent, that's a decision for the owner to make explicitly, reviewing this write-up, the same way M2 calibration output is advisory-only.

Nothing here writes cluster assignments back to Supabase either — this is read-only research tooling. Wiring cluster labels into a production surface (a dashboard "day type" badge, say) is a follow-up decision, not something this exploration pass makes for you.
