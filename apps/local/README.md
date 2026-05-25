# T1D Engine — local (OSS) shell

Streamlit dashboard over local parquet files via `ParquetStorage`. No Supabase, no auth, no Telegram — intended for self-hosted use with your own Tandem credentials.

**Not medical advice.** This software does not provide treatment recommendations. Do not change pump settings or insulin dosing based on dashboard output.

## Quickstart

```bash
# From repo root
uv sync --group local

# Edit thresholds (meal windows, BG targets, etc.)
# config/user_config.yaml

# Fetch data (requires tconnectsync credentials in your environment — never commit secrets)
uv run python main.py fetch
# or incremental:
uv run python main.py update

# Launch dashboard
uv run python main.py dashboard
# equivalent:
uv run streamlit run apps/local/app.py
```

## Pages (interactive Plotly)

| Page | Description |
|------|-------------|
| **Day view** | Three linked panels (CGM, bolus/events, basal). Hover any marker for details; zoom/pan with scroll or toolbar. Prev/Next day, date picker, and jump slider for days with CGM data. |
| **Heatmap** | Hour × date mean BG (up to 90 days). Hover cells for mean BG and reading count; click a cell to jump to that day in Day view. |
| **Time in range** | 7 / 14 / 30-day TIR metrics plus a daily TIR trend line. Click a point to open that day in Day view. |

Sidebar: **original** vs **enriched** view mode (same semantics as `check` / `viz`), doctor status (pipeline version + parquet presence), and sync commands.

CLI `uv run python main.py viz` still uses static matplotlib; only this dashboard uses Plotly.

## Data layout

Reads `data/processed/*.parquet` through `ParquetStorage(root=Path("data/processed"))`. Run `uv run python main.py doctor` for a CLI health check.

## Relationship to personal cloud dashboard

| | `apps/local/` (OSS) | `apps/web/` (personal) |
|--|---------------------|------------------------|
| Storage | Local parquet | Supabase Postgres |
| Auth | None | Supabase Auth + RLS |
| Sync | Manual `main.py fetch` / `update` | GitHub Actions nightly sync |
| UI | Streamlit | Next.js |

Both shells consume the same `core/` library and normalized table shapes; only the storage backend and deployment differ.

## Development

```bash
uv run pytest tests/test_local_dashboard.py -q
uv run python main.py dashboard
```

### Manual checklist

- [ ] Hover CGM point (BG, range band, backfilled flag)
- [ ] Hover bolus diamond (units, carbs, category in enriched view)
- [ ] Hover basal step and suspension span
- [ ] Zoom a post-meal window; double-click chart to reset axes
- [ ] Prev / Next buttons and date picker move days consistently
- [ ] Heatmap cell hover; click cell → Day view; fallback "Jump to date" picker works
- [ ] TIR trend point click → Day view; 70 % goal band visible
- [ ] Toggle original vs enriched (CGM gaps, bolus categories, site bands)
- [ ] No `use_container_width` deprecation warnings in terminal
