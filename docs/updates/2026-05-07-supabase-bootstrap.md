# Supabase Schema Bootstrap — Phase 1 Inventory + Phase 4 Verification

**Branch:** `feat/supabase-bootstrap`
**Migration:** `db/migrations/0001_init.sql`
**Bootstrap:** `scripts/bootstrap_supabase.py`
**Run date:** 2026-05-08
**Project:** `db.vvrvsxiqquucxytxdcvs.supabase.co` (Postgres 17.6)

This doc is the combined deliverable for Phases 1 and 4 of the one-time historical migration to Supabase. The Phase 2 schema design and Phase 3 implementation live in the migration SQL and bootstrap script respectively (both heavily commented; treat them as the source of truth).

---

## Phase 1 — Data inventory

Snapshot of the parquets in `data/processed/` at the time of bootstrap (pipeline_version = 3).

### Tables and row counts

| Table | Rows | Span (per primary timestamp) | Notes |
|---|---:|---|---|
| `cgm` | 300,324 | 2021-11-12 → 2026-04-14 | 5-min cadence; 51,770 backfilled; 387 timestamp-collisions resolved by `seqnum` PK |
| `bolus` | 11,115 | 2021-11-12 → 2026-04-14 | |
| `requests` | 11,114 | 2021-11-12 → 2026-04-14 | 1 historical orphan vs `bolus` (no FK declared) |
| `basal` | 341,885 | 2021-01-10 → 2026-04-14 | Largest table; 5-min cadence |
| `suspension` | 1,738 | 2021-11-13 → 2026-04-13 | 2 open suspends (NULL `resume_timestamp`) |
| `events` | 16,245 | 2021-01-01 → 2026-04-14 | 630 timestamp-collisions (cartridge + tubing same second); resolved by `seqnum` PK |
| `alarms` | 41,121 | 2021-01-01 → 2026-04-14 | 6,082 timestamp-collisions; 51 distinct `alarm_name` values incl. dynamic `unknown_<n>` |
| `site_issues` | 42 | 2021-11-19 → 2026-03-26 | Derived from clustered `OcclusionAlarm` |
| `cgm_gaps` | 2,078 | 2024-01-13 → 2026-04-14 | Derived from `cgm_out_of_range`; all currently `ongoing=False` |
| **Total** | **725,662** | | |

Six pump serials are present (`884750`, `984922`, `90693745`, `90899083`, `91727084`, `1513861`). The active pump is `1513861` (Jan 2025 → present). All composite PKs are `(pump_serial, <natural_id>)`.

### Vocabularies (became Postgres `ENUM`s)

| Type | Values |
|---|---|
| `bolus_source` | `user`, `auto`, `override`, `unknown` |
| `bolus_category` | `auto_correction`, `user_meal`, `user_meal_and_correction`, `user_correction_only`, `override_up`, `override_down`, `unknown` |
| `basal_rate_source` | `algorithm`, `profile`, `suspended`, `temp_rate`, `temp_rate_and_algorithm`, `unknown` |
| `suspend_reason` | `user`, `alarm`, `malfunction`, `plgs_auto`, `unknown` |
| `pump_event_type` | `site_change`, `cgm_session`, `mode_change`, `pcm_change`, `daily_marker` |
| `alarm_category` | `alarm`, `alert`, `cgm_alert` |
| `alarm_action` | `activated`, `cleared`, `ack` |

`alarm_name` and `event_subtype` were left as `text` (open vocabularies including dynamic `unknown_<n>` fallbacks).

### Data-quality observations (inserted as-is, not coerced)

Per spec ("don't silently coerce"), the bootstrap preserves the parquets verbatim. These are observed in the source data and carry over into Postgres without CHECK constraints.

| Table | Observation | Action |
|---|---|---|
| `cgm` | 1,217 rows (0.4%) have `bg_mgdl == 0`; 1,776 (0.6%) have `bg_mgdl < 40`. Live G7 hard floor is ~40, so these are sensor sentinels. | Inserted as-is; no CHECK. |
| `requests` | 2,392/11,114 rows (21.5%) have `bg_mgdl == 0` — manual fingerstick missing values, documented in DATA_NOTES. | Inserted as-is. |
| `bolus` | 3 rows have `insulin_units == 25.000001907348633` (float32→float64 promotion artifact). | Stored as `numeric(6,3)`; rounds cleanly to `25.000`. |
| `alarms` | `cgm_fall_rate` activations carry `param1 ≈ 4.29e9` (uint32 sentinel for "no value"). | Stored as `bigint`; consumer interprets sentinel. |
| `bolus`/`requests` | 1 bolus exists with no matching request; 0 requests with no bolus. | No FK on `requests.bolus_id`; `COMMENT ON COLUMN` records the reason. |

