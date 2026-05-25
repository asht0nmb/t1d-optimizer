# Tandem Nightly Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Scheduled incremental Tandem ingestion into Supabase with enrichment applied before every database write.

**Architecture:** Reuse `ingestion.client` + `build_all(..., load_config())` (same path as `ingestion/fetch.py`); persist via `SupabaseStorage(conn=...)` and direct `SUPABASE_DB_URL`. Fetch bookmarks live in Postgres `fetch_state` (one row per pump serial, `source_kind = tconnectsync`).

**Tech Stack:** Python 3.12, uv, psycopg2, tconnectsync, GitHub Actions.

---

### Task 1: Unit tests (RED → GREEN)

**Files:**
- Create: `tests/test_sync_tandem_to_supabase.py`
- Create: `scripts/sync_tandem_to_supabase.py`

- [x] Assert `build_all` receives non-`None` config
- [x] Assert `upsert_table` for `requests` includes `bolus_category`
- [x] Assert `site_issues` / `cgm_gaps` upserts when enrichment returns them
- [x] `--dry-run` skips upsert / fetch_state / pipeline version
- [x] Incremental window: full range vs overlap-one-day from `fetch_state.payload`

### Task 2: Sync script

- [x] `scripts/sync_tandem_to_supabase.py` with `--dry-run`, `--only`, `--verbose`
- [x] `compute_fetch_window` mirrors `ingestion/fetch.py`
- [x] Upsert all nine `core.schema.TABLES` keys when non-empty
- [x] `set_fetch_state` + `set_pipeline_version` after success

### Task 3: GitHub Actions

- [x] New `.github/workflows/tandem-nightly-sync.yml` (cron `0 6 * * *` UTC + `workflow_dispatch`)
- [x] Preserve `test-tandem-sync.yml` as manual smoke only (no schedule, no Supabase)

### Task 4: Docs

- [x] `docs/updates/2026-05-17-tandem-nightly-sync.md` — secrets, smoke vs sync, enrichment contract
