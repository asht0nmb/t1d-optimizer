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

## Pages

| Page | Description |
|------|-------------|
| **Day view** | Multi-panel day chart (CGM, bolus, basal) — same logic as `main.py viz` |
| **Heatmap** | Hour-of-day × date mean BG (up to 90 days) |
| **Time in range** | Rolling 7 / 14 / 30-day TIR vs `bg_targets` in config |

Sidebar: **original** vs **enriched** view mode (same semantics as `check` / `viz`), doctor status (pipeline version + parquet presence), and sync commands.

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
MPLBACKEND=Agg uv run streamlit run apps/local/app.py --server.headless true
```
