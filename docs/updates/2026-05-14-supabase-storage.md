# 2026-05-14 — SupabaseStorage: third Storage Protocol implementation

## Summary

Lands the third concrete `Storage` Protocol implementation:
`core.storage.supabase.SupabaseStorage`. Postgres-backed, talks via
psycopg2, validated against the same parameterized contract test suite
that already covers `ParquetStorage` and `InMemoryStorage`. Existing
callers are not migrated — they continue going through
`ingestion.storage` → `ParquetStorage`. The live alert loop, the Tandem
nightly sync, the dashboard backend, and Telegram handlers are now
independent follow-up workstreams; each can take a `SupabaseStorage`
via DI from day one.

This implements `docs/plans/2026-05-14-supabase-storage.md` end-to-end
(all five tasks).

## File inventory

**Created**

- `core/storage/_postgres_converters.py` — pandas-row → Postgres-tuple
  converters extracted from the bootstrap script. Leading underscore
  marks it as a Postgres-impl-only module within `core/storage/`.
- `core/storage/supabase.py` — `SupabaseStorage` class. Every Protocol
  method implemented in raw psycopg2.
- `db/migrations/0002_supabase_storage_setup.sql` — idle-in-transaction
  backstop + `detection_results` table.
- `tests/core/test_supabase_storage.py` — Postgres-only behaviors
  (partial unique index, connection ownership, identity reset on
  `clean_all`, defensive prod-host denylist). Skipped when
  `SUPABASE_TEST_URL` is missing.

**Modified**

- `core/storage/records.py` — `AlertRecord` gains `pump_serial: str |
  None = None` and `delivery: str = "pending"`; `FetchState` gains
  `source_kind: str = "unknown"`. All additive with defaults — no caller
  change required.
- `core/storage/parquet.py` — extends `_ALERT_COLS` with the new
  columns; `_load_alerts` defaults missing columns when reading older
  parquets; `_write_alerts` writes them; `_decode_state` / `_encode_state`
  round-trip `source_kind`. `record_alert` preserves the new fields.
- `core/storage/memory.py` — `record_alert` preserves the new fields.
- `core/storage/__init__.py` — public surface includes
  `SupabaseStorage` (+ the existing `Storage`, `ParquetStorage`,
  `InMemoryStorage`, and the four record dataclasses).
- `core/storage/protocol.py` — docstring updated to reflect that
  `SupabaseStorage` exists (no longer "implementation pending").
- `scripts/bootstrap_supabase.py` — imports `COLUMN_SPECS` and
  `CONVERTERS` from `core.storage._postgres_converters` instead of
  declaring them inline. Behavior unchanged.
- `tests/test_bootstrap_supabase.py` — import paths only; the 78 tests
  still pass against the moved code.
- `tests/core/test_records.py` — 6 new tests covering the additive
  field defaults.
- `tests/core/test_parquet_storage.py` — 3 new tests for the
  parquet-side round-trip of the additive fields.
- `tests/core/test_storage_contract.py` — adds `"supabase"` to the
  fixture parameterization. Skips when `SUPABASE_TEST_URL` is missing;
  refuses to run when the URL matches the production-host denylist.
- `CLAUDE.md` — drops the "pending" language on `SupabaseStorage`;
  updates the test-count line; keeps the connection management
  paragraph that the predecessor PR added.

## Migration application procedure

`db/migrations/0002_supabase_storage_setup.sql` is idempotent
(`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`,
`ALTER DATABASE ... SET ...`). Apply manually against the Supabase
project's **direct** connection (NOT the pooler), the same way
migration 0001 was applied:

```bash
psql "$SUPABASE_DB_URL" -f db/migrations/0002_supabase_storage_setup.sql
```

Where `SUPABASE_DB_URL` is the Direct connection string (port 5432,
host `db.<project>.supabase.co`). This is a one-shot manual step after
the PR merges. The agent does not apply this — no DB credentials in
the sandbox.

The `ALTER DATABASE postgres SET idle_in_transaction_session_timeout`
only takes effect on **new sessions**, so existing pooler-held sessions
pick up the new default the next time they are re-issued. No
intervention needed.

## Dedicated test-project setup

`SupabaseStorage` contract tests run against a dedicated Supabase
project, not the main one. To run them locally:

1. Create a second Supabase project (call it e.g.
   `t1d-engine-test`). Free tier is enough.
2. Apply `db/migrations/0001_init.sql` and
   `db/migrations/0002_supabase_storage_setup.sql` against it (one-shot).
3. Set `SUPABASE_TEST_URL` to the **transaction-mode pooler** URL of
   that project (port 6543, host
   `aws-0-<region>.pooler.supabase.com`). The contract fixture uses
   the pooler URL because that's how production callers connect, and
   exercising the pooler path is what catches incompatibilities early.
4. Run `uv run pytest tests/core/test_storage_contract.py
   tests/core/test_supabase_storage.py -q`. The fixture calls
   `clean_all()` before and after each test so runs are isolated.

The fixture refuses to run when the host matches
`tests.core.test_storage_contract._PROD_HOST_PATTERNS`. Add the
production project's hostname pattern to that tuple as soon as it's
known — the test that exercises the refusal is parameterized and will
pick up the new entry automatically.

