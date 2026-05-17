# Apply Migration 0002 — Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This is a config/DB change rather than feature code — TDD doesn't map; instead each task has a concrete MCP/SQL/shell command with the expected output that gates the next step.

**Goal:** Apply `db/migrations/0002_supabase_storage_setup.sql` to the t1dream Supabase project (`vvrvsxiqquucxytxdcvs`) via the Supabase MCP `apply_migration` path; concurrently bring `supabase_migrations.schema_migrations` into existence by re-applying the idempotent `0001_init.sql` first; capture verbatim verification gate outputs; write the dated audit-log entry.

**Architecture:** Two MCP `apply_migration` calls (Task 2 → Task 3) bracketed by a row-count baseline (Task 1) and a five-gate verification round (Task 4) plus repo-side acceptance (Task 5). Documentation-only commit at the end (Task 6).

**Tech Stack:** Supabase MCP (`apply_migration`, `execute_sql`, `list_migrations`), psycopg2 (existing `core.storage.supabase.SupabaseStorage`), `uv`, `pytest`, `git`.

---

## File Structure

- **Create**: `docs/updates/2026-05-17-apply-migration-0002.md` — the dated audit-log entry per CLAUDE.md
- **Create**: `docs/plans/2026-05-17-apply-migration-0002-execution.md` — this file
- **Modify**: none in `core/`, `ingestion/`, `scripts/`, `tests/`, or any `.sql` file
- **DB-side side effects** (not files): two rows in `supabase_migrations.schema_migrations`, the `detection_results` table + index + comments, and the database-level `idle_in_transaction_session_timeout` setting

---

## Task 1: Capture pre-apply baseline

**Files:** none (read-only DB introspection)

- [ ] **Step 1: Capture row counts on the 12 public tables**

Call MCP `execute_sql` with project_id=`vvrvsxiqquucxytxdcvs` and query:

```sql
SELECT 'cgm' AS t, count(*) FROM cgm UNION ALL
SELECT 'bolus', count(*) FROM bolus UNION ALL
SELECT 'requests', count(*) FROM requests UNION ALL
SELECT 'basal', count(*) FROM basal UNION ALL
SELECT 'suspension', count(*) FROM suspension UNION ALL
SELECT 'events', count(*) FROM events UNION ALL
SELECT 'alarms', count(*) FROM alarms UNION ALL
SELECT 'site_issues', count(*) FROM site_issues UNION ALL
SELECT 'cgm_gaps', count(*) FROM cgm_gaps UNION ALL
SELECT 'alerts_sent', count(*) FROM alerts_sent UNION ALL
SELECT 'fetch_state', count(*) FROM fetch_state UNION ALL
SELECT 'detection_config', count(*) FROM detection_config
ORDER BY t;
```

Expected baseline (from 2026-05-15 smoke test): `cgm=300324, basal=341885, alarms=41121, events=16245, bolus=11115, requests=11114, cgm_gaps=2078, suspension=1738, site_issues=42`, three metadata tables = 0. Save the actual output verbatim — Task 4 gate 4 compares against it.

---

## Task 2: Re-apply migration 0001 (initialise tracker)

**Files:** read `db/migrations/0001_init.sql`

- [ ] **Step 1: Read the full contents of `db/migrations/0001_init.sql`**

Use the Read tool. The file is ~330 lines. Idempotent by construction (DO/EXCEPTION on enums + IF NOT EXISTS on tables/indexes + unconditional COMMENT).

- [ ] **Step 2: Call MCP `apply_migration` with the contents**

```
server: plugin-supabase-supabase
tool:   apply_migration
args:
  project_id: vvrvsxiqquucxytxdcvs
  name:       init
  query:      <full contents of 0001_init.sql>
```

Expected: success, no error. The first `apply_migration` call also creates `supabase_migrations.schema_migrations` if it doesn't exist.

- [ ] **Step 3: Verify the tracker row exists**

Call MCP `execute_sql` with:

```sql
SELECT version, name FROM supabase_migrations.schema_migrations ORDER BY version;
```

Expected: at least one row, `name = 'init'`.

- [ ] **Step 4: Confirm row counts unchanged**

Re-run the Task 1 baseline query. Expected: every count identical to Task 1. (If any differ, abort — 0001 should be a strict no-op against an already-applied schema.)

