# Handoff: Session 3 — Data Verification & Exploration

**Date:** 2026-03-23
**Status:** Data verification in progress. 6 data issues documented, pipeline fixes pending.

---

## What Was Done This Session

1. **Added `fetch-day` CLI command** — targeted single-day fetch from the active pump (±1 day padding). Much faster than full fetch for spot-checking.
2. **Added `viz` CLI command** — matplotlib multi-panel daily chart (CGM trace, bolus/carb markers, basal step chart) modeled after the Tandem t:connect app.
3. **Verified Mar 18 and Mar 19 data** against the Tandem app. Identified 6 data issues and documented domain knowledge in DATA_NOTES.md.
4. **Explored the tconnectsync event model deeply** — decoded alarm/alert types, CGM backfill mechanism, bolus source classification.

### Files Created/Modified

| File | Action | Purpose |
|------|--------|---------|
| `ingestion/fetch.py` | Modified | Added `run_day_fetch(date_str)` |
| `ingestion/__init__.py` | Modified | Exports `run_day_fetch` |
| `main.py` | Modified | Added `fetch-day` and `viz` subcommands |
| `scripts/daily_viz.py` | Created | Multi-panel daily visualization |
| `docs/operating_docs/DATA_ISSUES.md` | Created | 6 pipeline issues with include/exclude recommendations |
| `docs/operating_docs/DATA_NOTES.md` | Created | Domain knowledge from user for detection engine |

### Test Status
```
48 passed, 1 skipped, 0 failed
```
No new tests were added this session (exploratory work only).

---

## Pipeline Architecture

```
tconnectsync API → ingestion/client.py → ingestion/builders.py → ingestion/storage.py
                                                                  (data/processed/*.parquet)
```

### File Inventory

| File | Purpose |
|------|---------|
| `config/user_config.yaml` | All thresholds and settings (timezone, bg_targets, meal_detection, etc.) |
| `ingestion/__init__.py` | Package exports: `run_full_fetch`, `run_incremental_fetch`, `run_day_fetch`, `clean_all` |
| `ingestion/client.py` | API auth, multi-pump metadata, chunked event fetching with error handling |
| `ingestion/builders.py` | 6 DataFrame builders + `build_all` router |
| `ingestion/storage.py` | Parquet read/write, dedup (concat-then-dedup), fetch state tracking |
| `ingestion/fetch.py` | Orchestrator: full fetch, incremental fetch, day fetch, per-pump processing |
| `main.py` | CLI: `fetch`, `fetch --clean`, `fetch-day`, `update`, `check`, `viz` |
| `scripts/sanity_check.py` | Human-readable day summary from parquet files |
| `scripts/daily_viz.py` | Multi-panel matplotlib chart (CGM, bolus, basal) |
| `tests/test_builders.py` | 34 unit tests for all builders |
| `tests/test_storage.py` | 7 parquet/dedup/state tests |
| `tests/test_suspension.py` | 7 suspension pairing edge-case tests |
| `tests/test_integration.py` | Skeleton gated behind `@pytest.mark.integration` |

### DataFrames Produced

| Name | Parquet | Key Columns | Notes |
|------|---------|-------------|-------|
| cgm | `cgm.parquet` | timestamp, bg_mgdl, pump_serial | Deduped on (timestamp, pump_serial) |
| bolus | `bolus.parquet` | timestamp, insulin_units, bolus_id, pump_serial | |
| requests | `requests.parquet` | timestamp, bolus_id, carbs_g, bg_mgdl, iob, bolus_source, food_insulin, correction_insulin, total_requested, pump_serial | carbs_g is RAW (not /1000). bolus_source: "auto"/"user"/"override"/"unknown" |
| basal | `basal.parquet` | timestamp, commanded_rate, rate_source, pump_serial | commanded_rate = commandedRate/1000 (u/hr). rate_source: "profile"/"algorithm"/"temp_rate"/etc |
| suspension | `suspension.parquet` | suspend_timestamp, resume_timestamp, duration_minutes, suspend_reason, insulin_at_suspend, pairing_suspect, pump_serial | Paired chronologically. pairing_suspect=True if >24h or double-suspend |
| events | `events.parquet` | timestamp, event_type, event_subtype, previous_mode, details, seqnum, pump_serial | Types: site_change, cgm_session, mode_change, pcm_change, daily_marker |

