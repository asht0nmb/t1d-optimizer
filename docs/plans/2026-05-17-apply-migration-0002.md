# Plan: Apply migration 0002 to t1dream + register migration tracker

**Date:** 2026-05-17
**Status:** Ready to execute (design approved)
**Predecessors:**
- [`docs/plans/2026-05-13-storage-protocol-and-schema-registry`](2026-05-13-storage-protocol-and-schema-registry.md)
- [`docs/plans/2026-05-14-supabase-storage`](2026-05-14-supabase-storage.md)
- [`docs/updates/2026-05-14-supabase-storage`](../updates/2026-05-14-supabase-storage.md)

## Context

The Supabase Storage PR landed `db/migrations/0002_supabase_storage_setup.sql` (creates `detection_results` + sets `idle_in_transaction_session_timeout = '5min'`) but the migration was never applied to the production "t1dream" project (`vvrvsxiqquucxytxdcvs`). A connection-test smoke run on 2026-05-15 confirmed:

- `to_regclass('public.detection_results')` → `null` (table missing)
- `SHOW idle_in_transaction_session_timeout` → `0` (Postgres default; backstop missing)
- `supabase_migrations.schema_migrations` → relation does not exist (the tracker schema itself has never been touched)

Existing tables from `0001_init.sql` ARE present and populated (~725.6K rows across the 9 data tables; `alerts_sent` carries the partial unique index `(alert_kind, event_ref) WHERE event_ref IS NOT NULL`). The Python `SupabaseStorage` data plane works against every Protocol method except `record_detection_result` / `list_detection_results`, which raise `UndefinedTable`.

The Supabase MCP `apply_migration` tool became available after the predecessor docs were written, so the original "agent never touches production DDL" constraint (which was about the absence of database credentials in the agent sandbox) no longer applies — the management API token is a separate credential the user explicitly provisioned by enabling the MCP. We're using the MCP path.

## Goal

Get the t1dream project to a state where:

1. `detection_results` exists with the schema and index from `0002_supabase_storage_setup.sql`.
2. `idle_in_transaction_session_timeout = '5min'` for new sessions.
3. `supabase_migrations.schema_migrations` exists and lists both `init` and `supabase_storage_setup` so future MCP-driven migrations have an honest ledger.
4. The `Storage` Protocol's two `detection_results` methods stop raising `UndefinedTable` against the live project.

## Non-goals

- No application-side code changes. `SupabaseStorage.record_detection_result` and `list_detection_results` are already implemented; they only need the table to exist.
- No RLS comment edit to `0001_init.sql`. The user explicitly scoped that out for this change.
- No backfill of `detection_config` rows; that table is intentionally empty per `0001_init.sql`'s comments and the bootstrap doc.
- No re-bootstrap of historical data. Row counts must be unchanged.

## Approach

Two `apply_migration` MCP calls plus a verification round. No psql, no direct connection from the agent for DDL.

### Step 1 — Pre-apply baseline

Capture row counts on the 9 data tables + the 3 metadata tables via a single `execute_sql` call. Stored in the audit-log entry as the proof that step 2 was a true no-op.

### Step 2 — Re-apply `0001_init.sql` via MCP `apply_migration`

- `name`: `init`
- `query`: full contents of `db/migrations/0001_init.sql`

The file is idempotent by construction: enums use `DO $$ ... EXCEPTION WHEN duplicate_object THEN NULL; END $$`, tables and indexes use `CREATE ... IF NOT EXISTS`, `COMMENT` statements always overwrite. Side effects on first MCP apply: creates `supabase_migrations.schema_migrations` and inserts the version row.

### Step 3 — Apply `0002_supabase_storage_setup.sql` via MCP `apply_migration`

- `name`: `supabase_storage_setup`
- `query`: full contents of `db/migrations/0002_supabase_storage_setup.sql`

Creates `detection_results` + `detection_results_kind_created_idx` + 5 `COMMENT` statements + sets the database-level `idle_in_transaction_session_timeout`. Idempotent.

### Step 4 — Verification gates

Five gates, all of which must pass before the change is declared green. Outputs captured verbatim in the audit-log entry.

