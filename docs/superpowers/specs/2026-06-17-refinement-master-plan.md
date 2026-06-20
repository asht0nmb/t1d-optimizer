# T1D Engine — Refinement Master Plan

Date: 2026-06-17
Branch: `product-completion` (main was rewound to `7ea5de0`; all completion +
refinement work lives here)
Basis: six isolated evaluation agents (visualizations, front-end UX, Python
code quality, data/metric correctness, infra/security, analytics design).

## Premise

The completion phases shipped a working product. This effort *refines* every
piece to a portfolio-grade, clinically-correct bar, with TDD-driven solutions.
The owner's emphases: graphs/interactivity go DEEP; the analytics layer is the
intellectual centerpiece and needs real clinical rigor; front-end work uses
`/find-skills`; complex TDD where it pays.

## Severity legend

- **CORRECTNESS-BUG** — produces wrong numbers on live health data. Fix first,
  test-first (capture the bug red, then green).
- **MAJOR** — substantial build or a real quality/safety gap.
- **REFINEMENT** — polish.

## Workstreams (execution order)

Ordered by risk-reduction then dependency. R and I-safety first (correctness +
silent-failure safety), then A (analytics engine, foundation for V's tiles),
then C (shared foundations), then V (the large front-end/viz overhaul that
consumes A).

---

### Workstream R — Correctness bugs (TDD red→green) — FIRST

1. **Web aggregate SQL day-window filter ignores user timezone.** CORRECTNESS-BUG.
   `agp/heatmap/trends/insulin/window-anchor.ts` bucket with `AT TIME ZONE $1`
   but filter `WHERE timestamp >= $2::date` — the date casts at the *session*
   TZ (UTC), so window edges are off by the UTC offset (and shift at DST).
   Fix: filter on UTC instant bounds like `day.ts`/`compare.ts` already do
   (`dayWindowUtc`). Tests: vitest on the query-builder bounds; cross-check
   against `day.ts` semantics.
2. **Local `cgm_in_read_bounds` windows by UTC midnight, not config tz.**
   CORRECTNESS-BUG. `apps/local/metrics.py:59` → `dates.date_window_bounds`
   defaults `tzinfo=utc`; day view + local AGP slice a UTC day while the TIR
   panel on the same page slices the local day (311 vs 407 rows verified). Fix:
   thread `tz=ZoneInfo(config.timezone)`; converge both helpers on one tz-aware
   definition. TDD: pytest asserting LA-day row counts.
3. **Basal integration cadence assumption.** MAJOR correctness. Web SQL
   (`insulin.ts:46`, `day.ts:150`) computes `SUM(commanded_rate*5/60)` assuming
   every basal row spans 5 min; python `_integrate_basal` integrates by true
   inter-row duration. Fix: integrate by `lead(timestamp)` duration in SQL to
   match python (or migrate to the analytics endpoint in A). TDD: golden TDD
   value on a non-uniform-cadence fixture.
4. **Heatmap mean vs median mismatch + mislabel.** MAJOR parity. Local plots
   `mean` (`heatmap.py:62`); web computes `PERCENTILE_CONT(0.5)` and labels
   "Median BG". Decide median (robust), align both shells + labels. TDD: assert
   the chosen aggregate in both.
5. **Ongoing-gap horizon can indefinitely suppress live detection.** MAJOR
   safety. `windowing.py:16,112` `ONGOING_GAP_HORIZON=3650d`: one uncleared
   `cgm_out_of_range` marks all future windows `has_gap=True` → silent
   false-negative on the alert loop. Fix: bound ongoing-gap effect to recent
   time / require overlap with the trailing window slice. TDD: parametrized
   overlap matrix (this is also Workstream C's windowing-hardening target).

### Workstream I-safety (pulled forward) — silent-failure safety

6. **Live 5-min cron has no failure alerting.** MAJOR safety. Worker 500s page
   no one; only an external cron-job.org setting. Fix: worker writes a
   heartbeat (`fetch_state` row `source_id='live_cron'`, `last_synced_at=now()`
   every run) and fires a Telegram "worker failing" on top-level exception; add
   a `/status` liveness signal (~15-min threshold) distinct from "last
   detection". TDD: handler test for heartbeat write + failure-path send.

### Workstream A — Clinical analytics engine (`core/metrics/`) — CENTERPIECE

Per `2026-06-17` analytics design (separate detailed plan). Pure
stdlib+numpy+pandas (core boundary; no scipy/sklearn). Single source of truth;
both shells consume identical numbers (Python computes; web calls a Python
worker endpoint; Streamlit imports directly).

- `windows.py` — DST-correct local-day windowing + active-time/sufficiency
  (14-day/70% gate; expected-readings from tz-aware span, not `days*288`).
- `cgm_metrics.py` — TIR/TITR/TAR1-2/TBR1-2 (fixed consensus cut points
  54/70/140/180/250, half-open partition summing to 100), mean/median/SD
  (ddof=1)/CV(+36% stability flag), GMI (`3.31+0.02392*mean`), eA1c.
- `risk_indices.py` — LBGI/HBGI (Kovatchev transform, clamp [20,600]), GRI
  (`3*hypo+1.6*hyper`, with components for the GRI-grid visual).
- `variability.py` — MODD, CONGA, J-index (cheap); MAGE (flagship, pinned
  Baghurst algorithm) last.
- `report.py` — `CgmReport` frozen dataclass + `compute_cgm_report(cgm, *,
  config, window)` orchestrator; `None` vs `0.0` discipline; retires the 4×
  TIR duplication (local metrics, features, telegram digest, web tir.ts all
  call the shared functions).

Complex TDD: golden values (GMI mean=150→6.898; GRI worked example→30.8; LBGI/
HBGI hand-computed), hypothesis property tests (partition invariant, bounds,
monotonicity), edge/DST matrix, cross-shell agreement contract test.

README fix: the clinical-metric section currently advertises GRI/GMI/LBGI/HBGI/
TITR/CV as if shipped — they were not. This workstream makes them real; update
README to match what now exists.

### Workstream C — Code-quality foundations

- **Lazy `SupabaseStorage` import** so `from core.storage import ParquetStorage`
  (OSS shell) no longer hard-requires psycopg2 (`core/storage/__init__.py:29`).
  TDD: import test in a psycopg2-absent shim.
- **Centralize `FOOD_CARRYING`/`_MEAL_CATEGORIES`** (dup 4×) and **local-day
  bounds / tz slicing** (dup across telegram, score_meal_rise, features) into
  `core/`. (TIR unification handled by A.)
- **Harden `windowing.py` gap-overlap** with parametrized + property tests
  (overlap geometry matrix; brute-force oracle) — the owner's "complex TDD"
  target; pairs with R#5.
- **Move unused ML deps** (xgboost/lightgbm/statsmodels/scipy/seaborn — 0
  imports) to an optional `ml` dependency-group; keep matplotlib. Slims the
  Vercel worker; aligns with the ML-deferral boundary.
- **Telegram handler**: log inside the inner storage-error catches
  (`handler.py:66,75,...`) instead of silently returning empty.

### Workstream I — Infra/security/CI hardening

- **Web CI gate.** MAJOR. `tests.yml` runs only pytest; add a path-filtered web
  job: `npm ci` → `lint` → `tsc --noEmit` → `vitest run` → `next build`.
- **Python lint/import-boundary gate**: `ruff check` + a test asserting `core/`
  imports no forbidden module; a vitest/test asserting every
  `app/api/**/route.ts` imports `requireSession`/`verifyCronAuth`.
- **Constant-time bearer auth** in `api/index.py` + `lib/cron/auth.ts`
  (`hmac.compare_digest` / `crypto.timingSafeEqual`).
- **Pin the cron worker `requirements.txt`** to exact versions from the lock.
- **Config/docs**: add `TELEGRAM_WEBHOOK_SECRET` to `.env.example`; a
  consolidated `docs/operating_docs/DEPLOY.md` runbook; move legacy-only config
  blocks to a marked namespace; `doctor --hosted` env/connectivity check.
- Action SHA-pinning; `ssl: verify` for the web pg pool.

### Workstream V — Visualization + front-end overhaul (largest; consumes A)

Front-end uses `/find-skills` for a component-library / data-fetching skill.

- **Design system**: adopt a component library (shadcn/ui candidate) + design
  tokens mapped to the BG palette; fix the dead **dark mode**; replace
  default-Tailwind-bland.
- **Overview/home page** (latest TIR, freshness, recent alerts) + **grouped
  nav**; reconcile web↔Streamlit feature sets.
- **Loading skeletons** + Next `loading.tsx`/`error.tsx`; kill the client-fetch
  waterfall (RSC or React Query/SWR); standard empty/error states.
- **Real smoothed AGP** (both shells): 15-min buckets + circular weighted
  smoothing (pure numpy), clock-time x-axis, clinical y-range.
- **Fix web DayChart**: numeric/time x-axis with a shared domain across the 3
  panels (proportional gaps, aligned bolus/CGM/basal), real overlay geometry.
- **Real web heatmap**: continuous colorscale + colorbar (replace the HTML
  `<table>`); weekly separators; clock-time hours.
- **Deep interactivity** (web): zoom/brush, click-through (heatmap→day,
  trends→day), synced cross-panel hover, custom rich tooltips.
- **Clinical summary tiles** (both shells): GMI/GRI/CV/time-in-bands stacked bar
  from `CgmReport` (depends on A).
- **Compare positional-merge fix** (web): shared minutes-since-midnight axis.
- Accessibility pass (aria-live, focus rings, contrast, non-color-only badges).

---

## Execution method

Each workstream gets a focused implementation plan (`docs/superpowers/plans/`)
and is executed task-by-task by subagents with spec+quality review, TDD
throughout, committing incrementally to `product-completion`. Correctness work
captures the bug as a failing test first. Front-end work pulls in a component
library via `/find-skills`. Every workstream lands with the full suite green
(python pytest + web vitest/tsc) and a dated `docs/updates/` entry.

## Traceability / safety

- No deletions of data, migrations, or history; main stays at `7ea5de0`
  untouched; nothing pushed without owner sign-off.
- ML layer stays deferred (owner-reserved). Analytics here is deterministic
  clinical math that *feeds* the future ML/LLM layer.
- Live-surface changes (worker, cron, Supabase) are called out for owner
  rollout; deploy-affecting items flagged in update docs.
