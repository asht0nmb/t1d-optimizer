# Control-IQ Field Capture (M4 data foundation): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to work through this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop discarding the therapy-setting and Control-IQ fields that Tandem v3 pump events already carry. This covers profile, algorithm and temp basal rates, per-bolus ISF, carb ratio and target BG, requested-versus-delivered insulin, and the pump's own IOB series. Persist them in Supabase and parquet so that M4 (the physiology pillar: effective-ISF tracking and an Autotune-style settings report) has real inputs instead of surrogates.

**Scope is plumbing only.** This plan adds columns, tables, builders and a backfill path. It adds no thresholds, no fitted parameters and no detection logic. It writes nothing to `config/user_config.yaml`. Per the ML-deferral boundary, every decision about what these numbers *mean* (effective ISF, basal recommendations) stays with the owner in M4. Nothing captured here is ever auto-applied to the pump.

**Architecture:** additive only.
- Nullable columns go on `basal`, `bolus` and `requests`.
- One new table, `pump_iob`, holds `LidDailyBasal`.
- One optional new table, `bg_readings`, holds `LidBgReadingTaken` (Tier 2; owner decision).
- An opt-in "fill new columns" upsert mode lets a one-shot re-fetch populate the new columns on rows that already exist. The default upsert stays `ON CONFLICT DO NOTHING`.

**Tech stack:** tconnectsync **3.0.3** (field names below were verified against it), pandas, psycopg2 (`execute_values`), pytest.

---

## 0. Background and motivation

The exploratory research PR #10 (`research/algorithm-improvements`, `docs/algorithm-research-findings.md` §2 Q1–Q3 and §9) concluded the following:

- `detection/iob.py` currently estimates the "profile basal rate" as a **trailing 7-day median of `commanded_rate`**, because `profileBasalRate` is not ingested (`basal_baseline_rate`). Under Control-IQ that surrogate absorbs the algorithm's own modulation into "baseline", which biases every basal-excursion pseudo-dose.
- `detection/deviation.py::DeviationConfig.isf_mgdl_per_unit` deliberately has **no default**. The ISF used by the bolus calculator is on every `LidBolusRequestedMsg2`, but `build_request_df` drops it. Capturing it allows a piecewise-constant ISF schedule taken from the pump's own records, rather than a guessed constant.
- `detection/deviation_categorize.py` cannot separate "user's basal profile" from "Control-IQ's decision" without the profile/algorithm split. That split is exactly what an Autotune-style basal report needs.

M4 in `docs/plans/2026-05-23-v2-development-roadmap.md` asks for "effective insulin sensitivity tracking and an Autotune-style settings report … surfaced as observation rather than auto-applied." Both halves need three inputs per instant:
1. the **programmed** settings in force (profile basal, ISF, CR, target);
2. what the **algorithm** actually did (algorithm rate, auto-correction boluses);
3. the pump's own **IOB** estimate, which is a reference for validating `detection/iob.py`'s reconstruction.

Every one of these is already in the event stream. None of them is persisted today.

---

## 1. Empirical field inventory (verified 2026-10-14)

**Method:**
- Ran a read-only fetch through the existing `ingestion.client.get_api` → `get_pump_metadata` → `fetch_pump_events`, using `uv run --with tconnectsync==3.0.3`.
- The `tconnectsync` loggers were held at WARNING.
- Picked the pump with the latest `availableDataRange` at runtime (6 pumps on the account have data).
- Window: 2026-08-10 → 2026-08-12, which is 5,807 events in total.
- Collected only aggregates: attribute names, Python types, min/max, distinct counts.
- No identifiers were printed or recorded, and no raw dump is committed.

> **Public-repo note:** this repo is public. The ranges below are bounds, as requested. The owner's time-of-day schedules (which hour maps to which ISF or basal segment) are **deliberately omitted**, because together they would reconstruct the full therapy profile. Implementers must not add them to fixtures, docs or test names. Use synthetic values in tests.

### 1.1 Event classes relevant to M4 (counts in the 2-day window)