1. **`detection_results` exists.** `SELECT to_regclass('public.detection_results')` → `'detection_results'`.
2. **Idle-tx timeout set.** Open a fresh psycopg2 connection from Python (using `SUPABASE_DB_URL`) and run `SHOW idle_in_transaction_session_timeout;` — must return `'5min'`. The MCP's own session may show the old value because the `ALTER DATABASE ... SET` only takes effect on new sessions; that's expected, not a regression. Verifying via a freshly-opened psycopg2 session is the load-bearing check.
3. **Migrations registered.** `list_migrations` via MCP returns at least two entries with names `init` and `supabase_storage_setup`.
4. **Row counts unchanged.** Re-run the count query from step 1; every count matches.
5. **Python smoke test passes.** Run an inline `uv run python -` script that opens a psycopg2 connection from `SUPABASE_DB_URL`, constructs `SupabaseStorage(conn=conn)`, and exercises the eight Protocol-method calls covered by the 2026-05-15 connection test (`read_all_table('site_issues')`, `read_table('cgm', since, until)`, `list_fetch_state`, `get_pipeline_version`, `recent_alerts`, the two contract guards on `read_table` / `delete_range`, and `list_detection_results(limit=1)`). Pass condition: `list_detection_results` returns `[]` instead of raising `UndefinedTable`; every other call's output matches the 2026-05-15 baseline. The full script + verbatim output land in the audit-log entry so the run is reproducible.

### Step 5 — Audit log

Write `docs/updates/2026-05-17-apply-migration-0002.md` per the CLAUDE.md "dated trail is the audit log" convention. Includes:

- One-line summary + cross-links to predecessor docs and to this plan.
- Explicit note that the apply path differs from the original `psql` procedure documented in `docs/updates/2026-05-14-supabase-storage.md` (rationale: MCP wasn't available then; now it is).
- Verbatim outputs of all five verification gates.
- A "future migrations" pointer: from this point on, migrations land via MCP `apply_migration` so the tracker stays in sync with `db/migrations/`.

### Step 6 — Commit

One commit covering this plan + the audit-log entry. No production code is changed; the commit is documentation-only.

## Considered and rejected

- **`INSERT` directly into `supabase_migrations.schema_migrations` to backdate the 0001 version row.** Bypasses the management API's normal write path and is brittle if Supabase changes the tracker schema. Re-applying the idempotent 0001 SQL is uniform and lower-risk; the version timestamp won't reflect the original apply date but the tracker's purpose is "is this migration registered with this codebase", not historical accuracy.
- **Apply only 0002, leave 0001 untracked.** Permanently leaves the tracker out of sync (1 entry for 2 applied migrations). Defeats the purpose of fixing issue #2.
- **Local `psql` against `SUPABASE_DB_URL`.** Same end state for the schema, but doesn't populate the tracker, so it doesn't actually fix issue #2.

## Risk / blast radius

- One Supabase project, no application code edits.
- 0001 re-apply: every statement is guarded by `IF NOT EXISTS`, `DO/EXCEPTION duplicate_object`, or unconditional `COMMENT`. The only failure mode is if `apply_migration` wraps the whole script in a single transaction and a `DO` block raises something other than `duplicate_object` — extremely unlikely given the file already applied cleanly via `psql` in the bootstrap.
- 0002 apply: `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` + `ALTER DATABASE ... SET`. All re-runnable.
- Rollback for 0002 (only needed if a downstream regression is traced to it):
  ```sql
  DROP TABLE IF EXISTS detection_results CASCADE;
  ALTER DATABASE postgres RESET idle_in_transaction_session_timeout;
  ```

## Acceptance criteria

- All five verification gates pass with the outputs captured in the audit-log entry.
- `uv run pytest -q` still reports `477 passed, 41 skipped, 47 deselected` (no test was touched; this confirms nothing else regressed).
- `uv run python main.py doctor` still prints `pipeline state: OK`.
- The new `docs/updates/2026-05-17-apply-migration-0002.md` exists and cross-links the predecessor docs.

## Sequencing

Steps run in order: 1 → 2 → 3 → 4 → 5 → 6. No parallelism opportunities.