## Connection management rules (pinned)

- **Pooler URL** (`*.pooler.supabase.com:6543`, transaction mode) for
  short-lived callers: Vercel cron functions, Telegram webhook handlers,
  dashboard API routes. Use the `SupabaseStorage.from_pooler_url(url)`
  classmethod or `with SupabaseStorage.from_pooler_url(url) as storage:`.
  The instance owns the conn and closes it on exit.
- **Direct URL** (`db.<project>.supabase.co:5432`) for long-lived
  callers: the GitHub Actions nightly Tandem sync, the one-shot
  `scripts/bootstrap_supabase.py`. Caller opens the conn, passes it
  via `SupabaseStorage(conn=existing_conn)`; the instance does NOT
  close it on context exit.
- `idle_in_transaction_session_timeout = '5min'` on the Postgres
  cluster is the belt-and-suspenders backstop — any leaked transaction
  is reclaimed automatically.

## Design decisions (pinned for this PR)

### `AlertRecord` and `FetchState` get additive fields

Rather than smuggling `pump_serial` / `delivery` / `source_kind`
through the `payload` / `meta` blobs:

- `AlertRecord.pump_serial: str | None = None` — nullable in Postgres,
  so `None` is a valid persistent value (not just "unset").
- `AlertRecord.delivery: str = "pending"` — mirrors the Postgres
  column default.
- `FetchState.source_kind: str = "unknown"` — Postgres column is NOT
  NULL; `"unknown"` is a transitional default for callers that haven't
  been updated. Connectors (`tconnectsync`, `pydexcom`) populate the
  real value at fetch time.

Defaults make every callsite continue to work unchanged; the parquet
backend defaults missing columns on read for older sidecars.

### `clean_all()` does NOT take a `confirm=True` argument

The Protocol declares `clean_all() -> None`; `ParquetStorage` and
`InMemoryStorage` follow that signature. `SupabaseStorage` matches.
The plan flagged this as an open question; the resolution is "match
the Protocol; don't diverge". Caller-side guardrails (CLI
confirmation prompts, etc.) live in the shells, not in the storage
layer.

### Pipeline version is a row in `detection_config`

Stored under `key = 'pipeline_version'` with the integer value JSON-
encoded into the `jsonb` `value` column. `get` decodes; `set` does
`INSERT ... ON CONFLICT (key) DO UPDATE`.

### `AlertRecord.id` stays typed as `str | None`

The bigserial value comes back via `RETURNING id`; `SupabaseStorage`
casts the int to str at the boundary so the dataclass shape is
uniform across backends (parquet uses `uuid4().hex`).

## Self-review and code-review fixes applied

Before opening the PR, the implementation was self-reviewed and run
through `requesting-code-review`. The fixes folded in below the green
test suite:

- **Test fixture refuses to run when prod denylist is empty.** Both
  `tests/core/test_storage_contract.py` and
  `tests/core/test_supabase_storage.py` now `pytest.fail(...)` when
  `SUPABASE_TEST_URL` is set but `_PROD_HOST_PATTERNS` is the empty
  tuple. Stops the defensive guardrail from silently no-op'ing if a
  future caller forgets to populate the denylist.
- **`record_alert` race-tolerant fallback.** The "ON CONFLICT DO
  NOTHING + RETURNING is empty → fetch the existing row" path now
  retries 3× with a 10ms backoff before raising `RuntimeError`, so a
  parallel writer that's still committing when our `find_alert` runs
  doesn't trip the contract. The assert that runs before the
  fallback documents the precondition (`alert.event_ref` is non-null
  whenever the conflict branch is reachable, by the partial-index
  shape).
- **`read_table` / `read_all_table` preserve columns on empty
  results.** Both methods capture `cur.description` and pass the
  column list through to `_rows_to_dataframe(rows, columns=...)`, so
  callers that key into a column off an empty window don't trip a
  `KeyError`.
- **JSONB columns re-encoded to JSON strings on `read_*`.** psycopg2
  decodes `jsonb` to native Python `dict`/`list`; parquet stores them
  as JSON-encoded strings. `_normalize_value` now re-encodes
  `dict`/`list` → `json.dumps(...)` so both backends return the same
  shape from `read_table` / `read_all_table`. Methods that consume
  payload dicts directly (`record_alert`, `list_detection_results`,
  `_row_to_fetch_state`) are unaffected because they decode before
  normalisation runs.
- **`FetchState.payload['last_cursor']` collision is rejected.**
  `set_fetch_state` now raises `ValueError` when the payload contains
  the reserved `last_cursor` key, rather than silently overwriting it
  on the meta round-trip.
- **`source_kind` empty string preserved.** `_row_to_fetch_state` now
  defaults only when the column is `NULL` (it can't be in Postgres,
  but the guard matches the parquet-side decode contract). Empty
  strings round-trip verbatim.
- **`list_fetch_state` adds a deterministic tie-break.** `ORDER BY
  updated_at ASC, source_id ASC` so seed rows inserted in a single
  transaction land in a stable order across calls.
