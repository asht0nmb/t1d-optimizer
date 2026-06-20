# T1D Engine — Product Completion Design

Date: 2026-06-11
Status: approved direction (owner delegated execution; ML explicitly deferred)

## Goal

Bring T1D Engine from its current state — working ingestion, enrichment,
live meal-rise alerting, two dashboards — to a finished product in two
shapes:

1. **OSS local shell**: Streamlit + parquet, runnable by anyone with a
   Tandem CSV export, no cloud accounts required.
2. **Hosted personal shell**: Next.js + Vercel + Supabase + cron worker +
   Telegram, already deployed for the owner.

"Finished" excludes the machine-learning layer (clustering, supervised
threshold models, LLM attribution) — see *ML deferral boundary* below.

## Current state (verified 2026-06-11)

| Area | State |
|------|-------|
| Ingestion (CSV + tconnectsync) + enrichment | Done |
| Storage Protocol (parquet / memory / Supabase) + contract tests | Done |
| Nightly Tandem→Supabase sync (GH Action, 06:00 UTC) | Done |
| Live meal-rise loop (cron-job.org → Vercel worker → Dexcom → Telegram) | Done, M1-hardened |
| M2 calibration scoring module | Landed `d63d5d8`; runner CLI missing |
| Web dashboard (Next.js Phase A: day/heatmap/trends/insulin/search/compare) | Done |
| Local dashboard (Streamlit: day/heatmap/TIR) | Done, thinner than web |
| Test suite | 576 passing default, 47 legacy opt-in; **no CI test workflow** |
| Secrets posture | Clean — only placeholder `.env.example` tracked (verified against git history) |
| LICENSE / CONTRIBUTING / SECURITY | Missing |
| TECHNICAL_SPEC.md | ~3 months stale (describes quarantined v1 detection) |

## Phases

Ordered by risk-reduction first, then user-visible depth. Each phase gets
its own implementation plan and dated `docs/updates/` entry; work executes
via subagents with review between tasks.

### Phase 1 — OSS hygiene & CI (foundation)
- `LICENSE` (MIT) + prominent medical disclaimer (not a medical device, no
  medical advice) in README and LICENSE notice section.
- `CONTRIBUTING.md` (dev setup with uv, test commands, update-doc
  convention, core/ import rules), `SECURITY.md` (private disclosure).
- Tidy root `.env.example` (remove duplicate/typo placeholder lines).
- **GitHub Actions test CI**: `uv sync --frozen` + `uv run pytest` on push
  / PR to main. This is the single biggest missing safety net.

### Phase 2 — Documentation truth-up
- Rewrite `TECHNICAL_SPEC.md`: v2 detection architecture (windowing +
  meal-rise + calibration), live loop topology, surfaces, storage
  Protocol; move v1 algorithm text to a legacy appendix or point at
  `detection/legacy/README.md`.
- Refresh `CLAUDE.md` (apps/, api/, db/ now exist; test counts).
- README: align claims, add OSS quickstart (CSV → parquet → Streamlit in
  three commands), screenshot placeholder.

### Phase 3 — M2 calibration runner (non-ML)
- `scripts/calibrate_meal_rise.py`: load historical CGM + requests from a
  `Storage` (DI per core rules), run `find_meal_rise_instances` +
  `score_instances`, emit a markdown/JSON report (label distribution,
  uncovered rate, per-time-of-day breakdown, sensitivity sweep over
  `base_slope_mgdl_per_min`).
- Report **proposes** config values; nothing auto-applied. Rerun
  instructions documented so the owner can redo after any data refresh.

### Phase 4 — Visualization depth
Local Streamlit (OSS flagship):
- Insulin page (daily bolus/basal bars) and two-day compare — parity with
  web.
- Enrichment overlays on day view: bolus-category markers, CGM-gap
  shading, site-issue bands (data already in parquet).
- AGP-style percentile plot (14/30/90-day 5th–95th percentile ribbons by
  hour of day) — the standard clinical visualization, currently absent
  from both shells.

Web:
- AGP page (same metric definitions, SQL aggregation).
- Alerts page: history from `alerts_sent` + `detection_results` (what
  fired, when, delivery status) — closes the loop on the live detector.
- Day-view enrichment overlays to match local.

Shared metric definitions live in `core/` (pure pandas) so both shells
render the same numbers.

### Phase 5 — Automation & observability depth
- Failure alerting: nightly-sync workflow notifies Telegram on failure
  (job-level `if: failure()` step using existing bot secrets).
- Cron-health surfacing: `last successful run` per automation (sync,
  meal-rise) queryable from `fetch_state` / `alerts_sent`, shown on the
  web dashboard footer or a `/status` page; `doctor` gains a remote mode.
- CI gets the doctor check where applicable.

### Phase 6 — Telegram command surface (deterministic)
- Webhook or polling handler answering `/today`, `/yesterday`, `/trends`
  with digests computed from `daily_features` via Storage. No LLM — that
  ships with the deferred intelligence layer.
- Lives in the personal shell (`apps/personal/`), reuses the alert bot.

### Phase 7 — Release prep
- Final docs sweep, version tag, GitHub release notes, repo description /
  topics, confirm `data/` privacy posture, dependency audit.

## ML deferral boundary

Deferred (owner will drive; nothing here blocks it):
- `detection/legacy/clustering.py` replacement (pattern layer).
- Supervised models (xgboost/lightgbm) on the M2-labeled dataset.
- LLM-backed cause attribution and LLM Telegram assistant.

Rules while deferred:
- Calibration (Phase 3) outputs are advisory: they may only suggest
  values for existing config variables, never change behavior silently.
- Everything ML-adjacent that *is* built (feature aggregation, labeled
  datasets, reports) must document exact rerun steps so results are
  reproducible later.

## Safety & traceability rules for execution

- No deletions of data, migrations, or history rewrites without explicit
  owner sign-off.
- Every phase lands with passing `uv run pytest` and a dated
  `docs/updates/` entry (append-only audit trail).
- Live surfaces (Vercel projects, cron-job.org, Supabase) are not
  reconfigured destructively; deploy-affecting changes are called out in
  the update doc for the owner to roll out.
- Thresholds/personal parameters stay in `config/user_config.yaml`; no
  hardcoding (existing rule, reaffirmed).

## Alternatives considered

- *Monorepo split (core as a package, shells as separate repos)*:
  rejected for now — single repo with the `core/` import boundary is
  working and simpler for a portfolio piece.
- *Replacing cron-job.org with GitHub Actions schedule for the 5-min
  loop*: rejected — Actions cron is best-effort (often 10–20 min late);
  current topology is correct.
- *Building the LLM Telegram assistant now*: rejected — entangled with
  the deferred intelligence layer; deterministic digests deliver most of
  the daily value.
