# Plan: Storage Protocol + Schema Registry

**Date:** 2026-05-13
**Branch:** `feat/storage-protocol` (new, off main)
**Status:** Design (implementation gated on review)
**Blocks:** detection v2 rework, live alert loop, Tandem→Supabase sync, dashboard reads

## Context

The codebase has two concrete storage backends in play: parquet (current production, on main) and Supabase Postgres (bootstrapped on `feat/supabase-bootstrap`, holds 725K migrated rows). All current callers import directly from `ingestion/storage.py`, which is parquet-only. Every subsequent workstream — detection v2, the live loop, the dashboard, the Tandem nightly sync — needs to read and write data without knowing which backend is in use.

This document specifies a `Storage` Protocol with three implementations (parquet, Supabase, in-memory), a consolidated schema registry that both backends consume, and the migration path for existing callers. It's the foundation for everything downstream.

## Architectural boundary: `core/`

A new top-level `core/` package is introduced. Modules under `core/` are the storage-agnostic library that both deployment shells consume.

**Rules for `core/`:**

- May import from: stdlib, pandas, numpy, pydantic (for typed records), typing/Protocol.
- May NOT import from: `ingestion/`, `scripts/`, `apps/`, `psycopg2`, `supabase-py`, parquet-specific code, Vercel SDK, Streamlit, FastAPI, Telegram libs, LLM clients.
- Backend-specific concrete code (psycopg2 calls, parquet I/O) lives in `core/storage/parquet.py` and `core/storage/supabase.py` respectively. These ARE allowed to import their backend SDKs because they are the only files that do.

**Two deployment shells consume `core/`:**

- **Personal cloud shell** (`apps/web/`, `apps/cron/`, GitHub Actions workflows): Next.js + Vercel + Supabase. Uses `SupabaseStorage`.
- **OSS local shell** (`apps/local/`, future): Streamlit + parquet/SQLite. Uses `ParquetStorage`.

Both shells choose which `Storage` implementation to instantiate at startup and pass it down via constructor injection. Code in `core/` never decides which backend to use.

**Current `ingestion/` becomes a thin shim during migration** (see "Migration plan" below). Eventually `ingestion/` becomes the personal-deployment adapter (tconnectsync) and `apps/local/` gets its own ingestion path (CSV import).

## Schema registry: `core/schema.py`

Single source of truth for table identity. Owns: canonical name, primary key columns, time column (for windowed reads). Does NOT own: column type definitions (parquet infers from pandas dtypes; Postgres types live canonically in `db/migrations/0001_init.sql`).

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class TableSpec:
    name: str
    primary_key: tuple[str, ...]
    time_column: str  # column used for since/until filtering

TABLES: dict[str, TableSpec] = {
    "cgm":         TableSpec("cgm",         ("pump_serial", "seqnum"),           "timestamp"),
    "bolus":       TableSpec("bolus",       ("pump_serial", "bolus_id"),         "timestamp"),
    "requests":    TableSpec("requests",    ("pump_serial", "bolus_id"),         "timestamp"),
    "basal":       TableSpec("basal",       ("pump_serial", "timestamp"),        "timestamp"),
    "suspension":  TableSpec("suspension",  ("pump_serial", "suspend_timestamp"),"suspend_timestamp"),
    "events":      TableSpec("events",      ("pump_serial", "seqnum"),           "timestamp"),
    "alarms":      TableSpec("alarms",      ("pump_serial", "seqnum"),           "timestamp"),
    "site_issues": TableSpec("site_issues", ("pump_serial", "first_occlusion_ts"),"first_occlusion_ts"),
    "cgm_gaps":    TableSpec("cgm_gaps",    ("pump_serial", "start_ts"),         "start_ts"),
}

def get_spec(name: str) -> TableSpec:
    if name not in TABLES:
        raise ValueError(f"unknown table {name!r}; known: {sorted(TABLES)}")
    return TABLES[name]
```

Both `ParquetStorage` and `SupabaseStorage` import from this module. `scripts/bootstrap_supabase.py` is updated to use `core.schema.TABLES` for the table list (its `COLUMN_SPECS` and `CONVERTERS` stay where they are — they're a Postgres-specific concern).

The migration SQL (`db/migrations/0001_init.sql`) remains the canonical Postgres column type definition. Any new column or table is added to BOTH the schema registry (for Python-side discovery) and the migration (for Postgres-side types). A future task may auto-generate one from the other, but not now.

## Storage Protocol: `core/storage/protocol.py`

```python
from datetime import datetime, timedelta
from typing import Protocol