- **`SupabaseStorage(conn=None)` raises `ValueError`.** Friendlier
  error than the `AttributeError` you'd otherwise get on the first
  `self._conn.cursor()` call. The error message points at
  `from_pooler_url`.
- **`__del__` finalizer closes leaked owned connections.** Belt-and-
  suspenders for instances that own their conn but never reach
  `close()` / `__exit__` (e.g. exception inside a Vercel handler). The
  finalizer no-ops on caller-managed connections and swallows
  exceptions per `__del__` discipline.
- **`_alert_columns_sql()` → `_ALERT_COLUMNS_SQL` constant.** Four
  call sites referenced the same column list; lifted to a module-
  level constant so they stay in lockstep with the table schema.
- **`_require_tz_aware` checks on `read_table` / `delete_range`.**
  Matches the parquet/memory contract; surfaces naive-datetime bugs
  at the call boundary rather than silently treating them as UTC.
- **`numeric → float` precision trade-off documented.** Module
  docstring explains the `Decimal` → `float` cast in
  `_normalize_value`; the `numeric(6,3)` columns in the current schema
  round-trip cleanly through float64, but a future wider-precision
  column should drop the cast.
- **Hardcoded `postgres` database name in migration 0002
  documented.** A header comment in
  `db/migrations/0002_supabase_storage_setup.sql` explains the
  literal name and what to do if a future Supabase project renames
  the default database.

## Gate-command outputs

`uv run pytest -q`:

```
477 passed, 41 skipped, 47 deselected, 5 warnings in 13.32s
```

(34 of the 41 skips are the supabase parameterizations of
`test_storage_contract.py` and the body of `test_supabase_storage.py`
when `SUPABASE_TEST_URL` is missing. The remaining 7 are pre-existing
skips unaffected by this PR.)

`uv run pytest -m legacy tests/legacy/ -q`:

```
47 passed, 1 warning in 0.94s
```

`uv run pytest tests/test_bootstrap_supabase.py -q`:

```
78 passed, 1 warning in 0.42s
```

`uv run python main.py doctor`:

```
code pipeline version: v3
on-disk pipeline version: v3
processed parquet tables present: 9/9
pipeline state: OK
```

`uv run python main.py check --date 2026-04-14 --view enriched`:
runs without error; enrichment overlays land verbatim against the
parquet shim.

`MPLBACKEND=Agg uv run python main.py viz --date 2026-04-14 --view enriched`:
runs without error (the only warning is the expected `FigureCanvasAgg
is non-interactive` from `plt.show()` under Agg, which is a sandbox
quirk and not a code regression).

`uv run python -c "from core.storage.supabase import SupabaseStorage;
from core.storage._postgres_converters import CONVERTERS, COLUMN_SPECS;
print(sorted(CONVERTERS))"`:

```
['alarms', 'basal', 'bolus', 'cgm', 'cgm_gaps', 'events', 'requests', 'site_issues', 'suspension']
```

`uv run python -c "from core.storage.records import AlertRecord,
FetchState; print(list(AlertRecord.__dataclass_fields__.keys()));
print(list(FetchState.__dataclass_fields__.keys()))"`:

```
['id', 'alert_kind', 'event_ref', 'sent_at', 'payload', 'pump_serial', 'delivery']
['source_id', 'last_cursor', 'last_fetched_at', 'payload', 'source_kind']
```

`SUPABASE_TEST_URL=... uv run pytest tests/core/test_storage_contract.py
tests/core/test_supabase_storage.py -q`: **not exercised in this PR's
sandbox** — no Supabase test project credentials in the agent
environment. Run by the human against a dedicated test project after
the PR is opened.

`uv run python scripts/bootstrap_supabase.py --batch-size 5000`
(idempotency check against the production project already at 725K
rows): **not exercised in this PR's sandbox** — same reason. The
converter migration in Task 1 didn't change behavior (78 unit tests
pass), but the live idempotency check is the load-bearing acceptance
criterion; please run it post-merge and confirm "0 inserted, ~725000
skipped per table".

## What this unblocks

All four are now independent, parallelisable workstreams:

1. **Live alert loop** (Vercel cron). Takes a
   `SupabaseStorage.from_pooler_url(...)`; calls
   `record_alert` / `find_alert` / `recent_alerts` against
   `alerts_sent` with the partial unique index already wired.
2. **Tandem nightly sync** (GitHub Action). Replaces the bootstrap's
   inline INSERT logic with `SupabaseStorage.upsert_table(...)`
   invocations through a direct (`db.<project>.supabase.co:5432`)
   connection.
3. **Dashboard backend** (Next.js API routes). Reads via
   `SupabaseStorage.read_table(...)`; aggregation methods (heatmap, TIR
   trends) land as SupabaseStorage-only additions per the plan's
   "Postgres-first aggregation contract".
4. **Telegram webhook handlers**. Compose `read_table(...)` calls to
   assemble context for DeepSeek prompts.

The detection v2 rebuild is independent of all four — it operates on
DataFrames in / DataFrames out, source-agnostic; whichever
implementation ends up calling it doesn't change its surface.
