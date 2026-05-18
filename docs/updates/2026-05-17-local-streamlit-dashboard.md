# 2026-05-17 — Local Streamlit dashboard (`apps/local/`)

## Summary

Added the OSS local shell under `apps/local/`: Streamlit dashboard reading `data/processed` via `ParquetStorage`, with day view (reuses `scripts/daily_viz.build_daily_figure`), BG heatmap, and 7/14/30-day TIR summary. Sidebar shows doctor-style pipeline status, original/enriched view toggle, and documented `main.py update` / `fetch --clean` sync steps.

## Commands

- `uv sync --group local` — install Streamlit
- `uv run python main.py dashboard` — launch UI
- `uv run streamlit run apps/local/app.py` — equivalent

## Tests

`tests/test_local_dashboard.py` covers pure helpers (TIR, date windows, empty storage, doctor status).
