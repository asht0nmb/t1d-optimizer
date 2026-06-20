# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

T1D Engine is a Type 1 diabetes data intelligence system. It ingests CGM + insulin pump data, detects events (missed meals, anomalies), clusters daily BG patterns, and surfaces insights via a live Telegram alert loop, a Next.js personal dashboard, and a local Streamlit dashboard. Read `TECHNICAL_SPEC.md` before writing any code.

## Commands

```bash
# Dependencies (uses uv, not pip)
uv sync

# Run
uv run python main.py

# Tests
uv run pytest
uv run pytest tests/test_specific.py
uv run pytest tests/test_specific.py::test_function_name

# Jupyter notebook
uv run jupyter notebook research.ipynb
```

## Architecture

The ingestion + enrichment + detection layers are in place, and three surfaces are shipped: a live Telegram meal-rise alert loop, a Next.js personal dashboard (Vercel + Supabase), and a local Streamlit dashboard. Current layout:

- `core/` — storage-agnostic library shared by every deployment shell. Houses the `Storage` Protocol (`core/storage/protocol.py`), the schema registry (`core/schema.py`), typed metadata records (`core/storage/records.py`), and three implementations (`core/storage/parquet.py`, `core/storage/memory.py`, `core/storage/supabase.py`). The pandas → Postgres converters that `SupabaseStorage` and the bootstrap script share live in `core/storage/_postgres_converters.py`. `core/detection/` holds the shared windowing helper and the meal-rise detector.
- `ingestion/` — tconnectsync client, per-event-type builders, enrichment (`bolus_category`, `forced_by_alarm`, `site_issues`, `cgm_gaps`), parquet storage shim (`ingestion/storage.py` delegates to `ParquetStorage`), shared view-mode helper (`view_data.py`).
- `detection/` — typed `AppConfig` + `daily_features` patterns-layer foundation. Source-agnostic: pure DataFrame-in / DataFrame-out, no ingestion imports. v1 reference implementation (anomaly / meal / clustering) is quarantined under `detection/legacy/` — see its README. `detection/calibration/` holds the M2 meal-rise scoring module.
- `apps/local/` — local Streamlit OSS dashboard: day view, heatmap, TIR panel, insulin, AGP, compare — Plotly charts. Runs against parquet files with no cloud accounts required. AGP percentiles come from `core/metrics/agp.py`.
- `apps/web/` — Next.js personal dashboard deployed on Vercel + Supabase. Routes: day view, heatmap, TIR trends, insulin panel, search, compare, AGP, alerts history, status. All `/api/*` data routes require a signed-in session (`lib/api/auth.ts`).
- `apps/personal/cron/` — live meal-rise alert loop: polls Dexcom every 5 min, runs the detector, sends a Telegram alert on a missed-meal signal. Invoked by the Vercel Python worker at `api/index.py` (a separate Vercel project), triggered by external cron-job.org.
- `apps/personal/telegram/` — deterministic Telegram command surface (`/today`, `/yesterday`, `/trends`, `/status`, `/help`). Webhook entrypoint `api/telegram.py` (same cron-worker Vercel project). No LLM — pure aggregates over the Storage layer; secret-token + chat-allowlist auth.
- `core/metrics/` — shared metric definitions (AGP hourly percentile profile); pure pandas/numpy, consumed by local charts and mirrored by web SQL.
- `db/migrations/` — Supabase schema migrations and RLS policies.
- `.github/workflows/` — nightly Tandem sync (Telegram alert on failure), manual meal-rise fallback, smoke test, and pytest CI.
- `scripts/` — CLI entry points: `sanity_check` (check), `daily_viz` (viz), `doctor`; `score_meal_rise.py` (M2 calibration report — advisory only).
- `tests/` — 730 passed, 42 skipped, 48 deselected in the default suite across builders, storage, enrichment, detection features/config, the `core/metrics/` clinical-analytics suite (golden + hypothesis property tests), CLI, the storage Protocol contract suite under `tests/core/`, and Telegram command/digest/handler tests under `tests/personal/` (supabase-parameterized tests skip unless `SUPABASE_TEST_URL` is set; integration-marked tests are deselected by default). 47 additional `legacy`-marked tests cover `detection/legacy/*` and run opt-in via `uv run pytest -m legacy`. The web shell has its own vitest suite (93 tests) + `tsc --noEmit` + `next build`, gated in CI alongside pytest.

### `core/` package boundary