| Class (tconnectsync 3.0.3) | n | Field: type, observed range, units | Handling today |
|---|---|---|---|
| `LidBasalDelivery` | 857 | `commandedRateSourceRaw`: int {0,1,3} (0 suspended, 1 profile, 3 algorithm; 2 and 4 not seen) | **kept** as `basal.rate_source` (enum) |
| | | `commandedRate`: int 0–9150 **milli-U/hr** | **kept** as `basal.commanded_rate` (÷1000, U/hr) |
| | | `profileBasalRate`: int 1000–1550 milli-U/hr, 4 distinct values; never sentinel | **DROPPED** |
| | | `algorithmRate`: int 0–9150 milli-U/hr, or **65535 sentinel** when source ≠ algorithm (232 profile rows + 31 suspended rows) | **DROPPED** |
| | | `tempRate`: int, **always 65535 sentinel** in this window (no temp basal was run) | **DROPPED** |
| `LidBolusRequestedMsg1` | 39 | `bolusId` int; `carbAmount` int 0–65 g; `bg` int 0–394 mg/dL; `iob` float 0–20.6 U | **kept** (`requests.carbs_g/bg_mgdl/iob`) |
| | | `bolusTypeRaw`: int {1,2,3} (1 Carb, 2 Automatic Correction, 3 Remote; 0 Insulin not seen) | **DROPPED** |
| | | `correctionBolusIncludedRaw`: int {0,1} | **DROPPED** |
| | | `carbRatioRaw`: int {0, one non-zero value in the low thousands}. **0 on every auto (type 2) and remote (type 3) request.** tconnectsync comments it as "g/u", but the magnitude implies ×1000 scaling (unverified, see §8 D1) | **DROPPED** |
| `LidBolusRequestedMsg2` | 39 | `optionsRaw` int {0,3,4}; `userOverrideRaw` int {0,1} | **transformed** into `requests.bolus_source` |
| | | `isf`: int, 3 distinct non-zero values in 18–23 mg/dL per U, or **0 sentinel** (the one `optionsRaw=4` / remote row). **Present and non-zero on Control-IQ auto-corrections.** | **DROPPED** |
| | | `targetBg`: int {0, 110} mg/dL, with **0 sentinel** on the same row as `isf=0` | **DROPPED** |
| | | `declinedCorrectionRaw` int {0}; `selectedIobRaw` {1}; `standardPercent` {100}; `duration` {0} | **DROPPED** (low value; `declinedCorrectionRaw` captured, see §2) |
| `LidBolusRequestedMsg3` | 39 | `foodBolusSize` 0–15.5 U; `correctionBolusSize` 0–9.9 U; `totalBolusSize` 0.025–17.5 U | **kept** (`food_insulin/correction_insulin/total_requested`) |
| `LidBolusCompleted` | 39 | `insulinDelivered` 0.025–17.5 U | **kept** (`bolus.insulin_units`) |
| | | `insulinRequested` 0.025–17.5 U; differs from delivered on 2/39 boluses | **DROPPED** |
| | | `iob` float 2.4–29.7 U. This is IOB at completion, and it always exceeds Msg1's pre-bolus `iob` | **DROPPED** |
| | | `completionStatusRaw` int {3} | **DROPPED** |
| `LidDailyBasal` | 407 | `dailyTotalBasal` float 0–40.1 U (running daily total); `lastBasalRate` float 0–9.15 U/hr; `iob` float 0–29.3 U. Spacing: median 10 min, min 0.27 min, max 60 min | **DROPPED entirely.** It is in `_HANDLED_TYPES`, so it is silenced as "known" but no builder reads it |
| | | `batteryChargePercent{MSB,LSB}Raw`, `batteryLipoMilliVolts`: always None on the JSON path | n/a |
| `LidBgReadingTaken` | 36 | `bg` int 120–394 mg/dL; `iob` float 0–24.4 U; `isf` int (same 3 values as Msg2); `targetBg` {110}; `bgEntryTypeRaw` {0,1}; `bgSourceTypeRaw` {0,1}; `cgmCalibrationRaw` {0} | **DROPPED entirely** (silenced in `_HANDLED_TYPES`) |
| `LidAaPcmChange` | 15 | `currentPcmRaw`/`previousPcmRaw` int {0,2,3} | **kept** as `events` rows with `event_type='pcm_change'`; previous goes in `details.previous_pcm` |
| | | `pumpSuspendedRaw` {0,1}; `cgmAvailableRaw` {0,1}; `calculationAvailableRaw` {1}; `closedLoopPreferredRaw` {1}; `sufficientClosedLoopParamsRaw` {1} | **DROPPED** (§2 Tier 2: add to `details`) |
| `LidAaUserModeChange` | 6 | `currentUserModeRaw`/`previousUserModeRaw` {0,1}; `requestedActionRaw` {1,2}; `exerciseTime` {90} | **kept** (`events` mode_change) |
| | | `exerciseChoiceRaw`, `activeSleepScheduleRaw` {0,2}, `sleepStartedByGuiRaw`, `exerciseStoppedByTimerRaw`, `eatingSoonStoppedByTimerRaw` | **DROPPED** (not M4-relevant; leave) |
| `LidAaDailyStatus` | 3 | `pumpControlStateRaw` {2,3}; `usermodeRaw` {0,1} | **kept** (`events` daily_status details) |
| | | `sensorTypeRaw` {3} | **DROPPED** (leave) |
| `LidNewDay` | 3 | `commandedBasalRate` float (U/hr) | **kept** (`events.details`) |
| `LidBolusDelivery` | 78 | `bolusSourceRaw` {1,7,8}; `bolusTypeRaw` bitmask {5,9,13,17,25}; `requestedNow`/`deliveredTotal`/`correction` in milli-units; `remoteId` | **DROPPED** (Tier 3: redundant with requests + bolus for M4) |
| `LidBolusActivated` | 39 | `iob` 0–20.6 U; `bolusSize` 0.025–17.5 U | **DROPPED** (redundant with Msg1/Msg3) |

**Verified invariants (for tests and docstrings):**
- When `commandedRateSourceRaw == 3`, `commandedRate == algorithmRate` (594/594).
- When `commandedRateSourceRaw == 1`, `commandedRate == profileBasalRate` (232/232).
- `algorithmRate == 65535` exactly when the source is not 3.
- **No duplicate `LidBasalDelivery` timestamps** in the window (0/857). This matters because the `basal` PK is `(pump_serial, timestamp)`.
- `LidBasalDelivery` spacing: min 2.7, median 5.0, max 9.8 minutes. It is close to a 5-minute cadence under Control-IQ.
- `LidDailyBasal` can be sub-minute apart (0.27 min min), so it **must** be keyed by `seqnum`, not timestamp.
- Event timestamps are tz-aware `America/Los_Angeles`.

**Not verified (do not assume):**
- Temp-rate semantics (`commandedRateSourceRaw` 2 and 4 and a non-sentinel `tempRate`), because no temp basal was run in the window.
- `bolusTypeRaw = 0` (plain "Insulin" bolus).
- Whether `profileBasalRate` is populated on pre-v3, older-pump history.

### 1.2 Unparsed events (out of scope, recorded for completeness)

On the JSON path, tconnectsync 3.0.3 returns a bare `RawEvent` for these event codes. `RawEvent.build_from_json` keeps only the code, the timestamp and the seqnum, and discards `eventProperties`:

| eventCode | n (2 days) |
|---|---|
| 219 | 1563 |
| 22 | 592 |
| 23 | 592 |
| 434 | 512 |
| 9 | 65 |
| 27 | 24 |
| 8 | 2 |
| 34 | 1 |
| 35 | 1 |

They are not guessed at here. Capturing them would need either an upstream tconnectsync change or a bypass of its typed parser. That is a separate decision (§8 D6).

### 1.3 Current storage surfaces (as of `origin/main` 7f6cdad)

- **Postgres:** `db/migrations/0001_init.sql` defines `basal(pump_serial, timestamp, commanded_rate numeric(6,3), rate_source basal_rate_source)` with PK `(pump_serial, timestamp)`.
  - `bolus` has PK `(pump_serial, bolus_id)` and `insulin_units numeric(6,3)`.
  - `requests` has PK `(pump_serial, bolus_id)` and 12 columns, all `NOT NULL` except `override_delta`.
  - `0003_enable_rls.sql` enables RLS and adds one `auth_required_all FOR ALL TO authenticated USING (true) WITH CHECK (true)` policy per table, wrapped in a `DO … EXCEPTION WHEN duplicate_object` block.