---

## Phase 2 — Schema design (summary)

Full design: `db/migrations/0001_init.sql` (390 lines, 35 explanatory `COMMENT` statements).

- 12 tables: 9 existing-data (above) + 3 new (`alerts_sent`, `fetch_state`, `detection_config`).
- 7 enums (above). Open vocabularies kept as `text`.
- All timestamps `timestamptz`. Numerics for insulin/rates/durations: `numeric(6,3)` or `numeric(10,3)` depending on observed range.
- Composite PKs `(pump_serial, natural_id)` enable `ON CONFLICT DO NOTHING` idempotency.
- 10 explicit non-PK indexes:
  - `(pump_serial, timestamp DESC)` on `cgm`, `bolus`, `requests`, `events`, `alarms` for "last N hours" queries.
  - `(pump_serial, event_type, timestamp DESC)` on `events` for site_change history.
  - `(pump_serial, alarm_name, action, timestamp DESC)` on `alarms` for the enrichment queries (OcclusionAlarm / cgm_out_of_range / BatteryShutdownAlarm).
  - `(pump_serial, resume_timestamp)` on `suspension` for the open-suspend lookup.
  - `(alert_kind, fired_at DESC)` on `alerts_sent` for "alerted in last N min" probes.
  - **Partial UNIQUE** on `alerts_sent (alert_kind, event_ref) WHERE event_ref IS NOT NULL` for idempotent live-alert inserts. (See "Finding 1" in the verification section for the exact `ON CONFLICT` syntax callers must use.)
