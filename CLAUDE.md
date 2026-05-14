# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

T1D Engine is a Type 1 diabetes data intelligence system. It ingests CGM + insulin pump data, detects events (missed meals, anomalies), clusters daily BG patterns, and will surface insights via Telegram and a Streamlit dashboard. Read `TECHNICAL_SPEC.md` before writing any code.

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

The ingestion + enrichment + detection layers are in place; surfaces (Telegram / Streamlit / live pydexcom) are not yet built. Current layout:

- `core/` — storage-agnostic library shared by every deployment shell. Houses the `Storage` Protocol (`core/storage/protocol.py`), the schema registry (`core/schema.py`), typed metadata records (`core/storage/records.py`), and the parquet + in-memory implementations (`core/storage/parquet.py`, `core/storage/memory.py`).
- `ingestion/` — tconnectsync client, per-event-type builders, enrichment (`bolus_category`, `forced_by_alarm`, `site_issues`, `cgm_gaps`), parquet storage shim (`ingestion/storage.py` delegates to `ParquetStorage`), shared view-mode helper (`view_data.py`).
- `detection/` — typed `AppConfig` + `daily_features` patterns-layer foundation. Source-agnostic: pure DataFrame-in / DataFrame-out, no ingestion imports. v1 reference implementation (anomaly / meal / clustering) is quarantined under `detection/legacy/` — see its README.
- `scripts/` — CLI entry points: `sanity_check` (check), `daily_viz` (viz), `doctor`.
- `tests/` — 463 passing tests in the default suite across builders, storage, enrichment, detection features/config, CLI, and the storage Protocol contract suite under `tests/core/`. 47 additional `legacy`-marked tests cover `detection/legacy/*` and run opt-in via `uv run pytest -m legacy`.

### `core/` package boundary

The `core/` package is the storage-agnostic library that both the personal deployment shell (Next.js + Vercel + Supabase) and the OSS local shell (Streamlit + parquet/SQLite) consume. Import rules are binding:

- `core/` MAY import from: stdlib, pandas, numpy, pydantic, typing/Protocol.
- `core/` MAY NOT import from: `ingestion/`, `scripts/`, `apps/`, `psycopg2`, `supabase-py`, parquet-specific code outside `core/storage/parquet.py`, Vercel SDK, Streamlit, FastAPI, Telegram libs, LLM clients.
- Backend-specific concrete code (psycopg2 calls, parquet I/O) lives in `core/storage/parquet.py` and the forthcoming `core/storage/supabase.py`. Those are the ONLY files allowed to import their respective backend SDKs.
- Code in `core/` never decides which backend to use; the shell instantiates a `Storage` implementation at startup and passes it down via constructor injection.
- New downstream code (detection v2, the live alert loop, the Tandem→Supabase sync, the dashboard backend) takes a `Storage` via DI from the start.

### Storage abstraction (in progress)

The `Storage` Protocol in `core/storage/protocol.py` is the backend-agnostic data layer every caller talks to. Phase 1 (this PR family) landed the Protocol, the schema registry, and two implementations (`ParquetStorage`, `InMemoryStorage`) — both validated by parameterized contract tests under `tests/core/test_storage_contract.py`. `SupabaseStorage` is pending in a follow-up PR built on top of `feat/supabase-bootstrap`; it will be added to the same fixture as a `"supabase"` parameter so it gets the contract suite for free. The existing `ingestion/storage.py` is now a thin shim over `ParquetStorage` so every pre-Protocol caller (fetch, view, detection, `bootstrap_supabase`) keeps working unchanged — Phase 3 (migrating existing callers to take a `Storage` via DI) is deferred to a later PR.

When you finish a substantive change, write a dated `docs/updates/YYYY-MM-DD-*.md` entry rather than mutating prior updates. The dated trail is the audit log.

### Data Pipeline

Two ingestion modes:
1. **Historical**: Tandem CSV exports (in `data/`) and tconnectsync
2. **Live**: pydexcom for real-time Dexcom CGM readings (every 5 min) — **not yet implemented**

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
- `detection/` — typed config + `daily_features` patterns-layer foundation. `detection/legacy/` holds the v1 reference implementation (not maintained, not imported from production code).
- `scripts/` — CLI entry points (`sanity_check`, `daily_viz`, `doctor`)
- `tests/` — pytest suite (463 default, plus 47 legacy-marked tests opt-in via `-m legacy`); Storage Protocol contract suite under `tests/core/`
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
