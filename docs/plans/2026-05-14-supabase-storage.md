# Plan: SupabaseStorage implementation

**Date:** 2026-05-14
**Branch:** `feat/supabase-storage`
**Status:** Ready to execute
**Predecessor:** `docs/plans/2026-05-13-storage-protocol-and-schema-registry` (landed)

## Context

Phase 1 of the storage abstraction landed `ParquetStorage` and `InMemoryStorage` against the `Storage` Protocol. Supabase Postgres is bootstrapped with 725K historical rows and an idempotent migration in `db/migrations/0001_init.sql`, but no application code can talk to it yet. This plan implements `SupabaseStorage`, the third Protocol implementation, which unblocks the live alert loop, the Tandem nightly sync, the dashboard backend, and Telegram handlers.

The hard work of pandas-to-Postgres translation is already written inside `scripts/bootstrap_supabase.py` (the `CONVERTERS`, `COLUMN_SPECS`, and null-safe scalar helpers, exercised by 78 test cases). The first task is to move them into `core/` where the implementation can consume them.

## Architectural rules

### Connection management

The binding risk for Postgres + serverless is connection leaks, not raw count. The 60 direct / 200 pooler connection limits on the Supabase free tier are 10x larger than the worst realistic concurrent demand on this single-user system. Two patterns and one Postgres-side backstop eliminate leak risk:

**Connection URL by caller:**

- Vercel cron functions, Telegram webhook handlers, dashboard API routes → transaction-mode pooler at `aws-0-<region>.pooler.supabase.com:6543`. Short-lived, ephemeral, fits how Vercel functions actually run.
- GitHub Actions nightly sync, one-shot bootstrap script → direct connection at `db.<project>.supabase.co:5432`. Long-lived, may use prepared statements, transaction-pooler-incompatible.

**SupabaseStorage lifecycle:** two constructor entry points.

```python
# Short-lived (Vercel functions)
with SupabaseStorage.from_pooler_url(url) as storage:
    storage.upsert_table("cgm", df)

# Caller-managed (bootstrap, GH Action)
storage = SupabaseStorage(conn=existing_conn)
storage.upsert_table("cgm", df)  # never closes a conn it didn't open
```

**Postgres-side backstop:** the migration sets `idle_in_transaction_session_timeout = '5min'` so any leaked connection is reclaimed automatically.

### Postgres-first aggregation contract

The dashboard does its heavy aggregations (heatmap by hour-of-day, TIR rolling trends, multi-day summaries) Postgres-side, not client-side. This is a 10-100x bandwidth reduction against the 5 GB/month Supabase free-tier egress cap, and shorter query windows reduce pooler pressure.

This affects future code more than `SupabaseStorage` itself. The Protocol surface stays as designed. Aggregation queries that are Supabase-specific (no parquet equivalent) live as additional methods on `SupabaseStorage` directly, not on the Protocol, and are called from dashboard routes that already know they're talking to Supabase.

## Implementation tasks

### Task 1 — Migrate the Postgres converters to `core/`

**Files:**
- Create: `core/storage/_postgres_converters.py`
- Modify: `scripts/bootstrap_supabase.py` (import from the new location)
- Modify: `tests/test_bootstrap_supabase.py` (update import paths only)

Move from `scripts/bootstrap_supabase.py` to `core/storage/_postgres_converters.py`:

- All `_*_or_none` scalar helpers (`_is_null`, `_ts_or_none`, `_int_or_none`, `_float_or_none`, `_bool_or_none`, `_str_or_none`, `_details_to_json`)
- `COLUMN_SPECS`
- `CONVERTERS` and the per-table `_<name>_row` functions

Keep `TABLE_SPECS` in `bootstrap_supabase.py` for now since it's table-name + primary-key, which is duplicated with `core/schema.TABLES`. Convergence is a Task 4 follow-up.

