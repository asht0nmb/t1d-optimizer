# T1D Engine — Technical Spec
Last updated: 2026-06-11

## System Overview

T1D Engine is a Type 1 diabetes data intelligence system. It ingests CGM + insulin pump data (Tandem via tconnectsync and CSV exports; Dexcom via the Share API), runs real-time and retrospective detection, and surfaces results through dashboards and Telegram alerts.

The system is organized as two deployment shells around a shared, storage-agnostic `core/` library:

- **OSS local shell** — Streamlit + Plotly dashboard (`apps/local/`) reading `data/processed/*.parquet` via `ParquetStorage`. Everything runs on the user's machine; no cloud dependencies.
- **Hosted personal shell** — Next.js dashboard on Vercel (`apps/web/`), Supabase Postgres as the system of record, a Vercel Python cron worker (`api/index.py`, triggered by cron-job.org) running the live meal-rise alert loop, Telegram for notifications, and a GitHub Actions nightly job syncing Tandem data into Supabase.

`core/` never knows which backend it runs against: shells instantiate a concrete `Storage` implementation at startup and pass it down via constructor injection. Detection is **source-agnostic** — pure DataFrame-in / DataFrame-out, no `ingestion/` imports — so the same detector runs against live Dexcom Share readings and historical parquet/Postgres frames.

---

## Data Schema

### Source 1: Tandem Basal/Bolus/BG / Control-IQ (via tconnectsync)
- See [DATA_CATALOG.md](DATA_CATALOG.md) for the complete field inventory and event type reference.

### Source 2: 'Backup' CSV BG/Bolus data
- Three data sets within each CSV (CGM BG (EGV) data, manually entered BG data (should be ignored), and bolus data)
    - sets must be extracted individually

### Source 3: Dexcom CGM (live, via pydexcom / Dexcom Share)
- Frequency: every 5 minutes
- Consumed by the live meal-rise cron loop (see Live Loop Topology below)

---

## Storage Layer

The backend-agnostic data layer is the `Storage` Protocol in `core/storage/protocol.py`. Three implementations exist, all validated by the parameterized contract suite in `tests/core/test_storage_contract.py`:

- `ParquetStorage` (`core/storage/parquet.py`) — local parquet files; the OSS shell default.
- `InMemoryStorage` (`core/storage/memory.py`) — in-process; used by tests.
- `SupabaseStorage` (`core/storage/supabase.py`) — Postgres via psycopg2; used by the personal stack (cron worker, GitHub Actions, dashboard backend).

Key Protocol invariants (each backed by contract tests):

- `read_table` **requires** `since`/`until` bounds to prevent accidental whole-table reads; `read_all_table` is the explicit unbounded escape hatch.
- `delete_range` requires at least one scope (`since` / `until` / `pump_serial`).
- `upsert_table` is idempotent by primary key.
- `record_alert` deduplicates on `(alert_kind, event_ref)` and returns `AlertInsertResult(record, inserted)` so callers can tell whether they won the insert race — the foundation of the live loop's claim-before-send idempotency.

**Connection rules (Supabase):** callers MUST use the transaction-mode pooler URL (`*.pooler.supabase.com:6543`) with an open-do-close lifecycle — `SupabaseStorage.from_pooler_url(url)` as a context manager for short-lived work, `SupabaseStorage(conn=...)` for caller-managed long-lived connections. Direct connections (`db.*.supabase.co:5432`) are reserved for the nightly GitHub Action and the one-shot `scripts/bootstrap_supabase.py`. Postgres-side `idle_in_transaction_session_timeout = '5min'` (migration `0002`) is the backstop.

