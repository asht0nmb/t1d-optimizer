"""One-time bootstrap of historical T1D parquet data into Supabase Postgres.

Usage:
    uv run python scripts/bootstrap_supabase.py [--dry-run] [--only TABLE,...]
                                                 [--batch-size N]

Connection model
----------------
This script uses Supabase's *direct* Postgres connection (port 5432, host
``db.<project>.supabase.co``), NOT the connection pooler (port 6543).

The bulk-insert path is ``psycopg2.extras.execute_values``, which builds a
single very large multi-row INSERT statement per chunk. PgBouncer in
transaction-pool mode (port 6543) imposes statement-size and prepared-
statement constraints that fight that pattern; the direct connection
holds one session for the whole run, which is exactly what a one-shot
historical load wants.

Idempotency
-----------
Each INSERT uses ``ON CONFLICT (<pk_cols>) DO NOTHING`` so re-running the
script after a partial failure is safe. ``cur.rowcount`` after each
``execute_values`` call reports the rows actually inserted; the rest are
conflict-skipped.

Modes
-----
* default:    load all 9 historical tables.
* --dry-run:  print parquet row counts and exit. Never opens a DB
              connection (so it works before psycopg2 is installed).
* --only T,T: process only the comma-separated subset.
* --batch-size N: ``execute_values`` page size (default 5000).

Tables
------
Touches the 9 historical tables (cgm, bolus, requests, basal,
suspension, events, alarms, site_issues, cgm_gaps). The 3 new tables
(alerts_sent, fetch_state, detection_config) are created by migration
0001 but intentionally left empty by the bootstrap.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

# Make ``import ingestion.storage`` work when this script is executed
# directly (``uv run python scripts/bootstrap_supabase.py``); the project
# is not installed as a package, so the repo root must be on sys.path.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

try:  # psycopg2 is not needed for --dry-run, so the import is best-effort.
    import psycopg2  # type: ignore[import-not-found]
    from psycopg2.extras import execute_values  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised before psycopg2 is installed
    psycopg2 = None  # type: ignore[assignment]
    execute_values = None  # type: ignore[assignment]

from core.storage._postgres_converters import COLUMN_SPECS, CONVERTERS
from ingestion.storage import PARQUET_FILES, PROCESSED_DIR

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 5000

# (table_name, primary-key columns) — must mirror db/migrations/0001_init.sql.
# Kept here (rather than imported from ``core.schema.TABLES``) because the
# bootstrap pre-dates the schema registry; convergence is a follow-up.
TABLE_SPECS: list[tuple[str, list[str]]] = [
    ("cgm",         ["pump_serial", "seqnum"]),
    ("bolus",       ["pump_serial", "bolus_id"]),
    ("requests",    ["pump_serial", "bolus_id"]),
    ("basal",       ["pump_serial", "timestamp"]),
    ("suspension",  ["pump_serial", "suspend_timestamp"]),
    ("events",      ["pump_serial", "seqnum"]),
    ("alarms",      ["pump_serial", "seqnum"]),
    ("site_issues", ["pump_serial", "first_occlusion_ts"]),
    ("cgm_gaps",    ["pump_serial", "start_ts"]),
]


# ── parquet I/O ──────────────────────────────────────────────────────────────

def load_parquet(name: str) -> pd.DataFrame:
    """Read ``data/processed/<name>.parquet`` into a DataFrame."""
    path = PROCESSED_DIR / PARQUET_FILES[name]
    if not path.exists():
        raise FileNotFoundError(
            f"missing parquet for table {name!r}: {path} "
            f"(run `uv run python main.py fetch` first)"
        )
    return pd.read_parquet(path)


def _df_to_rows(name: str, df: pd.DataFrame) -> list[tuple]:
    """Apply the per-table converter to every record in ``df``."""
    convert = CONVERTERS[name]
    return [convert(rec) for rec in df.to_dict(orient="records")]


# ── per-table insert ─────────────────────────────────────────────────────────

def insert_table(
    conn: Any,
    name: str,
    df: pd.DataFrame,
    batch_size: int,
) -> tuple[int, int, int, float]:
    """Bulk-INSERT ``df`` rows into table ``name``. Commits on success.

    Caller owns rollback / continue-on-error policy. Returns
    ``(parquet_rows, inserted, skipped, elapsed_seconds)``.
    """
    if execute_values is None:
        raise RuntimeError(
            "psycopg2 is required to insert into Supabase. "
            "Install via `uv add psycopg2-binary` (Task 3) before a real run."
        )

    pk_cols = dict(TABLE_SPECS)[name]
    cols = COLUMN_SPECS[name]
    sql = (
        f'INSERT INTO {name} ({", ".join(cols)}) VALUES %s '
        f'ON CONFLICT ({", ".join(pk_cols)}) DO NOTHING'
    )

    parquet_rows = len(df)
    started = time.perf_counter()
    rows = _df_to_rows(name, df)

    inserted = 0
    with conn.cursor() as cur:
        # Chunk in the outer loop so that each call to ``execute_values``
        # issues exactly one INSERT statement and ``cur.rowcount`` is the
        # correct per-chunk inserted count (it would only reflect the
        # *last* statement if execute_values handled the chunking itself).
        #
        # Commit per chunk per Supabase short-transactions guidance: every
        # chunk is durable, network blip / idle_in_transaction_session_timeout
        # can't roll back the whole table, and ON CONFLICT DO NOTHING makes
        # re-runs cheap.
        for start in range(0, len(rows), batch_size):
            chunk = rows[start:start + batch_size]
            execute_values(cur, sql, chunk, page_size=batch_size)
            inserted += cur.rowcount
            conn.commit()

    elapsed = time.perf_counter() - started
    skipped = parquet_rows - inserted
    return parquet_rows, inserted, skipped, elapsed


def verify_inserted(conn: Any, name: str) -> int:
    """Return ``SELECT count(*)`` for ``name`` (used by the post-run report)."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {name}")
        (n,) = cur.fetchone()
    return int(n)


