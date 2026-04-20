# Enrichment Layer + Detection Engine v1 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Always follow the "write failing tests first → verify FAIL → implement → verify PASS → commit" cycle. Do **not** skip the red step.

**Date:** 2026-04-20
**Depends on:** `docs/plans/2026-03-23-pipeline-fixes.md` — must be fully complete (all 6 resolved issues in `DATA_ISSUES.md` must show commit SHAs).

---

## Goal

Stand up two layers on top of the verified ingestion pipeline:

1. **Enrichment layer** — source-agnostic derived columns and helper tables (`bolus_category`, `override_delta`, `forced_by_alarm`, `site_issues.parquet`, `cgm_gaps.parquet`) that give the detection engine the semantic signals it needs.
2. **Detection engine v1** — the three detection pillars from `TECHNICAL_SPEC.md`: anomaly detection (spike/drop/flatline), missed-meal detection, and daily pattern clustering. All config-driven, all pure functions over normalized DataFrames.

Surfaces (Telegram notifications, Streamlit dashboard, pydexcom live feed) are **out of scope** for this plan and deferred to a future `docs/plans/YYYY-MM-DD-surfaces.md`.

---

## Non-negotiables (read before writing a single line of code)

These are the rules from `CLAUDE.md`, `TECHNICAL_SPEC.md`, `DATA_NOTES.md`, and `DATA_NOTES_2.md`. They override convenience, style preferences, or what looks like a simpler implementation.

1. **Source-agnostic detection.** Every detection function takes normalized DataFrames (`cgm_df`, `requests_df`, `bolus_df`, `basal_df`, `alarms_df`, `events_df`) and a config object. It must not know or care whether the data came from a CSV, tconnectsync, or pydexcom. No `if source == "tandem"` branches.
2. **No hardcoded thresholds.** Every number with clinical meaning (mg/dL, minutes, rates, cluster counts, window sizes) lives in `config/user_config.yaml` and is read at runtime. Magic numbers in source files are a review-blocking bug.
3. **Trailing-window only for real-time.** No function that targets real-time detection may look at rows with `timestamp > now`. Batch/historical helpers may use full context, but they must be clearly named (e.g., `detect_anomalies_historical` vs. `detect_anomalies_realtime`). This plan defines both where relevant.
4. **`BG == 0` is null.** In `requests_df.bg_mgdl`, zero means "BG not available at time of bolus," not actual glucose of 0. Every detection function that reads `bg_mgdl` from requests must filter or coerce `0 → NaN`.
5. **`carbs_g` is raw grams.** Do not divide by 1000. Already verified against CSV.
6. **Backfill timestamp rule (DATA_NOTES_2.md).** For `cgmDataTypeRaw == 2` readings, `timestamp` is the sensor-reading time (`egvTimestamp`), not the pump-received time. Detection and clustering treat backfilled readings as valid signal at their sensor timestamps. This is already handled in `build_cgm_df` — do **not** reintroduce pump-received-time assumptions downstream.
7. **Site changes after BatteryShutdownAlarm are mostly forced.** Per DATA_NOTES.md §2, ~90% of site_change events within a configurable window after BatteryShutdownAlarm are firmware-required, not real. Detection must tag and skip these for site rotation analysis.
8. **Occlusion clustering matters.** Per DATA_NOTES.md §1, 2+ occlusions in a short window = suspected site failure. Isolated occlusions are noise.
9. **Auto corrections never contain food** (DATA_NOTES.md §3). Any logic that computes "meal insulin" must exclude `bolus_source == "auto"` rows.

---

## Architecture overview

### Directory additions

```
ingestion/
  enrich.py              # NEW — post-build derivations (bolus_category, forced_by_alarm, site_issues, cgm_gaps)

detection/               # NEW package
  __init__.py
  config.py              # YAML loader + typed config dataclass + cached accessor
  anomaly.py             # detect_anomalies (spike/drop/flatline)
  meal.py                # detect_meals (missed-meal heuristic)
  features.py            # daily_features aggregator
  clustering.py          # cluster_days (kmeans on features)

data/
  models/                # NEW — pickled clustering model + scaler
```

### Rationale: `ingestion/enrich.py` vs. inlining in `builders.py`

**Decision: new `ingestion/enrich.py` module.** Reasons:
- Builders are pure single-event-list → DataFrame transforms. Enrichment needs **cross-frame lookups** (site changes require alarms; occlusion clusters require alarms+events). Mixing these into builders breaks the unit-testable per-builder contract.
- Enrichment is logically a second stage. Keeping it separate means `build_all` in `builders.py` stays a thin router; `enrich_all` runs after.
- Downstream detection code can import directly from `ingestion.enrich` for helpers (e.g., `pair_cgm_gaps`) without pulling in the whole builder module.

The call chain becomes:

```
fetch.py → builders.build_all(events, serial) → enrich.enrich_all(frames, config) → storage.save_df(...)
```

All downstream consumers (`scripts/sanity_check.py`, `scripts/daily_viz.py`, detection functions) see enriched frames automatically because enrichment runs before persistence.

### Data contracts (added by this plan)

| Frame / Column | Type | Notes |
|---|---|---|
| `requests.bolus_category` | str | One of: `auto_correction`, `user_meal`, `user_meal_and_correction`, `user_correction_only`, `override_up`, `override_down`, `unknown` |
| `requests.override_delta` | float | `total_requested - (food_insulin + correction_insulin)`, NaN unless `bolus_source == "override"` |
| `events.forced_by_alarm` | bool | Only populated for `event_type == "site_change"`; NaN otherwise |
| `site_issues.parquet` | table | Occlusion-cluster episodes (see Task 1.3) |
| `cgm_gaps.parquet` | table | Paired `cgm_out_of_range` episodes (see Task 1.4) |

### Detection output contracts

| Function | Returns |
|---|---|
| `detect_anomalies(cgm_df, config)` | DataFrame[`timestamp`, `anomaly_type`, `bg_at_event`, `rate_mgdl_per_min`, `confidence`, `is_backfilled_context`] |
| `detect_meals(cgm_df, requests_df, config)` | DataFrame[`timestamp`, `bg_start`, `bg_peak`, `rise_rate_per_5min`, `confidence`, `meal_window`] |
| `daily_features(frames, date)` | dict (one row per day) |
| `cluster_days(features_df, config)` | DataFrame[`date`, `cluster_id`, `distance_to_centroid`] |

---

## Tech stack

- Python 3.12+ (already required)
- pandas (frames), numpy (numerics)
- **pyyaml** — already transitively available; add explicitly if not direct.
- scikit-learn — KMeans, StandardScaler (already in pyproject)
- pytest — test runner (already set up)
- xgboost/lightgbm — **not used in v1**. Clustering is KMeans; anomaly/meal are heuristic. Reserved for a future plan.
- Stdlib: `dataclasses`, `functools.lru_cache`, `pickle`, `pathlib`.

If `pyyaml` isn't already installed as a direct dep, add it in Task 2.1 Step 0.

---

## Config changes (`config/user_config.yaml`)

Add these sections in Task 1.2 and Task 2.2 (in order — do not front-load). The final shape must be:

```yaml
ingestion:
  timezone: "America/Los_Angeles"
  chunk_days: 30
  overlap_days: 1

bg_targets:
  low: 70
  high: 180
  target: 110

site_change_detection:                    # NEW (Task 1.2 + 1.3)
  forced_window_minutes: 120              # after BatteryShutdownAlarm
  occlusion_cluster_window_minutes: 180
  min_occlusions_for_cluster: 2

meal_detection:
  rise_threshold_per_5min: 8
  sustained_intervals: 3
  no_bolus_window_minutes: 30
  meal_windows:
    - [6, 10]
    - [11, 14]
    - [17, 23]

anomaly_detection:
  spike_threshold: 180
  drop_threshold: 70
  flatline_tolerance: 2
  flatline_consecutive_intervals: 6       # NEW (Task 2.2) — 30 min at 5-min sampling

clustering:
  method: kmeans
  n_clusters: 5
  feature_mode: aggregated
  random_seed: 42                         # NEW (Task 2.5) — deterministic runs
  model_dir: "data/models"                # NEW (Task 2.5)

notifications:
  telegram_bot_token: ""
  telegram_chat_id: ""
  confidence_threshold: 0.75
  cooldown_minutes: 30
```

Any task that adds a new key must update `config/user_config.yaml` as part of its commit.

---

# Phase 1 — Enrichment Layer

## Task 1.1 — `bolus_category` + `override_delta` on `requests_df`

Per `DATA_NOTES.md` §3. The `bolus_source` field (`auto`/`user`/`override`) doesn't capture the full semantics: whether food was present, whether a correction rode along, and whether an override moved the total up or down. Detection engine needs a single categorical column.

