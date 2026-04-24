# Data Catalog

Complete inventory of available data streams for the t1d-engine project. Use this to plan the detection engine, ML clustering, and notification system.

---

## 1. Overview

| Source | Type | Time Coverage | Volume (sample) | Status |
|---|---|---|---|---|
| Tandem CSV Export | File | ~28 days (Feb 21 – Mar 20, 2026) | 7,407 EGV + 235 bolus | Exploration/verification only |
| **tconnectsync API** | **Live API** | **~15 months (Jan 1, 2025 – present)** | **~750 events/day** | **Primary source** |
| pydexcom | Live API | Real-time (every 5 min) | N/A | Not yet implemented |

**Primary source: tconnectsync API.** It provides everything we need — CGM, bolus, basal, suspensions, site changes, device settings — with ~15 months of history and timezone-aware timestamps. CSV exports are only used for exploration/verification, not in the production pipeline.

---

## 2. Source 1: Tandem CSV Export

### File Locations
- Real data: `data/CSV_Meyer-BibbinsAshton_1513861_20Mar2026_2109.csv`
- Test copy: `test_data/CSV_patient-data_copy.csv`

### File Structure
- **Lines 1–6**: Metadata header (device type, software version, report date)
- **Lines 7–7413**: EGV section (CGM readings)
- **Line 7414**: Blank separator
- **Lines 7415–7541**: Manual BG section (**ignored per spec**)
- **Line 7541**: Blank separator
- **Lines 7542–7778**: Bolus section

Each section has its own header row. Sections are separated by blank lines. The `file_extract()` function in `research.ipynb` splits them by detecting header rows containing "Date".

### 2.1 EGV Section (CGM Readings)

7,407 rows. 5-minute intervals. Date range: Feb 21 – Mar 20, 2026.

| Column | Type | Example | Notes |
|---|---|---|---|
| DeviceType | str | `t:slim X2™ Insulin Pump` | Constant |
| SerialNumber | int | `1513861` | Constant for this export |
| Description | str | `EGV` | Constant |
| EventDateTime | str | `2026-02-21T00:03:01` | ISO 8601, **no timezone** |
| Readings (mg/dL) | int | `127` | BG value |

### 2.2 Manual BG Section

127 rows. **Ignored per spec** — these are manually entered calibration/fingerstick values, not CGM readings. Columns: DeviceType, SerialNumber, Description, EventDateTime, BG (mg/dL), Note. Note field is empty for all rows.

### 2.3 Bolus Section

235 rows. 20 columns.

| Column | Type | Example | Notes |
|---|---|---|---|
| Type | str | `Bolus` | Always "Bolus" |
| BolusType | str | `Food`, `Correction`, `Food / Correction`, `Auto`, `Override` | Key for meal detection — Food/Food+Correction = user meal bolus, Auto = Control-IQ, Override = manual |
| BolusDeliveryMethod | str | `Standard`, `Auto` | Standard = user-initiated, Auto = CIQ algorithm |
| BG (mg/dL) | int | `141` | BG at time of bolus |
| SerialNumber | int | `1513861` | Constant |
| CompletionDateTime | str | `2026-02-21T00:15:56` | ISO 8601, **no timezone** |
| InsulinDelivered | float | `5.94` | Total units delivered |
| FoodDelivered | float | `5.94` | Insulin portion for carbs |
| CorrectionDelivered | float | `0` | Insulin portion for correction |
| CompletionStatusDesc | str | `Completed` | Bolus completion status |
| BolexStartDateTime | str | *(empty)* | Extended bolus — unused in this dataset |
| BolexCompletionDateTime | str | *(empty)* | Extended bolus — unused |
| BolexInsulinDelivered | str | *(empty)* | Extended bolus — unused |
| BolexCompletionStatusDesc | str | *(empty)* | Extended bolus — unused |
| StandardPercent | int | `100` | Always 100 (no extended boluses) |
| Duration (mins) | int | `0` | Always 0 (no extended boluses) |
| CarbSize | int | `25` | Grams of carbs entered. 0 for Auto/Correction boluses. |
| TargetBG (mg/dL) | int | `110` | From pump profile at time of bolus |
| CorrectionFactor | int | `20` | ISF at time of bolus (mg/dL per unit) |
| CarbRatio | float | `4.2` | Carb ratio at time of bolus (grams per unit) |