### Key Design Decisions

- **Pump overlap**: Fetch all date ranges, dedup by content. Old pumps with pre-loaded dates but no actual events return zero rows naturally.
- **events dedup key**: `(pump_serial, seqNum)` — seqNum is globally unique per pump (uint32 counter), not per-event-type.
- **carbamount**: Raw value = grams. Do NOT divide by 1000. (Verified against CSV.)
- **commandedRate**: milliunits/hr. Divide by 1000. Mean ~1396 → 1.396 u/hr matches pump profile.
- **`BG=0` in requests**: Means "missing BG", not actual glucose of 0. Detection engine must treat as null.
- **`requests_df` key is `"requests"`** (not `"request"`) — was a bug in an earlier session, now fixed.

---

## Where Things Are

- **`data/processed/`** has parquet files for Mar 16-21 (fetched via `fetch-day --date 2026-03-19` which pulls a ±1 day window, so 3 days of data landed)
- **`docs/operating_docs/DATA_ISSUES.md`** — 6 issues, all with clear "Include in pipeline: YES/NO" recommendations. Read this before making any builder changes.
- **`docs/operating_docs/DATA_NOTES.md`** — Domain knowledge from the user. Not derivable from code. Read this before building the detection engine.

---

## CLI Commands

```bash
uv run python main.py fetch                    # Full fetch, all 6 pumps, all history (10-30 min)
uv run python main.py fetch --clean             # Wipe data/processed/ then full fetch
uv run python main.py fetch-day --date YYYY-MM-DD  # Single day, active pump only (~4 sec)
uv run python main.py update                    # Incremental since last fetch
uv run python main.py check --date YYYY-MM-DD   # Text summary of a day
uv run python main.py viz --date YYYY-MM-DD     # Visual chart of a day (plt.show)
```

---

## Gotchas & Things Learned Working With the API

These are things I discovered through trial and error that aren't documented elsewhere:

### 1. tconnectsync event class names are inconsistent
- The class is `LidBolusRequestedMsg1`, not `LidBolusRequestMsg1` (note the `ed`). Will give an `ImportError` if you get it wrong.
- `optionsRaw` is on **Msg2**, not Msg1 or Msg3. `carbamount`, `BG`, `IOB` are on Msg1. `foodbolussize`, `correctionbolussize`, `totalbolussize` are on Msg3.

