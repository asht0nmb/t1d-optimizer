# T1D Engine — Personal dashboard (Phase A)

Next.js 14 App Router dashboard reading from Supabase Postgres. Auth via Supabase (email magic link). Server API routes use the **service role** for reads; the browser only sees the anon key + user session.

## Prerequisites

- Node 20+
- Supabase project with `db/migrations/0001_init.sql` applied and data bootstrapped
- RLS enabled (`0003_enable_rls.sql`) — signed-in `authenticated` users can read all rows; anon sees nothing

## Local development

```bash
cd apps/web
cp .env.example .env.local
# Fill NEXT_PUBLIC_* and SUPABASE_SERVICE_ROLE_KEY, SUPABASE_DB_URL
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) → redirects to login → magic link → day picker.

### Supabase Auth setup

1. In Supabase Dashboard → **Authentication** → enable **Email** provider.
2. **URL configuration**: add `http://localhost:3000/auth/callback` (and your Vercel URL in production) to **Redirect URLs**.
3. Create a user (Authentication → Users → invite email) or sign up via magic link on first visit.
4. For production, set Site URL to your Vercel domain.

Magic link is used (not GitHub OAuth) to keep single-user setup minimal.

### Environment variables

| Variable | Where | Purpose |
|----------|--------|---------|
| `NEXT_PUBLIC_SUPABASE_URL` | client + server | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | client + server | Auth session (RLS as `authenticated`) |
| `SUPABASE_SERVICE_ROLE_KEY` | server only | API route reads bypassing RLS |
| `SUPABASE_DB_URL` | server only | `pg` pool for aggregation SQL |
| `TZ` | server | Calendar-day windows (default `America/Los_Angeles`) |
| `DEFAULT_PUMP_SERIAL` | server | Optional single-pump filter |
| `USER_CONFIG_PATH` | server | Optional JSON/YAML path for `bg_targets` |

BG targets default to `config/bg-targets.json` (synced from repo `config/user_config.yaml`). The UI never hardcodes TIR thresholds.

## Pages (Phase A)

| Route | Description |
|-------|-------------|
| `/` | Date picker → `/day/[date]` |
| `/day/[date]` | CGM + bolus + basal panels (port of `scripts/daily_viz.py`) |
| `/heatmap` | Hour-of-day × date median BG |
| `/trends` | Stacked TIR bands (7 / 14 / 30 days) |
| `/insulin` | Daily bolus + basal totals |
| `/search` | Filter days (TIR, alarms) — paginated SQL |
| `/compare` | Two-day CGM overlay |

Phase B/C (episodes, clusters, LLM) are out of scope.

## Aggregation SQL

Server-only queries live under `lib/queries/`:

- `day.ts` — Supabase JS `read_table`-style day slice
- `heatmap.ts`, `trends.ts`, `insulin.ts`, `search.ts` — raw SQL via `pg` + `SUPABASE_DB_URL`
- `compare.ts` — dual CGM series via Supabase JS

## Scripts

```bash
npm run dev      # local server
npm run build    # production build (required gate)
npm run lint
npm run test     # vitest — TIR, dates, API helpers
```

## Vercel deploy

1. Import repo; set **Root Directory** to `apps/web`.
2. Add the same env vars as `.env.example` in Project Settings.
3. Deploy. Ensure Auth redirect URLs include `https://YOUR_APP.vercel.app/auth/callback`.

## Security checklist

- Confirm `SUPABASE_SERVICE_ROLE_KEY` is **not** in any `NEXT_PUBLIC_*` variable.
- After deploy: `grep -r service_role app components` should return empty (service key only in `lib/supabase/server.ts` via env).

## Known limitations

- Single-pump assumption when `DEFAULT_PUMP_SERIAL` is set; multi-pump UI deferred.
- Data freshness depends on nightly GitHub Action sync to Supabase.
- Enriched overlays (`bolus_category`, `cgm_gaps` shading) are partial vs matplotlib `daily_viz --view enriched`.
- `SUPABASE_DB_URL` required for heatmap/trends/insulin/search; day/compare work with service role + JS client only.

## Manual test plan

1. Sign in via magic link.
2. Open `/day/2026-04-14` — CGM line and summary cards render.
3. Open heatmap, trends, insulin, search, compare — no 500s when `SUPABASE_DB_URL` is set.
4. Sign out — redirected to login; API routes unreachable without session.
