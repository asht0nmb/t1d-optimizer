# 2026-05-17 — Enable Row-Level Security (migration 0003)

**Plan:** [`docs/plans/2026-05-17-enable-rls`](../plans/2026-05-17-enable-rls.md)
**Predecessors:**
- [`docs/plans/2026-05-14-supabase-storage`](../plans/2026-05-14-supabase-storage.md) — original SupabaseStorage plan; deferred RLS to "later"
- [`docs/updates/2026-05-17-apply-migration-0002`](2026-05-17-apply-migration-0002.md) — established the Supabase MCP `apply_migration` pattern; flagged the `rls_disabled` advisor warnings as a follow-up

## Summary

Applied `db/migrations/0003_enable_rls.sql` to the t1dream production project (`vvrvsxiqquucxytxdcvs`) via Supabase MCP `apply_migration`. The migration enables Row-Level Security on every public table (13 total: 9 data + 4 metadata) and grants a single permissive `auth_required_all` policy per table that matches `FOR ALL TO authenticated USING (true) WITH CHECK (true)`. After the apply, the `anon` Postgres role — the role behind any anon-key request from a client bundle — sees zero rows on every public table; the `postgres` role (`SupabaseStorage`, `bootstrap_supabase.py`, the nightly GitHub Action) is unaffected because it has `BYPASSRLS`, and `service_role` is similarly unaffected.

This is the minimum-viable lockdown described in the plan: it closes the anon-key data-leak vector before the dashboard introduces any client-side Supabase calls, and removes all 13 `rls_disabled_in_public` ERROR-level advisor warnings. The `rls_policy_always_true` WARN-level advisors that replace them are expected — the permissive policy is deliberate for the single-user phase, and tightening to per-row ownership (`USING (user_id = auth.uid())`) is deferred until a multi-user story exists.

**No application code was touched.** This is a data-layer-only change. `core/storage/supabase.py`, `scripts/bootstrap_supabase.py`, and every existing caller continue to work because they all connect as the `postgres` role.

## Changes against the production project

| Object | State before | State after |
| --- | --- | --- |
| `supabase_migrations.schema_migrations` | 2 rows (`init`, `supabase_storage_setup`) | 3 rows (adds `enable_rls` v20260517221655) |
| RLS on 13 public tables | `rowsecurity = false` on every table | `rowsecurity = true` on every table |
| `pg_policies` (schema = `public`) | 0 policies | 13 policies, one `auth_required_all FOR ALL TO authenticated USING (true) WITH CHECK (true)` per table |
| Security advisor (`rls_disabled_in_public`) | 13 ERROR-level warnings | 0 |
| Security advisor (`rls_policy_always_true`) | 0 | 13 WARN-level (expected; permissive single-user policy) |
| Anon-role visibility on `cgm` | 300,324 rows | 0 rows |
| Postgres-role visibility on every table | baseline row counts | identical |

## Verification gate outputs (verbatim)

### Gate 1 — pre-apply baseline: anon sees everything (RED)

MCP `execute_sql` against `vvrvsxiqquucxytxdcvs`:

```sql
BEGIN; SET LOCAL ROLE anon;
SELECT 'cgm' AS tbl, count(*) AS n FROM cgm
UNION ALL SELECT 'alerts_sent', count(*) FROM alerts_sent;
ROLLBACK;
```

```json
[{"tbl":"cgm","n":300324},{"tbl":"alerts_sent","n":0}]
```

(`alerts_sent` is genuinely empty; `cgm` returning 300,324 confirms anon was unfiltered.)

### Gate 2 — apply succeeded

MCP `apply_migration` (`project_id=vvrvsxiqquucxytxdcvs`, `name=enable_rls`):

```json
{"success":true}
```

### Gate 3 — `list_migrations` shows three entries

```json
{"migrations":[
  {"version":"20260517123541","name":"init"},
  {"version":"20260517123613","name":"supabase_storage_setup"},
  {"version":"20260517221655","name":"enable_rls"}
]}
```

### Gate 4 — `get_advisors` (type: security): zero `rls_disabled` warnings remain

The 13 ERROR-level `rls_disabled_in_public` entries (one per public table) that the May 17 update flagged are all gone. The advisor now reports 13 WARN-level `rls_policy_always_true` entries — one per table — which are expected and explicitly in-scope per the plan: the policy is intentionally permissive for the single-user phase. No other security advisor types were affected.

### Gate 5 — `pg_policies` count = 13

```sql
SELECT count(*) AS n_policies FROM pg_policies WHERE schemaname = 'public';
```

```json
[{"n_policies":13}]
```

### Gate 6 — every public table has `rowsecurity = true`

