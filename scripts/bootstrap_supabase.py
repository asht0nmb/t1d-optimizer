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
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping

# Make ``import ingestion.storage`` work when this script is executed
# directly (``uv run python scripts/bootstrap_supabase.py``); the project
# is not installed as a package, so the repo root must be on sys.path.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

try:  # psycopg2 is not needed for --dry-run, so the import is best-effort.
    import psycopg2  # type: ignore[import-not-found]
    from psycopg2.extras import Json, execute_values  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised before Task 3 installs the dep
    psycopg2 = None  # type: ignore[assignment]
    Json = None  # type: ignore[assignment]
    execute_values = None  # type: ignore[assignment]

from ingestion.storage import PARQUET_FILES, PROCESSED_DIR

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 5000

# (table_name, primary-key columns) — must mirror db/migrations/0001_init.sql.
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

# Per-table column order used by both the INSERT statement and the row
# converters. Order MUST match the converter tuples below.
COLUMN_SPECS: dict[str, list[str]] = {
    "cgm": [
        "pump_serial", "seqnum", "timestamp",
        "bg_mgdl", "backfilled", "sensor_timestamp",
    ],
    "bolus": [
        "pump_serial", "bolus_id", "timestamp", "insulin_units",
    ],
    "requests": [
        "pump_serial", "bolus_id", "timestamp", "carbs_g", "bg_mgdl", "iob",
        "bolus_source", "food_insulin", "correction_insulin", "total_requested",
        "bolus_category", "override_delta",
    ],
    "basal": [
        "pump_serial", "timestamp", "commanded_rate", "rate_source",
    ],
    "suspension": [
        "pump_serial", "suspend_timestamp", "resume_timestamp", "duration_minutes",
        "suspend_reason", "insulin_at_suspend", "pairing_suspect",
        "alarm_id", "alarm_name",
    ],
    "events": [
        "pump_serial", "seqnum", "timestamp", "event_type", "event_subtype",
        "previous_mode", "details", "forced_by_alarm",
    ],
    "alarms": [
        "pump_serial", "seqnum", "timestamp", "category", "action",
        "alarm_id", "alarm_name", "param1", "param2",
    ],
    "site_issues": [
        "pump_serial", "first_occlusion_ts", "last_occlusion_ts",
        "occlusion_count", "resolved_by_site_change_ts",
        "resolution_delay_minutes",
    ],
    "cgm_gaps": [
        "pump_serial", "start_ts", "end_ts", "duration_minutes", "ongoing",
    ],
}


# ── small null-safe scalar helpers ───────────────────────────────────────────
#
# Pandas leaks NaN/NaT into object columns once a column ever held a null,
# so the converters cannot trust dtype alone. Each helper accepts a single
# scalar (None, NaN, NaT, pd.Timestamp, str, int, float, bool) and returns
# the Postgres-friendly equivalent (None or the cast value).

def _is_null(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _ts_or_none(value: Any) -> Any:
    """Return a tz-aware ``datetime`` (preserving offset) or ``None``."""
    if _is_null(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def _int_or_none(value: Any) -> int | None:
    if _is_null(value):
        return None
    return int(value)


def _float_or_none(value: Any) -> float | None:
    if _is_null(value):
        return None
    return float(value)


def _bool_or_none(value: Any) -> bool | None:
    if _is_null(value):
        return None
    return bool(value)


def _str_or_none(value: Any) -> str | None:
    if _is_null(value):
        return None
    return str(value)


def _details_to_json(value: Any) -> Any:
    """events.details: JSON-encoded text → ``Json(parsed_dict)``.

    Empty / NaN / null defaults to ``Json({})`` because the column is
    NOT NULL and the parquet sometimes carries an empty payload.
    """
    if Json is None:
        # Only reachable from a real insert path; --dry-run skips converters.
        raise RuntimeError("psycopg2 is required to convert events.details")
    if _is_null(value):
        return Json({})
    text = str(value).strip()
    if not text:
        return Json({})
    return Json(json.loads(text))


# ── per-table row converters ─────────────────────────────────────────────────
#
# Each converter accepts a row-shaped Mapping (dict or pd.Series) and
# returns the tuple in COLUMN_SPECS order. Pure functions; no DB or
# psycopg2 dependency except for events (which needs Json). Easy to unit
# test in Task 4 with hand-written dicts.

def _cgm_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _int_or_none(r["seqnum"]),
        _ts_or_none(r["timestamp"]),
        _int_or_none(r["bg_mgdl"]),
        _bool_or_none(r["backfilled"]),
        _ts_or_none(r["sensor_timestamp"]),
    )


def _bolus_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _int_or_none(r["bolus_id"]),
        _ts_or_none(r["timestamp"]),
        _float_or_none(r["insulin_units"]),
    )