- **Registry:** `core/schema.py::TABLES` holds the logical name, the PK and the time column only (column types live in SQL).
- **Converters:** `core/storage/_postgres_converters.py` holds `COLUMN_SPECS` (column order) and `CONVERTERS` (row → tuple, null-safe helpers). Converters index `r["col"]` directly, so a frame missing a column raises `KeyError`.
- **Parquet:** `data/processed/<name>.parquet` via `core/storage/parquet.py`, which owns `PARQUET_FILES` and `DEDUP_KEYS` (re-exported by `ingestion/storage.py`). `upsert_table` is concat + `drop_duplicates(keep="first")`, so **existing rows win**.
- **In-memory:** `core/storage/memory.py` uses the same keep-first semantics, keyed on `TableSpec.primary_key`.
- **Supabase write:** `SupabaseStorage.upsert_table` → `INSERT … ON CONFLICT (pk) DO NOTHING`, so **existing rows are never updated**.
- **Bootstrap:** `scripts/bootstrap_supabase.py` has its own `TABLE_SPECS` list and imports `COLUMN_SPECS`/`CONVERTERS`.
- **Nightly sync:** `scripts/sync_tandem_to_supabase.py` already has `--start/--end/--only/--dry-run`, so it is the natural vehicle for the one-shot backfill.
- **Web:** `apps/web/lib/queries/day.ts` uses `select("*")`. New nullable columns appear in payloads harmlessly. No web change is in scope.
- **Enrichment:** `ingestion/enrich.py::enrich_all` passes extra `requests` columns through untouched (it only adds `bolus_category`/`override_delta`). No enrichment change is needed.

---

## 2. What to capture, and why (M4 relevance)

**Tier 1, required for M4:**

| New field | Source | Why M4 needs it |
|---|---|---|
| `basal.profile_basal_rate` (U/hr) | `LidBasalDelivery.profileBasalRate` / 1000 | Replaces the rolling-median surrogate in `detection/iob.py::basal_baseline_rate`. It is the "programmed" baseline that an Autotune-style basal report compares against. |
| `basal.algorithm_rate` (U/hr, NULL when the sentinel is 65535) | `.algorithmRate` / 1000 | Separates Control-IQ's decision from the profile. (`commanded_rate` alone is the blend.) This lets `deviation_categorize` attribute deviations to the algorithm instead of the profile. |
| `basal.temp_rate` (U/hr, NULL when the sentinel is 65535) | `.tempRate` / 1000 | Same purpose, for user temp basals. Semantics unverified (§1.1); captured raw-but-nulled so it is available once observed. |
| `requests.isf_mgdl_per_u` (NULL when 0) | `LidBolusRequestedMsg2.isf` | The **programmed** ISF in force at each bolus, which gives the pump-sourced ISF schedule for `DeviationConfig`. Effective-ISF tracking is defined *relative to* this value. |
| `requests.target_bg_mgdl` (NULL when 0) | `Msg2.targetBg` | The correction target in force. Needed to back out the calculator's correction math and to compare against observed outcomes. |
| `requests.carb_ratio_g_per_u` (NULL when 0) | `Msg1.carbRatioRaw` (scale per §8 D1) | The programmed CR, used by the CSF/CR half of an Autotune-style report. |
| `requests.request_type` (text) | `Msg1.bolusTypeRaw` → `insulin`/`carb`/`auto_correction`/`remote`/`unknown_<n>` | Distinguishes Control-IQ auto-corrections from user boluses using the pump's own label, independent of the `optionsRaw` heuristic in `_derive_bolus_source`. Useful as a cross-check, and it decides when ISF/CR sentinels are expected. |
| `requests.correction_included` (bool) | `Msg1.correctionBolusIncludedRaw` | Lets M4 tell whether the correction part of a bolus was accepted. That decides whether ISF was actually exercised. |
| `requests.declined_correction` (bool) | `Msg2.declinedCorrectionRaw` | Same purpose (always 0 in the sample, but cheap to keep). |
| `bolus.insulin_requested` (U) | `LidBolusCompleted.insulinRequested` | Requested versus delivered. It catches interrupted or partial boluses (2/39 in the sample), which would otherwise be mis-attributed in effective-ISF fits. |
| `bolus.iob_at_completion` (U) | `LidBolusCompleted.iob` | The pump's IOB right after the bolus. It is a validation point for `detection/iob.py`. |
| `bolus.completion_status` (smallint) | `LidBolusCompleted.completionStatusRaw` | Lets M4 exclude non-normal completions. Stored raw; the enum mapping is unverified. |
| **new table `pump_iob`** | `LidDailyBasal` | The pump's own IOB time series (~10-minute cadence) plus the running daily basal total. This is **ground truth for validating** the IOB reconstruction, which is the single biggest correctness risk in M4. |

**Tier 2, cheap and recommended, owner decides (§8 D3, D4):**
- **New table `bg_readings`** (`LidBgReadingTaken`): `bg`, `iob`, `isf`, `targetBg`, entry and source types. It gives a denser ISF/target observation stream than requests alone, plus fingerstick/CGM-popup BG entries.
  - This is unrelated to the CSV "Manual BG" section, which the spec says to ignore. That rule concerns CSV parsing, not this event.
- **`events.details` additions for `pcm_change`:** `pump_suspended`, `cgm_available`, `calculation_available`, `closed_loop_preferred`, `sufficient_closed_loop_params`. These explain *why* Control-IQ dropped to open loop (for example, CGM unavailable), so M4 can exclude non-closed-loop periods from attribution. This needs no schema change (`details` is jsonb). It only affects rows written after the change, or rows rewritten by a clean refetch (see §6).

**Tier 3, not captured (documented decision):**
- `LidBolusDelivery` and `LidBolusActivated` duplicate what requests + bolus already hold for M4.
- The remaining `LidAaUserModeChange` and `LidAaDailyStatus` flags are not M4 inputs.
- The unparsed `RawEvent` codes are covered in §1.2.

