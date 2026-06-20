# 2026-06-11 — Visualization depth (Phase 4) + API auth hardening

Executed from `docs/superpowers/plans/2026-06-11-phase4-visualization-depth.md`.

## Shared metric

- `core/metrics/agp.py` — canonical Ambulatory Glucose Profile definition:
  percentiles 5/25/50/75/95 of `bg_mgdl` grouped by local hour over a
  trailing window of calendar days. Pure pandas/numpy (core import rules).
  The web SQL mirrors this definition and says so in a comment; if either
  side changes, change both.

## Local shell (Streamlit) — three new pages

- **Insulin** — daily bolus/basal total bars; semantics mirror
  `apps/web/lib/queries/insulin.ts` (bolus = Σ insulin_units; basal =
  Σ commanded_rate × 5/60 per 5-minute row).
- **AGP** — median line + 25–75 and 5–95 ribbons by hour, 14/30/90-day
  windows, rendered from `core/metrics/agp.py`.
- **Compare** — two days overlaid on a shared time-of-day axis with
  CGM-date pickers.

App boots headless with all six pages (HTTP 200 check).

## Web shell (Next.js) — two new pages + overlays

- **AGP** (`/agp`) — SQL `PERCENTILE_CONT` 5/25/50/75/95 by local hour,
  `generate_series(0,23)` left-join so empty hours are NULL rows; response
  embeds bg targets (single fetch). Recharts ribbon chart.
- **Alerts** (`/alerts`) — `alerts_sent` history (ordered by `fired_at`
  DESC — the table has no `sent_at`), limit/offset pagination, delivery
  badges (sent/pending/failed), payload message column.
- **Day view** — now renders `cgm_gaps` (neutral shading) and
  `site_issues` (warning shading) as ReferenceAreas; interval clip/snap
  helpers are pure and unit-tested. Site-issue shading sits on the CGM
  panel because the bolus panel's categorical axis cannot address
  arbitrary interval endpoints.

## Security hardening (found during review)

The auth middleware bypasses `/api/*` entirely (the cron health route
uses bearer auth), and data routes query with service-role / direct-pg
credentials that bypass RLS — so every JSON data endpoint was publicly
readable on the deployed URL. New `requireSession()` guard
(`apps/web/lib/api/auth.ts`) returns 401 without a signed-in Supabase
session and fails closed on auth errors; applied to all nine data routes
(day, heatmap, trends, insulin, search, compare, config, agp, alerts).
A source-scan test fails CI if any non-cron route lacks the guard.

**Deploy note:** on the next `apps/web` deploy, unauthenticated API calls
start returning 401. Pages already required sign-in, so user-visible
behavior is unchanged. Nothing external calls these routes
unauthenticated (the 5-minute worker is a separate Vercel project hitting
`api/index.py`).

## Suites

- Python: 596 passed, 42 skipped, 48 deselected.
- Web: 46 vitest tests across 11 files; `tsc --noEmit` clean.