def _requests_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _int_or_none(r["bolus_id"]),
        _ts_or_none(r["timestamp"]),
        _int_or_none(r["carbs_g"]),
        _int_or_none(r["bg_mgdl"]),
        _float_or_none(r["iob"]),
        _str_or_none(r["bolus_source"]),
        _float_or_none(r["food_insulin"]),
        _float_or_none(r["correction_insulin"]),
        _float_or_none(r["total_requested"]),
        _str_or_none(r["bolus_category"]),
        _float_or_none(r["override_delta"]),
    )


def _basal_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _ts_or_none(r["timestamp"]),
        _float_or_none(r["commanded_rate"]),
        _str_or_none(r["rate_source"]),
    )


def _suspension_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _ts_or_none(r["suspend_timestamp"]),
        _ts_or_none(r["resume_timestamp"]),
        _float_or_none(r["duration_minutes"]),
        _str_or_none(r["suspend_reason"]),
        _int_or_none(r["insulin_at_suspend"]),
        _bool_or_none(r["pairing_suspect"]),
        _int_or_none(r["alarm_id"]),
        _str_or_none(r["alarm_name"]),
    )


def _events_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _int_or_none(r["seqnum"]),
        _ts_or_none(r["timestamp"]),
        _str_or_none(r["event_type"]),
        _str_or_none(r["event_subtype"]),
        _str_or_none(r["previous_mode"]),
        _details_to_json(r["details"]),
        _bool_or_none(r["forced_by_alarm"]),
    )


def _alarms_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _int_or_none(r["seqnum"]),
        _ts_or_none(r["timestamp"]),
        _str_or_none(r["category"]),
        _str_or_none(r["action"]),
        _int_or_none(r["alarm_id"]),
        _str_or_none(r["alarm_name"]),
        _int_or_none(r["param1"]),
        _float_or_none(r["param2"]),
    )


def _site_issues_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _ts_or_none(r["first_occlusion_ts"]),
        _ts_or_none(r["last_occlusion_ts"]),
        _int_or_none(r["occlusion_count"]),
        _ts_or_none(r["resolved_by_site_change_ts"]),
        _float_or_none(r["resolution_delay_minutes"]),
    )


def _cgm_gaps_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _ts_or_none(r["start_ts"]),
        _ts_or_none(r["end_ts"]),
        _float_or_none(r["duration_minutes"]),
        _bool_or_none(r["ongoing"]),
    )


CONVERTERS: dict[str, Callable[[Mapping[str, Any]], tuple]] = {
    "cgm": _cgm_row,
    "bolus": _bolus_row,
    "requests": _requests_row,
    "basal": _basal_row,
    "suspension": _suspension_row,
    "events": _events_row,
    "alarms": _alarms_row,
    "site_issues": _site_issues_row,
    "cgm_gaps": _cgm_gaps_row,
}


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
    conn = psycopg2.connect(db_url)

    failed: list[tuple[str, Exception]] = []
    report = []
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
    finally:
        conn.close()

    _print_report(report, dry_run=False)

    if failed:
        names = ", ".join(name for name, _ in failed)
        print(f"\nFAILED tables: {names}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
