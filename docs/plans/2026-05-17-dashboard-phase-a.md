# Plan: Dashboard Phase A (apps/web)

**Date:** 2026-05-17  
**Branch:** `feat/dashboard-phase-a`  
**Status:** Implemented (scaffold)

## Scope

Next.js personal cloud shell: Supabase Auth, Phase A views against current Postgres schema (`0001_init.sql` + `core/schema.py`). Typed API responses in `apps/web/lib/types/api.ts`.

## Pages

- `/` → date picker → `/day/[date]`
- `/day/[date]`, `/heatmap`, `/trends`, `/insulin`, `/search`, `/compare`

## Data access

- **Client:** anon key + session (`authenticated` RLS).
- **Server API:** `SUPABASE_SERVICE_ROLE_KEY` + optional `SUPABASE_DB_URL` for `pg` aggregations in `apps/web/lib/queries/`.
- **No edits** to `core/storage/supabase.py`.

## Auth

Email magic link; middleware protects all routes except `/login` and `/auth/callback`.

## Out of scope

Episodes, clusters, pattern flags, LLM panel (Phase B/C).

## References

- `docs/plans/2026-05-05-detection-rework-and-surfaces.md` (Dashboard Phase A)
- `docs/plans/2026-05-14-supabase-storage.md` (Postgres-first aggregation, RLS)
- `docs/updates/2026-05-17-enable-rls.md`