The leading underscore on `_postgres_converters` flags it as a Postgres-implementation-only module within `core/storage/`. Outside callers go through `SupabaseStorage`.

**Step 1: failing tests.** None new; the existing 78 cases in `tests/test_bootstrap_supabase.py` already cover this code. They must pass with only import path changes.

**Step 2: move code, update imports.** No behavior change.

**Step 3: verify.** `uv run pytest tests/test_bootstrap_supabase.py -v` — all pass.

**Step 4: commit.**

```bash
git add core/storage/_postgres_converters.py scripts/bootstrap_supabase.py tests/test_bootstrap_supabase.py
git commit -m "refactor: extract postgres converters into core/storage/_postgres_converters.py"
```

### Task 2 — Add the migration setting for `idle_in_transaction_session_timeout`

**Files:**
- Create: `db/migrations/0002_idle_timeout.sql`

```sql
-- Postgres backstop against connection leaks from serverless functions.
-- Any session that holds a transaction open for more than 5 minutes
-- without activity is automatically terminated. Belt-and-suspenders
-- behind the SupabaseStorage lifecycle discipline.
ALTER DATABASE postgres SET idle_in_transaction_session_timeout = '5min';
```

Apply manually against the Supabase project (one-time SQL). Document the apply in the eventual `docs/updates/` entry.

### Task 3 — Implement `core/storage/supabase.py`

**Files:**
- Create: `core/storage/supabase.py`
- Modify: `core/storage/__init__.py` (export `SupabaseStorage`)

Implement all Protocol methods. Key implementation notes per method:

`read_table(name, *, since, until, pump_serial=None)`:
- Parameterized `SELECT * FROM {name} WHERE {time_col} >= %s AND {time_col} < %s` with optional `AND pump_serial = %s`.
- `time_col` from `core.schema.get_spec(name).time_column`.
- Result fetched as list of dicts, converted to `pd.DataFrame` with explicit dtype enforcement matching what `ParquetStorage` returns. Contract tests will catch any divergence.

`read_all_table(name)`:
- `SELECT * FROM {name}`. Used by batch jobs only.

`upsert_table(name, df)`:
- Reuse the converter pattern from `bootstrap_supabase.insert_table`. `execute_values` with `ON CONFLICT ({pk_cols}) DO NOTHING`.
- PK columns from `core.schema.get_spec(name).primary_key`.
- Return `UpsertResult(rows_received, rows_inserted, rows_skipped, elapsed_seconds)`.
- Commit per-chunk for durability (proven pattern from the bootstrap).

`delete_range(name, *, since=None, until=None, pump_serial=None)`:
- `DELETE FROM {name} WHERE <conditions>`. At least one bound required (Protocol guarantees this).
- Return `cur.rowcount`.

`get_fetch_state(source_id)`, `set_fetch_state(...)`, `list_fetch_state()`:
- Direct SQL against `fetch_state` table. Read returns `FetchState(...)` dataclass; write does `INSERT ... ON CONFLICT (source_id) DO UPDATE`.

`get_pipeline_version()`, `set_pipeline_version(version)`:
- Stored in a single-row table or in `detection_config` keyed on `pipeline_version`. Subtask: pick one consistently with how `ParquetStorage` stores it (which uses a sidecar). Recommend `detection_config` row with key `'pipeline_version'`.

`record_alert(alert)`:
- `INSERT INTO alerts_sent (alert_kind, fired_at, pump_serial, event_ref, payload, delivery) VALUES (...) ON CONFLICT (alert_kind, event_ref) WHERE event_ref IS NOT NULL DO NOTHING`. The `WHERE` clause in the `ON CONFLICT` is mandatory because the unique index is partial; Postgres requires the predicate to match.
- Return the inserted (or existing, if conflict) record.

`find_alert(alert_kind, event_ref)`:
- `SELECT ... FROM alerts_sent WHERE alert_kind = %s AND event_ref = %s ORDER BY fired_at DESC LIMIT 1`.

