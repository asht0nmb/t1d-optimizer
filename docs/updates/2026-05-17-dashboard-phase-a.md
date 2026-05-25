# 2026-05-17 — Dashboard Phase A scaffold (apps/web)

**Branch:** `feat/dashboard-phase-a`  
**Plan:** [`docs/plans/2026-05-17-dashboard-phase-a.md`](../plans/2026-05-17-dashboard-phase-a.md)

## Summary

Scaffolded `apps/web/`: Next.js 14 App Router, Tailwind, Supabase Auth (email magic link), seven Phase A routes, typed API contracts, and server-only aggregation SQL under `apps/web/lib/queries/`. Service role is confined to server route handlers; client bundle uses anon key + session only.

## Pages shipped

| Route | API | SQL location |
|-------|-----|----------------|
| `/day/[date]` | `GET /api/day/[date]` | `lib/queries/day.ts` (Supabase JS) |
| `/heatmap` | `GET /api/heatmap` | `lib/queries/heatmap.ts` |
| `/trends` | `GET /api/trends` | `lib/queries/trends.ts` |
| `/insulin` | `GET /api/insulin` | `lib/queries/insulin.ts` |
| `/search` | `GET /api/search` | `lib/queries/search.ts` |
| `/compare` | `GET /api/compare` | `lib/queries/compare.ts` |

## Verification

```
cd apps/web && npm run test   # 9 passed (3 files)
cd apps/web && npm run build  # success (Next.js 14.2.35)
cd apps/web && npm run lint   # no warnings or errors
grep -r service_role apps/web/app apps/web/components  # empty (server uses env in lib/supabase/server.ts only)
```

Manual: sign in → `/day/2026-04-14` with populated Supabase — verify locally when credentials available.

## Known limitations

- Single pump when `DEFAULT_PUMP_SERIAL` set.
- TZ from `TZ` env (default `America/Los_Angeles`).
- Stale data until nightly sync merges new Tandem rows.
- `SUPABASE_DB_URL` required for heatmap/trends/insulin/search.
- Enriched view overlays from `daily_viz --view enriched` not fully ported.

## Not touched

`core/storage/supabase.py`, ingestion sync scripts, migrations, `apps/local/`.
