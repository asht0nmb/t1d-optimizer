"""Thin wrapper around TandemSourceApi for multi-pump fetching with chunking."""

import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta

from dotenv import load_dotenv
from tconnectsync.api.tandemsource import TandemSourceApi

from ingestion.redaction import mask_serial, protect_identifiers, quiet_tconnectsync

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Outcome of :func:`fetch_pump_events`.

    ``last_successful_end`` is the end date of the last chunk in the
    *contiguous prefix* of successful chunks (chunk 1 .. k, no gaps), or None
    when the very first chunk failed. It is the only value that is safe to
    persist as a fetch bookmark: events from chunks after a failed one are
    still returned (upserts are idempotent), but the bookmark must stay
    before the first failure so the gap is retried on the next run.
    """

    events: list = field(default_factory=list)
    last_successful_end: str | None = None
    chunks_total: int = 0
    chunks_failed: int = 0
    failed_ranges: list[str] = field(default_factory=list)


def get_api() -> TandemSourceApi:
    """Authenticate and return API client. Reads .env for credentials."""
    load_dotenv()
    # tconnectsync logs the decoded login JWT (name, email, account ids) at
    # INFO during construction; silence it before the login happens.
    quiet_tconnectsync()
    api = TandemSourceApi(
        email=os.getenv("TCONNECT_EMAIL"),
        password=os.getenv("TCONNECT_PASSWORD"),
    )
    protect_identifiers(api=api)
    return api


def get_pump_metadata(api: TandemSourceApi) -> list[dict]:
    """Return metadata for all pumps on the account, sorted oldest-first.

    tconnectsync v3 replaced pump_event_metadata() with get_pumper(), which
    nests pumps under BffPumper.pumps and renames tconnectDeviceId ->
    assignmentId (now a UUID string) and minDateWithEvents/maxDateWithEvents
    -> availableDataRange.start/end. Normalize back to the legacy field
    names here so every downstream caller (sync script, CI smoke test) is
    unaffected by the upstream rename. Pumps with no data range (never
    uploaded / retired) are skipped — there's nothing to sync for them, and
    the legacy code path never had to handle a None minDateWithEvents.
    """
    pumper = api.get_pumper()
    protect_identifiers(pumper=pumper)
    pumps = pumper.get("pumps") or []

    normalized = []
    for pump in pumps:
        data_range = pump.get("availableDataRange") or {}
        min_date = data_range.get("start")
        max_date = data_range.get("end")
        if not min_date or not max_date:
            continue
        normalized.append({
            **pump,
            "tconnectDeviceId": pump["assignmentId"],
            "minDateWithEvents": min_date,
            "maxDateWithEvents": max_date,
        })

    return sorted(normalized, key=lambda p: p["minDateWithEvents"])


def fetch_pump_events(
    api: TandemSourceApi,
    device_id: str,
    start_date: str,
    end_date: str,
    pump_serial: str,
    chunk_days: int = 30,
) -> FetchResult:
    """
    Fetch all events for one pump in date-range chunks.

    Continues on chunk failure rather than discarding successfully fetched
    data, but reports the failures (``FetchResult.chunks_failed``) so callers
    can fail loudly instead of silently syncing nothing.

    Args:
        api: Authenticated TandemSourceApi
        device_id: tconnectDeviceId from get_pump_metadata() (a UUID string
            under tconnectsync v3, an int under the retired v2 API)
        start_date: "YYYY-MM-DD"
        end_date: "YYYY-MM-DD"
        pump_serial: For logging only (always logged masked to the last 4)
        chunk_days: Days per API request (default 30)

    Returns:
        A :class:`FetchResult`; see its docstring for the bookmark semantics.
    """
    serial_log = mask_serial(pump_serial)
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

    # Consecutive chunks share a boundary date: chunk N ends on date X and
    # chunk N+1 starts on the same date X. Whether pump_events treats
    # max_date as inclusive or exclusive determines whether events on date X
    # are double-counted. Downstream dedup in storage.save_df collapses any
    # duplicates, so this is safe either way.
    chunks = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=chunk_days), end)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end

    all_events = []
    succeeded = 0
    failed = 0
    failed_ranges = []
    last_successful_end = None
    prefix_intact = True  # no failed chunk seen yet

    for i, (c_start, c_end) in enumerate(chunks, 1):
        start_str = c_start.isoformat()
        end_str = c_end.isoformat()

        try:
            events_iter = api.pump_events(
                device_id,
                min_date=start_str,
                max_date=end_str,
                fetch_all_event_types=True,
            )
            chunk_events = list(events_iter)
            all_events.extend(chunk_events)
            succeeded += 1
            if prefix_intact:
                last_successful_end = end_str
            logger.info(
                "Pump %s: chunk %d/%d (%s → %s): %d events",
                serial_log, i, len(chunks), start_str, end_str, len(chunk_events),
            )
        except Exception:
            failed += 1
            prefix_intact = False
            failed_ranges.append(f"{start_str} → {end_str}")
            logger.error(
                "Pump %s: chunk %d/%d (%s → %s) FAILED",
                serial_log, i, len(chunks), start_str, end_str,
                exc_info=True,
            )

    logger.info(
        "Pump %s: %d/%d chunks fetched, %d failed%s. Total events: %d",
        serial_log, succeeded, len(chunks), failed,
        f" (ranges: {failed_ranges})" if failed else "",
        len(all_events),
    )

    return FetchResult(
        events=all_events,
        last_successful_end=last_successful_end,
        chunks_total=len(chunks),
        chunks_failed=failed,
        failed_ranges=failed_ranges,
    )