---

## 3. Source 2: tconnectsync API

### 3.1 Connection
- Auth: OIDC/PKCE flow via `TandemSourceApi(email, password)`
- Credentials from `.env` (`TCONNECT_EMAIL`, `TCONNECT_PASSWORD`)
- Entry point: `api.pump_events(device_id, min_date, max_date)` yields typed event objects
- See `docs/api_levels.md` for the drill-down pattern and `docs/tconnectsync_api_map.md` for the full API reference.

### 3.2 Account & Device History

| # | Serial | Software | Data Range |
|---|---|---|---|
| 0 | 884750 | Control-IQ, 7.4 | 2021-01-01 → 2022-03-19 |
| 1 | 984922 | CONTROLIQ 7.6.0.1 | 2022-01-01 → 2023-10-04 |
| 2 | 90693745 | CONTROLIQ 7.8 Release | 2023-01-01 → 2024-08-02 |
| 3 | 90899083 | CONTROLIQ 7.8 Release | 2024-01-01 → 2024-11-09 |
| 4 | 91727084 | CONTROLIQ 7.8 Release | 2024-01-02 → 2025-12-09 |
| 5 | **1513861** | **CONTROLIQ+ 7.9.0.1** | **2025-01-01 → present** |

Current pump: serial `1513861`. Last upload: 2026-03-22.

### 3.3 Event Type Inventory

From a 22-day sample (Mar 1–22, 2026): 16,441 total events.

| Category | Event Type | Count | ~Per Day | Key Fields |
|---|---|---|---|---|
| **CGM** | `LidCgmDataG7` | 6,348 | 289 | `currentglucosedisplayvalue` (int, mg/dL), `egvTimestamp` |
| **Basal** | `LidBasalDelivery` | 6,648 | 302 | `commandedRate` (float, **milliunits/hr — see §6**), `commandedRateSource` |
| **Bolus** | `LidBolusDelivery` | 420 | 19 | Delivery progress events |
| **Bolus** | `LidBolusCompleted` | 211 | 9.6 | `insulindelivered` (float, units), `bolusid`, `IOB` |
| **Bolus** | `LidBolusRequestedMsg1` | 211 | 9.6 | `carbamount` (int, **see §6**), `BG` (int, mg/dL), `IOB` (float) |
| **Bolus** | `LidBolusRequestedMsg2` | 211 | 9.6 | `useroverride`, `declinedcorrection`, `OptionsMap` |
| **Bolus** | `LidBolusRequestedMsg3` | 211 | 9.6 | `totalRequestedInsulin` (float) |
| **Bolus** | `LidBolusActivated` | 210 | 9.5 | Bolus start marker |
| **Control-IQ** | `LidAaPcmChange` | 370 | 17 | CIQ algorithm mode changes |
| **Control-IQ** | `LidAaUserModeChange` | 52 | 2.4 | Exercise/Sleep mode toggles |
| **CGM Alert** | `LidCgmAlertActivatedDex` | 335 | 15 | High/low/urgent alerts |
| **CGM Alert** | `LidCgmAlertClearedDex` | 334 | 15 | Alert cleared |
| **CGM Alert** | `LidCgmAlertAckDex` | 104 | 4.7 | Alert acknowledged by user |
| **Alarm** | `LidAlertActivated` | 170 | 7.7 | Pump alerts |
| **Alarm** | `LidAlertCleared` | 169 | 7.7 | |
| **Alarm** | `LidAlarmActivated` | 70 | 3.2 | `alarmId`, `AlarmMap` |
| **Alarm** | `LidAlarmCleared` | 62 | 2.8 | |
| **Suspension** | `LidPumpingSuspended` | 31 | 1.4 | `suspendreasonRaw`, `insulinamount` |
| **Suspension** | `LidPumpingResumed` | 31 | 1.4 | |
| **Manual BG** | `LidBgReadingTaken` | 129 | 5.9 | Fingerstick readings |
| **Site Change** | `LidTubingFilled` | 10 | | |
| **Site Change** | `LidCartridgeFilled` | 9 | | |
| **Site Change** | `LidCannulaFilled` | 3 | | |
| **CGM Session** | `LidCgmJoinSessionG7` | 6 | | Sensor session start |
| **CGM Session** | `LidCgmStopSessionG7` | 2 | | Sensor session end — expect BG gaps after |
| **System** | `LidNewDay` | 24 | 1 | Day boundary marker |
| **System** | `LidAaDailyStatus` | 24 | 1 | Daily summary |
| **System** | `LidVersionsA` | 24 | 1 | Firmware info |
| **System** | `LidVersionInfo` | 4 | | |
| **System** | `LidShelfMode` | 4 | | Pump power state |
| **System** | `LidArmInit` | 4 | | |