---

## 3. Schema changes (draft SQL; do **not** apply in this PR)

The implementation PR creates `db/migrations/0004_control_iq_fields.sql`. The owner applies it (see §7). It follows the conventions of 0001/0003:
- it is idempotent (`ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`, and policies in a `DO/EXCEPTION duplicate_object` block);
- every new column is **nullable**;
- no defaults are set, so `ADD COLUMN` is a catalog-only change in Postgres 11+ with no table rewrite.

```sql
-- =============================================================================
-- t1d-engine migration 0004 -- Control-IQ / therapy-setting field capture
-- =============================================================================
-- Additive only. All new columns NULLable: pre-existing rows stay NULL until
-- the one-shot backfill fills them (see docs/superpowers/plans/
-- 2026-10-14-control-iq-field-capture.md §6); sentinel raw values
-- (65535 rates, 0 ISF/CR/target) are stored as NULL by the builders.
-- Idempotent: safe to re-run.
-- =============================================================================

-- basal -----------------------------------------------------------------------
ALTER TABLE basal ADD COLUMN IF NOT EXISTS profile_basal_rate numeric(6,3) NULL;
ALTER TABLE basal ADD COLUMN IF NOT EXISTS algorithm_rate     numeric(6,3) NULL;
ALTER TABLE basal ADD COLUMN IF NOT EXISTS temp_rate          numeric(6,3) NULL;

COMMENT ON COLUMN basal.profile_basal_rate IS
    'Programmed profile basal rate in force (U/hr; raw milliunits / 1000). NULL on rows ingested before PIPELINE_VERSION 5 and not yet backfilled.';
COMMENT ON COLUMN basal.algorithm_rate IS
    'Control-IQ algorithm rate (U/hr). NULL when raw = 65535 (UINT16 sentinel: algorithm not driving delivery). Equals commanded_rate when rate_source = algorithm.';
COMMENT ON COLUMN basal.temp_rate IS
    'User temp-basal rate (U/hr). NULL when raw = 65535 (no temp basal active). Semantics not yet verified against a real temp-basal event.';

-- bolus -----------------------------------------------------------------------
ALTER TABLE bolus ADD COLUMN IF NOT EXISTS insulin_requested  numeric(6,3) NULL;
ALTER TABLE bolus ADD COLUMN IF NOT EXISTS iob_at_completion  numeric(6,3) NULL;
ALTER TABLE bolus ADD COLUMN IF NOT EXISTS completion_status  smallint     NULL;

COMMENT ON COLUMN bolus.insulin_requested IS
    'Units requested for this bolus per LidBolusCompleted; differs from insulin_units on interrupted/partial deliveries.';
COMMENT ON COLUMN bolus.iob_at_completion IS
    'Pump-reported insulin on board at bolus completion (U). Validation reference for reconstructed IOB.';
COMMENT ON COLUMN bolus.completion_status IS
    'Raw LidBolusCompleted.completionStatusRaw (3 observed for normal completion). Unmapped on purpose.';

-- requests --------------------------------------------------------------------
ALTER TABLE requests ADD COLUMN IF NOT EXISTS isf_mgdl_per_u      smallint     NULL;
ALTER TABLE requests ADD COLUMN IF NOT EXISTS target_bg_mgdl      smallint     NULL;
ALTER TABLE requests ADD COLUMN IF NOT EXISTS carb_ratio_g_per_u  numeric(7,3) NULL;
ALTER TABLE requests ADD COLUMN IF NOT EXISTS request_type        text         NULL;
ALTER TABLE requests ADD COLUMN IF NOT EXISTS correction_included boolean      NULL;
ALTER TABLE requests ADD COLUMN IF NOT EXISTS declined_correction boolean      NULL;

COMMENT ON COLUMN requests.isf_mgdl_per_u IS
    'Programmed ISF (mg/dL per U) the bolus calculator used. NULL when raw = 0 (sentinel seen on remote boluses). Programmed, not effective, sensitivity.';
COMMENT ON COLUMN requests.target_bg_mgdl IS
    'Programmed correction target (mg/dL). NULL when raw = 0.';
COMMENT ON COLUMN requests.carb_ratio_g_per_u IS
    'Programmed carb ratio (g per U). NULL when raw = 0 (auto-correction and remote requests). Scale per plan decision D1.';
COMMENT ON COLUMN requests.request_type IS
    'LidBolusRequestedMsg1.bolusTypeRaw mapped: insulin | carb | auto_correction | remote | unknown_<n>. Open vocabulary (text, not enum) like events.event_subtype.';

-- pump_iob (new) --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pump_iob (
    pump_serial        text          NOT NULL,
    seqnum             bigint        NOT NULL,
    timestamp          timestamptz   NOT NULL,
    iob_units          numeric(6,3)  NOT NULL,
    daily_total_basal  numeric(7,3)  NULL,
    last_basal_rate    numeric(6,3)  NULL,
    PRIMARY KEY (pump_serial, seqnum)
);

CREATE INDEX IF NOT EXISTS pump_iob_pump_ts_idx
    ON pump_iob (pump_serial, timestamp DESC);

COMMENT ON TABLE pump_iob IS
    'Pump-reported IOB + running daily basal total from LidDailyBasal (~10 min cadence, sometimes sub-minute). PK is seqnum because timestamps are not unique.';

ALTER TABLE pump_iob ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY auth_required_all ON pump_iob
        FOR ALL TO authenticated
        USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- bg_readings (new; Tier 2 -- drop this block if decision D3 = no) ------------
CREATE TABLE IF NOT EXISTS bg_readings (
    pump_serial      text          NOT NULL,
    seqnum           bigint        NOT NULL,
    timestamp        timestamptz   NOT NULL,
    bg_mgdl          smallint      NOT NULL,
    iob_units        numeric(6,3)  NULL,
    isf_mgdl_per_u   smallint      NULL,
    target_bg_mgdl   smallint      NULL,
    bg_entry_type    smallint      NULL,
    bg_source_type   smallint      NULL,
    PRIMARY KEY (pump_serial, seqnum)
);

CREATE INDEX IF NOT EXISTS bg_readings_pump_ts_idx
    ON bg_readings (pump_serial, timestamp DESC);

COMMENT ON TABLE bg_readings IS
    'LidBgReadingTaken: BG entries on the pump (fingerstick / CGM pop-up) with the ISF + target in force. Raw type codes unmapped on purpose.';

ALTER TABLE bg_readings ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY auth_required_all ON bg_readings
        FOR ALL TO authenticated
        USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
```

