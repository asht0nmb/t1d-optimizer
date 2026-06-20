"""Incremental Tandem ingestion → Supabase via SupabaseStorage.

Fetches new pump events from tconnectsync, runs the full enrichment layer
(``build_all`` with ``load_config()``), and upserts all nine data tables.
Fetch-state bookmarks live in Postgres (``fetch_state``), not on disk.

Usage:
    uv run python scripts/sync_tandem_to_supabase.py
        [--dry-run] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--only SERIAL] [-v]

By default the window is the ``fetch_state`` bookmark → the pump's latest event
date; with no bookmark it is the pump's full history. Pass ``--start`` to bound
the sync to a gap (e.g. the last-known date) without re-pulling history — this
also makes ``--dry-run`` preview that window, since dry-run never opens the DB
to read the bookmark. A successful (non-dry) run seeds ``fetch_state`` so later
runs resume incrementally on their own. Upserts are ``ON CONFLICT DO NOTHING``,
so an overlapping start day is harmless.

Environment:
    TCONNECT_EMAIL, TCONNECT_PASSWORD — Tandem source API (required).
    SUPABASE_DB_URL — direct Postgres URL (``db.<project>.supabase.co:5432``).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd
from dotenv import load_dotenv

from core.schema import TABLES
from core.storage.records import FetchState, UpsertResult
from core.storage.supabase import SupabaseStorage
from ingestion.builders import build_all
from ingestion.client import fetch_pump_events, get_api, get_pump_metadata
from ingestion.enrich import load_config
from ingestion.pipeline_version import PIPELINE_VERSION

load_dotenv()

logger = logging.getLogger(__name__)

SOURCE_KIND = "tconnectsync"
REQUIRED_ENV: tuple[str, ...] = ("TCONNECT_EMAIL", "TCONNECT_PASSWORD")


def _mask_serial(serial: object) -> str:
    s = str(serial)
    return f"…{s[-4:]}" if len(s) >= 4 else "…"


def _check_tconnect_env() -> None:
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        logger.error(
            "Missing required env var(s): %s",
            ", ".join(missing),
        )
        raise SystemExit(2)


def _connect_storage() -> tuple[SupabaseStorage, Any]:
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        logger.error(
            "SUPABASE_DB_URL is not set. Use the Supabase direct connection "
            "(port 5432, host db.<project>.supabase.co), not the pooler."
        )
        raise SystemExit(2)

    try:
        import psycopg2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit(
            "psycopg2 is required for Supabase sync. Install via "
            "`uv add psycopg2-binary`."
        ) from exc

    conn = psycopg2.connect(
        db_url,
        connect_timeout=10,
        # TCP keepalives guard against network-level drops of an idle
        # connection during a long tconnectsync fetch.
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )
    # Autocommit so read-only helpers (e.g. get_fetch_state, which does not
    # commit) never leave the connection idle-in-transaction across the
    # multi-minute fetch between reading a pump's bookmark and upserting its
    # rows. That state tripped idle_in_transaction_session_timeout='5min'
    # (migration 0002) and dropped the SSL connection mid-sync. upsert_table's
    # per-chunk commit() calls become harmless no-ops under autocommit, and
    # row durability is unchanged (it already committed per chunk).
    conn.autocommit = True
    return SupabaseStorage(conn=conn), conn


def compute_fetch_window(
    pump: dict[str, Any],
    fetch_state: FetchState | None,
    *,
    start_override: str | None = None,
    end_override: str | None = None,
) -> tuple[str, str]:
    """Return ``(start_date, end_date)`` ISO strings for one pump.

    ``start_override`` / ``end_override`` (``--start`` / ``--end``) take
    precedence over both the pump's full range and the ``fetch_state``
    bookmark. This is the gap-fill path: it lets a caller sync only a bounded
    window (e.g. last-known-date → today) without re-pulling full history, and
    it makes ``--dry-run`` preview that same window even though dry-run never
    opens the DB to read the bookmark.
    """
    end = end_override if end_override is not None else pump["maxDateWithEvents"][:10]

    if start_override is not None:
        return start_override, end

    if fetch_state is None:
        return pump["minDateWithEvents"][:10], end

    last_end = fetch_state.payload.get("last_successful_chunk_end")
    if last_end is None:
        return pump["minDateWithEvents"][:10], end

    start = (date.fromisoformat(str(last_end)) - timedelta(days=1)).isoformat()
    return start, end


def _collect_timestamp_bounds(dfs: dict[str, pd.DataFrame]) -> tuple[str | None, str | None]:
    all_timestamps: list[Any] = []
    for df in dfs.values():
        if df.empty:
            continue
        ts_col = "timestamp" if "timestamp" in df.columns else "suspend_timestamp"
        if ts_col in df.columns:
            all_timestamps.extend(df[ts_col].dropna().tolist())
    if not all_timestamps:
        return None, None
    return str(min(all_timestamps))[:10], str(max(all_timestamps))[:10]


def _log_upsert(name: str, result: UpsertResult) -> None:
    logger.info(
        "%s: received=%d inserted=%d skipped=%d elapsed=%.2fs",
        name,
        result.rows_received,
        result.rows_inserted,
        result.rows_skipped,
        result.elapsed_seconds,
    )


def process_pump(
    api: Any,
    pump: dict[str, Any],
    storage: SupabaseStorage | None,
    config: dict,
    *,
    dry_run: bool,
    verbose: bool,
    start_override: str | None = None,
    end_override: str | None = None,
) -> bool:
    """Fetch, enrich, and upsert one pump. Returns True when events were processed."""
    serial = str(pump["serialNumber"])
    device_id = pump["tconnectDeviceId"]
    serial_log = _mask_serial(serial)

    fetch_state = None if storage is None else storage.get_fetch_state(serial)
    start, end = compute_fetch_window(
        pump,
        fetch_state,
        start_override=start_override,
        end_override=end_override,
    )

    if verbose:
        logger.info(
            "Pump %s: window %s → %s (dry_run=%s)",
            serial_log,
            start,
            end,
            dry_run,
        )
    else:
        logger.info("Pump %s: fetching %s → %s", serial_log, start, end)

    events, last_successful_end = fetch_pump_events(
        api,
        device_id,
        start,
        end,
        pump_serial=serial,
    )

    if not events:
        logger.info("Pump %s: no events returned", serial_log)
        return False

    dfs = build_all(events, serial, config)

    for name in TABLES:
        df = dfs.get(name, pd.DataFrame())
        row_count = len(df) if not df.empty else 0
        enriched_note = ""
        if name == "requests" and row_count and "bolus_category" in df.columns:
            enriched_note = " (enriched: bolus_category present)"
        elif name in ("site_issues", "cgm_gaps") and row_count:
            enriched_note = " (enriched derived table)"
        logger.info("  %s: %d rows%s", name, row_count, enriched_note)

        if dry_run or row_count == 0:
            continue

        assert storage is not None
        result = storage.upsert_table(name, df)
        _log_upsert(name, result)

    if dry_run:
        return True

    assert storage is not None
    actual_min, actual_max = _collect_timestamp_bounds(dfs)
    payload: dict[str, Any] = {}
    if last_successful_end:
        payload["last_successful_chunk_end"] = last_successful_end
    if actual_min:
        payload["actual_min_date"] = actual_min
    if actual_max:
        payload["actual_max_date"] = actual_max

    storage.set_fetch_state(
        serial,
        FetchState(
            source_id=serial,
            last_cursor=None,
            last_fetched_at=datetime.now(timezone.utc),
            payload=payload,
            source_kind=SOURCE_KIND,
        ),
    )
    logger.info("Pump %s: fetch state updated", serial_log)
    return True


def run_sync(
    *,
    dry_run: bool = False,
    only_serial: str | None = None,
    verbose: bool = False,
    start: str | None = None,
    end: str | None = None,
) -> int:
    """Orchestrate incremental sync for all (or one) pump(s)."""
    _check_tconnect_env()

    try:
        api = get_api()
    except Exception as exc:  # noqa: BLE001
        logger.error("Authentication failed: %s", type(exc).__name__)
        return 1

    try:
        metadata = get_pump_metadata(api)
    except Exception as exc:  # noqa: BLE001
        logger.error("pump_event_metadata failed: %s", type(exc).__name__)
        return 1

    if only_serial is not None:
        metadata = [p for p in metadata if str(p["serialNumber"]) == only_serial]
        if not metadata:
            logger.error("No pump with serial %s on account", only_serial)
            return 1

    config = load_config()
    logger.info(
        "Starting Tandem → Supabase sync (%d pump(s), dry_run=%s)",
        len(metadata),
        dry_run,
    )

    storage: SupabaseStorage | None = None
    conn = None
    if not dry_run:
        storage, conn = _connect_storage()

    processed_any = False
    try:
        for pump in metadata:
            if process_pump(
                api,
                pump,
                storage,
                config,
                dry_run=dry_run,
                verbose=verbose,
                start_override=start,
                end_override=end,
            ):
                processed_any = True

        if not dry_run and storage is not None and processed_any:
            storage.set_pipeline_version(PIPELINE_VERSION)
            logger.info("Pipeline version set to %d", PIPELINE_VERSION)
    except Exception:
        logger.exception("Sync failed")
        return 1
    finally:
        if conn is not None:
            conn.close()

    logger.info("Sync complete.")
    return 0


def _iso_date(value: str) -> str:
    """argparse type: accept only YYYY-MM-DD; return it unchanged."""
    try:
        date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected an ISO date (YYYY-MM-DD), got {value!r}"
        )
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Incremental Tandem ingestion into Supabase (enriched upsert)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run fetch + enriched build_all; log row counts; skip DB writes.",
    )
    parser.add_argument(
        "--start",
        type=_iso_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Override the fetch window start. Bounds the sync to a gap "
        "(e.g. last-known-date) instead of re-pulling full history; also "
        "makes --dry-run preview this window. Upserts are idempotent, so a "
        "day of overlap is harmless.",
    )
    parser.add_argument(
        "--end",
        type=_iso_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Override the fetch window end (default: pump's latest event date).",
    )
    parser.add_argument(
        "--only",
        dest="only_serial",
        default=None,
        metavar="SERIAL",
        help="Sync a single pump serial (default: all pumps on account).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Extra per-pump logging.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    return run_sync(
        dry_run=args.dry_run,
        only_serial=args.only_serial,
        verbose=args.verbose,
        start=args.start,
        end=args.end,
    )


if __name__ == "__main__":
    raise SystemExit(main())