The `core/` package is the storage-agnostic library that both the personal deployment shell (Next.js + Vercel + Supabase) and the OSS local shell (Streamlit + parquet/SQLite) consume. Import rules are binding:

- `core/` MAY import from: stdlib, pandas, numpy, pydantic, typing/Protocol.
- `core/` MAY NOT import from: `ingestion/`, `scripts/`, `apps/`, `psycopg2`, `supabase-py`, parquet-specific code outside `core/storage/parquet.py`, Vercel SDK, Streamlit, FastAPI, Telegram libs, LLM clients.
- Backend-specific concrete code (psycopg2 calls, parquet I/O) lives in `core/storage/parquet.py`, `core/storage/supabase.py`, and `core/storage/_postgres_converters.py`. Those are the ONLY files allowed to import their respective backend SDKs.
- Code in `core/` never decides which backend to use; the shell instantiates a `Storage` implementation at startup and passes it down via constructor injection.
- New downstream code (detection v2, the live alert loop, the Tandem→Supabase sync, the dashboard backend) takes a `Storage` via DI from the start.

### Storage abstraction

The `Storage` Protocol in `core/storage/protocol.py` is the backend-agnostic data layer every caller talks to. Phase 1 (this PR family) landed the Protocol, the schema registry, and three implementations: `ParquetStorage` (local files), `InMemoryStorage` (tests), and `SupabaseStorage` (Postgres via psycopg2) — all three validated by parameterized contract tests under `tests/core/test_storage_contract.py`. The supabase parameterization skips unless `SUPABASE_TEST_URL` is set, and refuses to run against any host that matches a production-host denylist. The existing `ingestion/storage.py` is still a thin shim over `ParquetStorage` so every pre-Protocol caller (fetch, view, detection, `bootstrap_supabase`) keeps working unchanged — migrating existing callers to take a `Storage` via DI is deferred to follow-up PRs (live alert loop, Tandem nightly sync, dashboard backend, Telegram handlers).

SupabaseStorage callers MUST use the transaction-mode pooler URL (`*.pooler.supabase.com:6543`) and an open-do-close lifecycle (context manager for short-lived via `SupabaseStorage.from_pooler_url(url)`, caller-managed conn via `SupabaseStorage(conn=...)` for long-lived). Direct connections (`db.*.supabase.co:5432`) are reserved for the nightly GitHub Action and the one-shot `scripts/bootstrap_supabase.py`. The Postgres-side `idle_in_transaction_session_timeout = '5min'` set by migration 0002 is the belt-and-suspenders backstop.

#### RLS model

Migration `0003_enable_rls.sql` enables Row-Level Security on every public table under a four-role threat model. `postgres` (psycopg2 with the DB password — used by `bootstrap_supabase.py`, the GitHub Action nightly sync, and `SupabaseStorage`) and `service_role` (Supabase JWT for server-side admin calls — used by future Vercel API routes) both have the `BYPASSRLS` attribute and are unaffected by RLS. `authenticated` (Supabase JWT for signed-in users) and `anon` (Supabase JWT for unauthenticated requests, the key embedded in client bundles) are subject to RLS.

Each of the 13 public tables carries one permissive policy: `auth_required_all FOR ALL TO authenticated USING (true) WITH CHECK (true)`. `anon` has no policy on any table, so the default-deny behaviour applies — anon-key requests see zero rows everywhere. This is the minimum-viable lockdown for the current single-user shape; per-row ownership (`USING (user_id = auth.uid())`) is deferred until a multi-user story exists.

Implication for new code: server-side handlers that need admin access should connect via `service_role` (or the postgres role through `SupabaseStorage`) and rely on application-level authorization. Client-side handlers (Next.js bundles, future Streamlit pages with `@supabase/supabase-js`) MUST go through the anon key + Supabase Auth and rely on RLS for tenant isolation — they cannot read or write any public table without a signed-in `authenticated` session.

When you finish a substantive change, write a dated `docs/updates/YYYY-MM-DD-*.md` entry rather than mutating prior updates. The dated trail is the audit log.

### Data Pipeline

Two ingestion modes:
1. **Historical**: Tandem CSV exports (in `data/`) and tconnectsync, synced nightly to Supabase via a GitHub Actions workflow.
2. **Live**: pydexcom polls the Dexcom Share API every 5 minutes. The live loop (`apps/personal/cron/`) runs the meal-rise detector on each new reading and fires a Telegram alert when a missed-meal signal is detected. The loop is invoked by a Vercel Python worker (`api/index.py`) triggered by cron-job.org.

