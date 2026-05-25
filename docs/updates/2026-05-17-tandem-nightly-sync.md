# 2026-05-17 — Tandem nightly sync to Supabase

## What shipped

- **`scripts/sync_tandem_to_supabase.py`** — incremental Tandem → Postgres sync using `SupabaseStorage` and the same enriched build path as `main.py update` (`load_config()` + `build_all(..., config)`).
- **`.github/workflows/tandem-nightly-sync.yml`** — daily 06:00 UTC cron + manual `workflow_dispatch`.
- **`tests/test_sync_tandem_to_supabase.py`** — proves enrichment before upsert (e.g. `bolus_category` on `requests`, `site_issues` / `cgm_gaps` tables).

`scripts/bootstrap_supabase.py` remains the one-shot historical parquet load. Ongoing sync is the new script.

## Smoke vs sync

| Workflow | Script | Enrichment | Persistence |
|----------|--------|------------|-------------|
| `test-tandem-sync.yml` (manual) | `ci_tandem_smoke` | No (`config=None`) | None |
| `tandem-nightly-sync.yml` (scheduled) | `sync_tandem_to_supabase` | Yes (all nine tables) | Supabase upsert + `fetch_state` |

## Enrichment contract (Postgres)

Every row written by the nightly job is enriched **before** `upsert_table`:

- **requests:** `bolus_category`, `override_delta`
- **events:** `forced_by_alarm` (site-change rows)
- **site_issues**, **cgm_gaps:** derived tables from `enrich_all`

The dashboard and future detection can rely on these columns in Postgres without client-side backfill.

## GitHub secrets

Configure under **Settings → Secrets and variables → Actions**:

| Secret | Purpose |
|--------|---------|
| `TCONNECT_EMAIL` | Tandem source API |
| `TCONNECT_PASSWORD` | Tandem source API |
| `SUPABASE_DB_URL` | **Direct** Postgres URL (`db.<project>.supabase.co:5432`), not the pooler |

Also set `CACHE_CREDENTIALS=false` in the workflow (already wired).

## First run

1. Merge PR (human review required).
2. Trigger **Tandem Nightly Sync** via **Actions → workflow_dispatch** once before relying on cron.
3. Optionally validate locally: `uv run python scripts/sync_tandem_to_supabase.py --dry-run` (needs creds in `.env`).

## Verification gates (2026-05-17)

| Gate | Result |
|------|--------|
| `uv run pytest -q` | **497 passed**, 42 skipped, 47 deselected; **1 failed** — pre-existing `tests/test_local_dashboard.py::test_date_window_bounds` (off-by-one date; unrelated to sync) |
| `uv run pytest tests/test_sync_tandem_to_supabase.py -q` | **10 passed** |
| `uv run python -c "import scripts.sync_tandem_to_supabase"` | OK |

Manual (credentials + network; not run green in this session):

```text
uv run python -m scripts.ci_tandem_smoke
uv run python scripts/sync_tandem_to_supabase.py --dry-run
```

Note: `--dry-run` does not open Postgres, so fetch windows always use the pump’s full `minDate` range (not incremental bookmarks). Nightly non-dry-run uses `fetch_state` from Supabase.

Known sync semantics (inherited from bootstrap / `ON CONFLICT DO NOTHING`): overlapping re-fetches do not update existing PK rows; enrichment backfill requires a deliberate re-bootstrap or a future upsert policy change.
