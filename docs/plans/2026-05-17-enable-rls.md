# Plan: Enable RLS on Supabase project

**Date:** 2026-05-17
**Branch:** `feat/enable-rls` (new, off main)
**Status:** Ready to execute
**Predecessors:**
- `docs/plans/2026-05-14-supabase-storage.md` — established `SupabaseStorage`
- `docs/updates/2026-05-17-apply-migration-0002.md` — set the MCP apply pattern, flagged the RLS-disabled advisor warning as a future follow-up

## Context

All 13 public tables on the t1dream Supabase project (`vvrvsxiqquucxytxdcvs`) currently have RLS disabled. The original "single-user, no anon key" rationale held while the only callers were `bootstrap_supabase.py` and `SupabaseStorage` — both connect via the postgres role through psycopg2, which bypasses RLS regardless of whether it's on or off.

That rationale is about to break. The May 5 plan commits to the dashboard using Supabase Auth, which means at least some calls go through the anon-key path. The anon key is designed to be embedded in client bundles; without RLS, any visitor on the internet who reads a Next.js bundle can pull the entire CGM history.

This plan enables RLS with a single permissive `authenticated`-only policy per table. It's the minimum-viable lockdown that:

- Closes the anon-key data-leak vector before any dashboard code is written.
- Doesn't break existing code paths (postgres role and `service_role` bypass RLS).
- Removes the persistent `rls_disabled` advisor warning.
- Locks the policy model in via a migration tracked in `supabase_migrations.schema_migrations`.

## Architectural rules

### Threat model

- **Postgres role** (psycopg2 with the database password): bypasses RLS by `BYPASSRLS` attribute. Used by `bootstrap_supabase.py`, `SupabaseStorage`, the contract test suite, and the Tandem nightly sync. Unaffected by this change.
- **`service_role`** (Supabase JWT for server-side admin calls): bypasses RLS. Used by future Vercel API routes that need admin access. Unaffected.
- **`authenticated`** (Supabase JWT for signed-in users): subject to RLS. Future Next.js client components using `@supabase/supabase-js` go through this role. The policy this plan creates grants them read/write on every table.
- **`anon`** (Supabase JWT for unauthenticated requests): subject to RLS. Has no policy after this migration; sees zero rows on every table.

### Policy model

Every `public` table gets one policy:

```sql
CREATE POLICY auth_required_all ON <table>
    FOR ALL TO authenticated
    USING (true) WITH CHECK (true);