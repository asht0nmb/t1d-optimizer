# Plan: Local Streamlit dashboard (`apps/local/`)

**Date:** 2026-05-17  
**Branch:** `feat/local-streamlit-dashboard`  
**Status:** Implemented

## Goal

Scaffold the OSS local shell: Streamlit over `ParquetStorage`, plus `main.py dashboard` and documented sync via existing `fetch` / `update`.

## Delivered

- `apps/local/app.py` — Streamlit entry (day view, heatmap, TIR, sidebar doctor + view mode + sync docs)
- `apps/local/{dates,metrics,data,heatmap,doctor_status}.py` — testable helpers
- `apps/local/README.md` — OSS quickstart
- `scripts/daily_viz.build_daily_figure()` — returns `Figure` for reuse (CLI unchanged)
- `main.py dashboard` — launches Streamlit
- `pyproject.toml` — `[dependency-groups] local` with `streamlit`
- `tests/test_local_dashboard.py` — TIR / date-window / empty-storage tests

## Out of scope (unchanged)

Telegram, Supabase, auth, first-run wizard, CSV import, `t1d-engine` package rename.

## Verification

```bash
uv sync --group local
uv run pytest -q
uv run pytest tests/test_local_dashboard.py -q
uv run python main.py doctor
```
