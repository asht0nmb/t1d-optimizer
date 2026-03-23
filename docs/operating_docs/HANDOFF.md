# Handoff: Ingestion Layer — Session 2

**Date:** 2026-03-23
**Status:** Implementation complete, awaiting real-data verification with user

---

## What Was Built This Session

The full ingestion layer is now implemented. The pipeline looks like:

```
tconnectsync API → ingestion/client.py → ingestion/builders.py → ingestion/storage.py
                                                                  (data/processed/*.parquet)
```

### Files Created

| File | Purpose |
|------|---------|
| `config/user_config.yaml` | All thresholds and settings (timezone, bg_targets, meal_detection, etc.) |
| `ingestion/__init__.py` | Package exports: `run_full_fetch`, `run_incremental_fetch`, `clean_all` |
| `ingestion/client.py` | API auth, multi-pump metadata, chunked event fetching with error handling |
| `ingestion/builders.py` | 6 DataFrame builders + `build_all` router |
| `ingestion/storage.py` | Parquet read/write, dedup (concat-then-dedup), fetch state tracking |
| `ingestion/fetch.py` | Orchestrator: full fetch, incremental fetch, per-pump processing |
| `main.py` | CLI: `fetch`, `fetch --clean`, `update`, `check --date YYYY-MM-DD` |
| `scripts/sanity_check.py` | Human-readable day summary from parquet files |
| `tests/test_builders.py` | 34 unit tests for all builders |
| `tests/test_storage.py` | 7 parquet/dedup/state tests |
| `tests/test_suspension.py` | 7 suspension pairing edge-case tests |
| `tests/test_integration.py` | Skeleton gated behind `@pytest.mark.integration` |

### Files Deleted
- `ingestion/tconnect.py` — was broken (string literal instead of os.getenv)

---

## DataFrames Produced

| Name | Parquet | Key Columns | Notes |
|------|---------|-------------|-------|
| cgm | `cgm.parquet` | timestamp, bg_mgdl, pump_serial | Deduped on (timestamp, pump_serial) |
| bolus | `bolus.parquet` | timestamp, insulin_units, bolus_id, pump_serial | |
| requests | `requests.parquet` | timestamp, bolus_id, carbs_g, bg_mgdl, iob, bolus_source, food_insulin, correction_insulin, total_requested, pump_serial | carbs_g is RAW (not /1000). bolus_source: "auto"/"user"/"override"/"unknown" |
| basal | `basal.parquet` | timestamp, commanded_rate, rate_source, pump_serial | commanded_rate = commandedRate/1000 (u/hr). rate_source: "profile"/"algorithm"/"temp_rate"/etc |
| suspension | `suspension.parquet` | suspend_timestamp, resume_timestamp, duration_minutes, suspend_reason, insulin_at_suspend, pairing_suspect, pump_serial | Paired chronologically. pairing_suspect=True if >24h or double-suspend |
| events | `events.parquet` | timestamp, event_type, event_subtype, previous_mode, details, seqnum, pump_serial | Catch-all. Types: site_change, cgm_session, mode_change, pcm_change, daily_marker |

---

## Test Status (Last Run)

```
48 passed, 1 skipped, 0 failed
```

The 1 skipped is `test_integration.py::TestIntegrationPipeline::test_single_day_fetch` — intentionally skipped until real reference data is captured.

---

## Pipeline Tested End-to-End

A test run was done against the current pump (serial 1513861) for `2026-03-22`. Output from `uv run python main.py check --date 2026-03-22`:

```
CGM readings: 122
  Mean BG: 152 mg/dL | Min/Max: 96/199 | TIR: 68% | Coverage: 42%

Boluses: 4  (Total: 27.28u)
  03:59  3.00u  | 10:04  11.67u  | 12:21  1.90u  | 12:46  10.71u

Bolus requests: 4  |  Meals (carbs > 0): 2  (Total: 90g)
  09:59  45g  BG=132  source=user
  12:42  45g  BG=0  source=user

Basal: 156 entries  |  22.94u  (algorithm: 145, profile: 11)
TDD: 50.22u  (bolus=27.28 + basal=22.94)

Suspensions: 0

Mode changes: 2
  00:00  normal → sleeping
  07:01  sleeping → normal
```

Coverage is 42% because March 22 data only runs to 13:09 (the last upload time per pump metadata).

**Note:** The test parquet data was cleaned up after verification — `data/processed/` is currently empty.

---

## Known Unhandled Event Types

After running against real data, two previously-unseen event types appeared:
- `LidDailyBasal` — daily basal summary event (intentionally not surfaced in any DataFrame)
- `LidCarbsEntered` — manual carb entry event (not used — carbs come from bolus requests)
- `RawEvent` — base class for events tconnectsync can't fully parse

All three are now in `_HANDLED_TYPES` in `builders.py` so they don't trigger warnings.

---

## What Remains: Verification with User

### Next Steps (in priority order)

1. **Full initial fetch across all 6 pumps**
   ```bash
   uv run python main.py fetch
   ```
   This will take 10-30 minutes. Expect ~16K events/day × 1900 days across all pumps. The fetch state in `data/processed/.fetch_state.json` will track progress; if interrupted, `uv run python main.py update` will retry failed chunks.

2. **User sanity check**
   ```bash
   uv run python main.py check --date YYYY-MM-DD
   ```
   User picks a day from the last week they remember well and verifies:
   - Bolus count and total insulin match their memory
   - Carb totals match meals they ate
   - BG pattern looks right (high morning, crashed afternoon, etc.)
   - Any site changes / mode changes match their recollection