**RLS parity check:** new tables get exactly the 0003 policy shape. Altered tables keep their existing policy, because `ADD COLUMN` doesn't touch RLS. After applying, `anon` must still see 0 rows on every table, including the two new ones (Task 9 verification query).

**Type choices:**
- `numeric(6,3)` matches the existing insulin columns (no float drift).
- `pump_iob.daily_total_basal` uses `numeric(7,3)` as headroom past the observed 40 U.
- `carb_ratio_g_per_u` uses `numeric(7,3)` so that a mis-scaled 4200 still fits if D1 resolves to "raw is already g/U". That is a defensive choice, not a prediction.
- `request_type` is `text`, not an enum, following the `events.event_subtype` precedent for open vocabularies whose upstream values are not fully observed.

---

## 4. File touchpoint checklist

Every new table must be registered in **all** of these places. Missing one is the classic failure mode in this repo:

1. `core/schema.py::TABLES`: `pump_iob` → PK `("pump_serial","seqnum")`, time `timestamp`; the same for `bg_readings`.
2. `core/storage/parquet.py`: `PARQUET_FILES` and `DEDUP_KEYS` (re-exported by `ingestion/storage.py`).
3. `core/storage/_postgres_converters.py`: `COLUMN_SPECS` and `CONVERTERS`, with new columns on `basal`/`bolus`/`requests` plus two new converters.
4. `core/storage/memory.py`: confirm it is registry-driven (it is, through `get_spec`); no change expected.
5. `scripts/bootstrap_supabase.py::TABLE_SPECS`: append the new tables.
6. `ingestion/builders.py`: the new builders, `build_all` dict entries and the module docstring.
7. `tests/test_tconnectsync_field_contract.py`: add every newly-read attribute name.
8. `tests/core/test_storage_contract.py`: covers the new tables automatically if it iterates `TABLES`; verify.
9. `ingestion/pipeline_version.py`: bump plus changelog.
10. `CLAUDE.md`: table counts ("13 public tables"), the test counts and the `core/` description. This is updated **by the implementation PR**, not by this plan PR.
11. `docs/updates/2026-MM-DD-control-iq-field-capture.md`: a dated entry once implementation lands.

---

## 5. Implementation tasks (TDD, bite-sized)

**Conventions:**
- Builder tests construct **real tconnectsync dataclass instances**, not `MagicMock(spec=…)`. The contract-test docstring explains why: mocks hid the v3 rename for 47 days.
- Use synthetic therapy values (for example ISF 50, profile 800 mU/hr). **Never** use the owner's real values.

> **Prerequisite (sequencing):** another agent's in-flight branch `fix/sync-fail-loud-and-redaction` changes `ingestion/client.py` and `scripts/sync_tandem_to_supabase.py`, and pins tconnectsync 3.0.3. **That branch must merge first.** Rebase this work on it, because the field names in §1 come from 3.0.3, and Tasks 7–8 edit the same sync script.

### Task 1: Test helper for real event instances

**Files:** create `tests/_tconnect_events.py`.

- [ ] **Step 1:** Write `make_raw(seq, ts)`. It builds a `RawEvent` through `RawEvent.build_from_json({"eventCode": <id>, "pumpDateTime": ts_iso, "sequenceNumber": seq})`, with `TIMEZONE_NAME` monkeypatched to a fixed zone in a fixture.
- [ ] **Step 2:** Write per-class factories: `basal_delivery(...)`, `bolus_req_msg1/2/3(...)`, `bolus_completed(...)`, `daily_basal(...)`, `bg_reading(...)`. Each calls the dataclass constructor with keyword args, so a renamed upstream field raises `TypeError` at test time.
- [ ] **Step 3:** Run `uv run pytest tests/_tconnect_events.py -q` as a smoke import. Expected: it collects 0 tests and raises no import error.
- [ ] **Step 4:** Commit with `test: real-instance tconnectsync event factories`.

### Task 2: Basal: profile, algorithm and temp rates

**Files:** `ingestion/builders.py` (`build_basal_df`), `tests/test_builders.py`.

- [ ] **Step 1: Failing tests:**
  - `test_basal_captures_profile_rate_in_u_per_hr`: profile 800 → 0.8.
  - `test_basal_algorithm_rate_sentinel_is_null`: source 1, algorithmRate 65535 → `algorithm_rate` NaN.
  - `test_basal_algorithm_rate_kept_when_algorithm_source`: source 3, 2350 → 2.35.
  - `test_basal_temp_rate_sentinel_is_null`.
  - `test_basal_columns_order`: the existing 4 columns, then `profile_basal_rate, algorithm_rate, temp_rate`.
  - `test_basal_empty_has_new_columns`.