### 3.4 Device Settings

From `device['lastUpload']['settings']` — the programmed pump settings at time of last upload.

**Active Basal Profile ("Profile 1"):**

| Time | Basal (u/hr) | ISF (mg/dL per u) | Carb Ratio (g/u) | Target BG |
|---|---|---|---|---|
| 00:00 | 1.000 | 23 | 4.2 | 110 |
| 03:00 | 1.200 | 23 | 4.2 | 110 |
| 06:00 | 1.550 | 23 | 4.2 | 110 |
| 12:00 | 1.450 | 18 | 4.2 | 110 |
| 18:00 | 1.550 | 20 | 4.2 | 110 |

Note: These are current settings only. Historical profile changes are not directly available from the API.

### 3.5 Normalized DataFrames

The ingestion layer should produce these DataFrames from API events:

**`cgm_df`** — CGM readings

| Column | Type | Source Field | Notes |
|---|---|---|---|
| timestamp | datetime64[tz] | `eventTimestamp.datetime` | Timezone-aware (America/Los_Angeles) |
| bg_mgdl | int64 | `currentglucosedisplayvalue` | mg/dL, ready to use |

Stats (22-day sample, n=6,348): mean=158.4, std=65.5, min=33, Q1=110, median=144, Q3=195, max=439

**`bolus_df`** — Completed boluses

| Column | Type | Source Field | Notes |
|---|---|---|---|
| timestamp | datetime64[tz] | `eventTimestamp.datetime` | |
| insulin_units | float | `insulindelivered` | Units, ready to use |
| bolus_id | int | `bolusid` | Links to request events |

**`request_df`** — Bolus requests (joins Msg1 + Msg2 + Msg3 on `bolusid`)

| Column | Type | Source Event.Field | Notes |
|---|---|---|---|
| timestamp | datetime64[tz] | Msg1 `eventTimestamp.datetime` | |
| bolus_id | int | Msg1 `bolusid` | Links to `bolus_df` |
| carbs_g | int | Msg1 `carbamount` | **Already in grams — do NOT divide by 1000** (verified against CSV) |
| bg_mgdl | int | Msg1 `BG` | mg/dL |
| iob | float | Msg1 `IOB` | Insulin on board at time of request |
| bolus_source | str | Msg2 `optionsRaw` + `useroverrideRaw` | `"auto"` / `"user"` / `"override"` — see derivation below |
| food_insulin | float | Msg3 `foodbolussize` | Insulin calculated for carbs (units) |
| correction_insulin | float | Msg3 `correctionbolussize` | Insulin calculated for BG correction (units) |
| total_requested | float | Msg3 `totalbolussize` | Total recommended (may differ from delivered if override) |
| bolus_category | str | derived (enrichment) | See DATA_NOTES §3. One of: `auto_correction` / `user_meal` / `user_meal_and_correction` / `user_correction_only` / `override_up` / `override_down` / `unknown` |
| override_delta | float | derived (enrichment) | `total_requested − (food_insulin + correction_insulin)` when `bolus_source == "override"`, else NaN. Positive = override increased dose. |