import pandas as pd

from core.storage.records import AlertRecord, FetchState, UpsertResult

class Storage(Protocol):
    """Backend-agnostic data layer.

    Implementations: ParquetStorage (local files), SupabaseStorage
    (Postgres), InMemoryStorage (tests). Callers never reference
    a concrete impl; they accept a Storage and the shell decides.
    """

    # ── data tables ──────────────────────────────────────────────────

    def read_table(
        self,
        name: str,
        *,
        since: datetime,
        until: datetime,
        pump_serial: str | None = None,
    ) -> pd.DataFrame:
        """Read rows from `name` in [since, until) for `pump_serial`.

        Time bounds are REQUIRED to prevent accidental whole-table reads.
        Use `read_all_table` for unbounded reads.
        """

    def read_all_table(self, name: str) -> pd.DataFrame:
        """Read all rows from `name`. Used by batch jobs (bootstrap,
        full-historical calibration sweeps). Explicit by name so it
        isn't reached for accidentally.
        """

    def upsert_table(self, name: str, df: pd.DataFrame) -> UpsertResult:
        """Insert rows, ignoring conflicts on the table's primary key.

        Idempotent: re-inserting identical rows is a no-op. Returns
        per-call counts (rows received, rows inserted, rows skipped).
        """

    def delete_range(
        self,
        name: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        pump_serial: str | None = None,
    ) -> int:
        """Delete rows from `name` whose time_column falls in [since,
        until), optionally scoped to `pump_serial`. Returns rows deleted.

        Used by the Tandem sync to clear pre-window CGM before backfill.
        At least one of `since`/`until`/`pump_serial` is required.
        """

    # ── fetch state (per-source sync bookmarks) ──────────────────────

    def get_fetch_state(self, source_id: str) -> FetchState | None: ...
    def set_fetch_state(self, source_id: str, state: FetchState) -> None: ...
    def list_fetch_state(self) -> list[FetchState]: ...

    # ── pipeline version (schema-drift guard) ────────────────────────

    def get_pipeline_version(self) -> int | None: ...
    def set_pipeline_version(self, version: int) -> None: ...

    # ── alerts (live-path dedup) ─────────────────────────────────────

    def record_alert(self, alert: AlertRecord) -> AlertRecord:
        """Insert an alert record. If `event_ref` is set and an alert
        with the same (alert_kind, event_ref) exists, return the
        existing record without inserting (the partial unique index
        on alerts_sent enforces this at the DB level)."""

    def find_alert(
        self, alert_kind: str, event_ref: str
    ) -> AlertRecord | None: ...

    def recent_alerts(
        self, alert_kind: str, within: timedelta
    ) -> list[AlertRecord]: ...

    # ── detection results (triggered analysis log) ───────────────────

    def record_detection_result(self, result: DetectionResult) -> None: ...
    def list_detection_results(
        self,
        *,
        kind: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[DetectionResult]: ...

    # ── housekeeping ─────────────────────────────────────────────────

    def clean_all(self) -> None:
        """Delete all rows from all tables. Destructive. Used by
        `fetch --clean` and dev workflows."""
```

**Typed records** (`core/storage/records.py`):

Dataclasses for the metadata-row types where DataFrames are overkill. `FetchState` mirrors `fetch_state` table. `AlertRecord` mirrors `alerts_sent`. `DetectionResult` is the minimal record shape (kind, anchor_timestamp, payload_jsonable, created_at). `UpsertResult` carries (rows_received, rows_inserted, rows_skipped, elapsed_seconds) for diagnostics.

DataFrames are used for the nine data tables because every existing caller expects them. Dataclasses are used for the metadata tables because callers work with individual records.

## Three implementations

**`core/storage/parquet.py` — `ParquetStorage`**

Wraps the current `ingestion/storage.py` logic behind the Protocol. `read_table` reads the parquet, applies the `since`/`until` filter in-memory. `upsert_table` does the existing concat → dedup → write. `delete_range` reads → filters out the range → writes back. Fetch state stays in `.fetch_state.json`. Pipeline version stays in `.pipeline_version.json`. Alerts live in a new `alerts_sent.parquet`. Detection results live in `detection_results.parquet`.

**`core/storage/supabase.py` — `SupabaseStorage`**

Uses `psycopg2` (already a dep from the bootstrap). Reuses the converters and column specs from `scripts/bootstrap_supabase.py` (they should move to `core/storage/_postgres_converters.py` and the bootstrap script imports them from there). `read_table` issues a parameterized `SELECT` with WHERE clause on the time column. `upsert_table` uses `execute_values` with the `ON CONFLICT (pk_cols) DO NOTHING` pattern the bootstrap proved out. `delete_range` is a single `DELETE`. Metadata methods are direct SQL against `fetch_state` / `detection_config` / `alerts_sent`.

**`core/storage/memory.py` — `InMemoryStorage`**

Stores DataFrames in a dict. Used by tests so contract tests for the other two implementations can run side-by-side against the same expected behavior. Trivial to implement; might be the smallest of the three.

## Migration plan

Goal: every existing caller continues to work without modification on day one. New code starts on the Protocol from the start.

**Phase 1 — Schema + Protocol + implementations.** Land `core/schema.py`, `core/storage/protocol.py`, `core/storage/records.py`, `core/storage/memory.py`, `core/storage/parquet.py`. Land `core/storage/supabase.py` if the workstream finishing Supabase auth wants to use it; otherwise it lands in Phase 3. Tests at this stage are contract tests against the three impls. No existing caller touched.

**Phase 2 — Shim `ingestion/storage.py`.** Reduce `ingestion/storage.py` to module-level wrappers that delegate to a singleton `ParquetStorage` instance. `save_df(name, df)` becomes `_default_storage().upsert_table(name, df)`. `load_df(name)` becomes a thin wrapper that reads the whole table (using `read_all_table` since these callers want everything). Module constants (`PARQUET_FILES`, `DEDUP_KEYS`, `PROCESSED_DIR`) are re-exported from `core/schema` and `core/storage/parquet` for backward compatibility but marked for removal. All existing callers work unchanged.

**Phase 3 — Migrate new code, not old.** New work (live alert loop, detection v2, Tandem→Supabase sync, dashboard backend) takes a `Storage` via dependency injection from the start. Existing callers (`check`, `viz`, `fetch`) stay on the shim. They migrate organically when someone touches them for a different reason. No big-bang refactor.

## Test strategy

Contract tests in `tests/core/test_storage_contract.py` define expected Protocol behavior. The test class is parameterized over the three implementations. Same test body, three fixtures, three runs:

```python
@pytest.fixture(params=["memory", "parquet", "supabase"])
def storage(request, tmp_path, supabase_test_conn) -> Storage:
    match request.param:
        case "memory":   return InMemoryStorage()
        case "parquet":  return ParquetStorage(root=tmp_path)
        case "supabase": return SupabaseStorage(conn=supabase_test_conn)

def test_upsert_then_read_window(storage):
    storage.upsert_table("cgm", _make_cgm(...))
    result = storage.read_table("cgm", since=..., until=...)
    assert len(result) == expected
    # ... etc
```

Supabase contract tests require a real test database (either a local Postgres container or a Supabase test project). Skipped when env var is missing, run in CI when present. In-memory and parquet tests always run.

## Open questions deferred to implementation

- **psycopg2 vs psycopg3 vs supabase-py.** Bootstrap uses psycopg2-binary. Connection pooling, async support, and the supabase-py SDK's auth integration may matter for the live loop. Defer to the SupabaseStorage implementer; the Protocol is agnostic.
- **Connection lifecycle in serverless.** Vercel functions are short-lived; Supabase connections are not free to open. Defer pooling strategy (PgBouncer in transaction-pooling mode is the likely answer) to the live-loop implementer.
- **Detection result schema.** Generic (kind, anchor, payload_jsonb, created_at) for now. When episodes or patterns demand more structure, add columns or a discriminated table per the user's earlier guidance.
- **CGM delete-and-replace primitive vs higher-level helper.** Protocol exposes the two primitives (`delete_range`, `upsert_table`). The Tandem sync composes them. If the pattern repeats elsewhere, a higher-level helper can move into `core/`.
- **OSS sqlite vs parquet.** ParquetStorage is the day-one OSS impl. Whether the OSS shell later ships a SqliteStorage (better for many small reads) is deferred. The Protocol supports either.

## Sequencing

This plan unblocks:

1. Detection v2 — takes a `Storage`, no parquet knowledge
2. Live alert loop — takes a `Storage`, writes to `alerts_sent`
3. Tandem→Supabase sync — uses `delete_range` + `upsert_table` for the CGM swap
4. Dashboard backend — reads via `read_table` against Supabase

The schema registry and the Protocol module (Phase 1) are the load-bearing piece. The three implementations can land in parallel after Phase 1 is in. Phase 2 (the shim) is small and uncontroversial.