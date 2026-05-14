# Diabetes Data Intelligence — Technical Spec & Agent Prompts 
Last updated: 3/21/26
 
## System Overview
 
A Python-based system that will ingest Type 1 diabetes device data (CGM + insulin pump), build event deterction for (missed meals, anomalies), use ML to cluster daily patterns and surface insights through a web dashboard. Using live data and detection models, Telegram will be used for notifications. Two ingestion modes: hitorical (using tconnectsync + CSVs (initially)) and live using pydexcom (for live anomaly detection). Detection engine is source-agnostic.
 
---

## Data Schema

### Source 1: Tandem Basal/Bolus/BG / Control-IQ (via tconnectsync)
- See [docs/DATA_CATALOG.md](docs/DATA_CATALOG.md) for complete field inventory and event type reference.


### Source 2: 'Backup' CSV BG/Bolus data
- Three data sets within each CSV (CGM BG (or EVP) data, manually entered BG data (should be ignored), and bolus data)
    - sets must be extracted individually

### Source 3: Dexcom CGM (pydexcom live)
- frequency: every 5 minutes


---
 
## Detection Logic

> **Note (2026-05-13).** The algorithms described below now live in `detection/legacy/` as the reference v1 implementation. They are not the active detection engine — v2 is in design per `docs/plans/2026-05-05-detection-rework-and-surfaces.md`. The schemas in `DATA_CATALOG.md` §4 describe legacy output. This section will be rewritten when v2's first module ships on `main`.

The detection engine lives in the `detection/` package and is source-agnostic — modules consume normalized DataFrames (shape defined in `DATA_CATALOG.md` §3.5) and an `AppConfig`, and never import from `ingestion/` or reference tconnectsync. Every threshold is config-driven; `detection.config.get_config()` is the single typed entry point (see `detection/config.py` for the `AppConfig` dataclass and its validators).

Output schemas for each function are documented in `DATA_CATALOG.md` §4.

### Anomaly Detection — `detection/anomaly.py`

`detect_anomalies(cgm_df, config)` emits one row per anomalous CGM event. Three classes:

- **spike** — a reading crosses above `anomaly_detection.spike_threshold` when the previous reading was at or below it. Repeat emissions while the series stays elevated are suppressed (one event per crossing).
- **drop** — mirror image against `anomaly_detection.drop_threshold`.
- **flatline** — a rolling window of `anomaly_detection.flatline_consecutive_intervals` (default **K=12**, one hour at 5-min cadence) readings whose sample variance is below `anomaly_detection.flatline_tolerance` and whose inter-sample gaps are all ≤ 7 min (contiguous sensor cadence — no filled-from-gap windows). After flagging the window's last index, scanning advances by K to avoid overlapping events.

Backfilled readings keep their sensor-time `timestamp` and are treated as valid signal; the output's `is_backfilled_context` mirrors the source reading's `backfilled` flag so downstream surfaces can segregate historical-only events.

### Meal Detection — `detection/meal.py`

`detect_meals(cgm_df, requests_df, config)` emits one row per sustained BG rise that isn't covered by a recent food-carrying bolus. Algorithm:

1. Sort CGM by timestamp and compute per-interval deltas and gap minutes. Only intervals whose gap is in `[4, 7]` minutes (normal Dexcom cadence) count — out-of-cadence intervals simply break runs, so sensor dropouts can't mint phantom meals.
2. A *run* is exactly `meal_detection.sustained_intervals` consecutive valid-cadence intervals whose delta is at least `meal_detection.rise_threshold_per_5min`. Using a fixed-size window (vs. greedy extension) keeps `rise_rate_per_5min` comparable across events.
3. A run is **suppressed** if any row in `requests_df` within `[run_start − no_bolus_window_minutes, run_start]` has `bolus_category` in the food-carrying set (`user_meal`, `user_meal_and_correction`, `override_up`). Auto corrections and `user_correction_only` explicitly do NOT suppress — per DATA_NOTES §3, Control-IQ auto corrections never contain food, and a user correction without carbs doesn't cover a meal either.
4. The meal window is labeled by position in the YAML `meal_detection.meal_windows` array (`window_0`, `window_1`, …) — keyed by position rather than hardcoded names so users can reorder windows without touching detection code. Off-hours rises are labeled `"off_window"`.

