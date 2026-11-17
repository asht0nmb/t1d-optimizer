---
paths:
  - "core/storage/**"
  - "core/schema.py"
  - "db/**"
  - "scripts/bootstrap_supabase.py"
  - "tests/core/test_storage_contract.py"
---

# Storage and Supabase

- All three `Storage` backends (Parquet, Supabase, InMemory) must pass `tests/core/test_storage_contract.py`. A behavior change goes into the contract test first.
- The Supabase leg of the contract test runs only when `SUPABASE_TEST_URL` is set, and it refuses production hosts. Never point it at production.
- `SupabaseStorage` callers use the transaction-mode pooler (`*.pooler.supabase.com:6543`) with open-do-close: `SupabaseStorage.from_pooler_url(url)` as a context manager for short-lived use, or `SupabaseStorage(conn=...)` when the caller manages the connection.
- Direct connections (`db.*.supabase.co:5432`) are only for the nightly GitHub Action and `scripts/bootstrap_supabase.py`.
- Never issue session-level `SET` on the pooler. That includes psycopg2 `readonly=True` with autocommit, which becomes a session `SET` and leaks to other clients. `SupabaseStorage` forces `BEGIN READ WRITE` as a defense, so don't remove that.
- Every new table migration ends with `alter table <name> enable row level security;` (Supabase doesn't enable it by default).
- RLS model: `postgres` and `service_role` bypass RLS and are server-side only. `authenticated` gets one permissive policy per table. `anon` has no policy, so it sees zero rows. Client-side code goes through the anon key plus a signed-in session, never `service_role`.
- Migrations are numbered files in `db/migrations/` and are applied by the owner. Agents write them but never apply them to production.