```sql
SELECT tablename, rowsecurity FROM pg_tables
WHERE schemaname='public' AND rowsecurity = false;
```

```json
[]
```

### Gate 7 — policy uniformity across the 13 tables

```sql
SELECT tablename, policyname, roles::text, cmd FROM pg_policies
WHERE schemaname = 'public' ORDER BY tablename;
```

```json
[
  {"tablename":"alarms","policyname":"auth_required_all","roles":"{authenticated}","cmd":"ALL"},
  {"tablename":"alerts_sent","policyname":"auth_required_all","roles":"{authenticated}","cmd":"ALL"},
  {"tablename":"basal","policyname":"auth_required_all","roles":"{authenticated}","cmd":"ALL"},
  {"tablename":"bolus","policyname":"auth_required_all","roles":"{authenticated}","cmd":"ALL"},
  {"tablename":"cgm","policyname":"auth_required_all","roles":"{authenticated}","cmd":"ALL"},
  {"tablename":"cgm_gaps","policyname":"auth_required_all","roles":"{authenticated}","cmd":"ALL"},
  {"tablename":"detection_config","policyname":"auth_required_all","roles":"{authenticated}","cmd":"ALL"},
  {"tablename":"detection_results","policyname":"auth_required_all","roles":"{authenticated}","cmd":"ALL"},
  {"tablename":"events","policyname":"auth_required_all","roles":"{authenticated}","cmd":"ALL"},
  {"tablename":"fetch_state","policyname":"auth_required_all","roles":"{authenticated}","cmd":"ALL"},
  {"tablename":"requests","policyname":"auth_required_all","roles":"{authenticated}","cmd":"ALL"},
  {"tablename":"site_issues","policyname":"auth_required_all","roles":"{authenticated}","cmd":"ALL"},
  {"tablename":"suspension","policyname":"auth_required_all","roles":"{authenticated}","cmd":"ALL"}
]
```

### Gate 8 — post-apply: anon sees zero rows (GREEN)

```sql
BEGIN; SET LOCAL ROLE anon;
SELECT 'cgm' AS tbl, count(*) AS n FROM cgm
UNION ALL SELECT 'bolus', count(*) FROM bolus
UNION ALL SELECT 'alarms', count(*) FROM alarms
UNION ALL SELECT 'detection_config', count(*) FROM detection_config;
ROLLBACK;
```

```json
[{"tbl":"cgm","n":0},{"tbl":"bolus","n":0},{"tbl":"alarms","n":0},{"tbl":"detection_config","n":0}]
```

`cgm` went 300,324 → 0 between Gate 1 and Gate 8; the migration is the only intervening change.

### Gate 9 — postgres-role row counts unchanged

```sql
SELECT tbl, count FROM (...) ORDER BY tbl;
```

```
alarms=41121, alerts_sent=0, basal=341885, bolus=11115, cgm=300324,
cgm_gaps=2078, detection_config=0, detection_results=0, events=16245,
fetch_state=0, requests=11114, site_issues=42, suspension=1738
```

Identical to the post-apply baseline from `docs/updates/2026-05-17-apply-migration-0002.md` Gate 4. The migration changed nothing observable to the `postgres` role.

### Gate 10 — idempotency

Re-running the `cgm` block via MCP `execute_sql`:

```sql
ALTER TABLE cgm ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY auth_required_all ON cgm
    FOR ALL TO authenticated USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
SELECT count(*) AS n_policies FROM pg_policies
WHERE schemaname='public' AND tablename='cgm';
```

```json
[{"n_policies":1}]
```

`ALTER TABLE ... ENABLE ROW LEVEL SECURITY` is a no-op when RLS is already on; `CREATE POLICY` is wrapped in `DO/EXCEPTION duplicate_object` so it's also a no-op. The full migration can be safely re-applied (e.g. against a new test project or a recovered backup).

### Repo-side acceptance gates

```
$ unset SUPABASE_TEST_URL && uv run pytest -q
477 passed, 42 skipped, 47 deselected, 5 warnings in 16.15s
```

The passing count is unchanged from the May 17 baseline of 477. The one extra skip (42 vs. 41) is the new `test_rls_denies_anon`, which auto-skips when `SUPABASE_TEST_URL` is not set.

```
$ uv run pytest -m legacy tests/legacy/ -q
47 passed, 1 warning in 1.69s
```

```
$ uv run python main.py doctor
code pipeline version: v3
  └─ Decode `egvTimestamp` as `int` seconds since `TANDEM_EPOCH` (...)
on-disk pipeline version: v3

processed parquet tables present: 9/9
  - cgm
  - bolus
  - requests
  - basal
  - suspension
  - events
  - alarms
  - site_issues
  - cgm_gaps

pipeline state: OK
```