**RLS model (migration `0003_enable_rls.sql`):** four roles. `postgres` (psycopg2 with the DB password) and `service_role` (server-side admin JWT) have `BYPASSRLS`. `authenticated` and `anon` are subject to RLS: each of the 13 public tables carries one permissive policy (`auth_required_all FOR ALL TO authenticated`), and `anon` has no policy anywhere, so anon-key requests see zero rows. Per-row ownership (`user_id = auth.uid()`) is deferred until a multi-user story exists. Client-side code must go through the anon key + Supabase Auth; server-side code connects as `service_role`/`postgres` and relies on application-level authorization.

Schema lives in `core/schema.py` (table registry) and `db/migrations/0001_init.sql` onward. `ingestion/storage.py` remains a thin shim over `ParquetStorage` so pre-Protocol callers (fetch, view, detection scripts) keep working; new downstream code takes a `Storage` via DI from the start.

---

## Detection v2 (active)

v2 lives in `core/detection/` (pure primitives) and `detection/` (typed config, daily features, calibration). All thresholds come from `config/user_config.yaml` through `detection.config.get_config()` — a cached, validated `AppConfig`.

### Windowing primitive — `core/detection/windowing.py`

`make_window(cgm_df, anchor, pre, post=0, *, expected_interval=5min, gaps_df=None)` slices a CGM frame around a tz-aware `Anchor` (kinds: `"live"` for the cron loop, `"sliding"` for calibration sweeps) and returns a frozen `Window` carrying:

- `samples` — readings in `[anchor − pre, anchor + post]`, sorted ascending;
- `coverage` — `n_present / n_expected`, where `n_expected = floor(span / expected_interval) + 1`;
- `has_gap` — True when the window overlaps any row of a `cgm_gaps` frame; open-ended/ongoing gaps are treated as extending indefinitely forward.

The module is storage-agnostic and side-effect free; everything downstream consumes `Window`, never raw frames.

### Meal-rise detector — `core/detection/meal_rise.py`

`detect_meal_rise(window, MealRiseConfig) -> MealRiseDetection | None` flags a sharp, sustained glucose rise (missed-meal proxy):

1. **Guards** — reject windows with fewer than `min_samples` readings, coverage below `min_coverage`, or `has_gap`.
2. **Slope** — Theil-Sen estimator: the median of all pairwise slopes (mg/dL per minute) across the window. Robust to single-reading jitter and compression spikes.
3. **Start-level gate** — the window's first reading must be inside `[start_level_min, start_level_max]`; rises out of a low recovery or already deep in hyperglycemia are ignored.
4. **Time-of-day multiplier** — the anchor's local hour selects a multiplier from `meal_rise.meal_windows` (inclusive hour ranges; multiplier < 1 lowers the bar during typical mealtimes) or `off_hours_multiplier` otherwise. Note these are inclusive `start_hour <= hour <= end_hour` ranges, unlike legacy `meal_detection`'s half-open pairs.
5. **Threshold** — fire when `slope >= base_slope_mgdl_per_min × multiplier`. The detection records slope, start/end levels, delta, coverage, the threshold and multiplier used, and the raw glucose values (`to_payload()` is JSON-serializable for persistence).

The current `base_slope_mgdl_per_min` is a placeholder pending M2-driven tuning (see calibration below).

### M1 hardening (live alert reliability)

The live loop in `apps/personal/cron/detect_meal_rise.py` adds production safeguards around the pure detector:

