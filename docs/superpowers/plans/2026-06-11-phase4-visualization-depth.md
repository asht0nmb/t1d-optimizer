# Phase 4: Visualization Depth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the local Streamlit shell to parity-plus (insulin, AGP, compare pages) and deepen the web shell (AGP page, alerts-history page, day-view enrichment overlays), with the AGP metric defined once in `core/`.

**Architecture:** AGP percentile math lands as a pure-pandas function in `core/metrics/agp.py` (new `core/metrics/` package, obeying core import rules: stdlib/pandas/numpy only). The local shell consumes it directly; the web shell mirrors the same definition in SQL (`PERCENTILE_CONT` by hour) with a comment pinning it to the core definition. New local pages follow the existing `PAGES` tuple + `_page_*` function + `charts/*.py` builder pattern (apps/local/app.py:54,410-430). New web pages follow the page.tsx → app/api/route.ts → lib/queries/*.ts pattern with recharts.

**Tech Stack:** pandas, Plotly (local), recharts + node-postgres/PERCENTILE_CONT (web), pytest, vitest.

**Conventions:** TDD per task; commit per task to main with trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`; suite baseline 584 passed / 42 skipped / 48 deselected; web checks = `npx vitest run` + `npx tsc --noEmit` from `apps/web/` (do NOT require `next build` — it needs deploy env vars).

**AGP definition (single source of truth):** for a window of N days ending at `end_date`, group CGM readings by local hour-of-day (config timezone), report percentiles **5/25/50/75/95** of `bg_mgdl` per hour plus reading count `n`. Hours with no readings are omitted (local) / NULL rows (web). This matches the standard Ambulatory Glucose Profile presentation (median line, 25–75 IQR band, 5–95 outer band).

---

### Task 1: `core/metrics/agp.py` (pure AGP math, TDD)

**Files:**
- Create: `core/metrics/__init__.py` (empty)
- Create: `core/metrics/agp.py`
- Create: `tests/core/test_agp.py`

- [ ] Write failing tests first in `tests/core/test_agp.py`: build a synthetic CGM frame (tz-aware UTC timestamps spanning 3 days, known values per hour — e.g. hour 6 always [100,110,120] so p50==110, p5==101.0 via linear interpolation), then assert:
  - `agp_profile(cgm, days=3, end_date=date(...), tz="UTC")` returns a DataFrame with columns `["hour","p05","p25","p50","p75","p95","n"]`, one row per hour with data, sorted by hour.
  - Median/percentile values match `numpy.percentile(..., method="linear")` for the known hour.
  - Timezone honored: with `tz="America/Los_Angeles"`, a reading at 14:00 UTC lands in hour 6 or 7 (assert via a single-reading frame).
  - Readings outside the `[end_date-days+1, end_date]` local-day window are excluded.
  - Empty frame → empty DataFrame with the same columns.
- [ ] Run: `uv run pytest tests/core/test_agp.py -v` → FAIL (module missing).
- [ ] Implement `agp_profile(cgm_df: pd.DataFrame, *, days: int, end_date: datetime.date, tz: str, percentiles: tuple[float, ...] = (5, 25, 50, 75, 95)) -> pd.DataFrame`. Pure pandas/numpy; convert timestamps to `tz`, filter to the local-date window (reuse the `[since, until)` convention from `apps/local/dates.date_window_bounds` — but implement locally; core may not import from apps), groupby hour, `quantile` with linear interpolation, column names `p05`-style zero-padded.
- [ ] Run tests → PASS. Verify core import rules: `grep -n 'import' core/metrics/agp.py` → only stdlib/pandas/numpy.
- [ ] Commit: `feat(core): AGP hourly percentile profile in core/metrics`.

### Task 2: Local Insulin page (TDD)

**Files:**
- Create: `apps/local/charts/insulin.py`
- Modify: `apps/local/app.py` (PAGES tuple :54, dispatch :425-430, new `_page_insulin`)
- Modify: `tests/test_local_dashboard.py`

- [ ] Failing tests: `build_plotly_insulin_figure(bolus, basal, end_date, days)` returns a `go.Figure` with two bar traces (daily bolus total from `insulin_delivered`; daily basal total integrated from the per-minute commanded-rate stream — mirror the daily-total semantics of `apps/web/lib/queries/insulin.ts`, read it first) and one row per day in window; empty frames → figure with empty traces (no crash).
- [ ] Implement chart builder (Plotly, follow `charts/tir_trend.py` style: module-level helpers, height constant, hover text) and wire the page (window selector 14/30/90 days via `st.radio` horizontal, mirroring `_page_tir`). Importable without streamlit (keep `import streamlit` out of charts module — existing rule enforced by `test_app_helpers_import_without_streamlit`).
- [ ] `uv run pytest tests/test_local_dashboard.py -q` → all pass. Commit: `feat(local): insulin history page (daily bolus/basal totals)`.

### Task 3: Local AGP page (TDD)

**Files:**
- Create: `apps/local/charts/agp.py`
- Modify: `apps/local/app.py`, `tests/test_local_dashboard.py`

- [ ] Failing tests: `build_plotly_agp_figure(cgm, low, high, end_date, days, tz)` consumes `core.metrics.agp.agp_profile` output and returns a figure containing: median line trace, 25–75 filled band, 5–95 filled band, target-range band (reuse low/high shading approach from `charts/day_view._add_range_bands` — read it; copy the pattern, don't import private helpers across modules), x-axis = hour of day 0–23. Empty CGM → valid empty figure.
- [ ] Implement; add "AGP" page with 14/30/90-day selector.
- [ ] Tests pass; commit: `feat(local): AGP percentile profile page`.

### Task 4: Local Compare page (TDD)

**Files:**
- Create: `apps/local/charts/compare.py`
- Modify: `apps/local/app.py`, `tests/test_local_dashboard.py`

- [ ] Failing tests: `build_plotly_compare_figure(cgm_a, cgm_b, date_a, date_b, low, high)` overlays two days' CGM on a shared time-of-day x-axis (normalize both to minutes-since-midnight local), two line traces labeled by date, target band present. One-empty-day → single-trace figure, no crash.
- [ ] Implement; page gets two `st.date_input` pickers constrained to available CGM dates (`navigation.list_cgm_dates_from_storage`), defaults: selected day and the day before.
- [ ] Tests pass; full python suite tail recorded. Commit: `feat(local): two-day compare page`.

### Task 5: Web AGP page

**Files:**
- Create: `apps/web/lib/queries/agp.ts`, `apps/web/app/api/agp/route.ts`, `apps/web/app/agp/page.tsx`, `apps/web/components/AgpChart.tsx`
- Modify: `apps/web/lib/types/api.ts`, `apps/web/components/AppNav.tsx`
- Test: `apps/web/__tests__/agp.test.ts`

- [ ] Read `lib/queries/heatmap.ts` + `insulin.ts` first and mirror their structure (raw SQL via `queryRows`, days param clamped, timezone from config). SQL: one row per local hour with `PERCENTILE_CONT(0.05/0.25/0.5/0.75/0.95) WITHIN GROUP (ORDER BY bg_mgdl)` + `COUNT(*)`, grouped by `EXTRACT(HOUR FROM timestamp AT TIME ZONE $tz)`, window = trailing N days. Comment in file: "Must match core/metrics/agp.py (5/25/50/75/95 by local hour)."
- [ ] Route: GET `/api/agp?days=30`, param parsing via the existing `parseIntParam` helper, `jsonOk/jsonError`. Page: client component fetching the route, `AgpChart` = recharts ComposedChart (Area for 5–95, Area for 25–75, Line for median, ReferenceArea or ReferenceLine for low/high targets from `/api/config` or the response itself — include `bgTargets` in the AGP response to avoid a second fetch). Nav link "AGP".
- [ ] Vitest: pure helpers only (e.g. response-shaping/clamping function extracted to `lib/queries/agp.ts` or a small `lib/agp.ts`); do not attempt to test SQL execution.
- [ ] Verify: `cd apps/web && npx vitest run` all pass, `npx tsc --noEmit` clean. Commit: `feat(web): AGP percentile profile page`.

### Task 6: Web Alerts-history page

**Files:**
- Create: `apps/web/lib/queries/alerts.ts`, `apps/web/app/api/alerts/route.ts`, `apps/web/app/alerts/page.tsx`
- Modify: `apps/web/lib/types/api.ts`, `apps/web/components/AppNav.tsx`
- Test: `apps/web/__tests__/alerts.test.ts`

- [ ] First inspect the real tables: `db/migrations/0002_supabase_storage_setup.sql` (detection_results) and `0001_init.sql` (alerts_sent) for exact columns. Query `alerts_sent` ordered by `sent_at` DESC with limit/offset pagination (mirror `lib/queries/search.ts` pagination if it exists — read it), returning alert_kind, event_ref, sent_at, delivery, payload (message text), pump_serial. No join needed for v1; detection_results context can come later.
- [ ] Page: table view (plain HTML table styled like Search page) with delivery-status badge (sent/pending/failed), payload message column, pagination controls. Nav link "Alerts".
- [ ] Vitest for the pure row-shaping helper. `npx vitest run` + `npx tsc --noEmit` clean. Commit: `feat(web): alerts history page`.

### Task 7: Web day-view enrichment overlays (gap + site-issue shading)

**Files:**
- Modify: `apps/web/components/DayChart.tsx` (or wherever the day chart renders — locate via `grep -rn 'DayChart' apps/web`)
- Test: `apps/web/__tests__/` if a pure helper is extracted

- [ ] `lib/queries/day.ts` already returns `cgm_gaps` and `site_issues` rows — confirm, then check whether DayChart renders them. If already rendered, record that and skip (report NOTHING-TO-DO). Otherwise: add recharts `ReferenceArea` shading on the CGM panel for each gap interval and a visually distinct band for site-issue intervals (match the local shell's semantics: gaps = neutral shading, site issues = warning shading on the bolus panel region). Extract interval-clipping helper (clip to day window) as a pure function with a vitest test.
- [ ] `npx vitest run` + `npx tsc --noEmit` clean. Commit: `feat(web): render cgm-gap and site-issue overlays on day view`.

### Task 8: Update doc + final verification

- [ ] `uv run pytest -q | tail -1` (expect 584 + new local/core tests, record exact), `cd apps/web && npx vitest run` tail, `npx tsc --noEmit` clean, `git status --short` clean.
- [ ] Write `docs/updates/2026-06-11-visualization-depth.md`: what shipped per shell, the shared-AGP-definition rule (core/metrics/agp.py is canonical; web SQL mirrors it), screenshots deferred. Note deploy implication: web changes ship on next Vercel deploy of apps/web — no action needed beyond merge if auto-deploy from main is configured (call this out for the owner).
- [ ] Commit: `docs: update entry for visualization depth phase`.