### Test-project supabase parameterizations (Gate 3 from prompt) — not exercised in this change

The supabase-parameterized suites in `tests/core/test_storage_contract.py` and `tests/core/test_supabase_storage.py` are gated on `SUPABASE_TEST_URL`, and additionally refuse to run unless `_PROD_HOST_PATTERNS` in `tests/core/test_storage_contract.py` is populated. The agent does not have test-project credentials, and `_PROD_HOST_PATTERNS` is still the empty tuple it has been since the Supabase storage PR landed (the TODO comment there documents the externalisation plan). The new `test_rls_denies_anon` was therefore exercised on the **production** target via the equivalent MCP `execute_sql` Gate 1 (RED) / Gate 8 (GREEN) pair rather than against a Python test client.

To run the smoke test under pytest, an operator with a test project should:

1. Apply `db/migrations/0003_enable_rls.sql` to the test project (one-line `psql` against the test project's direct connection URL — see "Test-project apply procedure" below).
2. Populate `_PROD_HOST_PATTERNS` in `tests/core/test_storage_contract.py` with the production project's hostname patterns (`db.vvrvsxiqquucxytxdcvs.supabase.co` and `aws-0-<region>.pooler.supabase.com:6543` with the right region prefix).
3. Run `SUPABASE_TEST_URL=<test-pooler-url> uv run pytest tests/core/test_supabase_storage.py::test_rls_denies_anon -v`.

The test asserts that after seeding one `cgm` row and one `alerts_sent` row as the postgres role, the same connection — after `SET LOCAL ROLE anon` inside a transaction — sees zero rows on both tables. The role switch is wrapped in `try` / `finally` with an explicit `RESET ROLE` so failed assertions never leak `anon` onto the shared pooler connection.

## Test-project apply procedure

The production project went via Supabase MCP `apply_migration` (see Gates 2-3 above). Test projects — and any environment where the production-bound MCP is not appropriate — use the `psql` fallback below. The same `db/migrations/0003_enable_rls.sql` file applies through either path.

```bash
psql "$SUPABASE_TEST_DB_URL" -f db/migrations/0003_enable_rls.sql
```

where `SUPABASE_TEST_DB_URL` is the test project's Direct connection string (`db.<test-project>.supabase.co:5432`). The migration is idempotent — re-running it against a project that already has RLS on is a no-op (Gate 10 above).

## Future migration policy

No change from the policy set in `docs/updates/2026-05-17-apply-migration-0002.md`:

- All future migrations land via Supabase MCP `apply_migration` from this codebase, which keeps `db/migrations/*.sql` and `supabase_migrations.schema_migrations` in lockstep.
- The `psql` invocation above remains the documented fallback for environments where the MCP is unavailable (and is the only path for the test project, since the MCP is bound to the production project).
- Idempotency discipline: `IF NOT EXISTS` on tables/indexes, `DO/EXCEPTION duplicate_object` on enums and policies, unconditional `COMMENT`s. Migration 0003 follows this convention.

## Rollback (for reference)

If a downstream regression is traced to RLS specifically, the migration can be reversed table-by-table:

```sql
DROP POLICY IF EXISTS auth_required_all ON cgm;
ALTER TABLE cgm DISABLE ROW LEVEL SECURITY;
-- repeat for each of the 12 other public tables
```

Or wholesale (programmatic):

```sql
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP
        EXECUTE format('DROP POLICY IF EXISTS auth_required_all ON %I', r.tablename);
        EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', r.tablename);
    END LOOP;
END $$;
```

The tracker row can be removed if desired:

```sql
DELETE FROM supabase_migrations.schema_migrations WHERE name = 'enable_rls';
```

Existing `SupabaseStorage` / `bootstrap_supabase.py` callers will continue working through the rollback because they connect as the `postgres` role and bypass RLS in either direction.

## Not exercised in this change

Per the user-approved scope decision in the plan, the following were intentionally deferred:

- **Supabase Auth wiring.** No `auth.users` rows, no login flow, no JWT acceptance path. The `authenticated`-policy grants are dormant until a dashboard ships.
- **Per-row ownership policies.** The policy is `USING (true)`; multi-tenant tightening to `USING (user_id = auth.uid())` is a future migration when (if) the project grows past one user.
- **Realtime, Storage bucket, and Edge Function security.** This migration only covers `public.*` tables.
- **No application-side code changes.** `core/storage/supabase.py`, `_postgres_converters.py`, `scripts/bootstrap_supabase.py`, the contract test fixture, and every existing caller are unmodified.
- **No re-application of migrations 0001 or 0002.** The cross-reference comment added to `0001_init.sql` is a repo-side documentation edit only; the migration tracker stays as-is.