# ── CLI orchestration ────────────────────────────────────────────────────────

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bulk-load processed parquet data into Supabase Postgres."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the parquet row count for each selected table and exit. "
            "Does NOT open a DB connection (works without psycopg2 installed)."
        ),
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated subset of tables to process (e.g. cgm,bolus).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"execute_values page size (default {DEFAULT_BATCH_SIZE}).",
    )
    return parser.parse_args(argv)


def _resolve_tables(only: str | None) -> list[str]:
    all_names = [name for name, _ in TABLE_SPECS]
    if not only:
        return all_names
    selected = [s.strip() for s in only.split(",") if s.strip()]
    unknown = [s for s in selected if s not in all_names]
    if unknown:
        raise SystemExit(
            f"unknown table(s) in --only: {', '.join(unknown)}; "
            f"valid choices: {', '.join(all_names)}"
        )
    return selected


def _print_report(
    rows: list[tuple[str, int, int | None, int | None, float | None]],
    *,
    dry_run: bool,
) -> None:
    """Render the per-table report to stdout, padded to even columns."""
    if dry_run:
        formatted = [("table", "parquet_rows")]
        formatted.extend((name, f"{parquet_rows}") for name, parquet_rows, *_ in rows)
    else:
        formatted = [("table", "parquet_rows", "inserted", "skipped", "elapsed")]
        for name, parquet_rows, inserted, skipped, elapsed in rows:
            formatted.append((
                name,
                f"{parquet_rows}",
                "-" if inserted is None else f"{inserted}",
                "-" if skipped is None else f"{skipped}",
                "-" if elapsed is None else f"{elapsed:.1f}s",
            ))
        if rows and all(r[2] is not None for r in rows):
            total_parquet = sum(r[1] for r in rows)
            total_inserted = sum(r[2] for r in rows)  # type: ignore[misc]
            total_skipped = sum(r[3] for r in rows)  # type: ignore[misc]
            total_elapsed = sum(r[4] for r in rows)  # type: ignore[misc]
            formatted.append((
                "TOTAL",
                f"{total_parquet}",
                f"{total_inserted}",
                f"{total_skipped}",
                f"{total_elapsed:.1f}s",
            ))

    widths = [max(len(row[i]) for row in formatted) for i in range(len(formatted[0]))]
    for row in formatted:
        print("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    if args.batch_size <= 0:
        print("error: --batch-size must be positive", file=sys.stderr)
        return 2

    tables = _resolve_tables(args.only)

    if args.dry_run:
        report: list[tuple[str, int, int | None, int | None, float | None]] = []
        for name in tables:
            df = load_parquet(name)
            report.append((name, len(df), None, None, None))
            logger.info("[dry-run] %s: %d row(s)", name, len(df))
        _print_report(report, dry_run=True)
        return 0

    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        print(
            "error: SUPABASE_DB_URL is not set. Use the Supabase 'Direct connection' "
            "string (port 5432, host db.<project>.supabase.co), not the pooler.",
            file=sys.stderr,
        )
        return 2

    if psycopg2 is None:
        print(
            "error: psycopg2 is not installed. Run `uv add psycopg2-binary` "
            "(Task 3) before a real bootstrap.",
            file=sys.stderr,
        )
        return 2

    logger.info("connecting to Supabase Postgres (direct, port 5432)")
    conn = psycopg2.connect(db_url, connect_timeout=10)

    failed: list[tuple[str, Exception]] = []
    report = []
    interrupted = False
    try:
        for name in tables:
            try:
                df = load_parquet(name)
                logger.info("loading %s: %d parquet row(s)", name, len(df))
                stats = insert_table(conn, name, df, args.batch_size)
                report.append((name, *stats))
                parquet_rows, inserted, skipped, elapsed = stats
                logger.info(
                    "%s: parquet_rows=%d inserted=%d skipped=%d elapsed=%.2fs",
                    name, parquet_rows, inserted, skipped, elapsed,
                )
            except Exception as exc:  # noqa: BLE001 — we explicitly want to keep going
                conn.rollback()
                logger.exception("failed to load %s: %s", name, exc)
                failed.append((name, exc))
    except KeyboardInterrupt:
        logger.warning(
            "Interrupted by user; partial progress on this table is rolled back, "
            "prior tables stay committed."
        )
        conn.rollback()
        interrupted = True
    finally:
        conn.close()

    _print_report(report, dry_run=False)

    if interrupted:
        return 130

    if failed:
        names = ", ".join(name for name, _ in failed)
        print(f"\nFAILED tables: {names}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
