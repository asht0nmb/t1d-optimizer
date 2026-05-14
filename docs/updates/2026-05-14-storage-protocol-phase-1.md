# 2026-05-14 — Storage Protocol, schema registry, parquet shim (Phase 1 + 2)

**Branch:** `feat/storage-protocol` (off main; does not touch `feat/supabase-bootstrap`)
**Plan:** [`docs/plans/2026-05-13-storage-protocol-and-schema-registry`](../plans/2026-05-13-storage-protocol-and-schema-registry)

## Summary

Phase 1 (Protocol + schema registry + parquet/in-memory impls + contract
tests) and Phase 2 (`ingestion/storage.py` reduced to a shim over
`ParquetStorage`) of the plan now live on `feat/storage-protocol`.
Every existing caller (`check`, `viz`, `fetch`, `doctor`,
`bootstrap_supabase`) continues to import from `ingestion.storage`
unmodified — the shim re-exports `PARQUET_FILES`, `DEDUP_KEYS`,
`PROCESSED_DIR`, `STATE_FILE`, `PIPELINE_VERSION_FILE` and preserves
the signatures of `save_df`, `load_df`, `load_fetch_state`,
`save_fetch_state`, `write_pipeline_version`, `read_pipeline_version`,
and `clean_all`.

Phase 3 (migrate the existing callers to take a `Storage` via
dependency injection) is intentionally deferred — new code (detection
v2, the live alert loop, the Tandem→Supabase sync, the dashboard
backend) will take a `Storage` from the start; old callers migrate
organically.

## File inventory

New files:

- `core/__init__.py` — package docstring describing the boundary rules.
- `core/schema.py` — `TableSpec` dataclass, `TABLES` registry, `get_spec()`.
- `core/storage/__init__.py`
- `core/storage/protocol.py` — `Storage` Protocol with full docstrings.
- `core/storage/records.py` — `FetchState`, `AlertRecord`,
  `DetectionResult`, `UpsertResult` (`@dataclass(frozen=True)` —
  rationale documented in the module docstring).
- `core/storage/memory.py` — `InMemoryStorage` reference impl.
- `core/storage/parquet.py` — `ParquetStorage` impl; ports the existing
  `ingestion/storage.py` I/O behind the Protocol and adds
  `alerts_sent.parquet` + `detection_results.parquet`.
- `tests/core/__init__.py`
- `tests/core/test_schema.py` — registry + migration-PK consistency.
- `tests/core/test_records.py` — dataclass shapes + frozen guard.
- `tests/core/test_protocol_signatures.py` — method-name smoke test.
- `tests/core/test_storage_contract.py` — Protocol contract suite,
  parameterized over `["memory", "parquet"]` (`"supabase"` added in a
  follow-up).
- `tests/core/test_parquet_storage.py` — disk-specific behaviors
  (sidecar files, on-disk filename layout, `version_guard` interop).
- `tests/core/test_inmemory_storage.py` — minimal memory-specific
  invariants.

Modified files:

- `ingestion/storage.py` — replaced with a thin shim that delegates
  to a per-process cached `ParquetStorage`. Module constants are
  preserved (and remain monkey-patchable for tests).
- `CLAUDE.md` — added the `core/` package-boundary paragraph, updated
  the storage-abstraction paragraph and the test-count line.

No file was deleted. No caller of `ingestion.storage` was edited.

## Contracts that future callers MUST respect

These three contracts come straight out of the Protocol's docstrings
and are the most likely tripwires for new code:

- **`read_table` requires both `since` AND `until`.** Time bounds are
  keyword-only and non-default — implementations raise `TypeError` if
  either is omitted. Callers that genuinely want every row use
  `read_all_table(name)`, which is explicit by name so it isn't
  reached for by accident. This prevents the obvious footgun of an
  unbounded `SELECT *` against `cgm` (700K+ rows on the Supabase side)
  and keeps the parquet impl honest by mirroring that contract.