- No FKs declared (one historical `requests`/`bolus` orphan + several derived tables don't have stable references).
- No RLS (single-user system, deferred).
- No partitioning, materialized views, or triggers.

---

## Phase 4 — Verification report

### Migration (`db/migrations/0001_init.sql`)

| Step | Result | Time |
|---|---|---:|
| Apply (run 1, fresh schema) | OK | 0.39s |
| Apply (run 2, idempotency check) | OK, no errors | 0.27s |
| Schema landed: 12 tables, 7 enums, 22 indexes (12 PK + 10 explicit) | OK | — |

### Bootstrap (`scripts/bootstrap_supabase.py`)

**First run (full insert):**

```
table        parquet_rows  inserted  skipped  elapsed
cgm          300324        300324    0        19.0s
bolus        11115         11115     0        0.6s
requests     11114         11114     0        1.1s
basal        341885        341885    0        18.2s
suspension   1738          1738      0        0.2s
events       16245         16245     0        1.5s
alarms       41121         41121     0        3.6s
site_issues  42            42        0        0.1s
cgm_gaps     2078          2078      0        0.2s
TOTAL        725662        725662    0        44.4s
```

**Second run (idempotency check):**

```
table        parquet_rows  inserted  skipped  elapsed
cgm          300324        0         300324   18.5s
bolus        11115         0         11115    0.5s
requests     11114         0         11114    1.1s
basal        341885        0         341885   16.1s
suspension   1738          0         1738     0.1s
events       16245         0         16245    1.2s
alarms       41121         0         41121    2.6s
site_issues  42            0         42       0.1s
cgm_gaps     2078          0         2078     0.2s
TOTAL        725662        0         725662   40.4s
```

Every row was conflict-skipped on the second run. No duplicates created.

### Per-table parity (parquet vs Postgres)

| Table | Parquet rows | Postgres rows | Match |
|---|---:|---:|:---:|
| `cgm` | 300,324 | 300,324 | OK |
| `bolus` | 11,115 | 11,115 | OK |
| `requests` | 11,114 | 11,114 | OK |
| `basal` | 341,885 | 341,885 | OK |
| `suspension` | 1,738 | 1,738 | OK |
| `events` | 16,245 | 16,245 | OK |
| `alarms` | 41,121 | 41,121 | OK |
| `site_issues` | 42 | 42 | OK |
| `cgm_gaps` | 2,078 | 2,078 | OK |

### Per-table date-range match

Every primary timestamp's `min` and `max` agree to the second across the round-trip. Postgres echoes timestamps in UTC (e.g. `2026-04-14 20:31:19-07:00` becomes `2026-04-15 03:31:19+00:00`) — this is `timestamptz` storage doing exactly what it's supposed to: same instant, equivalent representation. Python `datetime` equality (instant comparison) returns `True` across both representations.

### Spot check — last 7 days of pump `1513861`

Anchor: max `cgm.timestamp` for the live pump = `2026-04-14 20:31:19-07:00`.
Window: `[2026-04-07 20:31:19-07:00 → 2026-04-14 20:31:19-07:00]`.

| Metric | Parquet | Postgres | Match |
|---|---:|---:|:---:|
| `cgm` row count | 1,767 | 1,767 | OK |
| `cgm` mean `bg_mgdl` | 155.81 | 155.81 | OK |
| `bolus` row count | 58 | 58 | OK |

### New-tables sanity

| Table | Initial row count | Insert + delete | UNIQUE / ON CONFLICT semantics |
|---|---:|---|---|
| `alerts_sent` | 0 | OK | Partial-unique enforced on duplicate `(alert_kind, event_ref)`; NULL `event_ref` rows are not deduped (intentional). See Finding 1. |
| `fetch_state` | 0 | OK | — |
| `detection_config` | 0 | OK | — |

### Findings

#### Finding 1 — `ON CONFLICT` against the partial unique requires the predicate

The first naive form `INSERT … ON CONFLICT (alert_kind, event_ref) DO NOTHING` fails with:

```
ERROR: there is no unique or exclusion constraint matching the ON CONFLICT specification
```

This is a documented Postgres semantic: when the unique index is *partial*, `ON CONFLICT` must respecify the partial predicate so the planner can prove that the inserted row falls inside the indexed subset. The correct form is:

```sql
INSERT INTO alerts_sent (alert_kind, event_ref, payload)
VALUES ('anomaly_spike', 'cgm:1513861:243687', '{"bg": 295}'::jsonb)
ON CONFLICT (alert_kind, event_ref) WHERE event_ref IS NOT NULL DO NOTHING;
```

This was working as designed (and the design choice — keep the index partial so rows without an `event_ref` aren't treated as duplicates of each other — is intentional). The `COMMENT ON INDEX alerts_sent_event_ref_uq` was updated to spell out the exact `ON CONFLICT` clause callers need; future-detector implementers will see it via `\d+ alerts_sent_event_ref_uq` in psql or the Supabase dashboard.

No code change needed in this branch — the bootstrap doesn't insert into `alerts_sent`.

#### Finding 2 — Direct host is IPv6-only on this Supabase project

`db.vvrvsxiqquucxytxdcvs.supabase.co` has an `AAAA` record but no `A` record (Supabase removed default IPv4 from new projects in 2024). The connection still works because this user's network has IPv6, and psycopg2 happily takes the v6 route. The connect time observed was 0.24s.

If a future runner (e.g. a CI machine without IPv6) needs to use the same `SUPABASE_DB_URL`, the alternative is the **session-mode pooler** (port 5432, host `aws-0-<region>.pooler.supabase.com`, user `postgres.<project_ref>`). Session-mode does NOT have the prepared-statement / statement-size constraints of transaction mode, so it's a safe replacement for the direct connection in the bulk-insert path.

---

## Files changed (Phase 3 + Phase 4)

| File | Lines | Purpose |
|---|---:|---|
| `db/migrations/0001_init.sql` | 390 | Idempotent schema migration (12 tables + 7 enums + 22 indexes) |
| `scripts/bootstrap_supabase.py` | 569 | Per-table parquet → bulk-insert via `psycopg2.extras.execute_values` |
| `tests/test_bootstrap_supabase.py` | 654 | 39 test functions / 78 cases for converters + cross-checks |
| `pyproject.toml` | +1 dep | `psycopg2-binary>=2.9.12` |
| `uv.lock` | regen | — |
| `.env.example` | 13 | Documents `SUPABASE_DB_URL` + the three existing vars |
| `.gitignore` | +1 | Negation pattern so `.env.example` isn't ignored |
| `docs/operating_docs/2026-05-07-supabase-bootstrap.md` | this file | Inventory + verification report |

Test suite: `400 passed, 1 skipped, 6 warnings` (was 322 + 1 before this work).

---

## Out-of-scope follow-ups

These were noted in code review but deferred per the spec ("don't over-engineer"):

1. `_str_or_none` in the bootstrap script silently stringifies non-string inputs. Not a current problem (no caller passes non-strings), but a future converter author could rely on this and produce surprising rows.
2. `suspension_pump_resume_idx` could be a partial index `WHERE resume_timestamp IS NULL` (the natural "open suspends" query), which would shrink the index ~99%. Today it's a plain index on `(pump_serial, resume_timestamp)`.
3. `alerts_sent.delivery` is `text` with hand-documented values (`pending|sent|failed`). Could be promoted to an enum or a CHECK constraint for consistency with the other status columns.
4. The bootstrap's `verify_inserted` helper is defined but unused. Either wire it into the post-run report (defense-in-depth) or delete it.
5. The follow-up task that retargets the nightly GitHub Action to Supabase is out of scope for this PR (per the spec's scope note).