**Files:**
- Create: `ingestion/enrich.py` — add `enrich_requests_df` and module-level `enrich_all`.
- Modify: `ingestion/builders.py::build_all` — call `enrich_all(result, config)` before returning.
- Modify: `ingestion/fetch.py` — pass the loaded config into `build_all`. (Builders currently don't receive config; pass it through the fetch orchestrator.)
- Create: `tests/test_enrich.py` — unit tests for enrichment.
- Modify: `docs/DATA_CATALOG.md` §3.5 `request_df` — add the two new columns.

**Logic table (exact):**

| `bolus_source` | `carbs_g` | `food_insulin` | `correction_insulin` | `total_requested` vs `food+correction` | → `bolus_category` |
|---|---|---|---|---|---|
| `auto` | 0 | 0 | > 0 | n/a | `auto_correction` |
| `auto` | 0 | 0 | 0 | n/a | `auto_correction` *(rare, zero-delivered)* |
| `user` | > 0 | > 0 | > 0 | n/a | `user_meal_and_correction` |
| `user` | > 0 | > 0 | 0 | n/a | `user_meal` |
| `user` | 0 | 0 | > 0 | n/a | `user_correction_only` |
| `user` | 0 | 0 | 0 | n/a | `unknown` *(log a warning; shouldn't happen)* |
| `override` | any | any | any | `total_requested > food+correction + ε` | `override_up` |
| `override` | any | any | any | `total_requested < food+correction − ε` | `override_down` |
| `override` | any | any | any | within ±ε | `user_meal` / `user_correction_only` (fall back to the non-override logic) |
| `unknown` | any | any | any | n/a | `unknown` |

Where `ε = 0.01` (units) to absorb float noise.

`override_delta` formula:
- If `bolus_source == "override"`: `override_delta = total_requested - (food_insulin + correction_insulin)`
- Else: `override_delta = NaN`

Sign convention: positive = override increased dose, negative = override decreased dose. Matches DATA_NOTES §3 ("Overrides are always increases in this dataset" is an observation, not an invariant — code must not assume it).

**Step 1: Write the failing tests.**

Create `tests/test_enrich.py` with a `TestEnrichRequestsDf` class covering:

```python
from ingestion.enrich import enrich_requests_df

def test_auto_correction_no_food():
    # bolus_source=auto, carbs=0, food=0, correction=1.2
    df = _requests_row(source="auto", carbs=0, food=0.0, correction=1.2, total=1.2)
    out = enrich_requests_df(df)
    assert out.iloc[0]["bolus_category"] == "auto_correction"
    assert pd.isna(out.iloc[0]["override_delta"])

def test_user_meal_only():
    df = _requests_row(source="user", carbs=40, food=9.5, correction=0.0, total=9.5)
    assert enrich_requests_df(df).iloc[0]["bolus_category"] == "user_meal"

def test_user_meal_and_correction():
    df = _requests_row(source="user", carbs=40, food=9.5, correction=1.1, total=10.6)
    assert enrich_requests_df(df).iloc[0]["bolus_category"] == "user_meal_and_correction"

def test_user_correction_only():
    df = _requests_row(source="user", carbs=0, food=0.0, correction=1.4, total=1.4)
    assert enrich_requests_df(df).iloc[0]["bolus_category"] == "user_correction_only"

def test_override_up():
    df = _requests_row(source="override", carbs=0, food=0.0, correction=0.2, total=2.5)
    out = enrich_requests_df(df)
    assert out.iloc[0]["bolus_category"] == "override_up"
    assert out.iloc[0]["override_delta"] == pytest.approx(2.3)

def test_override_down():
    df = _requests_row(source="override", carbs=30, food=7.0, correction=0.0, total=4.0)
    out = enrich_requests_df(df)
    assert out.iloc[0]["bolus_category"] == "override_down"
    assert out.iloc[0]["override_delta"] == pytest.approx(-3.0)

def test_override_within_epsilon_falls_back_to_user():
    df = _requests_row(source="override", carbs=30, food=7.0, correction=0.0, total=7.005)
    out = enrich_requests_df(df)
    assert out.iloc[0]["bolus_category"] == "user_meal"
    assert out.iloc[0]["override_delta"] == pytest.approx(0.005)  # still computed

def test_non_override_has_nan_override_delta():
    df = _requests_row(source="user", carbs=0, food=0.0, correction=1.0, total=1.0)
    assert pd.isna(enrich_requests_df(df).iloc[0]["override_delta"])

def test_unknown_source_passes_through():
    df = _requests_row(source="unknown", carbs=0, food=0.0, correction=0.0, total=0.0)
    assert enrich_requests_df(df).iloc[0]["bolus_category"] == "unknown"

def test_empty_df_preserves_columns():
    df = pd.DataFrame(columns=["timestamp", "bolus_id", "carbs_g", "bg_mgdl", "iob",
                               "bolus_source", "food_insulin", "correction_insulin",
                               "total_requested", "pump_serial"])
    out = enrich_requests_df(df)
    assert out.empty
    assert "bolus_category" in out.columns
    assert "override_delta" in out.columns
```

`_requests_row` is a helper fixture — build a single-row DataFrame with the standard request columns.

**Step 2: Run tests to verify they fail.**

```bash
uv run pytest tests/test_enrich.py -v
```
Expected: `ImportError: cannot import name 'enrich_requests_df' from 'ingestion.enrich'`.

**Step 3: Implement `enrich_requests_df` in `ingestion/enrich.py`.**

```python
import pandas as pd

_EPSILON = 0.01  # units of insulin

def enrich_requests_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        out = df.copy()
        out["bolus_category"] = pd.Series(dtype="object")
        out["override_delta"] = pd.Series(dtype="float64")
        return out

    out = df.copy()
    food = out["food_insulin"].fillna(0.0)
    corr = out["correction_insulin"].fillna(0.0)
    total = out["total_requested"].fillna(0.0)
    carbs = out["carbs_g"].fillna(0)
    source = out["bolus_source"]

    expected = food + corr
    delta = total - expected

    def _categorize(row, exp, d):
        src = row["bolus_source"]
        c = row["carbs_g"] or 0
        f = row["food_insulin"] or 0
        k = row["correction_insulin"] or 0
        if src == "auto":
            return "auto_correction"
        if src == "override":
            if d > _EPSILON:
                return "override_up"
            if d < -_EPSILON:
                return "override_down"
            # within epsilon → fall back to non-override categorization
            if c > 0 and f > 0 and k > 0:
                return "user_meal_and_correction"
            if c > 0 and f > 0:
                return "user_meal"
            if c == 0 and k > 0:
                return "user_correction_only"
            return "unknown"
        if src == "user":
            if c > 0 and f > 0 and k > 0:
                return "user_meal_and_correction"
            if c > 0 and f > 0:
                return "user_meal"
            if c == 0 and k > 0:
                return "user_correction_only"
            return "unknown"
        return "unknown"

    out["bolus_category"] = [
        _categorize(r, e, d) for r, e, d in zip(
            out.to_dict("records"), expected.tolist(), delta.tolist()
        )
    ]
    out["override_delta"] = delta.where(source == "override", other=float("nan"))
    return out
```

Add an `enrich_all` function that routes:

```python
def enrich_all(frames: dict[str, pd.DataFrame], config: dict) -> dict[str, pd.DataFrame]:
    frames = dict(frames)  # shallow copy
    frames["requests"] = enrich_requests_df(frames["requests"])
    # later tasks add: events, site_issues, cgm_gaps
    return frames
```

Wire `enrich_all` into `build_all`:
- `build_all` takes a new `config: dict | None = None` kwarg (default None for back-compat).
- If `config is not None`, call `enrich_all(result, config)` before returning.
- Update `ingestion/fetch.py` to load config (reuse the loader introduced in Task 2.1, or a temporary local load until Task 2.1 lands — if Task 1.x runs first, define a minimal `load_config()` in `ingestion/enrich.py` for now and consolidate in Task 2.1).

**Pragmatic ordering note:** Because Task 2.1 formalizes the config loader, implement Tasks 1.x with a local `_load_config()` helper in `ingestion/enrich.py` that just does `yaml.safe_load(open("config/user_config.yaml"))`. Task 2.1 will replace that with a shared loader.

**Step 4: Run tests to verify they pass.**

```bash
uv run pytest tests/test_enrich.py::TestEnrichRequestsDf -v
uv run pytest -v
```
All must pass. If `test_builders.py::test_build_all_includes_alarms` or similar breaks because of signature change, update those tests to pass `config=None`.

**Step 5: Update `docs/DATA_CATALOG.md`.**

In §3.5 `request_df` table, append two rows:

| bolus_category | str | derived | See DATA_NOTES §3. Values: auto_correction / user_meal / user_meal_and_correction / user_correction_only / override_up / override_down / unknown |
| override_delta | float | derived | `total_requested − (food_insulin + correction_insulin)` when `bolus_source="override"`, else NaN |

**Step 6: Commit.**

```bash
git add ingestion/enrich.py ingestion/builders.py ingestion/fetch.py tests/test_enrich.py docs/DATA_CATALOG.md
git commit -m "feat(enrich): derive bolus_category and override_delta on requests_df"
```

---

## Task 1.2 — Tag forced-site-change events after `BatteryShutdownAlarm`

Per `DATA_NOTES.md` §2. Roughly 90% of site_change events inside a post-BatteryShutdown window are firmware-forced cartridge/tubing refills, not true site changes. Detection engine must ignore them for site rotation / infusion set lifetime analysis.

**v1 heuristic (timestamp-only):** any site_change event within `forced_window_minutes` after a `BatteryShutdownAlarm` activated event gets `forced_by_alarm = True`. Cartridge fill volume refinement is deferred (see Open Questions).

**Files:**
- Modify: `config/user_config.yaml` — add `site_change_detection.forced_window_minutes: 120`.
- Modify: `ingestion/enrich.py` — add `enrich_events_df(events_df, alarms_df, config)`.
- Modify: `enrich_all` — call the new function.
- Modify: `tests/test_enrich.py` — add `TestEnrichEventsDf`.
- Modify: `docs/DATA_CATALOG.md` — document `forced_by_alarm` on events.

**Step 1: Write the failing tests.**

```python
class TestEnrichEventsDf:
    def test_site_change_within_window_tagged_forced(self):
        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {"timestamp": ts("09:15"), "event_type": "site_change", "event_subtype": "cartridge"},
        ])
        out = enrich_events_df(events, alarms, {"forced_window_minutes": 120})
        assert out.iloc[0]["forced_by_alarm"] == True

    def test_site_change_outside_window_not_forced(self):
        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {"timestamp": ts("11:30"), "event_type": "site_change", "event_subtype": "cartridge"},  # 3h24m after
        ])
        out = enrich_events_df(events, alarms, {"forced_window_minutes": 120})
        assert out.iloc[0]["forced_by_alarm"] == False

    def test_site_change_before_alarm_not_forced(self):
        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {"timestamp": ts("07:00"), "event_type": "site_change", "event_subtype": "cartridge"},
        ])
        out = enrich_events_df(events, alarms, {"forced_window_minutes": 120})
        assert out.iloc[0]["forced_by_alarm"] == False

    def test_non_site_change_has_nan_forced(self):
        events = _events_frame([
            {"timestamp": ts("10:00"), "event_type": "mode_change", "event_subtype": "exercising"},
        ])
        out = enrich_events_df(events, _alarms_frame([]), {"forced_window_minutes": 120})
        # Only site_change rows have a meaningful value; others should be NaN/None
        val = out.iloc[0]["forced_by_alarm"]
        assert pd.isna(val) or val is None

    def test_no_battery_shutdown_alarm_all_false(self):
        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {"timestamp": ts("09:00"), "event_type": "site_change", "event_subtype": "cartridge"},
        ])
        out = enrich_events_df(events, alarms, {"forced_window_minutes": 120})
        assert out.iloc[0]["forced_by_alarm"] == False

    def test_multiple_site_changes_some_forced(self):
        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {"timestamp": ts("09:00"), "event_type": "site_change", "event_subtype": "cartridge"},  # forced
            {"timestamp": ts("09:05"), "event_type": "site_change", "event_subtype": "tubing"},     # forced
            {"timestamp": ts("15:00"), "event_type": "site_change", "event_subtype": "cannula"},   # real
        ])
        out = enrich_events_df(events, alarms, {"forced_window_minutes": 120})
        assert list(out["forced_by_alarm"]) == [True, True, False]
```

**Step 2: Run tests, verify FAIL.**
```bash
uv run pytest tests/test_enrich.py::TestEnrichEventsDf -v
```
Expected: `ImportError` / `AttributeError` on `enrich_events_df`.

**Step 3: Implement.**

```python
def enrich_events_df(
    events_df: pd.DataFrame,
    alarms_df: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    out = events_df.copy()
    if out.empty:
        out["forced_by_alarm"] = pd.Series(dtype="object")
        return out

    out["forced_by_alarm"] = pd.NA

    if alarms_df is None or alarms_df.empty:
        mask = out["event_type"] == "site_change"
        out.loc[mask, "forced_by_alarm"] = False
        return out

    shutdowns = alarms_df[
        (alarms_df["alarm_name"] == "BatteryShutdownAlarm")
        & (alarms_df["action"] == "activated")
    ]["timestamp"].tolist()

    window = pd.Timedelta(minutes=config["forced_window_minutes"])
    site_mask = out["event_type"] == "site_change"

    def _is_forced(ts):
        return any(s <= ts <= s + window for s in shutdowns)

    out.loc[site_mask, "forced_by_alarm"] = out.loc[site_mask, "timestamp"].apply(_is_forced)
    return out
```

Wire into `enrich_all`:

```python
def enrich_all(frames, config):
    frames = dict(frames)
    frames["requests"] = enrich_requests_df(frames["requests"])
    frames["events"] = enrich_events_df(
        frames["events"], frames.get("alarms"), config.get("site_change_detection", {})
    )
    return frames
```

**Step 4: Update `config/user_config.yaml`** — add the `site_change_detection` block with `forced_window_minutes: 120`.

**Step 5: Run tests.**
```bash
uv run pytest tests/test_enrich.py -v
uv run pytest -v
```
All pass.

**Step 6: Update `docs/DATA_CATALOG.md`** — add `forced_by_alarm` to the events_df schema.

**Step 7: Commit.**
```bash
git add ingestion/enrich.py config/user_config.yaml tests/test_enrich.py docs/DATA_CATALOG.md
git commit -m "feat(enrich): tag firmware-forced site changes after BatteryShutdownAlarm"
```

---

## Task 1.3 — Occlusion cluster helper → `site_issues.parquet`

Per `DATA_NOTES.md` §1. Cluster `OcclusionAlarm` activations that arrive close together and flag them as suspected site failures. Optionally link to the resolving `site_change` event.

**Files:**
- Modify: `config/user_config.yaml` — add `occlusion_cluster_window_minutes: 180`, `min_occlusions_for_cluster: 2`.
- Modify: `ingestion/enrich.py` — add `build_site_issues_df(alarms_df, events_df, config)`.
- Modify: `enrich_all` — call and attach as `frames["site_issues"]`.
- Modify: `ingestion/storage.py` — register `site_issues` in `PARQUET_FILES` and `DEDUP_KEYS`.
- Modify: `tests/test_enrich.py` — add `TestBuildSiteIssuesDf`.

**Schema:**

| Column | Type | Notes |
|---|---|---|
| `first_occlusion_ts` | datetime64[tz] | Timestamp of first occlusion in the cluster |
| `last_occlusion_ts` | datetime64[tz] | Timestamp of last occlusion in the cluster |
| `occlusion_count` | int | Number of activated occlusions in cluster |
| `resolved_by_site_change_ts` | datetime64[tz] / NaT | First site_change after `last_occlusion_ts` (excluding `forced_by_alarm=True`) |
| `resolution_delay_minutes` | float | `(resolved_by_site_change_ts - last_occlusion_ts).total_seconds() / 60`, NaN if unresolved |
| `pump_serial` | str | |

**Clustering rule:** Sort occlusion-activated rows by timestamp. Start a new cluster when the gap to the previous occlusion exceeds `occlusion_cluster_window_minutes`. Emit only clusters with `count >= min_occlusions_for_cluster`.

**Resolution:** The first `site_change` event (any subtype, with `forced_by_alarm != True`) strictly after `last_occlusion_ts`. If no such event exists, leave NaT / NaN.

**Dedup key:** `["first_occlusion_ts", "pump_serial"]`.

**Step 1: Write failing tests.**

```python
class TestBuildSiteIssuesDf:
    def test_single_occlusion_not_a_cluster(self):
        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        out = build_site_issues_df(alarms, _empty_events(), _cfg())
        assert out.empty

    def test_two_occlusions_within_window_cluster(self):
        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("10:45"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        out = build_site_issues_df(alarms, _empty_events(), _cfg())
        assert len(out) == 1
        assert out.iloc[0]["occlusion_count"] == 2

    def test_three_occlusions_all_one_cluster(self):
        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("11:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("12:30"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        out = build_site_issues_df(alarms, _empty_events(), _cfg())
        assert len(out) == 1
        assert out.iloc[0]["occlusion_count"] == 3

    def test_occlusions_split_into_two_clusters_when_gap_exceeds_window(self):
        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("10:30"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("16:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("16:30"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        out = build_site_issues_df(alarms, _empty_events(), _cfg())
        assert len(out) == 2

    def test_resolution_linked_to_site_change(self):
        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("10:30"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {"timestamp": ts("11:00"), "event_type": "site_change", "event_subtype": "cannula",
             "forced_by_alarm": False},
        ])
        out = build_site_issues_df(alarms, events, _cfg())
        assert out.iloc[0]["resolved_by_site_change_ts"] == ts("11:00")
        assert out.iloc[0]["resolution_delay_minutes"] == pytest.approx(30.0)

    def test_forced_site_change_does_not_resolve(self):
        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("10:30"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {"timestamp": ts("11:00"), "event_type": "site_change", "event_subtype": "cartridge",
             "forced_by_alarm": True},
        ])
        out = build_site_issues_df(alarms, events, _cfg())
        assert pd.isna(out.iloc[0]["resolved_by_site_change_ts"])

    def test_unresolved_cluster_has_nat_resolution(self):
        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("10:30"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        out = build_site_issues_df(alarms, _empty_events(), _cfg())
        assert pd.isna(out.iloc[0]["resolved_by_site_change_ts"])
        assert pd.isna(out.iloc[0]["resolution_delay_minutes"])

    def test_empty_alarms_returns_empty(self):
        out = build_site_issues_df(_alarms_frame([]), _empty_events(), _cfg())
        assert out.empty
        for col in ["first_occlusion_ts", "last_occlusion_ts", "occlusion_count",
                    "resolved_by_site_change_ts", "resolution_delay_minutes", "pump_serial"]:
            assert col in out.columns
```

`_cfg()` returns `{"occlusion_cluster_window_minutes": 180, "min_occlusions_for_cluster": 2}`.

**Step 2: Run tests, verify FAIL.**

**Step 3: Implement.** Standard sort + group-by-gap algorithm. Note: if events_df doesn't yet have `forced_by_alarm` (e.g., Task 1.2 didn't run first), default to treating all site_changes as non-forced. Execution order must be 1.1 → 1.2 → 1.3.

**Step 4: Register in `storage.py`.**

```python
PARQUET_FILES["site_issues"] = "site_issues.parquet"
DEDUP_KEYS["site_issues"] = ["first_occlusion_ts", "pump_serial"]
```

**Step 5: Wire into `enrich_all`** — call after `enrich_events_df` (it depends on `forced_by_alarm`).

**Step 6: Verify fetch pipeline persists it.** `storage.save_df("site_issues", frames["site_issues"])` should already work via the generic routing in `fetch.py`. If `fetch.py` hardcodes the list of frames it saves, extend that list.

**Step 7: Run full suite.**
```bash
uv run pytest -v
```

**Step 8: Commit.**
```bash
git add ingestion/enrich.py ingestion/storage.py config/user_config.yaml tests/test_enrich.py docs/DATA_CATALOG.md
git commit -m "feat(enrich): cluster occlusion alarms into site_issues.parquet"
```

---

## Task 1.4 — CGM out-of-range episodes → `cgm_gaps.parquet`

Per `DATA_ISSUES.md` #6. Pair `cgm_out_of_range` activated/cleared rows from `alarms_df` into episodes with durations. Also exposes the same data as a helper view that detection can use to exclude windows where Control-IQ was blind.

**Schema:**

| Column | Type | Notes |
|---|---|---|
| `start_ts` | datetime64[tz] | `cgm_out_of_range` activated |
| `end_ts` | datetime64[tz] / NaT | Matching cleared event; NaT if unclosed at end of data |
| `duration_minutes` | float | NaN if unclosed |
| `pump_serial` | str | |
| `ongoing` | bool | True when `end_ts` is NaT (unclosed at time of build) |

**Pairing rule:** Iterate alarm rows sorted by timestamp where `alarm_name == "cgm_out_of_range"`. Maintain one open activated event; when a `cleared` arrives, pair them. If a new `activated` fires while one is open, close the previous one at the new timestamp and log a warning (analogous to double-suspend handling).

**Files:**
- Modify: `ingestion/enrich.py` — add `build_cgm_gaps_df(alarms_df)`.
- Modify: `enrich_all`, `storage.PARQUET_FILES`, `storage.DEDUP_KEYS`.
- Modify: `tests/test_enrich.py`.

**Dedup key:** `["start_ts", "pump_serial"]`.

**Step 1: Failing tests.**

```python
class TestBuildCgmGapsDf:
    def test_single_closed_gap(self):
        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "cgm_out_of_range", "action": "activated"},
            {"timestamp": ts("10:25"), "alarm_name": "cgm_out_of_range", "action": "cleared"},
        ])
        out = build_cgm_gaps_df(alarms)
        assert len(out) == 1
        assert out.iloc[0]["duration_minutes"] == pytest.approx(25.0)
        assert out.iloc[0]["ongoing"] == False

    def test_unclosed_gap_marked_ongoing(self):
        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "cgm_out_of_range", "action": "activated"},
        ])
        out = build_cgm_gaps_df(alarms)
        assert len(out) == 1
        assert pd.isna(out.iloc[0]["end_ts"])
        assert pd.isna(out.iloc[0]["duration_minutes"])
        assert out.iloc[0]["ongoing"] == True

    def test_multiple_sequential_gaps(self):
        alarms = _alarms_frame([
            {"timestamp": ts("08:00"), "alarm_name": "cgm_out_of_range", "action": "activated"},
            {"timestamp": ts("08:10"), "alarm_name": "cgm_out_of_range", "action": "cleared"},
            {"timestamp": ts("14:00"), "alarm_name": "cgm_out_of_range", "action": "activated"},
            {"timestamp": ts("14:30"), "alarm_name": "cgm_out_of_range", "action": "cleared"},
        ])
        out = build_cgm_gaps_df(alarms)
        assert len(out) == 2

    def test_double_activated_closes_previous(self, caplog):
        alarms = _alarms_frame([
            {"timestamp": ts("08:00"), "alarm_name": "cgm_out_of_range", "action": "activated"},
            {"timestamp": ts("08:10"), "alarm_name": "cgm_out_of_range", "action": "activated"},  # no clear!
            {"timestamp": ts("08:30"), "alarm_name": "cgm_out_of_range", "action": "cleared"},
        ])
        with caplog.at_level("WARNING"):
            out = build_cgm_gaps_df(alarms)
        assert len(out) == 2
        assert "unpaired" in caplog.text.lower() or "double" in caplog.text.lower()

    def test_ignores_non_cgm_out_of_range(self):
        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "cgm_high", "action": "activated"},
            {"timestamp": ts("10:05"), "alarm_name": "cgm_high", "action": "cleared"},
        ])
        out = build_cgm_gaps_df(alarms)
        assert out.empty

    def test_empty_alarms(self):
        out = build_cgm_gaps_df(_alarms_frame([]))
        assert out.empty
        for col in ["start_ts", "end_ts", "duration_minutes", "pump_serial", "ongoing"]:
            assert col in out.columns
```

**Step 2: Run, verify FAIL.**

**Step 3: Implement.** Mirror `build_suspension_df`'s pairing style in `builders.py`. Use `logging.getLogger(__name__).warning(...)` for double-activated.

**Step 4: Register in storage, wire into enrich_all.**

**Step 5: Run tests.**

**Step 6: Commit.**
```bash
git add ingestion/enrich.py ingestion/storage.py tests/test_enrich.py docs/DATA_CATALOG.md
git commit -m "feat(enrich): derive cgm_gaps.parquet from cgm_out_of_range alarm pairs"
```

---

## Task 1.5 — Documentation & status updates

**Files:**
- Modify: `docs/DATA_CATALOG.md` — ensure all new columns / tables are documented. Add a new §3.6 section "Enriched tables" describing `site_issues` and `cgm_gaps`.
- Modify: `docs/operating_docs/HANDOFF.md` — append a "Session 5 — Enrichment Layer" entry summarizing what landed, current frame inventory, and any non-obvious gotchas discovered during implementation.
- Modify: `docs/operating_docs/DATA_ISSUES.md` — update Issue #6 status to "Resolved in `<SHA>`" once Task 1.4 is committed.

**Step 1: Write the doc updates.** No tests here; this is pure documentation.

**Step 2: Eyeball-verify** by running:
```bash
uv run python main.py fetch-day --date 2026-03-19
uv run python main.py check --date 2026-03-19
```
Confirm the new columns/tables appear in parquet inventory and print sensibly.

**Step 3: Commit.**
```bash
git add docs/
git commit -m "docs: document enrichment layer (bolus_category, forced_by_alarm, site_issues, cgm_gaps)"
```

---

# Phase 2 — Detection Engine v1

## Task 2.1 — Config loader (`detection/config.py`)

Central, validated, cached config access. Everything downstream uses this — no one re-opens the YAML file.

**Files:**
- Create: `detection/__init__.py` (empty `__all__` for now).
- Create: `detection/config.py`.
- Create: `tests/test_detection_config.py`.
- Modify (if needed): `pyproject.toml` — add `pyyaml>=6.0` as an explicit dep if it's not already declared. Check first:
  ```bash
  uv run python -c "import yaml; print(yaml.__version__)"
  ```
  If it imports (transitive), still add it explicitly to `pyproject.toml` so we own the dep.

**Design:**

```python
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import yaml

@dataclass(frozen=True)
class BgTargets:
    low: int
    high: int
    target: int

@dataclass(frozen=True)
class MealDetectionConfig:
    rise_threshold_per_5min: float
    sustained_intervals: int
    no_bolus_window_minutes: int
    meal_windows: tuple[tuple[int, int], ...]

@dataclass(frozen=True)
class AnomalyDetectionConfig:
    spike_threshold: float
    drop_threshold: float
    flatline_tolerance: float
    flatline_consecutive_intervals: int

@dataclass(frozen=True)
class ClusteringConfig:
    method: str
    n_clusters: int
    feature_mode: str
    random_seed: int
    model_dir: str

@dataclass(frozen=True)
class SiteChangeDetectionConfig:
    forced_window_minutes: int
    occlusion_cluster_window_minutes: int
    min_occlusions_for_cluster: int

@dataclass(frozen=True)
class AppConfig:
    bg_targets: BgTargets
    meal_detection: MealDetectionConfig
    anomaly_detection: AnomalyDetectionConfig
    clustering: ClusteringConfig
    site_change_detection: SiteChangeDetectionConfig
    timezone: str
    raw: dict  # untyped escape hatch for future keys

CONFIG_PATH = Path("config/user_config.yaml")

def load_config(path: Path | None = None) -> AppConfig: ...

@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return load_config()
```

**Validation rules (enforced in `load_config`):**
- Required top-level keys: `ingestion`, `bg_targets`, `meal_detection`, `anomaly_detection`, `clustering`, `site_change_detection`. Missing → `KeyError` with explicit key name.
- `bg_targets.low < bg_targets.target < bg_targets.high`. Invalid → `ValueError`.
- `anomaly_detection.drop_threshold < anomaly_detection.spike_threshold`. Invalid → `ValueError`.
- `meal_detection.meal_windows`: list of `[start_hour, end_hour]` where `0 <= start < end <= 24`. Invalid → `ValueError`.
- `clustering.n_clusters >= 2`. Invalid → `ValueError`.
- `anomaly_detection.flatline_consecutive_intervals >= 2`. Invalid → `ValueError`.

**Step 1: Failing tests.**

```python
class TestLoadConfig:
    def test_valid_config_loads(self, tmp_path):
        p = tmp_path / "cfg.yaml"
        p.write_text(_VALID_CONFIG_YAML)
        cfg = load_config(p)
        assert cfg.bg_targets.target == 110
        assert cfg.anomaly_detection.flatline_consecutive_intervals == 6

    def test_missing_top_level_key_raises(self, tmp_path):
        p = tmp_path / "cfg.yaml"
        p.write_text("bg_targets: {low: 70, high: 180, target: 110}\n")
        with pytest.raises(KeyError, match="meal_detection"):
            load_config(p)

    def test_invalid_bg_targets_ordering(self, tmp_path):
        # target > high
        p = tmp_path / "cfg.yaml"
        p.write_text(_CONFIG_WITH_BAD_TARGETS)
        with pytest.raises(ValueError, match="bg_targets"):
            load_config(p)

    def test_drop_threshold_not_below_spike(self, tmp_path):
        p = tmp_path / "cfg.yaml"
        p.write_text(_CONFIG_WITH_INVERTED_THRESHOLDS)
        with pytest.raises(ValueError):
            load_config(p)

    def test_n_clusters_below_2_invalid(self, tmp_path):
        p = tmp_path / "cfg.yaml"
        p.write_text(_CONFIG_WITH_N_CLUSTERS_1)
        with pytest.raises(ValueError):
            load_config(p)

    def test_get_config_caches(self, monkeypatch):
        # Call twice, assert yaml.safe_load called once
        ...
```

**Step 2: Run, verify FAIL.**

**Step 3: Implement.**

**Step 4: Also expose via `ingestion.enrich`:** replace the temporary `_load_config()` in `enrich.py` with `from detection.config import get_config`. Update `enrich_all` callers accordingly.

**Step 5: Run full suite, verify PASS.**

**Step 6: Commit.**
```bash
git add detection/ tests/test_detection_config.py ingestion/enrich.py pyproject.toml uv.lock
git commit -m "feat(detection): config loader with validation and caching"
```

---

## Task 2.2 — Anomaly detection (`detection/anomaly.py`)

Simplest of the three. Implement first.

**Function signature:**
```python
def detect_anomalies(cgm_df: pd.DataFrame, config: AppConfig) -> pd.DataFrame: ...
```

**Output columns:**

| Column | Type | Notes |
|---|---|---|
| `timestamp` | datetime64[tz] | Time of the anomalous event |
| `anomaly_type` | str | `"spike"` \| `"drop"` \| `"flatline"` |
| `bg_at_event` | int | BG reading at the flagged timestamp |
| `rate_mgdl_per_min` | float | For spike/drop: delta from previous reading ÷ Δminutes. For flatline: 0.0. |
| `confidence` | float in [0,1] | Heuristic — see below |
| `is_backfilled_context` | bool | True if the flagged reading has `backfilled=True` |

**Detection rules:**

- **Spike:** Any reading where `bg_mgdl > config.anomaly_detection.spike_threshold` **and** the previous reading (sorted by `timestamp`) was `<= threshold`. This catches the *crossing* moment, not every reading while high. One event per crossing.
- **Drop:** Mirror image. `bg_mgdl < drop_threshold` and previous was `>= threshold`.
- **Flatline:** Rolling window of length `flatline_consecutive_intervals` (default 6 = 30 min). If `variance` over the window is `< flatline_tolerance` and each reading-to-reading Δtime is within normal sensor cadence (≤ 7 min), flag the last reading in the window. Do **not** emit repeated overlapping flatlines — after flagging, skip forward by the window length before re-checking.

**Confidence heuristic (v1, pure arithmetic):**
- Spike: `min(1.0, (bg - spike_threshold) / spike_threshold)` — farther over threshold = more confident. A reading of 220 with threshold 180 → confidence 0.22.
- Drop: `min(1.0, (drop_threshold - bg) / drop_threshold)`.
- Flatline: `1.0 - (variance / flatline_tolerance)`.

These are deliberately simple placeholders. Detection engine notifications should not use these directly until v2 refines confidence scoring — see Open Questions.

**Backfill handling (non-negotiable rule #6):**
- Include `backfilled=True` readings in the series — their `timestamp` is already the true sensor time.
- Set `is_backfilled_context = True` on any output row whose originating reading is backfilled. Downstream (surfaces) may choose to treat them as historical-only.

**Real-time variant (deferred to Phase 3):** Out of scope for v1. The function signature is built so a `trailing_only=True` kwarg is a future addition that slices `cgm_df` to rows with `timestamp <= now`.

**Files:**
- Create: `detection/anomaly.py`.
- Create: `tests/test_detection_anomaly.py`.

**Step 1: Failing tests.**

```python
from detection.anomaly import detect_anomalies
from detection.config import AppConfig  # or a test fixture builder

def _cgm_series(readings, start=datetime(2026, 3, 19, 0, 0, tzinfo=PST), step_min=5):
    rows = []
    for i, bg in enumerate(readings):
        rows.append({
            "timestamp": start + timedelta(minutes=i * step_min),
            "bg_mgdl": bg,
            "backfilled": False,
            "sensor_timestamp": None,
            "pump_serial": "TEST",
            "seqnum": i,
        })
    return pd.DataFrame(rows)

class TestSpikeDetection:
    def test_spike_detected_at_crossing(self, default_config):
        df = _cgm_series([120, 150, 175, 190, 210, 220])  # crosses 180 between idx 2 and 3
        out = detect_anomalies(df, default_config)
        spikes = out[out["anomaly_type"] == "spike"]
        assert len(spikes) == 1
        assert spikes.iloc[0]["bg_at_event"] == 190

    def test_no_spike_when_already_high(self, default_config):
        df = _cgm_series([200, 210, 220, 230])
        out = detect_anomalies(df, default_config)
        assert (out["anomaly_type"] == "spike").sum() == 0

class TestDropDetection:
    def test_drop_detected_at_crossing(self, default_config):
        df = _cgm_series([100, 85, 75, 65, 60])
        out = detect_anomalies(df, default_config)
        drops = out[out["anomaly_type"] == "drop"]
        assert len(drops) == 1
        assert drops.iloc[0]["bg_at_event"] == 65

class TestFlatlineDetection:
    def test_flatline_detected(self, default_config):
        # 6 readings with variance well below tolerance
        df = _cgm_series([140, 141, 140, 141, 140, 141])
        out = detect_anomalies(df, default_config)
        assert (out["anomaly_type"] == "flatline").sum() == 1

    def test_noisy_series_no_flatline(self, default_config):
        df = _cgm_series([140, 160, 120, 180, 100, 200])
        out = detect_anomalies(df, default_config)
        assert (out["anomaly_type"] == "flatline").sum() == 0

    def test_flatline_not_repeated_overlapping(self, default_config):
        # 12 flat readings — should emit at most 2 non-overlapping events
        df = _cgm_series([140] * 12)
        out = detect_anomalies(df, default_config)
        flats = out[out["anomaly_type"] == "flatline"]
        assert 1 <= len(flats) <= 2

class TestBackfilledContext:
    def test_backfilled_reading_flagged(self, default_config):
        df = _cgm_series([120, 150, 175, 190, 210])
        df.loc[3, "backfilled"] = True
        out = detect_anomalies(df, default_config)
        row = out[out["bg_at_event"] == 190].iloc[0]
        assert row["is_backfilled_context"] == True

class TestEmptyOrInsufficientData:
    def test_empty_df(self, default_config):
        df = _cgm_series([])
        out = detect_anomalies(df, default_config)
        assert out.empty
        for col in ["timestamp", "anomaly_type", "bg_at_event",
                    "rate_mgdl_per_min", "confidence", "is_backfilled_context"]:
            assert col in out.columns

    def test_single_reading_no_anomalies(self, default_config):
        df = _cgm_series([200])
        assert detect_anomalies(df, default_config).empty

class TestConfidence:
    def test_spike_confidence_increases_with_magnitude(self, default_config):
        low = _cgm_series([120, 185])
        high = _cgm_series([120, 250])
        c_low = detect_anomalies(low, default_config).iloc[0]["confidence"]
        c_high = detect_anomalies(high, default_config).iloc[0]["confidence"]
        assert c_high > c_low
```

A pytest `conftest.py` fixture `default_config` returns a built `AppConfig` from the checked-in YAML.

**Step 2: Verify FAIL.**

**Step 3: Implement.**

**Step 4: Run, verify PASS.**

**Step 5: Also add `flatline_consecutive_intervals: 6`** to `config/user_config.yaml` in the same commit.

**Step 6: Commit.**
```bash
git add detection/anomaly.py tests/test_detection_anomaly.py config/user_config.yaml tests/conftest.py
git commit -m "feat(detection): anomaly detection for spikes, drops, and flatlines"
```

---

## Task 2.3 — Missed-meal detection (`detection/meal.py`)

Identify sustained BG rises that lack a covering bolus — candidate missed meals.

**Function:**
```python
def detect_meals(cgm_df: pd.DataFrame, requests_df: pd.DataFrame, config: AppConfig) -> pd.DataFrame: ...
```

**Logic:**

1. Sort `cgm_df` by `timestamp`. Compute `delta_5min = bg_mgdl.diff()`, `gap_minutes = timestamp.diff().dt.total_seconds() / 60`. Only consider deltas where the gap is in `[4, 7]` minutes — normal sensor cadence.
2. Identify runs of `sustained_intervals` (config) consecutive intervals where `delta_5min >= rise_threshold_per_5min`.
3. For each run, look back `no_bolus_window_minutes` from the **first** rising interval's timestamp. If any row in `requests_df` within that window has `bolus_category` ∈ {`user_meal`, `user_meal_and_correction`, `override_up`} (i.e., any food-carrying bolus), **skip** the run.
4. Otherwise, emit one detection:
   - `timestamp` = first rising interval's timestamp
   - `bg_start` = bg at that first rising interval
   - `bg_peak` = max bg in the next 2 hours (clipped to end of cgm_df)
   - `rise_rate_per_5min` = mean of the run's 5-min deltas
   - `meal_window` = label matching `config.meal_detection.meal_windows`, or `"off_window"` if outside all configured windows
   - `confidence` = heuristic below

**Confidence heuristic (v1):**
`base = min(1.0, rise_rate / (2 * rise_threshold))`. Bonus +0.1 if `meal_window != "off_window"`. Bonus +0.1 if `bg_peak > bg_targets.high`. Clamp to [0, 1].

**Exclude** backfilled gaps — if the run includes readings spanning a `cgm_gaps` episode, skip (Control-IQ was blind; gap is noise not meal).

**Non-negotiables applied:**
- Exclude `bolus_source == "auto"` from bolus-lookback: auto corrections don't cover food.
- Treat `bg_mgdl == 0` in `requests_df` as null (filter rows that have that field zero — they still count as boluses, just the BG at bolus was missing; do not filter by BG here).

**Files:**
- Create: `detection/meal.py`.
- Create: `tests/test_detection_meal.py`.

**Step 1: Failing tests.**

```python
class TestDetectMeals:
    def test_sustained_rise_without_bolus_detected(self, default_config):
        # 5-min deltas: 0, 12, 15, 18, 10 → 3 consecutive >= 8
        cgm = _cgm_series([100, 100, 112, 127, 145, 155])
        requests = _empty_requests()
        out = detect_meals(cgm, requests, default_config)
        assert len(out) == 1

    def test_sustained_rise_with_recent_user_meal_ignored(self, default_config):
        cgm = _cgm_series([100, 112, 127, 145])
        requests = _requests_frame([
            {"timestamp": cgm.iloc[0]["timestamp"] - timedelta(minutes=10),
             "bolus_category": "user_meal", "bolus_source": "user",
             "carbs_g": 40, "food_insulin": 9.5, "correction_insulin": 0.0,
             "total_requested": 9.5, "bg_mgdl": 120, "iob": 0.0},
        ])
        out = detect_meals(cgm, requests, default_config)
        assert out.empty

    def test_auto_correction_does_not_count_as_meal_bolus(self, default_config):
        cgm = _cgm_series([100, 112, 127, 145])
        requests = _requests_frame([
            {"timestamp": cgm.iloc[0]["timestamp"] - timedelta(minutes=10),
             "bolus_category": "auto_correction", "bolus_source": "auto",
             "carbs_g": 0, "food_insulin": 0.0, "correction_insulin": 1.2,
             "total_requested": 1.2, "bg_mgdl": 120, "iob": 0.0},
        ])
        out = detect_meals(cgm, requests, default_config)
        assert len(out) == 1  # auto correction does not suppress

    def test_short_rise_below_sustained_intervals_ignored(self, default_config):
        # only 2 rising intervals, need 3
        cgm = _cgm_series([100, 112, 127, 127])
        out = detect_meals(cgm, _empty_requests(), default_config)
        assert out.empty

    def test_meal_window_label_applied(self, default_config):
        # Run starts at 07:00 → within [6,10] breakfast window
        cgm = _cgm_series(
            [100, 112, 127, 145],
            start=datetime(2026, 3, 19, 7, 0, tzinfo=PST),
        )
        out = detect_meals(cgm, _empty_requests(), default_config)
        assert out.iloc[0]["meal_window"] != "off_window"

    def test_off_hours_labeled_off_window(self, default_config):
        cgm = _cgm_series(
            [100, 112, 127, 145],
            start=datetime(2026, 3, 19, 3, 0, tzinfo=PST),
        )
        out = detect_meals(cgm, _empty_requests(), default_config)
        assert out.iloc[0]["meal_window"] == "off_window"

    def test_gap_in_cgm_does_not_create_false_rise(self, default_config):
        # 12-min gap between readings should break the sustained-intervals check
        t0 = datetime(2026, 3, 19, 8, 0, tzinfo=PST)
        df = pd.DataFrame([
            {"timestamp": t0,                          "bg_mgdl": 100},
            {"timestamp": t0 + timedelta(minutes=5),   "bg_mgdl": 112},
            {"timestamp": t0 + timedelta(minutes=17),  "bg_mgdl": 127},  # 12-min gap
            {"timestamp": t0 + timedelta(minutes=22),  "bg_mgdl": 145},
        ])
        # fill required columns
        for col in ["backfilled", "sensor_timestamp", "pump_serial", "seqnum"]:
            df[col] = None
        out = detect_meals(df, _empty_requests(), default_config)
        assert out.empty or (out.iloc[0]["rise_rate_per_5min"] != pytest.approx(12.5))

    def test_empty_cgm(self, default_config):
        out = detect_meals(_cgm_series([]), _empty_requests(), default_config)
        assert out.empty
```

**Step 2–5:** Red, implement, green, commit.

```bash
git add detection/meal.py tests/test_detection_meal.py
git commit -m "feat(detection): missed-meal detection from sustained BG rise without bolus"
```

---

## Task 2.4 — Feature extraction (`detection/features.py`)

Produce a dict of daily features for clustering.

**Function:**
```python
def daily_features(
    cgm_df: pd.DataFrame,
    bolus_df: pd.DataFrame,
    basal_df: pd.DataFrame,
    requests_df: pd.DataFrame,
    alarms_df: pd.DataFrame,
    cgm_gaps_df: pd.DataFrame,
    date: datetime.date,
    config: AppConfig,
) -> dict: ...
```

Slice each frame to the `[date 00:00, date+1 00:00)` window in `config.timezone` before computing.

**Feature set (v1 — 14 features):**

| Feature | Definition |
|---|---|
| `date` | the input date (key) |
| `tir_70_180` | fraction of CGM readings with `70 <= bg <= 180` |
| `time_below_70` | fraction `bg < 70` |
| `time_above_180` | fraction `180 < bg <= 250` |
| `time_above_250` | fraction `bg > 250` |
| `mean_bg` | mean of `bg_mgdl` |
| `std_bg` | std of `bg_mgdl` |
| `cv_bg` | `std_bg / mean_bg` |
| `total_daily_insulin` | sum of `bolus_df.insulin_units` + integrated `basal_df` (commanded_rate × elapsed hours, using timestamp diffs) |
| `basal_bolus_ratio` | `basal_total / bolus_total`; NaN if `bolus_total == 0` |
| `meal_count` | count of `requests_df` rows where `bolus_category in {user_meal, user_meal_and_correction, override_up}` |
| `total_carbs_g` | sum of `requests_df.carbs_g` for those meal rows only |
| `overnight_dip` | `mean(bg @ 00:00–06:00) - mean(bg @ 22:00–24:00 previous day inside frame)`. Simplify v1: `mean(bg @ 04:00–06:00) - mean(bg @ 00:00–02:00)` to stay within a single date. |
| `mean_postprandial_peak` | mean over all meal events of `max(bg within 2 hours after bolus) - bg_at_bolus` |
| `alarm_count` | rows in `alarms_df` with `action == "activated"` that date |
| `suspension_minutes` | sum of suspension duration within the date (requires `suspension_df`; add it as a parameter — update signature) |
| `out_of_range_minutes` | sum of `cgm_gaps_df.duration_minutes` for gaps that intersect the date |

**Revised signature** to accommodate suspension_df:

```python
def daily_features(frames: dict[str, pd.DataFrame], date, config: AppConfig) -> dict: ...
```

Taking a single dict is cleaner than 7 positional args. Expected keys in `frames`: `cgm`, `bolus`, `basal`, `requests`, `alarms`, `suspension`, `cgm_gaps`.

**Files:**
- Create: `detection/features.py`.
- Create: `tests/test_detection_features.py`.

**Step 1: Failing tests.** Build a synthetic day with known values (e.g., 288 readings all at 150 → tir=1.0, mean=150, std=0). Separate tests for each feature.

```python
class TestDailyFeatures:
    def test_perfect_tir_day(self, default_config):
        cgm = _flat_day_cgm(bg=150)  # 288 readings all at 150
        frames = _empty_frames_with(cgm=cgm)
        f = daily_features(frames, date(2026, 3, 19), default_config)
        assert f["tir_70_180"] == 1.0
        assert f["time_below_70"] == 0.0
        assert f["mean_bg"] == 150.0
        assert f["std_bg"] == 0.0
        assert f["cv_bg"] == 0.0

    def test_low_bg_time_below_70(self, default_config):
        cgm = _mixed_day_cgm({60: 100, 150: 188})  # 100 readings at 60, 188 at 150
        frames = _empty_frames_with(cgm=cgm)
        f = daily_features(frames, date(2026, 3, 19), default_config)
        assert f["time_below_70"] == pytest.approx(100 / 288, abs=0.01)

    def test_total_daily_insulin(self, default_config):
        boluses = _boluses([1.5, 2.0, 3.0])  # 6.5 u total
        basal_const_1u = _basal_constant_rate(1.0)  # 1 u/hr × 24 = 24 u
        frames = _empty_frames_with(cgm=_flat_day_cgm(150), bolus=boluses, basal=basal_const_1u)
        f = daily_features(frames, date(2026, 3, 19), default_config)
        assert f["total_daily_insulin"] == pytest.approx(6.5 + 24.0, rel=0.02)

    def test_meal_count_excludes_auto_and_correction_only(self, default_config):
        reqs = _requests_frame([
            {"bolus_category": "user_meal", "carbs_g": 30, ...},
            {"bolus_category": "auto_correction", "carbs_g": 0, ...},
            {"bolus_category": "user_correction_only", "carbs_g": 0, ...},
            {"bolus_category": "user_meal_and_correction", "carbs_g": 45, ...},
        ])
        frames = _empty_frames_with(cgm=_flat_day_cgm(150), requests=reqs)
        f = daily_features(frames, date(2026, 3, 19), default_config)
        assert f["meal_count"] == 2
        assert f["total_carbs_g"] == 75

    def test_bolus_zero_handles_nan_basal_ratio(self, default_config):
        frames = _empty_frames_with(cgm=_flat_day_cgm(150))  # no bolus
        f = daily_features(frames, date(2026, 3, 19), default_config)
        assert pd.isna(f["basal_bolus_ratio"])

    def test_out_of_range_minutes_sums_gaps_within_date(self, default_config):
        gaps = _cgm_gaps_frame([
            {"start_ts": ts_on(2026, 3, 19, "10:00"), "end_ts": ts_on(2026, 3, 19, "10:30"),
             "duration_minutes": 30.0, "ongoing": False, "pump_serial": "TEST"},
            {"start_ts": ts_on(2026, 3, 18, "23:55"), "end_ts": ts_on(2026, 3, 19, "00:10"),
             "duration_minutes": 15.0, "ongoing": False, "pump_serial": "TEST"},
        ])
        frames = _empty_frames_with(cgm=_flat_day_cgm(150), cgm_gaps=gaps)
        f = daily_features(frames, date(2026, 3, 19), default_config)
        assert f["out_of_range_minutes"] >= 30  # full gap on date
        # partial gap crossing midnight: only the portion inside the date counts (10 min)
        # implementation may choose to count full gap if start OR end is within date — document the choice
```

**Decision to document:** For gaps crossing midnight, v1 counts a gap if it intersects the day window, contributing only the overlapping minutes. Simpler implementations may count the full gap if the start is within the day — pick one in the implementation and document.

**Step 2–5:** Red, implement, green, commit.

```bash
git add detection/features.py tests/test_detection_features.py
git commit -m "feat(detection): daily feature aggregation for clustering"
```

---

## Task 2.5 — Daily clustering (`detection/clustering.py`)

KMeans over standardized features. Deterministic via `random_seed` from config. Persist the model + scaler so subsequent runs assign clusters consistently.

**Function:**
```python
def cluster_days(features_df: pd.DataFrame, config: AppConfig, retrain: bool = False) -> pd.DataFrame: ...
```

**Output columns:** `date`, `cluster_id`, `distance_to_centroid`.

**Behavior:**
- If `retrain=True` or no saved model exists at `config.clustering.model_dir`, fit a `StandardScaler` + `KMeans(n_clusters=config.clustering.n_clusters, random_state=config.clustering.random_seed, n_init=10)` on the feature matrix. Save both to `{model_dir}/kmeans_v1.pkl` and `{model_dir}/scaler_v1.pkl`.
- Else, load and `transform` + `predict` only.
- Feature matrix: drop the `date` column, fill remaining NaNs with the column median (scikit's KMeans fails on NaN). Record which features were filled in a log message.
- `distance_to_centroid` = Euclidean distance from each row to its assigned centroid in the scaled space.

**Files:**
- Create: `detection/clustering.py`.
- Create: `tests/test_detection_clustering.py`.
- Create (at test time): `data/models/` — directory, empty gitignored. Add `data/models/` to `.gitignore` if not already.

**Step 1: Failing tests.**

```python
class TestClusterDays:
    def test_deterministic_with_fixed_seed(self, default_config, tmp_path):
        cfg = _cfg_override(default_config, model_dir=str(tmp_path))
        feats = _synthetic_features_df(30)  # 30 days
        out1 = cluster_days(feats, cfg, retrain=True)
        # Wipe model and refit
        shutil.rmtree(tmp_path); tmp_path.mkdir()
        out2 = cluster_days(feats, cfg, retrain=True)
        assert list(out1["cluster_id"]) == list(out2["cluster_id"])

    def test_predict_after_train_uses_saved_model(self, default_config, tmp_path):
        cfg = _cfg_override(default_config, model_dir=str(tmp_path))
        feats = _synthetic_features_df(30)
        cluster_days(feats, cfg, retrain=True)
        # Second call without retrain should load and predict
        out = cluster_days(feats, cfg, retrain=False)
        assert len(out) == 30
        assert (tmp_path / "kmeans_v1.pkl").exists()

    def test_output_columns(self, default_config, tmp_path):
        cfg = _cfg_override(default_config, model_dir=str(tmp_path))
        feats = _synthetic_features_df(15)
        out = cluster_days(feats, cfg, retrain=True)
        assert set(out.columns) == {"date", "cluster_id", "distance_to_centroid"}

    def test_nan_features_imputed_not_crash(self, default_config, tmp_path):
        cfg = _cfg_override(default_config, model_dir=str(tmp_path))
        feats = _synthetic_features_df(10)
        feats.loc[0, "basal_bolus_ratio"] = float("nan")  # day with zero bolus
        out = cluster_days(feats, cfg, retrain=True)
        assert len(out) == 10
        assert out["cluster_id"].notna().all()

    def test_n_clusters_respected(self, default_config, tmp_path):
        cfg = _cfg_override(default_config, model_dir=str(tmp_path), n_clusters=3)
        feats = _synthetic_features_df(30)
        out = cluster_days(feats, cfg, retrain=True)
        assert out["cluster_id"].nunique() <= 3
```

`_cfg_override` builds a new `AppConfig` with specific clustering fields swapped.

**Step 2–5:** Red, implement, green, commit.

```bash
git add detection/clustering.py tests/test_detection_clustering.py config/user_config.yaml .gitignore
git commit -m "feat(detection): KMeans clustering over daily features with persisted model"
```

---

## Task 2.6 — CLI integration

Expose detection via `main.py` subcommands.

**New subcommands:**

```
uv run python main.py analyze-anomalies --date YYYY-MM-DD
uv run python main.py analyze-meals --date YYYY-MM-DD
uv run python main.py cluster-days [--retrain] [--start YYYY-MM-DD] [--end YYYY-MM-DD]
```

**Behavior:**
- `analyze-anomalies` — load `cgm.parquet`, slice to the date, run `detect_anomalies`, print a table.
- `analyze-meals` — load `cgm.parquet` and `requests.parquet`, slice to the date, run `detect_meals`, print a table.
- `cluster-days` — iterate over all dates present in `cgm.parquet` (bounded by `--start`/`--end` if provided), build `features_df` via `daily_features`, run `cluster_days`, save results to `data/processed/daily_clusters.parquet` and print a summary.

**Files:**
- Modify: `main.py` — add subcommands, wire to detection functions.
- Create: `scripts/run_detection.py` — thin entry points to keep `main.py` readable.
- Create: `tests/test_cli_detection.py` — smoke tests (import only + subprocess).

**Step 1: Failing tests.**

```python
class TestDetectionCli:
    def test_analyze_anomalies_importable(self):
        from scripts.run_detection import run_anomalies  # noqa

    def test_cluster_days_importable(self):
        from scripts.run_detection import run_clustering  # noqa

    @pytest.mark.integration
    def test_analyze_anomalies_runs_on_seeded_parquet(self, tmp_processed_dir):
        # Write a seeded cgm.parquet, invoke via subprocess, assert exit 0
        subprocess.run(
            ["uv", "run", "python", "main.py", "analyze-anomalies", "--date", "2026-03-19"],
            check=True, cwd=tmp_processed_dir,
        )
```

Keep the subprocess test behind `@pytest.mark.integration` since it's slow.

**Step 2–5:** Red, implement, green, commit.

```bash
git add main.py scripts/run_detection.py tests/test_cli_detection.py
git commit -m "feat(detection): CLI subcommands for anomalies, meals, and clustering"
```

---

## Task 2.7 — Documentation

**Files:**
- Modify: `docs/operating_docs/HANDOFF.md` — add "Session 6 — Detection Engine v1" section. Include:
  - What landed (all of Phase 2)
  - How to run the new CLI commands
  - Known limitations (confidence heuristic is placeholder, clustering is unsupervised with no labeled ground truth, no real-time path yet)
- Modify: `docs/TECHNICAL_SPEC.md` — replace the "TBD" content in `## Detection Logic` sections with the actual algorithms implemented here. Reference back to `detection/*.py` files.
- Modify: `docs/DATA_CATALOG.md` — add a new section §4 "Detection outputs" documenting the schemas of `detect_anomalies`, `detect_meals`, `daily_features`, and `cluster_days`.

**Commit:**
```bash
git add docs/
git commit -m "docs: document detection engine v1 algorithms and schemas"
```

---

# Phase 3 — Surfaces (OUT OF SCOPE)

Deferred to a future plan (tentative: `docs/plans/YYYY-MM-DD-surfaces.md`). Covers:

- pydexcom live feed (5-min polling; real-time anomaly detection path)
- Telegram notifications (threshold, cooldown, message formatting)
- Streamlit dashboard (daily view, cluster explorer, event log)
- Real-time-variant detection functions (`detect_anomalies_realtime` etc. that enforce trailing-window-only)

Leave the groundwork clean: keep detection functions pure and DataFrame-in/out so the surface layer can wrap them without refactor.

---

# Validation & rollout

After all tasks commit:

1. **Regression check.** `uv run pytest -v`. Expected: all prior tests plus the new enrichment + detection tests pass. Target: ≥100 tests green.
2. **End-to-end smoke.** On a verified day (2026-03-19):
   ```bash
   uv run python main.py fetch-day --date 2026-03-19
   uv run python main.py analyze-anomalies --date 2026-03-19
   uv run python main.py analyze-meals --date 2026-03-19
   ```
   Eyeball the outputs. Known ground truth for 2026-03-19 from sanity_check + viz: pump died 08:06, occlusion at 22:36, lots of high-BG excursions. Anomaly detection should find multiple spikes (including the post-shutdown rise), the 22:36 drop if one occurred, and probably a flatline during the dead-pump window (CGM was out-of-range — **verify** that `is_backfilled_context=True` on those rows, since after the Task 1.4 fix those timestamps fall inside a `cgm_gaps` episode).
3. **Performance target.** Time the full detection pass on a single day:
   ```bash
   time uv run python main.py analyze-anomalies --date 2026-03-19
   time uv run python main.py analyze-meals --date 2026-03-19
   ```
   Both must return in **< 1 second** on a typical day (≤ 300 CGM readings, ≤ 20 boluses). If slower, profile and optimize hot loops (likely candidates: the flatline rolling window, meal bolus-lookback linear scan).
4. **Clustering on the full history.** After doing a full fetch (`uv run python main.py fetch`), run `cluster-days --retrain`. Expected: ≥ ~30 days in output with cluster IDs assigned and a sensible distribution (no cluster containing only 1 day unless it's genuinely pathological).
5. **Config robustness.** Manually corrupt one key in `config/user_config.yaml` and rerun any detection command. Must get a clean validation error, not a cryptic traceback.

---

# Open questions for the user (resolve before or during execution)

1. **Cartridge fill volume threshold** (DATA_NOTES §2). The v1 heuristic in Task 1.2 uses timestamp-only: any site_change within `forced_window_minutes` after BatteryShutdownAlarm is tagged forced. DATA_NOTES says the true signal is cartridge `insulin_volume`. Two questions:
   - What's the threshold (observed: 240 = real, 180 = forced)? Propose `≥ 220`.
   - Should we refine Task 1.2 now to read `insulin_volume` from the `details` JSON and override the forced flag when volume ≥ threshold? (Recommend: **yes, in Task 1.2 Step 3b**, even without a confirmed threshold — add the config knob `cartridge_real_fill_threshold` defaulting to a placeholder, and let the user dial it.)
2. **Flatline `K` parameter.** Proposed default: `flatline_consecutive_intervals: 6` (30 min at 5-min sampling). Is that the right sensitivity, or should it be tighter (e.g., 4 = 20 min) to catch sensor drop-outs earlier?
3. **Confidence scoring.** The v1 heuristics in anomaly/meal detection are placeholder arithmetic. Before notifications go live (Phase 3), we need either (a) a calibrated heuristic based on false-positive / true-positive rates over labeled data, or (b) an ML classifier. Which direction?
4. **Historical data volume before clustering.** KMeans on 5 days of data isn't meaningful. Recommend doing a full fetch (`uv run python main.py fetch`, ~10-30 min, 15 months of history) **before** Task 2.5 so the initial model is trained on hundreds of days. Flag: do this between Task 2.4 and Task 2.5.
5. **Override decrease invariant.** DATA_NOTES §3 notes no override decreases observed yet. The `bolus_category` logic handles `override_down` speculatively. If the user has strong domain reason to believe these never occur, we could instead raise on seeing one; v1 treats it as legitimate.
6. **Real-time `now`.** When Phase 3 adds realtime variants, what provides `now`? The system clock (and pydexcom lag means "now" is actually ~1 min ago), or an explicit `as_of` parameter? Preference for explicit `as_of: datetime` for testability.

---

# Appendix — Task execution order

Execute tasks strictly in this order; some depend on earlier state:

1. Task 1.1 — `bolus_category` + `override_delta`
2. Task 1.2 — `forced_by_alarm`
3. Task 1.3 — `site_issues.parquet` (depends on 1.2)
4. Task 1.4 — `cgm_gaps.parquet`
5. Task 1.5 — docs
6. Task 2.1 — config loader (consolidates 1.x's temporary loader)
7. Task 2.2 — anomaly detection
8. Task 2.3 — meal detection (depends on 1.1 for `bolus_category`)
9. Task 2.4 — features (depends on 1.1, 1.4)
10. **Full historical fetch** (see Open Question #4) — do not skip
11. Task 2.5 — clustering
12. Task 2.6 — CLI
13. Task 2.7 — docs
14. Validation & rollout checklist