- **`delete_range` requires at least one scope.** At least one of
  `since` / `until` / `pump_serial` MUST be non-`None`; otherwise
  implementations raise `ValueError`. This prevents the equally
  obvious footgun of a `DELETE` with no `WHERE`. The Tandem
  delete-and-replace flow composes `delete_range(since=…, until=…,
  pump_serial=…)` with `upsert_table(name, df)`.
- **`AlertRecord.sent_at` MUST be tz-aware.** `recent_alerts`
  compares against tz-aware "now", so a naive datetime would
  `TypeError` at compare time. `record_alert` validates at the source
  and raises `ValueError` so the failure surfaces where the caller
  can fix it. (Caught in code review; covered by
  `test_record_alert_rejects_tz_naive_sent_at` for both memory and
  parquet impls.)

## Gate results

Captured verbatim from the runs performed before this update was
written.

### `uv run pytest -q`
```
465 passed, 1 skipped, 47 deselected, 5 warnings in 8.52s
```
(baseline before this PR was 343 passed, 1 skipped, 47 deselected; the
122 new tests live under `tests/core/`.)

### `uv run pytest -m legacy tests/legacy/ -q`
```
47 passed, 1 warning in 1.07s
```

### `uv run pytest tests/core/test_storage_contract.py -q`
```
66 passed in 0.28s
```
(33 tests × 2 parameter values — `"memory"` and `"parquet"`.)

### `uv run python main.py doctor`
```
code pipeline version: v3
  └─ Decode `egvTimestamp` as `int` seconds since `TANDEM_EPOCH` ...
on-disk pipeline version: v3

processed parquet tables present: 9/9
  - cgm
  - bolus
  - requests
  - basal
  - suspension
  - events
  - alarms
  - site_issues
  - cgm_gaps

pipeline state: OK
```

### `uv run python main.py check --date 2026-04-14 --view enriched`
Runs to completion (exit 0), prints enriched sections including
`Bolus categories`, `Site issues overlapping day`, and
`CGM gaps overlapping day`. Exercises `load_df` through the shim.

### `uv run python main.py viz --date 2026-04-14 --view enriched`
Runs to completion (exit 0 under `MPLBACKEND=Agg`; under the default
interactive backend it builds the figure and blocks on `plt.show()` as
before). Exercises `load_df` + the enrichment view-mode helper.

### `uv run python -c "from core.storage.protocol import Storage; from core.storage.memory import InMemoryStorage; from core.storage.parquet import ParquetStorage; from core.schema import TABLES, get_spec; print(sorted(TABLES))"`
```
['alarms', 'basal', 'bolus', 'cgm', 'cgm_gaps', 'events', 'requests', 'site_issues', 'suspension']
```

### `uv run python -c "from ingestion.storage import save_df, load_df, PARQUET_FILES, DEDUP_KEYS, PROCESSED_DIR; print(sorted(PARQUET_FILES))"`
```
['alarms', 'basal', 'bolus', 'cgm', 'cgm_gaps', 'events', 'requests', 'site_issues', 'suspension']
```

### Diff grep for forbidden imports
`rg 'import psycopg2|from psycopg2|import supabase|from supabase'`
under `core/` returns no matches. `rg '\.to_parquet'` confirms every
parquet write lives inside `core/storage/parquet.py`. The
`bootstrap_supabase` script still imports psycopg2 as expected (out of
scope; it's not under `core/`).

## Deferred to follow-up PRs

- `core/storage/supabase.py` (and the matching `"supabase"` fixture
  branch in `tests/core/test_storage_contract.py`). Lands on top of
  `feat/supabase-bootstrap`; the bootstrap script's `CONVERTERS` and
  `COLUMN_SPECS` migrate to `core/storage/_postgres_converters.py` in
  that same PR (where they get their first real consumer).
- The `apps/` top-level directory (mentioned in the plan as a future
  concept; not created here).
- Phase 3: migrating existing callers to take a `Storage` via DI.
- Detection v2, live alert loop, Tandem nightly sync, dashboard
  backend — they all consume `Storage`, but none of them is built
  here.