### 2. CGM event field names
- The BG value field is `currentglucosedisplayvalue`, not `bgReading` or `bg` or `glucose`.
- `cgmDataTypeRaw` and `egvTimestamp` are on the CGM event object but not used by our builder yet. These are critical for the backfill fix (Issue #5).

### 3. tconnectsync enum warnings are noisy but harmless
- Many `dalertidRaw` values (1, 2, 3, 6, 8) are unmapped in tconnectsync's enum and produce stderr warnings like `"2 is not a valid LidCgmAlertActivatedDex.DalertidEnum"`. The events still parse fine — the `.dalertid` property just returns `None`. Use `.dalertidRaw` (the int) instead.
- Suppress with `warnings.filterwarnings('ignore')` or `2>/dev/null` when querying bulk data.

### 4. Alarm/alert attributes vary by class
- `LidAlarmActivated` has `alarmidRaw`, `alarmid`, `param1`, `param2`
- `LidAlarmCleared` has `alarmidRaw`, `alarmid` but **no** `param1`/`param2`
- Same pattern for `LidAlertActivated` vs `LidAlertCleared`
- Always use `hasattr()` before accessing `param1`/`param2`

### 5. The active pump is the last in the sorted metadata list
- `get_pump_metadata(api)` returns pumps sorted by `minDateWithEvents` (oldest first)
- The current/active pump is `metadata[-1]` (serial 1513861)

### 6. `LidUsbConnected` and `LidUsbDisconnected` are unhandled
- These appeared during real data fetch but aren't in `_HANDLED_TYPES`. They trigger builder warnings. Harmless — just USB cable events. Add to `_HANDLED_TYPES` to suppress.

### 7. Bolus source classification is binary
- `optionsRaw=3` + `bolustypeRaw=2` = auto correction (every time). `optionsRaw=0` + `bolustypeRaw=1` = user-initiated (every time). No `optionsRaw=6` observed. The split is perfectly clean — `bolustypeRaw` alone would suffice.
- Override is a sub-classification within user-initiated: `useroverrideRaw=1` on Msg2.

### 8. Suspension alarm correlation is by exact timestamp
- `LidAlarmActivated` fires at the **exact same timestamp** as `LidPumpingSuspended` when the alarm causes the suspension. Match on timestamp to enrich suspensions with alarm names.

---

## Priority Work for Next Session

### Must-do (pipeline fixes, in order)
1. **Build `alarms.parquet`** — New builder for `LidAlarmActivated/Cleared`, `LidAlertActivated/Cleared`, `LidCgmAlertActivatedDex/Cleared/Ack`. See DATA_ISSUES #2, #4, #6 for the full type map and column schema.
2. **Fix CGM backfill** — Update `build_cgm_df` to preserve `cgmDataTypeRaw=2` readings. See DATA_ISSUES #5 for the exact fix. This recovers 30% of CGM data.
3. **Fix stale CGM readings** — Drop readings <60s apart in `build_cgm_df`. See DATA_ISSUES #1.
4. **Enrich suspensions with alarm name** — Timestamp-match `LidAlarmActivated` to `LidPumpingSuspended` in `build_suspension_df`. See DATA_ISSUES #3.

### Should-do
5. **Full historical fetch** — `uv run python main.py fetch` across all 6 pumps. Needed before any real analysis.
6. **Add `LidUsbConnected`/`LidUsbDisconnected` to `_HANDLED_TYPES`** — Suppresses warnings.
7. **Capture integration test reference data** — Pick a verified day, record exact values in `tests/test_integration.py`.

### User needs to provide
- Cartridge fill amount threshold for distinguishing real vs forced site changes (DATA_NOTES #2)

---

# Handoff: Session 5 — Enrichment Layer

**Date:** 2026-04-21
**Status:** Enrichment layer complete. All 4 planned enrichments landed on `feat/enrichment-detection-v1`. Ready to start Phase 2 (Detection Engine v1).

## What Shipped

Four commits on `feat/enrichment-detection-v1` (above main), implementing Tasks 1.1–1.4 of `docs/plans/2026-04-20-enrichment-and-detection-v1.md`:

| SHA | Task | Summary |
|---|---|---|
| `861379d` | 1.1 | `enrich_requests_df` — adds `bolus_category` and `override_delta` to `requests.parquet` |
| `ae2059f` | 1.2 | `enrich_events_df` — adds `forced_by_alarm` to `events.parquet` for site_change rows |
| `54bb609` | 1.3 | `build_site_issues_df` — clusters `OcclusionAlarm` activations into a new `site_issues.parquet` |
| `b8f12b6` | 1.4 | `build_cgm_gaps_df` — pairs `cgm_out_of_range` activations/cleared rows into a new `cgm_gaps.parquet` |

All four are wired through `enrich_all(frames, config)` in `ingestion/enrich.py`, which is invoked from `builders.build_all` whenever a config dict is passed. The production `fetch` / `fetch-day` / `update` paths always pass config, so downstream consumers see enriched frames on disk. See `docs/DATA_CATALOG.md` §3.6 (Enriched tables) and §3.7 (Enrichment pipeline) for the column-level schema and step ordering.

## Updated Frame Inventory

| Parquet | Status | Notes |
|---|---|---|
| `cgm.parquet` | unchanged | Live + backfilled CGM (see DATA_ISSUES #5). |
| `bolus.parquet` | unchanged | Completed boluses. |
| `requests.parquet` | **enriched** | Now carries `bolus_category` + `override_delta` (Task 1.1). |
| `basal.parquet` | unchanged | |
| `suspension.parquet` | unchanged | Still carries `alarm_id` / `alarm_name` from the earlier suspension-enrichment (DATA_ISSUES #3). |
| `events.parquet` | **enriched** | Site_change rows now carry `forced_by_alarm` (Task 1.2). |
| `alarms.parquet` | unchanged | Source frame for Tasks 1.2, 1.3, 1.4. |
| `site_issues.parquet` | **new** | Suspected site-failure episodes (Task 1.3). |
| `cgm_gaps.parquet` | **new** | Sensor-blind windows (Task 1.4). Resolves DATA_ISSUES #6. |

## New Config Block

`config/user_config.yaml` gained a `site_change_detection` block with four keys:

```yaml
site_change_detection:
  forced_window_minutes: 120            # DATA_NOTES §2 — minutes after BatteryShutdownAlarm
  cartridge_real_fill_threshold: 220    # DATA_NOTES §2 — units; placeholder pending more data
  occlusion_cluster_window_minutes: 180 # DATA_NOTES §1 — max gap between occlusions in a cluster
  min_occlusions_for_cluster: 2         # DATA_NOTES §1 — minimum cluster size to emit
```

All enrichment code reads these through `enrich_all(frames, config)`; no thresholds are hardcoded. Task 2.1 will replace the raw-dict load with a typed `AppConfig` — `load_config` in `ingestion/enrich.py` is intentionally thin because of that.

## Test Status

```
167 passed, 1 skipped, 1 warning in 0.89s
```

Up from 108 at the end of Session 4. The skip is the API integration test gated behind `@pytest.mark.integration`.

## Gotchas Discovered During Implementation

1. **`cartridge_real_fill_threshold: 220` is a placeholder.** DATA_NOTES §2 observed only three cartridge fills (180 = forced, 240 = real). 220 is the midpoint. Revisit once more cartridge fills accumulate. All three observed samples classify correctly with this value, but the decision boundary is not well-calibrated.
2. **Forced-site-change heuristic is timestamp-first, volume-second.** `enrich_events_df` first checks the `[shutdown_ts, shutdown_ts + forced_window_minutes]` window; only inside that window does the cartridge volume override kick in. Outside the window every site_change is `forced_by_alarm = False` regardless of volume. `tubing` and `cannula` subtypes carry no volume signal, so inside the window they are always forced — this is fine because a real site rotation always includes a cartridge fill that will dominate the decision.
3. **`build_site_issues_df` has a fallback when `forced_by_alarm` is missing.** If called on a raw (un-enriched) `events_df`, every `site_change` is treated as a valid resolver. This is intentional — the alternative is silently dropping clusters. `enrich_all` runs the steps in the right order so the fallback only fires for ad-hoc callers and older tests.
4. **`cgm_gaps` pairing mirrors `build_suspension_df`.** Double-activated and unpaired-cleared cases both log warnings rather than raising, matching how suspensions handle pairing anomalies. A trailing unpaired activation emits a row with `ongoing=True` and `duration_minutes=NaN`.
5. **Override category edge case.** When `bolus_source == "override"` but `override_delta` net-zeros (within `_OVERRIDE_EPSILON = 0.01`), `enrich_requests_df` falls back to the standard user-branch categorization rather than emitting a spurious `override_up`/`override_down`. No such case observed in the real data, but it's covered in `test_enrich.py`.
6. **Events dict order is preserved into enrichment.** `build_all` constructs `result` with a fixed key order (`cgm`, `bolus`, `requests`, `basal`, `suspension`, `events`, `alarms`) and `enrich_all` only adds (`site_issues`, `cgm_gaps`) — never renames or reorders existing keys. Callers that iterate `dfs.items()` (e.g. `fetch.save_df` loop) continue to work unchanged.

## What's Next

**Phase 2 — Detection Engine v1**, starting with **Task 2.1 — Config loader (`detection/config.py`)**. The plan (`docs/plans/2026-04-20-enrichment-and-detection-v1.md` ~line 787) specifies a typed, validated, `lru_cache`-backed `AppConfig` that supersedes the thin `load_config` in `ingestion/enrich.py`. Everything downstream of Task 2.1 (meal detection, anomaly detection, suspension analysis) reads config through that single typed entry point.

After 2.1, Tasks 2.2+ build the detection primitives on top of the enriched frames this session produced: `site_issues.parquet` and `cgm_gaps.parquet` become first-class inputs that gate / enrich BG excursion analysis.

---

# Handoff: Session 6 — Detection Engine v1

**Date:** 2026-04-21
**Status:** Phase 2 complete. Detection Engine v1 (anomaly / missed-meal / daily clustering) shipped on `feat/enrichment-detection-v1`. All of Tasks 2.1–2.6 plus this documentation task (2.7) landed. Phase 3 (surfaces: Telegram, Streamlit, pydexcom live) is deferred.

## What Shipped

Six commits on `feat/enrichment-detection-v1` above the Session 5 tip, implementing Tasks 2.1–2.6 of `docs/plans/2026-04-20-enrichment-and-detection-v1.md`:

| SHA | Task | Summary |
|---|---|---|
| `a4e2273` | 2.1 | `detection/config.py` — typed, validated, `lru_cache`-backed `AppConfig` |
| `e932686` | 2.2 | `detection/anomaly.py` — spike / drop / flatline detection (K=12 default) |
| `920f022` | 2.3 | `detection/meal.py` — sustained-rise + bolus-lookback missed-meal detection |
| `b76218e` | 2.4 | `detection/features.py` — 14-feature per-day aggregation for clustering |
| `3477e0d` | 2.5 | `detection/clustering.py` — KMeans over scaled daily features, persisted |
| `8a417c2` | 2.6 | CLI subcommands: `analyze-anomalies`, `analyze-meals`, `cluster-days` |

All detection code is source-agnostic — modules import only from `detection/` and never from `ingestion/` or tconnectsync. Everything reads config through `detection.config.get_config()` (the single typed entry point introduced in Task 2.1).

## New Package Layout

```
detection/
├── __init__.py
├── config.py        # AppConfig + per-block dataclasses (Task 2.1)
├── anomaly.py       # detect_anomalies         (Task 2.2)
├── meal.py          # detect_meals             (Task 2.3)
├── features.py      # daily_features           (Task 2.4)
└── clustering.py    # cluster_days             (Task 2.5)
```

### Function contracts

| Function | Signature | Inputs | Output |
|---|---|---|---|
| `detect_anomalies` | `(cgm_df, config) -> DataFrame` | `cgm_df` shaped like `build_cgm_df` (needs `timestamp`, `bg_mgdl`, `backfilled`); `AppConfig` | One row per event with `timestamp, anomaly_type ∈ {spike,drop,flatline}, bg_at_event, rate_mgdl_per_min, confidence, is_backfilled_context` |
| `detect_meals` | `(cgm_df, requests_df, config) -> DataFrame` | CGM as above; enriched `requests_df` (needs `timestamp`, `bolus_category`); `AppConfig` | One row per detected missed meal: `timestamp, bg_start, bg_peak, rise_rate_per_5min, meal_window, confidence` |
| `daily_features` | `(frames, date, config) -> dict` | `frames` dict (cgm/bolus/basal/requests/suspension/alarms/cgm_gaps); `datetime.date`; `AppConfig` | 16-key dict (`date` + 14 features — see DATA_CATALOG §4.3) |
| `cluster_days` | `(features_df, config, retrain=False) -> DataFrame` | One-row-per-day features (must have `date`); `AppConfig`; optional retrain flag | `date, cluster_id, distance_to_centroid` |

### CLI commands

```bash
uv run python main.py analyze-anomalies --date YYYY-MM-DD   # Runs detect_anomalies for one day
uv run python main.py analyze-meals     --date YYYY-MM-DD   # Runs detect_meals for one day
uv run python main.py cluster-days [--retrain] [--start ...] [--end ...]
                                                            # Builds daily_features across the range and clusters
```

`cluster-days --retrain` refits the scaler + KMeans and overwrites the persisted pickles; without `--retrain`, it loads the saved model and predicts. Output is written to `data/processed/daily_clusters.parquet`.

## Config Additions

`config/user_config.yaml` gained one explicit key this session:

```yaml
anomaly_detection:
  flatline_consecutive_intervals: 12   # K: 12 × 5-min = 1 hour of flat signal
```

Clustering defaults (`random_seed: 42`, `model_dir: "data/models"`) are supplied by `detection/config.py` when absent from the YAML — so a stock `clustering:` block continues to validate. Add them explicitly in the YAML to override.

## Decisions That Diverged From the Plan

All recorded in the respective module docstrings / PR notes:

1. **Flatline K = 12, not 6.** The plan proposed `flatline_consecutive_intervals: 6` (30 min). During Task 2.2 we bumped to 12 (1 hour) to suppress false positives during normal stable periods; 6 fires on ordinary overnight plateaus. Config-controlled so it's easy to revisit.
2. **`cartridge_real_fill_threshold: 220` is still a placeholder** (inherited from Session 5). Only three cartridge fills observed (180 = forced, 240 = real); 220 is the midpoint. Revisit once more fills accumulate.
3. **Meal windows labeled `window_0` / `window_1` / `window_2`** (position-keyed), not `"breakfast"` / `"lunch"` / `"dinner"`. Keeps the code config-driven: users can reorder or rename YAML entries without touching detection code. Off-hours rises are labeled `"off_window"`.
4. **Meal detection uses fixed-size runs of `sustained_intervals`**, not greedy extension. Each run is exactly `N` consecutive valid-cadence rising intervals; on a hit we advance past the run's final index. Keeps `rise_rate_per_5min` comparable across events.
5. **`daily_features` empty-frame defaults.** Counts/sums (`total_daily_insulin`, `meal_count`, `total_carbs_g`, `alarm_count`, `suspension_minutes`, `out_of_range_minutes`) default to `0` when their source frame is missing. Ratios/means (`tir_*`, `time_*`, `mean_bg`, `std_bg`, `cv_bg`, `basal_bolus_ratio`, `overnight_dip`, `mean_postprandial_peak`) default to `NaN` — they're undefined, not zero. `std_bg` uses `ddof=0` so single-reading days don't produce NaN.
6. **`cluster_days` fit-and-warn.** When `retrain=False` and no saved model exists on disk, we fit a fresh pipeline and emit a WARNING naming the model_dir, rather than raising. Rationale: first-run ergonomics — a brand-new checkout should work without requiring the user to type `--retrain`. The warning keeps the implicit training visible.
7. **Third persisted artefact `features_v1.json`** alongside `kmeans_v1.pkl` and `scaler_v1.pkl`. Captures the training-time column ordering so `predict` can reorder a caller's feature matrix to match — callers can pass columns in any order as long as the set is a superset of training.

## Test Status

```
251 passed, 1 skipped, 2 warnings
```

Up from 108 at the end of Session 4 and 167 at the end of Session 5. The skip is the same API integration test gated behind `@pytest.mark.integration`. New Phase 2 test modules: `test_detection_config.py`, `test_detection_anomaly.py`, `test_detection_meal.py`, `test_detection_features.py`, `test_detection_clustering.py`, `test_cli_detection.py`.

## Known Limitations

1. **Confidence heuristics are placeholder arithmetic.** `detect_anomalies` and `detect_meals` both emit a `confidence` column derived from simple ratios of magnitude-over-threshold. They're intended for ordering, not calibration. Need recalibration (or replacement with a classifier) before Phase 3 notifications go live — otherwise the Telegram cooldown will either spam or starve.
2. **KMeans clustering is unsupervised.** There is no labeled ground truth for cluster validation; `n_clusters=5` is the plan's starting point. Cluster quality is judged by eyeball only until we collect labels.
3. **No real-time variant yet.** `detect_anomalies_realtime`, `detect_meals_realtime`, etc. are deferred to Phase 3. v1 is batch-only over a normalized DataFrame — it's the right shape for wrapping in a streaming loop, but nothing enforces the trailing-window-only constraint yet.
4. **Clustering model is undertrained.** The persisted `kmeans_v1.pkl` / `scaler_v1.pkl` were fit on ≤ 6 days of local data (everything currently in `data/processed/`). The first thing the user should do is run a full historical fetch and re-cluster — see **Next steps** below.
5. **`cartridge_real_fill_threshold: 220` placeholder** (see Decision 2 above). Dial in once more cartridge fills accumulate.

## Next Steps for the User

In this order:

1. **Full historical fetch.** Run `uv run python main.py fetch` to pull the full ~15 months of data across all six pumps (10–30 min).
2. **Refit clustering.** Run `uv run python main.py cluster-days --retrain` to rebuild `kmeans_v1.pkl` / `scaler_v1.pkl` / `features_v1.json` on the full history. Expect ≥ ~30 days in the output with a sensible cluster-size distribution (no singleton clusters unless a day is genuinely pathological).
3. **Eyeball detection on a known day.** The plan's validation target is 2026-03-19 (pump died 08:06, occlusion at 22:36, lots of high-BG excursions). Run:
   ```bash
   uv run python main.py analyze-anomalies --date 2026-03-19
   uv run python main.py analyze-meals --date 2026-03-19
   ```
   Expected: multiple spikes including the post-shutdown rise, the 22:36 drop if present, and a flatline during the dead-pump window — **verify** `is_backfilled_context=True` on rows whose timestamps fall inside a `cgm_gaps` episode.
4. **Tune `cartridge_real_fill_threshold`** once the full fetch surfaces more cartridge fills. The YAML comment spells out the signal (insulin_volume at fill time; ≥ threshold inside the forced-window overrides the forced flag).

## What's Next

Phase 3 — **Surfaces**. See the bottom of `docs/plans/2026-04-20-enrichment-and-detection-v1.md` (the OUT OF SCOPE block). Tentative filename: `docs/plans/YYYY-MM-DD-surfaces.md`. Covers pydexcom live feed, Telegram notifications (threshold + cooldown + message formatting), Streamlit dashboard, and real-time variants of the detection functions. The v1 detection API is intentionally pure DataFrame-in / DataFrame-out so the surface layer can wrap it without refactor.

---

# Handoff: Session 7 — Enrichment visibility in `check` / `viz`

**Date:** 2026-04-21
**Plan:** `docs/plans/2026-04-21-check-viz-enrichment-visibility.md`
**Branch:** `feat/enrichment-detection-v1`

## What Shipped

`check` and `viz` now accept a `--view {original,enriched}` flag so enrichment (bolus_category / override_delta / forced_by_alarm / site_issues / cgm_gaps) is inspectable without changing the default output. Shared backfill logic now lives in **one** helper, reused by `scripts/run_detection.py`.

| File | Change |
|---|---|
| `ingestion/view_data.py` | **new** — `VIEW_MODES`, `ENRICHED_COLUMNS`, `strip_enriched_columns`, `ensure_enriched`, `load_frames` |
| `scripts/run_detection.py` | `_ensure_enriched` now delegates to `view_data.ensure_enriched` (no behavior change) |
| `scripts/sanity_check.py` | `sanity_check(date_str, view="original")`; adds Bolus-category / Forced site-change / Site-issue / CGM-gap sections in enriched view |
| `scripts/daily_viz.py` | `daily_viz(date_str, view="original")`; `_shade_oor_from_alarms` vs `_shade_oor_from_gaps`, forced-site differentiation, `site_issues` band, `bolus_category` cluster labels |
| `main.py` | `--view` argparse choice on `check` and `viz` (default `original`) |
| `tests/test_view_data.py` | **new** — 15 tests for the helper |
| `tests/test_sanity_check.py` | **new** — 7 `capsys` tests for the CLI |
| `tests/test_daily_viz.py` | **new** — 6 matplotlib smoke tests (`plt.show` mocked, `Agg` backend) |

## View-Mode Behavior Table

| Aspect | `--view original` (default) | `--view enriched` |
|---|---|---|
| Enrichment columns on disk | Hidden in print/plot via `strip_enriched_columns` | Preserved; backfilled in memory if absent |
| `site_issues` / `cgm_gaps` | Shown only if present on disk | Always: built in memory from `alarms` when missing |
| `check` sections | CGM / Bolus / Requests / Basal / TDD / Suspensions / Events / Alarms | + Bolus categories / Forced site changes / Site issues overlapping day / CGM gaps overlapping day |
| `viz` CGM OOR shading | Alarm-pair derived (light gray, no hatch) | `cgm_gaps`-derived (dotted hatch); alarm-pair path **skipped** — no double-draw |
| `viz` site change marker | Solid gray square | Hollow gray square + "site (forced)" label when `forced_by_alarm=True`; solid + "site" when `False` |
| `viz` bolus panel extras | — | `bolus_category` label below each cluster; `site_issues` episodes drawn as a gold hatched band |
| Header suffix | — | `[view: enriched]` appended |

## Example Commands

```bash
# Default (pre-enrichment) behavior preserved byte-for-byte.
uv run python main.py check --date 2026-03-19
uv run python main.py viz   --date 2026-03-19

# Enriched view: backfills bolus_category / forced_by_alarm / site_issues
# / cgm_gaps in memory and prints/plots the extra sections.
uv run python main.py check --date 2026-03-19 --view enriched
uv run python main.py viz   --date 2026-03-19 --view enriched
```

Parquets on disk are never modified by either command — the enriched view is a pure projection.

## Eyeball Checklist (viz)

When comparing `viz --date 2026-03-19` original vs enriched:

1. Header should end in `[view: enriched]` only in enriched mode.
2. Gray shading on the CGM panel should appear **at most once per OOR episode**. If you see overlapping dotted-hatch and solid-gray spans, the single-source-of-truth rule was broken.
3. The 08:06 post-shutdown site change should render as a hollow square in enriched mode (forced), solid in original.
4. Bolus cluster markers should have a small italic category label (`user_meal`, `auto_correction`, …) below them in enriched mode only.
5. A gold hatched band should appear on the bolus panel across the 22:36-ish occlusion cluster (if the day qualifies under `min_occlusions_for_cluster`).

## Gotchas

1. **"Original" is a projection, not byte-identical raw.** On pre-enrichment parquets the two views differ only in the extra sections/overlays. On already-enriched parquets, `original` still hides the enriched columns via `strip_enriched_columns` — this is the documented contract. See `view_data.ENRICHED_COLUMNS` for the exact column catalog.
2. **`ensure_enriched` is the single backfill site.** `run_detection._ensure_enriched` is now a one-line delegate. If you change backfill semantics (e.g. different default for missing `site_issues`), change `ingestion/view_data.ensure_enriched` and every consumer picks it up.
3. **Tests patch `load_df` inside `scripts.sanity_check` / `scripts.daily_viz`.** `sanity_check` and `daily_viz` call `load_df` directly (then call `ensure_enriched`) rather than going through `view_data.load_frames`, specifically so the existing `patch("scripts.sanity_check.load_df", ...)` style keeps working.
4. **Viz smoke tests need `matplotlib.use("Agg")` before importing pyplot** — the fixture enforces this, don't remove the top-level backend hint.

## Follow-Ups (not in this session)

- `viz --compare` to render original + enriched side-by-side — deferred; the current differentiation is legible enough on a single figure.
- Integration test against a real day in `data/processed/` asserting at least one enriched section appears in `check --view enriched` output (currently covered only by unit-level synthetic fixtures).
- Automated visual regression (matplotlib `compare_images`) — not yet added; eyeball checklist above is the interim contract.
