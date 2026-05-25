"""Incremental Tandem ingestion → Supabase via SupabaseStorage.

Fetches new pump events from tconnectsync, runs the full enrichment layer
(``build_all`` with ``load_config()``), and upserts all nine data tables.
Fetch-state bookmarks live in Postgres (``fetch_state``), not on disk.

Usage:
    uv run python scripts/sync_tandem_to_supabase.py [--dry-run] [--only SERIAL] [-v]

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

    conn = psycopg2.connect(db_url, connect_timeout=10)
    return SupabaseStorage(conn=conn), conn


def compute_fetch_window(
    pump: dict[str, Any],
    fetch_state: FetchState | None,
) -> tuple[str, str]:
    """Return ``(start_date, end_date)`` ISO strings for one pump."""
    max_date = pump["maxDateWithEvents"][:10]
    if fetch_state is None:
        return pump["minDateWithEvents"][:10], max_date

    last_end = fetch_state.payload.get("last_successful_chunk_end")
    if last_end is None:
        return pump["minDateWithEvents"][:10], max_date

    start = (date.fromisoformat(str(last_end)) - timedelta(days=1)).isoformat()
    return start, max_date


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
) -> bool:
    """Fetch, enrich, and upsert one pump. Returns True when events were processed."""
    serial = str(pump["serialNumber"])
    device_id = pump["tconnectDeviceId"]
    serial_log = _mask_serial(serial)

    fetch_state = None if storage is None else storage.get_fetch_state(serial)
    start, end = compute_fetch_window(pump, fetch_state)

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
    )


if __name__ == "__main__":
    raise SystemExit(main())