3. **Capture integration test reference data**
   Once a day is verified as correct, record the exact values in `tests/test_integration.py` so future pipeline changes can be regression-tested.

4. **Run `uv run pytest`** on the full test suite after first real fetch to confirm no regressions.

---

## Key Design Decisions (Revisitable)

All documented in the plan at `/Users/ashtonmeyer-bibbins/.claude/plans/toasty-rolling-noodle.md`. Most important:

- **Pump overlap**: Fetch all date ranges, dedup by content. Old pumps with pre-loaded dates but no actual events return zero rows naturally. See "Pump Overlap Strategy" section in the plan.
- **events dedup key**: `(pump_serial, seqNum)` — seqNum is globally unique per pump (uint32 counter), not per-event-type.
- **carbamount**: Raw value = grams. Do NOT divide by 1000. (Verified against CSV in prior session.)
- **commandedRate**: milliunits/hr. Divide by 1000. Mean ~1396 → 1.396 u/hr matches pump profile.

---

## Catches & Surprises from Real Data

### 1. RawEvent volume is high (~77% of events)
In the test run (2139 total events for one day), **1644 were `RawEvent`** — the tconnectsync base class for events it couldn't parse into a typed subclass. These are silently skipped. This is expected: the tconnectsync library has a `DEFAULT_EVENT_IDS` filter and a lot of pump events fall outside those IDs. We added `RawEvent` to `_HANDLED_TYPES` to suppress warnings, but if the detection engine ever needs an event type that's currently parsing as `RawEvent`, you'd need to add it to the tconnectsync event parser or handle the raw bytes directly.

### 2. `fetch_all_event_types=True` in client.py
The API call in `client.py` uses `fetch_all_event_types=True`, which bypasses tconnectsync's `DEFAULT_EVENT_IDS` filter and fetches the full binary blob. This is intentional — it means we're getting *everything* from the API and letting `build_all` decide what to do with it. If `fetch_all_event_types=False` were used instead, some event types (like `LidAaUserModeChange`) might not come back at all.

### 3. Cross-pump CGM dedup is NOT yet implemented
The plan mentioned deduplicating CGM across pumps on `(timestamp, bg_mgdl)`. This was **not implemented**. Currently `build_cgm_df` deduplicates within a single pump call on `timestamp` only, and `storage.py` deduplicates on `(timestamp, pump_serial)`. If two different pumps uploaded the same CGM reading (same timestamp, same BG), both would survive into the final parquet with different `pump_serial` values. This is a low-risk gap (CGM comes from the sensor, not the pump, but each pump gets a copy on upload) — but worth addressing during the initial full-fetch verification.

### 4. `BG=0` appears in bolus requests
In the test run, one meal bolus (`carbs_g=45` at 12:42) had `bg_mgdl=0`. This is a real API value, not a parsing error — it means the user didn't have a BG reading at the time of bolusing (or the pump had no recent CGM value). The detection engine must treat `bg_mgdl=0` in `requests_df` as "missing BG", not as an actual glucose of 0.

### 5. Unpaired suspend has `pairing_suspect=False` — potentially misleading
An unpaired suspend at end-of-data is stored with `resume_timestamp=NaT`, `duration_minutes=NaN`, and `pairing_suspect=False`. The `False` flag means "we don't think this is a pairing error" — but it does mean there's no resume. A downstream consumer querying `WHERE pairing_suspect=False AND resume_timestamp IS NOT NULL` would miss these. Consider whether to add a separate `unpaired` boolean column, or flip `pairing_suspect=True` for unpaired suspends.

### 6. `insulin_at_suspend` meaning is unverified
The `insulinamount` field on `LidPumpingSuspended` is documented in tconnectsync as "units" but it's unclear if this is insulin remaining in cartridge, IOB, or something else. Needs validation during the real-data sanity check.

### 7. `basal_df` doesn't include `profile_rate` or `algorithm_rate`
`LidBasalDelivery` has `profileBasalRate`, `algorithmRate`, and `tempRate` fields (all in milliunits/hr) in addition to `commandedRate`. Currently only `commandedRate` (as `commanded_rate`) and `commandedRateSourceRaw` (as `rate_source`) are stored. For meal detection, the signal "CIQ is ramping up above the scheduled rate" requires comparing `commanded_rate` to `profile_rate`. Either add these columns to `basal_df`, or derive them from `user_config.yaml` pump settings at analysis time. This is a gap for the detection engine.

### 8. `requests_df` key is `"requests"` (not `"request"`)
This was a bug caught mid-session: `build_all` originally returned key `"request"` but `storage.py` expected `"requests"`. Fixed in the final code. Mentioning it because test mocks or downstream consumers that hardcode `"request"` will silently fail.

---

## Architecture Notes for Detection Engine

The detection engine (not yet built) will consume these parquet files. Critical constraints:
- **Source-agnostic**: Detection operates on normalized DataFrames, not raw events
- **Trailing window only**: No future BG context in real-time mode
- **Config-driven**: All thresholds read from `config/user_config.yaml` at runtime
- `rate_source` in `basal_df` is the key signal for "is CIQ actively adjusting?" vs. "scheduled program running"
- `bolus_source` in `requests_df` distinguishes auto-correction boluses from user-initiated meals

---

## How to Run

```bash
# Initial full fetch (all 6 pumps, all history)
uv run python main.py fetch

# Incremental update (new data since last fetch)
uv run python main.py update

# Sanity check a specific day
uv run python main.py check --date 2026-03-20

# Run tests
uv run pytest
uv run pytest -m "not integration"   # unit tests only

# Wipe and re-fetch from scratch
uv run python main.py fetch --clean
```