- [ ] **Step 2:** Run `uv run pytest tests/test_builders.py -k basal -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** Add a module constant `_UINT16_SENTINEL = 65535`, a protocol-level fact (not a tunable threshold, so it does not belong in config), and a helper `_milli_rate_or_nan(raw)`. It returns NaN for `None` or the sentinel, and `raw / 1000.0` otherwise. Extend the row dict and the `columns`.
- [ ] **Step 4:** Run `uv run pytest tests/test_builders.py -q`. Expected: all PASS.
- [ ] **Step 5:** Commit with `feat(ingestion): capture profile/algorithm/temp basal rates`.

### Task 3: Requests: ISF, target, CR, request type, correction flags

**Files:** `ingestion/builders.py` (`build_request_df`), `tests/test_builders.py`.

- [ ] **Step 1: Failing tests:**
  - `test_request_isf_and_target_captured`.
  - `test_request_isf_zero_sentinel_is_null`.
  - `test_request_target_zero_sentinel_is_null`.
  - `test_request_carb_ratio_zero_is_null_for_auto_correction`.
  - `test_request_carb_ratio_scaled` (asserts the D1 scale).
  - `test_request_type_mapping`: 0 insulin, 1 carb, 2 auto_correction, 3 remote, 9 → `unknown_9`.
  - `test_request_missing_msg2_leaves_isf_null` (Msg2 orphan path).
  - `test_request_correction_flags_bool`.
  - `test_auto_correction_keeps_isf`, a regression test for the observation that Control-IQ auto-corrections carry a real ISF.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Add `_REQUEST_TYPE_MAP`.
  - Add `_positive_int_or_none(raw)`, which turns 0 or None into None.
  - Add a carb-ratio scale constant `_CARB_RATIO_RAW_SCALE`. Its value comes from D1, with an inline comment citing the verification. It is a unit conversion, not a threshold.
  - Read Msg2 fields only when `m2` is present.
  - Keep integer-like nullable columns as pandas `Int16`/`boolean` extension dtypes, so that parquet round-trips without float coercion.
- [ ] **Step 4:** Run `uv run pytest tests/test_builders.py tests/test_enrich.py -q`. Expected: PASS. Enrichment must pass the new columns through, so add `test_enrich_requests_preserves_new_columns` to `tests/test_enrich.py`.
- [ ] **Step 5:** Commit with `feat(ingestion): capture ISF/target/CR/request type on bolus requests`.

### Task 4: Bolus: insulin requested, IOB at completion, completion status

**Files:** `ingestion/builders.py` (`build_bolus_df`), `tests/test_builders.py`.

- [ ] **Step 1: Failing tests:**
  - `test_bolus_captures_insulin_requested`.
  - `test_bolus_partial_delivery_requested_differs`.
  - `test_bolus_iob_at_completion`.
  - `test_bolus_completion_status_raw`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit with `feat(ingestion): capture requested insulin + completion IOB on bolus`.

### Task 5: New builders `build_pump_iob_df` (and `build_bg_readings_df` if D3 = yes)

**Files:** `ingestion/builders.py`, `tests/test_builders.py`.

- [ ] **Step 1: Failing tests:**
  - `test_pump_iob_rows_keyed_by_seqnum`, with two events 16 s apart that both survive.
  - `test_pump_iob_units`.
  - `test_pump_iob_dedup_on_seqnum`.
  - `test_build_all_includes_pump_iob`.
  - The equivalent `bg_readings` tests, including `isf=0 → NULL`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Add the builders and register them in `build_all`.
  - `LidDailyBasal`/`LidBgReadingTaken` stay in `_HANDLED_TYPES`, so there is no new unknown-type warning.
  - Do **not** forward-fill or interpolate anything. Raw events only.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit with `feat(ingestion): pump_iob (+ bg_readings) builders`.

### Task 6: Registry, converters, parquet keys, bootstrap

**Files:**
- `core/schema.py`
- `core/storage/parquet.py`
- `core/storage/_postgres_converters.py`
- `scripts/bootstrap_supabase.py`
- `tests/core/test_schema.py`
- `tests/test_bootstrap_supabase.py`
- `tests/core/test_storage_contract.py`

- [ ] **Step 1: Failing tests:**
  - `test_tables_include_pump_iob`.
  - `test_column_specs_match_converter_arity` for every table (it may already exist; extend it).
  - `test_basal_converter_handles_missing_new_columns`: a legacy frame without the new keys produces None, not `KeyError`.
  - `test_bootstrap_table_specs_cover_registry`.
  - A storage contract round-trip for `pump_iob`.
- [ ] **Step 2:** Run `uv run pytest tests/core tests/test_bootstrap_supabase.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Converters read the **new** columns with `r.get(col)`. The original columns keep `r[col]`, so their missing-column behaviour stays loud.
  - Append the new columns at the **end** of each `COLUMN_SPECS` list.
- [ ] **Step 4:** Run the full `uv run pytest`. Expected: PASS. The supabase-parameterized tests skip unless `SUPABASE_TEST_URL` is set. If a test DB is available, apply 0004 there first.
- [ ] **Step 5:** Commit with `feat(core): register Control-IQ columns + pump_iob in storage layer`.

### Task 7: Opt-in "fill new columns" upsert mode (the backfill UPDATE path)

**Problem:** neither backend fills new columns on rows that already exist.
- Supabase uses `DO NOTHING`.
- Parquet and in-memory use `keep="first"`, where the old row wins and the new columns stay NaN.

The nightly sync alone therefore populates new columns **only for rows first written after deploy**.

**Files:**
- `core/storage/protocol.py`
- `core/storage/supabase.py`
- `core/storage/parquet.py`
- `core/storage/memory.py`
- `tests/core/test_storage_contract.py`

- [ ] **Step 1: Failing contract tests,** parameterized over all backends:
  - `test_upsert_fill_columns_populates_nulls_on_existing_rows`.
  - `test_upsert_fill_columns_never_overwrites_non_null`.
  - `test_upsert_fill_columns_does_not_touch_unlisted_columns`.
  - `test_upsert_default_still_do_nothing`, a regression guard.
  - `test_upsert_fill_columns_rejects_pk_columns`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement** `upsert_table(name, df, *, fill_columns: Sequence[str] | None = None)`. The default `None` keeps today's behaviour exactly.
  - **Supabase:** when `fill_columns` is set, the statement becomes `ON CONFLICT (pk) DO UPDATE SET c = COALESCE(t.c, EXCLUDED.c), … WHERE t.c1 IS NULL OR t.c2 IS NULL …`, aliasing the target table as `t`. Count updated rows separately: extend `UpsertResult` with `rows_filled: int = 0`. Validate `fill_columns` against `COLUMN_SPECS[name]` and reject PK columns.
  - **Parquet and in-memory:** align on the PK, `existing[c] = existing[c].fillna(incoming[c])` for each fill column only, then the normal keep-first concat for new rows.
- [ ] **Step 4:** Run `uv run pytest tests/core -q`. Expected: PASS.
- [ ] **Step 5:** Commit with `feat(storage): opt-in fill_columns upsert for additive backfills`.