- **Freshness guard** — if the newest CGM reading is older than `meal_rise.max_reading_age_minutes` (default 15, ~3 missed 5-min cycles), detection is skipped entirely. Dexcom Share can serve an hours-old window when no sensor session is active; without this guard the loop could alert on a long-past rise.
- **Idempotent claim-before-send** — order is: refractory check → `find_alert` on `event_ref` (`meal_rise:<latest_ts to the minute>`) → claim via `record_alert` (delivery `pending`) → send Telegram → `record_detection_result` with the delivery outcome. If `record_alert` reports `inserted=False`, another invocation won the race and this one exits without sending. The Telegram outcome lives in `detection_results.payload.telegram_sent`, not on the alert row.
- **Refractory window** — no new alert within `meal_rise.refractory_minutes` of the previous one, regardless of event_ref.
- **Delivery retry with backoff** — each run first sweeps recent claimed alerts whose latest delivery attempt failed and retries them, bounded by `delivery_retry_lookback_hours` (default 24), `delivery_retry_backoff_minutes` (default 15) between attempts, and `delivery_retry_max_attempts` (default 3). Every attempt is recorded as a new `DetectionResult` (`delivery_stage: retry`, incrementing `delivery_attempt`).
- **DST-safe normalization** — Dexcom readings are deduplicated to one per 5-minute bucket by flooring timestamps in UTC (not local time), so clock-change transitions can never raise `AmbiguousTimeError`/`NonExistentTimeError`.
- **Config-driven fetch depth** — the number of readings requested from Dexcom derives from `window_minutes + fetch_buffer_minutes` at `expected_interval_minutes` cadence plus `fetch_readings_padding`; no magic numbers.

### M2 calibration scoring — `detection/calibration/meal_rise_scoring.py`

Retrospective labeling of meal-rise detections against pump bolus context, to tune the placeholder slope threshold from observed data instead of guesses. Pure DataFrame-in / dataclass-out:

- `find_meal_rise_instances(cgm_df, config)` slides the *production* detector across a historical CGM frame (one sliding anchor per reading, timestamps converted to the configured timezone so the time-of-day multiplier sees local hours), then applies the same refractory de-duplication as the live loop so one sustained rise yields one instance.
- `score_instances(detections, requests_df, calib, pump_serial=...)` labels each instance by searching food-carrying boluses (`user_meal`, `user_meal_and_correction`, `override_up`) in `[rise_start − pre_bolus_lookback_minutes, rise_start + late_bolus_lookahead_minutes]`:
  - `pre_bolused` — nearest food bolus precedes the rise start (negative signed delay);
  - `late_bolused` — nearest food bolus follows the rise start;
  - `uncovered` — no food bolus in the window. Uncovered misses are then attributed to how they resolved: the earliest `user_correction_only` / `auto_correction` bolus within `correction_lookahead_minutes` (resolution `user_correction` / `auto_correction`), or `none`.
- `summarize(scored)` reports label counts, uncovered rate, and the resolution breakdown.

Config windows default to 30 / 45 / 180 minutes (`meal_rise_calibration` block). Calibration outputs inform **config edits only** — no automatic threshold changes; retuning lands as a reviewed change to `config/user_config.yaml` with its own dated update doc. Supervised modeling on the labeled dataset is deferred (see Roadmap).

---

## Live Loop Topology

Production path (every 5 minutes):

```
cron-job.org  ──GET/POST + Authorization: Bearer <CRON_SECRET>──▶
Vercel Python worker (api/index.py, repo-root project, framework "Other";
  /api/meal_rise_cron rewritten to /api/index via vercel.json)
  └─▶ run_cron() in apps/personal/cron/detect_meal_rise.py
        1. retry pass over previously failed Telegram deliveries
        2. fetch recent readings from Dexcom Share (pydexcom)
        3. freshness guard (max_reading_age_minutes)
        4. make_window + detect_meal_rise
        5. claim alert + persist DetectionResult in Supabase
           (SupabaseStorage.from_pooler_url — transaction-mode pooler)
        6. send Telegram message (HTML parse mode)
```

The worker returns `401` without the bearer secret and `200`/`500` with the cron exit code. The Next.js app keeps `/api/cron/meal-rise` as a health-only route. Required env: `CRON_SECRET`, `SUPABASE_DB_URL`, `DEXCOM_USERNAME`/`DEXCOM_PASSWORD` (optional `DEXCOM_OUS`), `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`. Without `SUPABASE_DB_URL` the loop refuses to run unless `MEAL_RISE_ALLOW_PARQUET_FALLBACK=true` (local testing only).

Supporting jobs:

- **Nightly Tandem sync** — GitHub Actions workflow `tandem-nightly-sync.yml` runs `scripts/sync_tandem_to_supabase.py` at **06:00 UTC** daily (plus `workflow_dispatch`): incremental tconnectsync fetch, enrichment, then upsert into Supabase over a direct connection.
- **Manual fallback** — `meal-rise-cron.yml` is `workflow_dispatch`-only; it runs the same `detect_meal_rise` worker from GitHub Actions if the Vercel worker or cron-job.org is down.

---

## Daily Features — `detection/features.py`

`daily_features(frames, date, config)` slices the seven normalized/enriched frames to a single day in the configured `ingestion.timezone` and returns a dict of 14 features plus a `date` key (16 fields total). The 14 features span time-in-range / time-in-band breakdown, BG moments (mean, std with `ddof=0`, CV), insulin totals (bolus sum, basal integrated across the day, basal-bolus ratio), meal summary (count + total carbs from food-carrying `bolus_category` rows), an overnight dip metric (04:00–06:00 vs 00:00–02:00 mean), the mean 2-hour postprandial peak anchored at the nearest CGM reading before each meal bolus, and pump-state minutes (alarms, suspensions, out-of-range CGM from `cgm_gaps`). See `detection/features.py` docstring for per-feature semantics and the empty-frame default policy (counts/sums → 0, ratios/means → NaN); see `DATA_CATALOG.md` §4.3 for the column inventory and types.

---

## Detection v1 (legacy, quarantined)

The v1 reference implementation lives in `detection/legacy/` (see `detection/legacy/README.md`). It is not maintained, receives no fixes, and **must never be imported from production code** — any import of `detection.legacy.*` outside tests/notebooks is a review-blocking bug. It is preserved for v2 design reference; the 47 `legacy`-marked tests run opt-in via `uv run pytest -m legacy`. Output schemas are documented in `DATA_CATALOG.md` §4 (legacy output).

Condensed algorithm summaries:

- **Anomaly detection** (`detection/legacy/anomaly.py`) — `detect_anomalies(cgm_df, config)` emits spike events (reading crosses above `anomaly_detection.spike_threshold`, one event per crossing), drop events (mirror against `drop_threshold`), and flatlines (a rolling window of `flatline_consecutive_intervals` contiguous-cadence readings with variance below `flatline_tolerance`).
- **Meal detection** (`detection/legacy/meal.py`) — `detect_meals(cgm_df, requests_df, config)` flags runs of exactly `sustained_intervals` consecutive in-cadence intervals each rising at least `rise_threshold_per_5min`, suppressed when a food-carrying bolus (`user_meal`, `user_meal_and_correction`, `override_up`) exists within `no_bolus_window_minutes` before the run start; labeled by position in the half-open `meal_detection.meal_windows` pairs or `off_window`.
- **Daily clustering** (`detection/legacy/clustering.py`) — `cluster_days(features_df, config, retrain=False)` fits/loads a deterministic `StandardScaler` + `KMeans` pipeline (seeded, artifacts pickled to `clustering.model_dir` with the training column order), with per-batch median imputation for NaNs.

The `meal_detection`, `anomaly_detection`, and `clustering` config blocks exist only for this code (see Config below).

---

## Roadmap (deferred — explicitly not built)

- **Episode / pattern / cause layers** — grouping detections into episodes, clustering recurring patterns, and attributing causes. Design only.
- **Supervised models on the M2-labeled dataset** — once the calibration runner produces a labeled historical corpus, train classifiers/regressors to replace hand-tuned thresholds. Not started; M2 labeling is deterministic window logic only.
- **LLM Telegram assistant** — conversational querying/summarization over the data via Telegram. Not started; today Telegram is one-way alert delivery.

---

## Config (`config/user_config.yaml`)

