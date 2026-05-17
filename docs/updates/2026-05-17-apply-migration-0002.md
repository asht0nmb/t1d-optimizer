# 2026-05-17 — Apply migration 0002 + initialise migration tracker

**Plan:** [`docs/plans/2026-05-17-apply-migration-0002`](../plans/2026-05-17-apply-migration-0002.md)
**Execution plan:** [`docs/plans/2026-05-17-apply-migration-0002-execution`](../plans/2026-05-17-apply-migration-0002-execution.md)
**Predecessors:**
- [`docs/plans/2026-05-14-supabase-storage`](../plans/2026-05-14-supabase-storage.md) — original SupabaseStorage plan
- [`docs/updates/2026-05-14-supabase-storage`](2026-05-14-supabase-storage.md) — predecessor update; documented the `psql` apply procedure that this entry supersedes

## Summary

Applied `db/migrations/0002_supabase_storage_setup.sql` to the t1dream production project (`vvrvsxiqquucxytxdcvs`). The change creates `detection_results` (table, index, comments) and sets the database-level `idle_in_transaction_session_timeout = '5min'` connection-leak backstop. Concurrently, the idempotent `0001_init.sql` was re-applied to bring up `supabase_migrations.schema_migrations` so the migration tracker now reflects every migration in `db/migrations/`.

**Apply path: Supabase MCP `apply_migration`, NOT the `psql` procedure documented in `docs/updates/2026-05-14-supabase-storage.md`.** The original procedure assumed the agent had no DB credentials. The Supabase MCP server installed on 2026-05-15 provides admin-level access via the management API (a different credential than the database password, provisioned by the user enabling the MCP), so the agent can now drive DDL applies. From this point forward, migrations land via MCP `apply_migration` so `supabase_migrations.schema_migrations` and `db/migrations/` stay in lockstep.

`SupabaseStorage.record_detection_result` and `list_detection_results` now work against the live project — they previously raised `UndefinedTable`.

## Changes against the production project

| Object | State before | State after |
| --- | --- | --- |
| `supabase_migrations.schema_migrations` | did not exist | exists; 2 rows (`init` v20260517123541, `supabase_storage_setup` v20260517123613) |
| `public.detection_results` | did not exist | exists; PK `id` (bigserial), columns `kind text NOT NULL`, `anchor_timestamp timestamptz NOT NULL`, `payload jsonb NOT NULL DEFAULT '{}'::jsonb`, `created_at timestamptz NOT NULL DEFAULT now()` |
| `public.detection_results_kind_created_idx` | did not exist | exists; `(kind, created_at DESC)` |
| `idle_in_transaction_session_timeout` (database setting) | `0` (Postgres default) | `5min` (effective on new sessions) |
| 9 data tables + 3 metadata tables | row counts as listed below | row counts identical |

## Verification gate outputs (verbatim)

### Gate 1 — `detection_results` exists

MCP `execute_sql`: `SELECT to_regclass('public.detection_results'), to_regclass('public.detection_results_kind_created_idx');`

```
[{"detection_results":"detection_results","detection_results_index":"detection_results_kind_created_idx"}]
```

### Gate 2 — idle-tx timeout = 5min on a fresh psycopg2 session

```
$ uv run python -c "..."  # see execution plan Task 4 Gate 2 for exact script
idle_in_transaction_session_timeout: 5min
```

(The MCP's own pooled session may still report the old value because `ALTER DATABASE … SET` only takes effect on **new** sessions. A freshly-opened psycopg2 connection is the load-bearing check; pooler-held sessions inherit on next re-issue.)

### Gate 3 — `list_migrations` shows both

```json
{"migrations": [
  {"version":"20260517123541","name":"init"},
  {"version":"20260517123613","name":"supabase_storage_setup"}
]}
```

### Gate 4 — row counts unchanged

Pre-apply baseline (Task 1):

```
alarms=41121, alerts_sent=0, basal=341885, bolus=11115, cgm=300324,
cgm_gaps=2078, detection_config=0, events=16245, fetch_state=0,
requests=11114, site_issues=42, suspension=1738
```

Post-apply (Task 4 Gate 4):

```
alarms=41121, alerts_sent=0, basal=341885, bolus=11115, cgm=300324,
cgm_gaps=2078, detection_config=0, detection_results=0, events=16245,
fetch_state=0, requests=11114, site_issues=42, suspension=1738
```

The only difference is the new `detection_results=0` entry (the table did not exist pre-apply). All other counts are identical.

### Gate 5 — Python `SupabaseStorage` smoke test

```
[1] site_issues rows: 42
[2] cgm window rows: 286
[3] tz-naive guard: ValueError
[4] no-scope guard: ValueError
[5] fetch_state rows: 0
[6] pipeline_version: None
[7] recent_alerts: 0
[8] detection_results rows: 0
[done] connection closed
```

Line `[8]` previously raised `UndefinedTable: relation "detection_results" does not exist`; it now returns 0 rows. Lines `[1]`–`[7]` match the 2026-05-15 connection-test baseline verbatim.

### Repo-side acceptance gates (Task 5)

```
$ uv run pytest -q
477 passed, 41 skipped, 47 deselected, 5 warnings in 10.19s
```

```
$ uv run python main.py doctor
code pipeline version: v3
on-disk pipeline version: v3
processed parquet tables present: 9/9
pipeline state: OK
```

## Future migration policy

- All future migrations land via Supabase MCP `apply_migration` from this codebase. The MCP records each apply in `supabase_migrations.schema_migrations`, so `db/migrations/*.sql` and the tracker stay in lockstep automatically.
- The `psql "$SUPABASE_DB_URL" -f db/migrations/<file>.sql` procedure documented in the predecessor update is now legacy. Keep it in the repo as a fallback for environments where the MCP is unavailable, but the MCP path is preferred.
- Future migrations should keep the same idempotency discipline as `0001_init.sql` and `0002_supabase_storage_setup.sql` (`IF NOT EXISTS` on tables/indexes, `DO/EXCEPTION duplicate_object` on enums, unconditional `COMMENT`s) so re-applies stay no-ops.

## Rollback (for reference)

If a downstream regression is traced to migration 0002 specifically:

```sql
DROP TABLE IF EXISTS detection_results CASCADE;
ALTER DATABASE postgres RESET idle_in_transaction_session_timeout;
```

Cascading the drop also removes the `detection_results_kind_created_idx` index. After rollback, `SupabaseStorage.record_detection_result` and `list_detection_results` will go back to raising `UndefinedTable`; everything else continues to work.

The migration tracker rows can be removed if desired:

```sql
DELETE FROM supabase_migrations.schema_migrations WHERE name = 'supabase_storage_setup';
```

## Not exercised in this change

Per the user-approved scope decision, the following were intentionally deferred:

- **RLS comment edit to `db/migrations/0001_init.sql`.** The `get_advisors` MCP advisor still flags `rls_disabled` on all 12 public tables. RLS is deliberately disabled for this single-user, no-anon-key project; documenting that decision inline in 0001 is a future follow-up.
- **No application-side code changes.** `SupabaseStorage.record_detection_result` and `list_detection_results` were already implemented in [`docs/updates/2026-05-14-supabase-storage`](2026-05-14-supabase-storage.md); they only needed the table to exist.
- **No re-bootstrap of historical data.** Bootstrap remains the correct first-time loader; subsequent CGM/pump syncs go through `SupabaseStorage.upsert_table(...)` per the predecessor docs.