### Task 8: Sync script backfill flags and the PK-alignment dry-run

**Files:** `scripts/sync_tandem_to_supabase.py`, `tests/test_sync_tandem_to_supabase.py`. Rebase on the fail-loud branch first.

- [ ] **Step 1: Failing tests:**
  - `test_fill_new_columns_flag_passes_fill_columns_only_for_listed_tables`: basal, bolus and requests get their new columns, and the new tables do a plain insert.
  - `test_check_pk_alignment_reports_match_rate`, using an in-memory storage seeded with existing rows, some with drifted timestamps.
  - `test_fill_requires_explicit_window`: `--fill-new-columns` without `--start/--end` exits non-zero.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Add a single constant `NEW_COLUMNS_V5 = {"basal": [...], "bolus": [...], "requests": [...]}`.
  - Add `--fill-new-columns`, which requires `--start` and `--end`.
  - Add `--check-pk-alignment`, which is read-only:
    1. Build the frames for the window.
    2. For each of basal/bolus/requests, count how many fetched PKs already exist in storage, versus how many existing rows in the window are *not* matched.
    3. Print the per-table counts and match rate.
    4. Write nothing.
  - Log fill counts with `rows_filled`. Never log serials unmasked (reuse `_mask_serial`).
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit with `feat(sync): --fill-new-columns backfill + --check-pk-alignment dry-run`.

### Task 9: PIPELINE_VERSION bump, contract test, docs

**Files:**
- `ingestion/pipeline_version.py`
- `tests/test_pipeline_version.py`
- `tests/test_tconnectsync_field_contract.py`
- `CLAUDE.md`
- `docs/updates/…`

- [ ] **Step 1:** In `tests/test_tconnectsync_field_contract.py`, add the newly read attributes:
  - `LidBasalDelivery`: `profileBasalRate`, `algorithmRate`, `tempRate`.
  - `LidBolusRequestedMsg1`: `bolusTypeRaw`, `carbRatioRaw`, `correctionBolusIncludedRaw`.
  - `LidBolusRequestedMsg2`: `isf`, `targetBg`, `declinedCorrectionRaw`.
  - `LidBolusCompleted`: `insulinRequested`, `iob`, `completionStatusRaw`.
  - `LidDailyBasal`: `dailyTotalBasal`, `lastBasalRate`, `iob`.
  - `LidBgReadingTaken`: `bg`, `iob`, `isf`, `targetBg`, `bgEntryTypeRaw`, `bgSourceTypeRaw`.
  - Run it. Expected: PASS against 3.0.3.
- [ ] **Step 2:** Bump `PIPELINE_VERSION = 5` with this changelog entry: *"Additive: capture profile/algorithm/temp basal rates, per-request ISF/target/CR/request type/correction flags, requested insulin + completion IOB on bolus, and new `pump_iob` (+ `bg_readings`) tables from previously-dropped tconnectsync v3 fields. Sentinels (65535 rates; 0 ISF/CR/target) stored as NULL. Prior parquets lack these columns: run `fetch --clean` (parquet keep-first dedup will not fill them in place). Supabase rows are filled by the one-shot `--fill-new-columns` backfill."* Update `tests/test_pipeline_version.py` if it pins the number.
- [ ] **Step 3:** Update the `CLAUDE.md` table count (13 → 14 or 15) and the test counts after the full run.
- [ ] **Step 4:** Write `docs/updates/<date>-control-iq-field-capture.md`.
- [ ] **Step 5:** Run `uv run pytest`, `uv run python main.py doctor` (expect a stale-pipeline warning on any old local parquet, which is the intended signal), and the web CI unchanged (`tsc --noEmit`, vitest).
- [ ] **Step 6:** Commit with `chore: PIPELINE_VERSION 5 + docs for Control-IQ field capture`.

---

## 6. Backfill strategy and idempotency

1. **Forward capture (automatic).** Once the code is deployed, the nightly sync writes the new columns on every *newly inserted* row. Its window overlaps the previous day by one day. Overlap rows that already exist hit `DO NOTHING`, so they keep NULLs. The one-shot fill below covers them.
2. **New tables (automatic plus one-shot).** `pump_iob`/`bg_readings` are plain inserts, so `ON CONFLICT DO NOTHING` is already correct and idempotent. The historical window is covered by the same one-shot run.
3. **One-shot historical fill (Supabase).** Run `uv run --with tconnectsync==3.0.3 python -m scripts.sync_tandem_to_supabase --start A --end B [--only SERIAL] --fill-new-columns`, in chunks of 30–90 days, newest first.
   - It is **idempotent**: `COALESCE(t.c, EXCLUDED.c)` with a `WHERE … IS NULL` guard means a re-run fills nothing new and never overwrites.
   - It is **resumable**: re-run any chunk freely.
   - It is **non-destructive**: it never touches columns outside `NEW_COLUMNS_V5`, and never deletes.
4. **Gate before step 3: the PK alignment dry-run.** `basal` is keyed on `(pump_serial, timestamp)`, not seqnum. History was written by (a) v2 byte decode, (b) v3 JSON, and (c) the `repair_nightly_sync_tz.py` +3h correction. If a re-fetched timestamp differs from the stored one in any way (TZ label, DST, second rounding), the fill **inserts a shifted duplicate** instead of filling the existing row.
   - Run `--check-pk-alignment` on a sample window from each era: pre-2026-05-31 bootstrap era, the repaired 2026-05-31…06-30 window, and post-2026-07 v3 era.
   - Proceed only where basal and requests match at ≥ 99.9% (bolus/requests are keyed by `bolus_id`, so they are inherently safer).
   - If an era misaligns, stop and escalate. Do not "fix" by deleting.
5. **Window choice is an owner decision (§8 D2).** The options are full history across all 6 pumps or only the current pump. Older pumps were never validated against v3 field population. For example, `profileBasalRate` may be 0 or absent on old firmware. Run `--dry-run` on one early chunk per pump and inspect null rates before filling.
6. **Parquet / local shell.** There is no in-place fill by default. The documented path is `uv run python main.py fetch --clean`, a full re-pull that rebuilds every parquet at PIPELINE_VERSION 5. `doctor` and `version_guard` already warn when the sidecar is behind. The `fill_columns` parquet implementation from Task 7 exists for contract parity and for a possible future `fetch --fill-new-columns`, but wiring it into `main.py fetch` is **not** in scope.
7. **`events.details` Tier 2 additions** are not fillable by COALESCE because `details` is non-null jsonb. They apply only to rows written after deploy, or after a parquet `fetch --clean`. M4 code must treat the keys as optional.