---

## Task 3: Apply migration 0002 (detection_results + idle-tx timeout)

**Files:** read `db/migrations/0002_supabase_storage_setup.sql`

- [ ] **Step 1: Read the full contents of `db/migrations/0002_supabase_storage_setup.sql`**

Use the Read tool. The file is ~76 lines: `ALTER DATABASE postgres SET idle_in_transaction_session_timeout = '5min'`, `CREATE TABLE IF NOT EXISTS detection_results (...)`, `CREATE INDEX IF NOT EXISTS detection_results_kind_created_idx`, five `COMMENT` statements.

- [ ] **Step 2: Call MCP `apply_migration` with the contents**

```
server: plugin-supabase-supabase
tool:   apply_migration
args:
  project_id: vvrvsxiqquucxytxdcvs
  name:       supabase_storage_setup
  query:      <full contents of 0002_supabase_storage_setup.sql>
```

Expected: success, no error.

---

## Task 4: Run the 5 verification gates

**Files:** none (read-only)

- [ ] **Gate 1: `detection_results` exists**

MCP `execute_sql`:
```sql
SELECT to_regclass('public.detection_results') AS detection_results,
       to_regclass('public.detection_results_kind_created_idx') AS detection_results_index;
```
Expected: `detection_results = 'detection_results'`, `detection_results_index = 'detection_results_kind_created_idx'`.

- [ ] **Gate 2: idle-tx timeout = 5min on a fresh psycopg2 session**

Run inline:
```bash
uv run python - <<'PY'
import pathlib, psycopg2
env = {}
for line in pathlib.Path(".env").read_text().splitlines():
    line = line.split("#", 1)[0].strip()
    if "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip("'\"")
conn = psycopg2.connect(env["SUPABASE_DB_URL"], connect_timeout=10)
with conn.cursor() as cur:
    cur.execute("SHOW idle_in_transaction_session_timeout;")
    print("idle_in_transaction_session_timeout:", cur.fetchone()[0])
conn.close()
PY
```
Expected stdout: `idle_in_transaction_session_timeout: 5min`.

- [ ] **Gate 3: `list_migrations` shows both**

MCP `list_migrations` with project_id=`vvrvsxiqquucxytxdcvs`. Expected: at least two entries, names include both `init` and `supabase_storage_setup`.

- [ ] **Gate 4: row counts unchanged**

MCP `execute_sql` with the Task 1 baseline query. Expected: every count identical to the Task 1 capture.

- [ ] **Gate 5: Python `SupabaseStorage` smoke test**

Run inline:
```bash
uv run python - <<'PY'
import pathlib, psycopg2
from datetime import datetime, timedelta, timezone
env = {}
for line in pathlib.Path(".env").read_text().splitlines():
    line = line.split("#", 1)[0].strip()
    if "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip("'\"")
conn = psycopg2.connect(env["SUPABASE_DB_URL"], connect_timeout=10)
from core.storage.supabase import SupabaseStorage
storage = SupabaseStorage(conn=conn)
print("[1] site_issues rows:", len(storage.read_all_table("site_issues")))
until = datetime(2026, 4, 14, tzinfo=timezone.utc) + timedelta(days=1)
since = until - timedelta(days=1)
print("[2] cgm window rows:", len(storage.read_table("cgm", since=since, until=until)))
try:
    storage.read_table("cgm", since=datetime(2026, 4, 14), until=datetime(2026, 4, 15))
    print("[3] tz-naive guard: FAIL")
except (ValueError, TypeError) as e:
    print("[3] tz-naive guard:", type(e).__name__)
try:
    storage.delete_range("cgm")
    print("[4] no-scope guard: FAIL")
except ValueError:
    print("[4] no-scope guard: ValueError")
print("[5] fetch_state rows:", len(storage.list_fetch_state()))
print("[6] pipeline_version:", storage.get_pipeline_version())
print("[7] recent_alerts:", len(storage.recent_alerts("low_bg", within=timedelta(hours=24))))
print("[8] detection_results rows:", len(storage.list_detection_results(limit=1)))
conn.close()
print("[done] connection closed")
PY
```