`recent_alerts(alert_kind, within)`:
- `SELECT ... WHERE alert_kind = %s AND fired_at > now() - %s::interval`.

`record_detection_result(result)`, `list_detection_results(...)`:
- Direct SQL against `detection_results` table. Schema: `(id bigserial, kind text, anchor_timestamp timestamptz, payload jsonb, created_at timestamptz default now())`. Migration in Task 2 also adds this table (move to its own migration if cleaner).

`clean_all()`:
- `TRUNCATE` every table in `core.schema.TABLES` plus `alerts_sent`, `fetch_state`, `detection_config`, `detection_results`. Wrap in a transaction. Refuses to run without an explicit `confirm=True` argument (since this is destructive — match what `ParquetStorage` does or doesn't do, then pick the safer of the two).

Lifecycle:
- `__init__(conn)`: caller-managed. `__enter__/__exit__` no-op (does not own the conn).
- `from_pooler_url(url)` classmethod: opens a connection, returns a `SupabaseStorage` instance that DOES own it. `__enter__` returns self; `__exit__` closes the conn.

### Task 4 — Add `"supabase"` to the contract test fixture

**Files:**
- Modify: `tests/core/test_storage_contract.py`

Add a third parameterization. Skipped if `SUPABASE_TEST_URL` env var is missing. Each test method that requires a fresh state runs `storage.clean_all(confirm=True)` (or its test-only equivalent) before the test body to isolate runs.

```python
@pytest.fixture(params=["memory", "parquet", "supabase"])
def storage(request, tmp_path):
    match request.param:
        case "memory":
            return InMemoryStorage()
        case "parquet":
            return ParquetStorage(root=tmp_path)
        case "supabase":
            url = os.environ.get("SUPABASE_TEST_URL")
            if not url:
                pytest.skip("SUPABASE_TEST_URL not set")
            return SupabaseStorage.from_pooler_url(url)
```

Decision needed during implementation: dedicated test Supabase project, vs. a `test_` schema in the main project that gets truncated between runs. The dedicated project is cleaner; the in-project test schema is faster to set up. Implementer's call; not load-bearing.

### Task 5 — Update `docs/updates/` with the dated entry

`docs/updates/2026-05-XX-supabase-storage.md` describing what landed, the connection management rules, the contract test status, and pointers to this plan.

## Acceptance criteria

- All existing tests pass unchanged (no caller of `Storage` is modified by this PR).
- `tests/core/test_storage_contract.py` runs the same test bodies against all three backends; the `"supabase"` branch is skipped when env var is missing, exercised in CI when present.
- `bootstrap_supabase.py` continues to insert all 725K rows successfully (this validates the converter migration in Task 1).
- The `read_table` and `delete_range` methods correctly push WHERE clauses into SQL; verified by integration tests against a real Supabase instance.
- `record_alert` correctly handles the partial unique constraint `WHERE event_ref IS NOT NULL` in its `ON CONFLICT` clause.

## Sequencing within the task

Tasks 1, 2, 3 in order. Task 4 can begin as soon as Task 3 has a skeleton (it shares the test file). Task 5 last.


## What this unblocks

Once merged:

1. The live alert loop (Vercel cron) can take a `SupabaseStorage` and run end-to-end.
2. The Tandem nightly sync (GitHub Action) replaces `bootstrap_supabase.py`'s direct calls with `SupabaseStorage.upsert_table(...)` invocations, runs incremental, and writes via the Protocol.
3. The dashboard backend (Next.js API routes) can read via `SupabaseStorage.read_table(...)` for day views and add Supabase-specific aggregation methods for heatmap and TIR trends.
4. Telegram webhook handlers can compose `SupabaseStorage.read_table(...)` calls to assemble context for DeepSeek prompts.

All four are independent workstreams from this point. They can run in parallel. When we reach this step we will discuss our approach.