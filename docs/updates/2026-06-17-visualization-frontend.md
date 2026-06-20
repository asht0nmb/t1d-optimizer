# 2026-06-17 — Visualization + front-end overhaul (Workstream V)

From `docs/superpowers/plans/2026-06-17-workstream-v-frontend.md`. Front-end work
used the installed `shadcn-ui` and `chart-visualization` skills.

## V-1 — Clinical report made visible (both shells)

The Workstream A analytics engine now has a UI on both surfaces, computed by the
same `core.metrics.compute_cgm_report` (single source of truth).

- **Local (Streamlit):** a new **Report** page — GRI / GMI / mean / CV(+stability)
  tiles, TIR / TITR / below-70 / above-180, a stacked time-in-bands bar, and a
  risk/variability detail panel (LBGI/HBGI/eA1c/MAGE/MODD/CONGA/J-index), with
  the data-sufficiency gate respected. Pure chart builder + pytest.
- **Web (Next.js):** a Python compute endpoint on the Vercel worker
  (`api/metrics_report.py`, bearer-auth) → a session-guarded Next.js proxy
  (`/api/report`) → a `/report` page with `ReportTiles` + `TimeInBandsBar`. The
  web shell does **no** metric math. (Deploy-affecting: new worker route + two
  env vars — see below.)

## V-2 — Design system + UX foundation (web)

- shadcn-style CVA primitives (`Card`/`Badge`/`Button`/`Skeleton`/`EmptyState`/
  `ErrorState`) on HSL design tokens; a working class-strategy **dark mode**
  (replacing the dead `prefers-color-scheme` vars).
- An at-a-glance **Overview** home (latest TIR, freshness from `/api/status`,
  recent alerts) replacing the bare date-picker landing; the date picker moved to
  `/day`. The flat 9-link nav is **grouped** (Daily / Trends / System) with
  focus-visible rings and `aria-current`.
- Every page migrated to Skeleton / ErrorState(+retry) / EmptyState with
  `aria-live` async regions; status conveyed by text label, not color alone.

## V-3 — Chart correctness + depth (web)

- **CompareChart**: fixed the positional index-merge bug — both days now project
  onto a shared minutes-since-midnight axis.
- **DayChart**: numeric/time x-axis with a shared `[0,1440]` domain across all
  three panels, so gaps are proportional and CGM/bolus/basal line up; overlay
  geometry uses the same coordinates.
- **Heatmap**: replaced the 4-bucket HTML `<table>` with a continuous-colorscale
  CSS-grid heatmap anchored to the BG targets, a gradient colorbar, clock-time
  hours, and weekly separators; cells keyboard-focusable.
- **Click-through**: heatmap cells and trends points navigate to `/day/[date]`.

The smoothed 15-min AGP from Workstream A is rendered by the local AGP chart on a
continuous axis; the web AGP continues via SQL (matched percentile method).

## Deploy notes (owner)

- The web `/report` needs `METRICS_WORKER_URL` (the worker's base URL) and
  `CRON_SECRET` (reused, must match the worker) set on the web Vercel project;
  redeploy the worker to pick up `api/metrics_report.py`. Added to
  `apps/web/.env.example` and `DEPLOY.md`.
- All other web changes ship on the next `apps/web` deploy; no schema changes.

## Suites

Python 730 passed / 42 skipped / 48 deselected. Web 93 vitest (17 files), `tsc`
clean, lint clean, `next build` green. Charts/analytics are observations only —
never dosing advice.
