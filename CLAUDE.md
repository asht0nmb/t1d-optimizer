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

**Early stage** — the project has a skeleton (`main.py`, empty `ingestion/` and `tests/` dirs) with a research notebook. Most logic is yet to be built.

### Data Pipeline (planned)

Two ingestion modes:
1. **Historical**: Tandem CSV exports (in `data/`) and tconnectsync
2. **Live**: pydexcom for real-time Dexcom CGM readings (every 5 min)

The detection engine must be **source-agnostic** — it operates on normalized data regardless of ingestion source.

### CSV Format (Tandem Export)

Each CSV contains **three sections** separated by blank lines, each with its own header row:
1. **EGV (CGM readings)**: `DeviceType,SerialNumber,Description,EventDateTime,Readings (mg/dL)` — lines 7–7413
2. **Manual BG**: `DeviceType,SerialNumber,Description,EventDateTime,BG (mg/dL),Note` — should be **ignored** per spec
3. **Bolus data**: `Type,BolusType,BolusDeliveryMethod,BG (mg/dL),SerialNumber,CompletionDateTime,InsulinDelivered,FoodDelivered,CorrectionDelivered,...` — lines 7542+

The first 6 lines are a metadata header (device info, software version, report date). Ingestion must parse these sections separately.

### Key Directories

- `data/` — real patient CSV exports (do not commit new data files without asking)
- `test_data/` — anonymized copies for testing
- `ingestion/` — data loading and normalization (to be built)
- `tests/` — pytest tests (to be built)
- `research.ipynb` — exploratory analysis notebook

## Critical Rules

- **Never hardcode thresholds or personal parameters.** All config lives in `config/user_config.yaml` (see `TECHNICAL_SPEC.md` for schema). Detection logic reads from config at runtime.
- **Real-time detection uses trailing window only** — no future BG context available.
- Python 3.12+ required. Dependencies managed with `uv` (see `pyproject.toml`).
- ML stack: scikit-learn, xgboost, lightgbm, statsmodels, scipy.