Expected: line `[8]` prints `detection_results rows: 0` (was previously raising `UndefinedTable`); lines `[1]` through `[7]` match the 2026-05-15 baseline (`site_issues=42`, `cgm window=286`, two guards trip, fetch_state=0, pipeline_version=None, recent_alerts=0).

---

## Task 5: Repo-side acceptance gates

**Files:** none

- [ ] **Step 1: Pytest suite still green**

Run: `uv run pytest -q`
Expected: `477 passed, 41 skipped, 47 deselected` (matches the 2026-05-14 baseline; the supabase contract tests stay skipped because `SUPABASE_TEST_URL` is not the production URL).

- [ ] **Step 2: Doctor still clean**

Run: `uv run python main.py doctor`
Expected: `pipeline state: OK`, all 9 parquet tables present, code/on-disk pipeline version `v3`.

---

## Task 6: Write the dated audit-log entry

**Files:**
- Create: `docs/updates/2026-05-17-apply-migration-0002.md`

- [ ] **Step 1: Draft the audit-log entry**

Required sections:
1. **Summary** (1 paragraph): what landed, the apply path (Supabase MCP, deviating from the original `psql` procedure documented in `docs/updates/2026-05-14-supabase-storage.md`), and why (MCP wasn't available when the predecessor doc was written).
2. **Cross-links**: this plan, the predecessor plan (`docs/plans/2026-05-14-supabase-storage`), the predecessor update (`docs/updates/2026-05-14-supabase-storage`).
3. **Verification gate outputs** (verbatim from Task 4 + Task 5).
4. **Future migration policy**: from this point forward, migrations land via MCP `apply_migration` so `supabase_migrations.schema_migrations` and `db/migrations/` stay in lockstep.
5. **Rollback** (for reference): the `DROP TABLE IF EXISTS detection_results CASCADE; ALTER DATABASE postgres RESET idle_in_transaction_session_timeout;` snippet from the spec.

---

## Task 7: Commit

**Files:** the new audit-log entry + this plan

- [ ] **Step 1: Stage and commit**

```bash
git add docs/updates/2026-05-17-apply-migration-0002.md \
        docs/plans/2026-05-17-apply-migration-0002-execution.md
git commit -m "$(cat <<'EOF'
ops: apply migration 0002 to t1dream + initialise migration tracker

Two MCP apply_migration calls landed against the t1dream production
project: re-applied the idempotent 0001_init.sql to bring up
supabase_migrations.schema_migrations, then applied
0002_supabase_storage_setup.sql to create detection_results +
detection_results_kind_created_idx and set
idle_in_transaction_session_timeout = '5min'. Five verification gates
pass; row counts on the 9 data tables are unchanged. Audit-log entry
+ execution plan committed.
EOF
)"
git log -1 --format='%h %s'
```

Expected: clean commit, working tree clean, top of `git log` shows the new commit.

---

## Acceptance criteria (cross-checked against the spec)

| Spec requirement | Plan task |
| --- | --- |
| Goal 1 (`detection_results` exists) | Task 3 + Gate 1 |
| Goal 2 (idle-tx timeout = 5min for new sessions) | Task 3 + Gate 2 |
| Goal 3 (`schema_migrations` lists both) | Tasks 2/3 + Gate 3 |
| Goal 4 (`list_detection_results` stops raising) | Gate 5 line `[8]` |
| Non-goal: no app-side code changes | File Structure section above |
| Non-goal: no RLS comment edit | Not in any task |
| Verification gate 1 (`to_regclass`) | Gate 1 |
| Verification gate 2 (timeout via fresh session) | Gate 2 |
| Verification gate 3 (`list_migrations`) | Gate 3 |
| Verification gate 4 (row counts unchanged) | Tasks 1 & 2 step 4 & Gate 4 |
| Verification gate 5 (smoke test) | Gate 5 |
| Acceptance criterion (pytest green) | Task 5 step 1 |
| Acceptance criterion (`doctor` OK) | Task 5 step 2 |
| Acceptance criterion (audit-log exists) | Task 6 |
| Audit log: rollback section | Task 6 step 1 item 5 |
| Audit log: future-migration policy | Task 6 step 1 item 4 |

Every spec requirement maps to a concrete task; no orphan tasks.