The detection engine must be **source-agnostic** — it operates on normalized data regardless of ingestion source.

### View Modes (`check` / `viz`)

`check` and `viz` both accept `--view {original,enriched}` (default `original`):

```bash
uv run python main.py check --date YYYY-MM-DD [--view enriched]
uv run python main.py viz   --date YYYY-MM-DD [--view enriched]
```

- `original` — hides enrichment columns/overlays; preserves pre-enrichment output for regression comparisons.
- `enriched` — adds `bolus_category` / `override_delta` / `forced_by_alarm` sections to `check` and forced-site / site-issue / `cgm_gaps`-based OOR shading overlays to `viz`. Backfilled in memory if the parquets on disk predate enrichment; on-disk files are never mutated.

Shared backfill lives in `ingestion/view_data.ensure_enriched`; `scripts/run_detection` and both CLI commands all delegate to it.

### CSV Format (Tandem Export)

Each CSV contains **three sections** separated by blank lines, each with its own header row:
1. **EGV (CGM readings)**: `DeviceType,SerialNumber,Description,EventDateTime,Readings (mg/dL)` — lines 7–7413
2. **Manual BG**: `DeviceType,SerialNumber,Description,EventDateTime,BG (mg/dL),Note` — should be **ignored** per spec
3. **Bolus data**: `Type,BolusType,BolusDeliveryMethod,BG (mg/dL),SerialNumber,CompletionDateTime,InsulinDelivered,FoodDelivered,CorrectionDelivered,...` — lines 7542+

The first 6 lines are a metadata header (device info, software version, report date). Ingestion must parse these sections separately.

### Key Directories

- `data/` — real patient CSV exports and `data/processed/*.parquet` (do not commit new data files without asking)
- `test_data/` — anonymized copies for testing
- `core/` — storage-agnostic Protocol library (`core/schema.py`, `core/storage/`)
- `ingestion/` — tconnectsync client, builders, enrichment, storage shim, view-mode helper
- `detection/` — typed config + `daily_features` patterns-layer foundation. `detection/legacy/` holds the v1 reference implementation (not maintained, not imported from production code). `detection/calibration/` holds the M2 meal-rise scoring module.
- `core/detection/` — shared windowing helper and meal-rise detector (used by the live loop).
- `apps/local/` — local Streamlit OSS dashboard (day/heatmap/TIR, Plotly).
- `apps/web/` — Next.js personal dashboard (Vercel + Supabase, Phase A routes).
- `apps/personal/cron/` — live Dexcom poll → detect → Telegram alert loop; `api/index.py` is the Vercel Python worker that invokes it.
- `db/migrations/` — Supabase schema and RLS migrations.
- `.github/workflows/` — nightly Tandem sync, manual meal-rise fallback, smoke test, pytest CI.
- `scripts/` — CLI entry points (`sanity_check`, `daily_viz`, `doctor`)
- `tests/` — pytest suite (730 passed / 42 skipped / 48 deselected by default, plus 47 legacy-marked tests opt-in via `-m legacy`); Storage Protocol contract suite and `core/metrics/` analytics tests under `tests/core/`
- `docs/operating_docs/` — `TECHNICAL_SPEC.md`, `DATA_CATALOG.md`, `DATA_NOTES.md`, `DATA_NOTES_2.md`, `DATA_ISSUES.md`, `api_levels.md`, `tconnectsync_api_map.md`
- `docs/updates/` — dated session write-ups (`YYYY-MM-DD-*.md`); append-only audit log
- `research.ipynb` — exploratory analysis notebook

## Critical Rules

- **Do not extend `detection/legacy/`; do not import from it in production code.** v2 modules land at the top level of `detection/` (or `detection/v2/` if symmetry with legacy is preferred when v2 ships).
- **Never hardcode thresholds or personal parameters.** All config lives in `config/user_config.yaml` (see `TECHNICAL_SPEC.md` for schema). Detection logic reads from config at runtime.
- **Real-time detection uses trailing window only** — no future BG context available.
- **Bump `ingestion.pipeline_version.PIPELINE_VERSION` (and add a changelog entry) whenever a builder or enricher changes output schema or timestamp semantics in a way that invalidates existing `data/processed/*.parquet`.** Run `uv run python main.py doctor` to confirm on-disk data matches the code.
- Python 3.12+ required. Dependencies managed with `uv` (see `pyproject.toml`).
- ML stack: scikit-learn, xgboost, lightgbm, statsmodels, scipy.