### Daily Features — `detection/features.py`

`daily_features(frames, date, config)` slices the seven normalized/enriched frames to a single day in the configured `ingestion.timezone` and returns a dict of 14 features plus a `date` key (16 fields total). The 14 features span time-in-range / time-in-band breakdown, BG moments (mean, std with `ddof=0`, CV), insulin totals (bolus sum, basal integrated across the day, basal-bolus ratio), meal summary (count + total carbs from food-carrying `bolus_category` rows), an overnight dip metric (04:00–06:00 vs 00:00–02:00 mean), the mean 2-hour postprandial peak anchored at the nearest CGM reading before each meal bolus, and pump-state minutes (alarms, suspensions, out-of-range CGM from `cgm_gaps`). See `detection/features.py` docstring for per-feature semantics and the empty-frame default policy (counts/sums → 0, ratios/means → NaN); see `DATA_CATALOG.md` §4.3 for the column inventory and types.

### Daily Clustering — `detection/clustering.py`

`cluster_days(features_df, config, retrain=False)` fits or loads a `StandardScaler` + `KMeans` pipeline over the one-row-per-day feature matrix and returns `date`, `cluster_id`, `distance_to_centroid`. Key properties:

- **Deterministic.** `clustering.random_seed` (default `42`) is passed to `KMeans(random_state=…)` and `n_init=10` is fixed.
- **Persisted.** The fitted scaler and kmeans are pickled to `clustering.model_dir` (default `data/models/`) as `scaler_v1.pkl` and `kmeans_v1.pkl`. The training-time column ordering is persisted alongside them as `features_v1.json` so `predict` can reorder a caller's feature matrix to match training.
- **Fit-and-warn.** With `retrain=False` and no saved pipeline on disk, the function fits a fresh pipeline and emits a WARNING — first-run ergonomics. `retrain=True` always refits and overwrites.
- **NaN handling.** Per-batch column median imputation, computed fresh at every call; columns actually imputed are logged at WARNING level. An all-NaN column falls back to `0.0`.

### Config entry point — `detection/config.py`

All detection modules read config through `detection.config.get_config()`, which returns a cached `AppConfig` with typed, validated sub-blocks (`BgTargets`, `MealDetectionConfig`, `AnomalyDetectionConfig`, `ClusteringConfig`, `SiteChangeDetectionConfig`, plus `timezone`). Validation is defense-in-depth: missing top-level keys raise `KeyError`; invariant violations (e.g. `drop_threshold < spike_threshold`, `low < target < high`, `n_clusters >= 2`, `flatline_consecutive_intervals >= 2`, `0 <= start < end <= 24` for meal windows) raise `ValueError` with a clear message.

### Out of scope for v1

Real-time variants (`detect_anomalies_realtime`, `detect_meals_realtime`) that enforce a trailing-window-only invariant, pydexcom live-feed integration, Telegram notifications, and the Streamlit dashboard are all Phase 3 work — not part of this version. The v1 detection API is intentionally pure DataFrame-in / DataFrame-out so the surface layer can wrap it without refactor.

---
 
## Real-Time Detection Constraints
- Trailing window only (no future BG context)
- Confidence threshold must balance false positives (notification fatigue) vs. late alerts (not actionable)
- Telegram notifications will fire when confidence exceeds threshold

---
 
## Config Example (user_config.yaml)
```yaml
bg_targets:
  low: 70
  high: 180
  target: 110
 
meal_detection:
  rise_threshold_per_5min: 8        # mg/dL per interval to trigger
  sustained_intervals: 3             # how many consecutive rising intervals
  no_bolus_window_minutes: 30        # lookback for recent food bolus
  meal_windows:                      # weighted higher during these times
    - [6, 10]
    - [11, 14]
    - [17, 21]
 
anomaly_detection:
  spike_threshold: 180
  drop_threshold: 70
  flatline_tolerance: 2              # mg/dL variance over N readings = suspect
 
clustering:
  method: kmeans
  n_clusters: 5                      # starting point, evaluate and adjust
  feature_mode: aggregated           # or "raw_curve"
 
notifications:
  telegram_bot_token: ""
  telegram_chat_id: ""
  confidence_threshold: 0.75         # minimum confidence to send alert
  cooldown_minutes: 30               # don't re-alert within this window
```
 
---