**`bolus_source` derivation** (from Msg2 fields, verified against CSV `BolusType`):

```
if optionsRaw in (3, 6):     → "auto"       # Control-IQ automatic correction
elif useroverrideRaw == 1:    → "override"   # User changed the suggested amount
else:                         → "user"       # Standard user-initiated bolus
```

`Msg2.optionsRaw` maps to:
| Value | Label | Meaning |
|---|---|---|
| 0 | Standard Bolus | User-initiated via pump UI |
| 1 | Extended Bolus | Extended delivery |
| 2 | Quick Bolus | Quick bolus button |
| 3 | Automatic Bolus | **Control-IQ auto-correction** |
| 4 | BLE Standard Bolus | Via phone/BLE |
| 5 | BLE Extended Bolus | Extended via phone/BLE |
| 6 | Eating Soon Automatic Bolus | **CIQ eating-soon auto** |
| 7 | Late Bolus | Late bolus |

Additional Msg2 fields: `declinedcorrectionRaw` (0=no, 1=user declined correction portion), `ISF`, `targetbg`.

**`basal_df`** — 5-minute basal delivery

| Column | Type | Source Field | Notes |
|---|---|---|---|
| timestamp | datetime64[tz] | `eventTimestamp.datetime` | |
| commanded_rate | float | `commandedRate / 1000` | **Must divide by 1000 — see §6 Bug 2** |

**`events_df`** — Non-bolus pump events (site changes, mode changes, CGM sessions, etc.)

| Column | Type | Source Field | Notes |
|---|---|---|---|
| timestamp | datetime64[tz] | `eventTimestamp.datetime` | |
| event_type | str | derived | e.g. `site_change`, `mode_change`, `cgm_session` |
| event_subtype | str | derived | e.g. `cartridge`, `tubing`, `cannula`, `exercising` |
| previous_mode | str / None | derived (mode_change only) | |
| details | str (JSON) | per-event | JSON payload (e.g. `{"insulin_volume": 240}` for cartridge fills) |
| seqnum | int | `seqNum` | Monotonic pump sequence number |
| pump_serial | str | | |
| forced_by_alarm | bool / NA | derived (enrichment) | Only populated for `event_type == "site_change"` (NaN / None otherwise). `True` when a site_change falls within `site_change_detection.forced_window_minutes` of an activated `BatteryShutdownAlarm` — i.e. the fill is firmware-forced, not a real site rotation. Override: a `cartridge` subtype whose `details.insulin_volume >= site_change_detection.cartridge_real_fill_threshold` is treated as a genuine site change (`False`) even inside the window. See DATA_NOTES §2. |

### 3.6 Enriched tables

Derived frames produced by `ingestion/enrich.py` on top of the normalized frames in §3.5. These are not direct projections of API events — each is the output of a builder that consumes one or more normalized frames plus config. They are persisted alongside the normalized parquets and are what downstream consumers (sanity_check, daily_viz, detection) actually load.

For the broader column-level enrichments that attach to §3.5 frames themselves (`requests.bolus_category`, `requests.override_delta`, `events.forced_by_alarm`), see the corresponding columns in §3.5 and DATA_NOTES §2–§3.

**`site_issues_df`** — Suspected site-failure episodes clustered from `alarms_df` (see DATA_NOTES §1)

