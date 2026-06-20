# Workstream V — Visualization + Front-end Overhaul Plan

From `docs/superpowers/specs/2026-06-17-refinement-master-plan.md`. Largest
workstream; consumes the Workstream A analytics engine. Front-end work uses the
installed `shadcn-ui` and `chart-visualization` skills.

Baseline: Python 723 passed; web 62 vitest, tsc clean, `next build` green.

## Sub-tracks (sequenced to avoid merge conflicts)

### V-1 — Clinical summary tiles (make the analytics visible)
- **Local (Streamlit, inline):** a metrics tile row on the AGP page (and/or a
  dedicated "Report" page) from `core.metrics.compute_cgm_report`: GMI, GRI (+
  components), %CV (+ stability), eA1c, mean, and a horizontal time-in-bands
  stacked bar (TBR2/TBR1/TIR/TAR1/TAR2). Respect the sufficiency gate (show "—"
  / a banner when insufficient). Pure-python chart builder + pytest.
- **Web:** a Python compute endpoint on the existing Vercel worker
  (`api/metrics_report.py` or a handler) returning `CgmReport` JSON via
  `SupabaseStorage` (DI); a `<ReportTiles>` + `<TimeInBandsBar>` component; a
  page/section consuming it. Single source of truth — web does NO metric math.

### V-2 — Design system + UX foundation (web)
- Adopt shadcn/ui + design tokens mapped to the BG palette (`lib/colors.ts`);
  Card/Badge/Button/Table/EmptyState/ErrorState/Skeleton primitives.
- Working dark mode (remove dead CSS vars; Tailwind `dark:` class strategy).
- Overview/home page (latest TIR, freshness from `/api/status`, recent alerts)
  replacing the bare date-picker landing; grouped nav (Daily / Trends / System).
- Loading skeletons + Next `loading.tsx`/`error.tsx`; standardized empty/error
  states; reduce the client `useEffect` fetch waterfall (SWR or server fetch).
- Accessibility: aria-live on async regions, focus-visible rings, contrast,
  non-color-only status badges.

### V-3 — Chart correctness + depth (web)
- Fix `DayChart`: numeric/time x-axis with a shared domain across the 3 panels
  (proportional gaps, aligned bolus/CGM/basal); correct overlay geometry.
- Real heatmap: continuous colorscale + colorbar (replace the HTML `<table>`),
  weekly separators, clock-time hours.
- AGP: consume the smoothed profile (via the report/AGP endpoint or matched SQL)
  on a clock-time axis with clinical y-range.
- Compare: shared minutes-since-midnight axis (fix positional merge).
- Deep interactivity: zoom/brush, click-through (heatmap→day, trends→day),
  synced cross-panel hover, custom rich tooltips.

## Method

TDD where unit-testable (chart-prep helpers, report shaping, classifiers);
visual components verified via tsc + vitest + `next build`. Commit incrementally;
each track lands a dated update entry. Every change keeps Python + web suites
green. Deploy-affecting items (the web report endpoint) flagged for owner
rollout.