---

## 7. Rollout order

0. **Prerequisite:** `fix/sync-fail-loud-and-redaction` (tconnectsync 3.0.3 pin, fail-loud, redaction) is merged to `main`.
1. **Owner decisions D1–D6 recorded** (§8). D1 (CR scale) blocks Task 3. D3 blocks the `bg_readings` block of the migration.
2. **Owner applies `0004_control_iq_fields.sql`** to Supabase through the SQL editor or psql (direct connection). Verify:
   - (a) `\d basal`, `\d bolus` and `\d requests` show the new nullable columns;
   - (b) `pump_iob` (and `bg_readings`) exist with RLS enabled;
   - (c) `SET ROLE anon; SELECT count(*) FROM pump_iob;` → 0;
   - (d) the existing nightly sync still succeeds. This is a no-op for it, because the old code's INSERT lists only the old columns.

   The migration is safe to apply **before** the code: every addition is nullable and additive.
3. **Merge the code PR** (Tasks 1–9). The next nightly sync writes the new columns for new rows and starts filling `pump_iob`.
4. **Watch one nightly cycle.** Check non-null rates on the last 24h of basal, requests and bolus, and the row count in `pump_iob`.
5. **Run `--check-pk-alignment`** per era (§6.4).
6. **Run the one-shot `--fill-new-columns` backfill** in chunks, newest first.
7. **Local:** `uv run python main.py fetch --clean`, then `doctor`.
8. **Write the dated `docs/updates/` entry** with null-rate tables (aggregate only, no therapy values).

**Rollback:**
- Code: revert the PR. The old INSERTs ignore the new columns.
- Schema: the columns can stay (harmless), or the owner can run `ALTER TABLE … DROP COLUMN IF EXISTS …; DROP TABLE IF EXISTS pump_iob, bg_readings;`.
- No existing data is modified at any step except NULL → value fills.

---

## 8. Open decisions for the owner

- **D1: Carb-ratio scale.** The observed `carbRatioRaw` is in the low thousands, and tconnectsync labels it "g/u". Confirm against the pump's programmed CR: is raw ÷ 1000 equal to g/U? This blocks Task 3. The fallback is to store it as `carb_ratio_raw integer` and defer interpretation.
- **D2: Backfill extent.** Fill all 6 pumps' history, only the current pump, or only the last N months? Older-pump field population is unverified.
- **D3: `bg_readings` table** (Tier 2): yes or no.
- **D4: `pcm_change` details enrichment** (Tier 2): yes or no.
- **D5: `pump_iob` naming and retention.** It is roughly 200 rows per day, about 73k per year, which is trivial. Confirm the name. Also decide whether `daily_total_basal` should become a daily rollup rather than a per-event column.
- **D6: Unparsed RawEvent codes** (219, 22/23, 434, …): pursue upstream decoding, or not?
- **D7: The basal PK is timestamp-based.** Should a nullable `seqnum` be added to `basal` now? It would make future fills robust to timestamp drift. That is a cheap column, but switching the PK later is a separate migration. This plan does not change the PK.

---

## 9. Test plan summary

| Layer | Tests |
|---|---|
| Builders | Real-instance fixtures. Sentinel → NULL for every sentinel observed (65535 ×2, 0 ×3). Units (÷1000). Empty frames carry new columns. Orphan Msg2 → NULL ISF. Auto-correction keeps ISF. |
| Contract (upstream) | `test_tconnectsync_field_contract.py` lists every newly-read attribute. |
| Registry / converters | Arity match. Legacy frames without new columns convert to None instead of raising `KeyError`. Bootstrap covers the registry. |
| Storage contract (all 3 backends) | `fill_columns` fills NULLs only, never overwrites, rejects PK columns; default unchanged. New-table round-trip. |
| Sync script | Flag wiring, explicit-window requirement, read-only PK-alignment report. |
| Enrichment | `enrich_all` preserves new `requests` columns. |
| Regression | Full `uv run pytest` (`-m legacy` unaffected). Web `tsc`/vitest/`next build` unchanged. |
| Live verification (owner, read-only) | Post-deploy null-rate query; `anon` sees 0 rows; PK-alignment per era. |

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| **Sentinels leak as data**: 65535 → 65.535 U/hr phantom rate, ISF 0 → divide-by-zero in M4 | Explicit sentinel → NULL rules with named tests; column COMMENTs document the sentinel |
| **Backfill inserts shifted duplicates** on timestamp-keyed `basal` after the TZ repair or v2/v3 decode differences | Mandatory `--check-pk-alignment` gate per era; stop if < 99.9% |
| **Parquet silently keeps NULLs** because keep-first dedup means old rows win | PIPELINE_VERSION bump, `doctor` warning, documented `fetch --clean` |
| **Concurrent sync-script edits** (fail-loud branch) | Hard prerequisite: rebase after it merges; Task 8 is last in the sync-script path |
| **tconnectsync drift** (field renames, as in the v2→v3 outage) | Contract test extended; pinned to 3.0.3 by the prerequisite branch |
| **Old-pump history lacks fields** | Dry-run null-rate inspection per pump before filling (D2) |
| **Therapy profile exposed in the public repo** | No real values in fixtures, docs or test names; update docs report aggregates only |
| **Misreading "programmed" as "effective" ISF** in M4 | Column COMMENTs say *programmed*; effective-ISF modelling stays an owner-reserved M4 decision |
| **Temp-basal semantics unverified** (source 2/4 never observed) | Column captured but documented unverified; add a fixture from real data the first time a temp basal is observed |
| **Migration and code deployed out of order** | Migration then code is safe, because the old code ignores the new columns. Code then migration is not safe: the new INSERT fails loudly on the missing columns. That is why the migration is applied first (§7 step 2). |