| Column | Type | Source | Notes |
|---|---|---|---|
| first_occlusion_ts | datetime64[tz] | `alarms_df` | Timestamp of the first `OcclusionAlarm` activation in the cluster |
| last_occlusion_ts | datetime64[tz] | `alarms_df` | Timestamp of the last activation in the cluster |
| occlusion_count | int | derived | Number of activated occlusions in the cluster (always `>= site_change_detection.min_occlusions_for_cluster`) |
| resolved_by_site_change_ts | datetime64[tz] / NaT | `events_df` | Timestamp of the first `site_change` event strictly after `last_occlusion_ts` whose `forced_by_alarm != True`. NaT if no such site change exists yet. |
| resolution_delay_minutes | float | derived | `(resolved_by_site_change_ts − last_occlusion_ts)` in minutes; NaN when unresolved |
| pump_serial | str | | |

Clustering rule: activated occlusions are grouped while the gap to the previous activation is `<= site_change_detection.occlusion_cluster_window_minutes`; a larger gap starts a new cluster. Only clusters meeting `min_occlusions_for_cluster` are emitted.

Resolution lookup requires `events_df` to already carry `forced_by_alarm` (i.e. `enrich_events_df` has run). If the column is absent, `build_site_issues_df` falls back to treating every `site_change` as a valid resolver — a conservative default that over-resolves clusters rather than silently dropping them. The enrich pipeline (§3.7) orders the steps correctly so this fallback only matters for ad-hoc callers.

Dedup key: `["first_occlusion_ts", "pump_serial"]`. Persisted to `data/processed/site_issues.parquet`.

