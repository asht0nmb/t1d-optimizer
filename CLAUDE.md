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

- `ingestion/` — tconnectsync client, per-event-type builders, enrichment (`bolus_category`, `forced_by_alarm`, `site_issues`, `cgm_gaps`), parquet storage, shared view-mode helper (`view_data.py`).
- `detection/` — typed `AppConfig` + anomaly / missed-meal / daily-feature / KMeans clustering modules. Source-agnostic: pure DataFrame-in / DataFrame-out, no ingestion imports.
- `scripts/` — CLI entry points: `sanity_check` (check), `daily_viz` (viz), `run_detection` (analyze-anomalies / analyze-meals / cluster-days).
- `tests/` — 282 passing tests across builders, storage, enrichment, detection, and CLI.

See `docs/operating_docs/HANDOFF.md` (the most recent Session entry) for what shipped last and what's next.

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
- `ingestion/` — tconnectsync client, builders, enrichment, parquet storage, view-mode helper
- `detection/` — typed config + anomaly / meal / features / clustering modules
- `scripts/` — CLI entry points (`sanity_check`, `daily_viz`, `run_detection`)
- `tests/` — pytest suite (282 passing, 1 skipped)
- `docs/operating_docs/` — `HANDOFF.md`, `DATA_NOTES.md`, `DATA_ISSUES.md`, `DATA_NOTES_2.md`
- `research.ipynb` — exploratory analysis notebook

## Critical Rules

- **Never hardcode thresholds or personal parameters.** All config lives in `config/user_config.yaml` (see `TECHNICAL_SPEC.md` for schema). Detection logic reads from config at runtime.
- **Real-time detection uses trailing window only** — no future BG context available.
- Python 3.12+ required. Dependencies managed with `uv` (see `pyproject.toml`).
- ML stack: scikit-learn, xgboost, lightgbm, statsmodels, scipy.