All thresholds and personal parameters live here — nothing is hardcoded in detection code. `detection.config.get_config()` returns a cached, frozen `AppConfig`; missing required top-level blocks raise `KeyError`, invariant violations raise `ValueError`. Required blocks: `ingestion`, `bg_targets`, `meal_detection`, `anomaly_detection`, `clustering`, `site_change_detection`, `meal_rise`; `meal_rise_calibration` is optional (defaults applied when absent).

Current shape (example values — tune per user):

```yaml
ingestion:
  timezone: "America/Los_Angeles"
  chunk_days: 30
  overlap_days: 1

bg_targets:
  low: 70          # require low < target < high
  high: 180
  target: 110

# meal_detection / anomaly_detection / clustering are consumed only by
# detection/legacy/*. Safe to remove when legacy is retired (still required
# top-level keys today).
meal_detection:
  rise_threshold_per_5min: 8
  sustained_intervals: 3
  no_bolus_window_minutes: 30
  meal_windows:                 # half-open [start, end) hour pairs
    - [6, 10]
    - [11, 14]
    - [17, 23]

anomaly_detection:
  spike_threshold: 180          # require drop_threshold < spike_threshold
  drop_threshold: 70
  flatline_tolerance: 2
  flatline_consecutive_intervals: 12   # >= 2; K=12 == 1 hour at 5-min cadence

clustering:
  method: kmeans
  n_clusters: 5                 # >= 2
  feature_mode: aggregated
  # random_seed: 42             # optional, defaults shown
  # model_dir: data/models

site_change_detection:          # consumed by ingestion enrichment (site_issues)
  forced_window_minutes: 120
  cartridge_real_fill_threshold: 220
  occlusion_cluster_window_minutes: 180
  min_occlusions_for_cluster: 2

notifications:                  # read via raw config by the live loop;
  telegram_bot_token: ""        # env vars TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
  telegram_chat_id: ""          # take precedence

meal_rise:                      # active live detector (v2)
  window_minutes: 30            # trailing window (pre)
  fetch_buffer_minutes: 15      # extra Dexcom history beyond window_minutes
  expected_interval_minutes: 5
  fetch_readings_padding: 3     # extra polls beyond computed count
  min_samples: 4
  min_coverage: 0.7             # in (0, 1]
  base_slope_mgdl_per_min: 1.8  # PLACEHOLDER, tuned in M2 against bolus data
  start_level_min: 70           # gate: ignore rises out of a low recovery
  start_level_max: 250          # gate: ignore rises already deep in hyper
  meal_windows:                 # inclusive local-hour ranges; multiplier < 1 lowers the bar
    - {start_hour: 6,  end_hour: 10, multiplier: 0.7}
    - {start_hour: 11, end_hour: 14, multiplier: 0.7}
    - {start_hour: 17, end_hour: 21, multiplier: 0.7}
  off_hours_multiplier: 1.3
  refractory_minutes: 60
  max_reading_age_minutes: 15   # freshness guard: skip detection on stale CGM
  alert_template: "Fast glucose rise: {start} to {end} mg/dL (about {delta} up in {minutes} min). Flagging in case a meal went unbolused."
  # delivery_retry_lookback_hours: 24     # optional retry knobs (defaults shown)
  # delivery_retry_backoff_minutes: 15
  # delivery_retry_max_attempts: 3

meal_rise_calibration:          # M2 labeling windows (optional block; all > 0)
  pre_bolus_lookback_minutes: 30    # food bolus this far before rise start → pre_bolused
  late_bolus_lookahead_minutes: 45  # food bolus up to this far after rise start → late_bolused
  correction_lookahead_minutes: 180 # attribute an uncovered miss to a correction
```

---

## Real-Time Detection Constraints

- Trailing window only — no future BG context is available to the live loop.
- Thresholds must balance false positives (notification fatigue) against late alerts (no longer actionable). The time-of-day multipliers, start-level gate, refractory window, and M2 calibration all exist to manage this tradeoff.
- Telegram notifications fire only after the alert claim is persisted (claim-before-send), so retries and concurrent invocations can never double-notify.