**`cgm_gaps_df`** — CGM out-of-range episodes paired from `alarms_df` (see DATA_ISSUES #6)

| Column | Type | Source | Notes |
|---|---|---|---|
| start_ts | datetime64[tz] | `alarms_df` | Timestamp of the `cgm_out_of_range` activated row |
| end_ts | datetime64[tz] / NaT | `alarms_df` | Timestamp of the matching `cleared` row; `NaT` if the gap is still open at the time the frame is built |
| duration_minutes | float | derived | `(end_ts − start_ts)` in minutes; `NaN` when `ongoing` |
| pump_serial | str | | |
| ongoing | bool | derived | `True` when `end_ts` is `NaT` (unpaired activation at end of data) |

Pairing rule: iterate `alarm_name == "cgm_out_of_range"` rows sorted by timestamp, maintaining one open activated event. A second `activated` without an intervening `cleared` force-closes the prior episode at the new activation timestamp and logs a warning (analogous to double-suspend handling); an unpaired `cleared` is logged and skipped.

Detection code uses these windows to exclude periods where Control-IQ had no CGM signal (and therefore couldn't adjust basal / deliver auto-corrections) from trend and anomaly analysis.

Dedup key: `["start_ts", "pump_serial"]`. Persisted to `data/processed/cgm_gaps.parquet`.

### 3.7 Enrichment pipeline

Enrichment is a pure in-memory transform layer that runs inside `build_all(events, serial, config)` after the normalized-frame builders finish but before `storage.save_df` is called. Passing `config=None` skips enrichment (back-compat for raw-frame tests); the production `fetch` / `fetch-day` / `update` paths always pass the loaded config, so downstream consumers (`sanity_check`, `daily_viz`, detection) always see enriched frames on disk.

The top-level orchestrator is `enrich_all(frames, config)` in `ingestion/enrich.py`. It runs the four enrichment steps in this order:

1. **`enrich_requests_df(requests)`** — derives `bolus_category` and `override_delta` columns on `requests_df` (§3.5). Pure per-row transform; no cross-frame dependencies.
2. **`enrich_events_df(events, alarms, site_cfg)`** — adds the `forced_by_alarm` column to `events_df` (§3.5) by cross-referencing `BatteryShutdownAlarm` activations in `alarms_df` and, for `cartridge` subtypes, parsing `details.insulin_volume` against the `cartridge_real_fill_threshold` override.
3. **`build_site_issues_df(alarms, events, site_cfg)`** — produces `site_issues_df` (§3.6). Runs after step 2 so that `forced_by_alarm` is available to distinguish real site rotations from firmware-forced refills when looking for a cluster's resolver.
4. **`build_cgm_gaps_df(alarms)`** — produces `cgm_gaps_df` (§3.6). Depends only on `alarms_df`, so ordering versus the others is not load-bearing.

All four functions are side-effect-free: no API calls, no I/O, no config mutation. Missing input frames are tolerated (callers may pass partial dicts for testing). Tunables live under the `site_change_detection` block in `config/user_config.yaml`; see that file for the four keys (`forced_window_minutes`, `cartridge_real_fill_threshold`, `occlusion_cluster_window_minutes`, `min_occlusions_for_cluster`).

---

## 4. Detection outputs

Schemas returned by the `detection/` package (see `TECHNICAL_SPEC.md` "Detection Logic" for the algorithms). All functions are pure, source-agnostic, and DataFrame-in / DataFrame-out; they consume the normalized / enriched frames of §3.5 and §3.6 plus an `AppConfig`.

### 4.1 `detect_anomalies` (detection/anomaly.py)

Input: `cgm_df` (shape: §3.5 `cgm_df`, plus the `backfilled` bool column), `AppConfig`.

Output: one row per anomalous CGM event.

| Column | Type | Notes |
|---|---|---|
| timestamp | datetime64[tz] | Sensor timestamp of the event (mirrors `cgm_df.timestamp`, including backfilled rows) |
| anomaly_type | str | One of `"spike"`, `"drop"`, `"flatline"` |
| bg_at_event | int64 | BG at the flagged reading (mg/dL) |
| rate_mgdl_per_min | float64 | Slope into the event `(bg − prev_bg) / Δt_min`. `0.0` for flatline rows. |
| confidence | float64 | v1 heuristic in `[0, 1]`; placeholder — use for ordering only, not calibration |
| is_backfilled_context | bool | Mirrors the source reading's `backfilled` flag; surfaces can segregate historical-only events |

### 4.2 `detect_meals` (detection/meal.py)

Input: `cgm_df` (§3.5), enriched `requests_df` (§3.5, including `bolus_category`), `AppConfig`.

Output: one row per detected missed meal (sustained BG rise without a food-carrying bolus in the lookback window).

| Column | Type | Notes |
|---|---|---|
| timestamp | datetime64[tz] | Run start (the first CGM timestamp of the sustained rise) |
| bg_start | int64 | BG at the reading immediately before `timestamp` (the baseline that started the rise) |
| bg_peak | int64 | Max BG in `[timestamp, timestamp + 2h]`, clipped to available data |
| rise_rate_per_5min | float64 | Mean of the `sustained_intervals` per-interval deltas in the detected run (mg/dL per 5-min interval) |
| meal_window | str | `"window_0"`, `"window_1"`, … positionally keyed to `meal_detection.meal_windows` in YAML; `"off_window"` for rises outside any window |
| confidence | float64 | v1 heuristic in `[0, 1]`; placeholder — use for ordering only, not calibration |

### 4.3 `daily_features` (detection/features.py)

Input: `frames` dict (keys `cgm`, `bolus`, `basal`, `requests`, `alarms`, `suspension`, `cgm_gaps`), `datetime.date`, `AppConfig`.

Output: a `dict` of 16 keys (one row of features for the given day).

Day boundaries use `config.ingestion.timezone`. Empty-frame defaults: counts/sums → `0`, ratios/means → `NaN` (undefined, not zero). `std_bg` uses `ddof=0`.

| Key | Type | Definition |
|---|---|---|
| date | `datetime.date` | The date being summarized (same value passed in) |
| tir_70_180 | float | Fraction of CGM readings in `[bg_targets.low, bg_targets.high]` |
| time_below_70 | float | Fraction of CGM readings below `bg_targets.low` |
| time_above_180 | float | Fraction of CGM readings in `(bg_targets.high, 250]` |
| time_above_250 | float | Fraction of CGM readings above 250 mg/dL |
| mean_bg | float | Mean CGM BG across the day (mg/dL) |
| std_bg | float | Population std (`ddof=0`) of CGM BG (mg/dL) |
| cv_bg | float | `std_bg / mean_bg`; NaN when `mean_bg == 0` |
| total_daily_insulin | float | Sum of bolus units plus basal integrated across the day (`commanded_rate × duration`); units |
| basal_bolus_ratio | float | `basal_total / bolus_total`; NaN when no bolus |
| meal_count | int | Count of `requests` rows with `bolus_category` in `{user_meal, user_meal_and_correction, override_up}` |
| total_carbs_g | int | Sum of `carbs_g` across those meal rows (grams) |
| overnight_dip | float | `mean(bg[04:00–06:00]) − mean(bg[00:00–02:00])` (mg/dL); NaN if either window is empty. Windows hardcoded in v1. |
| mean_postprandial_peak | float | Mean Δ between the 2-hour post-bolus peak and the nearest-CGM-before-bolus anchor (10-min tolerance) across meal-category boluses; NaN when no meal rows have a nearby CGM anchor. Uses the CGM anchor — not `requests.bg_mgdl` — because the latter is a finger-stick and often 0/missing. |
| alarm_count | int | Count of `alarms` rows with `action == "activated"` |
| suspension_minutes | float | Total minutes of pump suspension overlap with the day window. Ongoing (unpaired) suspends are treated as ending at `day_end`. |
| out_of_range_minutes | float | Total minutes of `cgm_gaps` overlap with the day window. `ongoing=True` gaps are treated as ending at `day_end`. |

### 4.4 `cluster_days` (detection/clustering.py)

Input: `features_df` (one row per day; must contain `date` plus numeric feature columns from §4.3), `AppConfig`, optional `retrain: bool = False`.

Output:

| Column | Type | Notes |
|---|---|---|
| date | same dtype as input | Passed through from `features_df.date` |
| cluster_id | int64 | `KMeans.predict` label in `[0, clustering.n_clusters)` |
| distance_to_centroid | float64 | Euclidean distance in the scaled feature space to the assigned centroid |

Determinism: `clustering.random_seed` (default `42`) is passed to `KMeans(random_state=…)`; `n_init=10`. Model artefacts live in `clustering.model_dir` (default `data/models/`): `scaler_v1.pkl`, `kmeans_v1.pkl`, and `features_v1.json` (training-time column ordering — `predict` reorders the caller's feature matrix to match). `retrain=True` refits and overwrites; `retrain=False` with no saved model fits-and-warns (first-run ergonomics). NaNs are imputed per-batch with column median (all-NaN columns fall back to `0.0`); imputed columns are logged at WARNING level.

### 4.5 `daily_clusters.parquet`

Produced by the `cluster-days` CLI (`scripts/run_detection.py::run_clustering`). Identical schema to `cluster_days` output (§4.4); written to `data/processed/daily_clusters.parquet`. One row per day covered by the `--start` / `--end` range (defaults: earliest → latest CGM date).

---

## 5. Source 3: pydexcom (Planned)

Not yet implemented. Will provide real-time Dexcom G7 CGM readings every 5 minutes via the Dexcom Share API.

- Same underlying data as `LidCgmDataG7` from tconnectsync
- Lower latency (direct from Dexcom cloud vs. Tandem upload delay)
- Purpose: live anomaly detection and Telegram notifications
- Open question: how much faster is pydexcom vs. tconnectsync autoupdate? This determines whether pydexcom is strictly necessary.

---

## 6. Data Quality Issues

### Bug 1: `carbamount` is already in grams — do NOT divide by 1000 ✅ RESOLVED

**Problem:** `api_levels.md` documented `carbamount` as milliunits (divide by 1000 for grams). The notebook applied `/1000`, producing values like `0.040g`.

**Verification:** Matched 3 API bolus events against CSV rows by timestamp + BG:

| API `carbamount` | CSV `CarbSize` | BG | Match |
|---|---|---|---|
| 40 | 40 | 146 | exact |
| 55 | 55 | 98 | exact |
| 20 | 20 | 238 | exact |

**Conclusion:** `carbamount` is already in grams. The `/1000` in the notebook and the "milliunits" note in `api_levels.md` are wrong. Use the raw value directly.

### Bug 2: `commandedRate` is milliunits/hr, not u/hr ✅ RESOLVED

**Problem:** The notebook documents `commandedRate` as "already in u/hr" and does not divide. But `basal_df.describe()` shows mean=1396, max=6898.

**Verification:** Pump profile basal rates are 1.0–1.55 u/hr. 1396/1000 = 1.396 u/hr — matches the profile range. 6898/1000 = 6.898 u/hr — plausible CIQ correction max.

**Conclusion:** `commandedRate` is in milliunits/hr. Divide by 1000 when building `basal_df`.

### Issue 3: CSV timestamps lack timezone

CSV `EventDateTime` values (e.g., `2026-02-21T00:03:01`) have no timezone offset. API timestamps include it (e.g., `2026-02-26T20:53:13-08:00`).

**Resolution:** Ingestion must attach the configured timezone when parsing CSV timestamps.

### Issue 4: Duplicate CGM readings (API only)

Notebook `cgm_df` rows 2–3 show identical timestamps (`2026-02-26 21:08:27`) with values 146 and 147. Verified: CSV EGV data has **zero** duplicate timestamps — this is an API-only issue.

**Resolution:** Deduplicate API CGM readings — keep one per 5-min interval.

---

## 7. Field → Use Case Mapping

| Use Case | Required Data | Source(s) | Key Fields |
|---|---|---|---|
| **Meal Detection** | BG rising pattern + bolus history | CGM + bolus requests | `bg_mgdl` time series, `carbs_g`, `timestamp`, `no_bolus_window` from config |
| **Anomaly Detection (spikes/lows)** | BG time series | CGM (trailing window only) | `bg_mgdl`, thresholds from config |
| **Anomaly Detection (suspensions)** | BG + pump suspensions | CGM + suspension events | `bg_mgdl`, `suspendreasonRaw` |
| **Daily Pattern Clustering** | Full-day BG curves + insulin + meals | CGM + bolus + basal | 288 pts/day, time-in-range, mean, std, meal count, total daily dose |
| **Basal Analysis** | CIQ-adjusted vs. programmed rates | Basal delivery + settings | `commanded_rate`, `commandedRateSource`, profile basals |
| **IOB Modeling** | Insulin delivery + decay | Bolus + basal + settings | `insulindelivered`, `IOB`, `insulin_duration` from settings |
| **Site Change Tracking** | Infusion set age | Site change events | Cartridge/cannula/tubing fill timestamps |
| **Control-IQ Mode Analysis** | Mode changes + BG outcomes | Mode events + CGM | `LidAaUserModeChange` (exercise/sleep) correlated with BG |

---

## 8. Open Questions

1. ~~**`carbamount` units**~~ — **RESOLVED.** Already in grams; do not divide by 1000.
2. **Historical pump settings** — API only provides current settings. If ISF/carb ratio changed over time, historical bolus calculations may use stale parameters. Is there a way to get past profiles?
3. **pydexcom vs. tconnectsync latency** — How much faster is pydexcom for real-time detection? If tconnectsync autoupdate polls every 5 min anyway, is pydexcom needed?
5. ~~**Auto vs. user bolus distinction**~~ — **RESOLVED.** `Msg2.optionsRaw` (3=Auto, 6=EatingSoon) + `Msg2.useroverrideRaw` (1=override). Verified against CSV. See §3.5 `request_df` for full derivation.
6. **CGM gap handling** — When `LidCgmStopSessionG7` fires, there will be gaps in BG data. Detection engine needs a strategy (mark and exclude from analysis).
7. **Normalized schema definition** — Need to finalize the canonical DataFrame schemas for the API ingestion layer.
