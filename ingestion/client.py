"""Thin wrapper around TandemSourceApi for multi-pump fetching with chunking."""

import logging
import os
from datetime import date, timedelta

from dotenv import load_dotenv
from tconnectsync.api.tandemsource import TandemSourceApi

logger = logging.getLogger(__name__)


def get_api() -> TandemSourceApi:
    """Authenticate and return API client. Reads .env for credentials."""
    load_dotenv()
    return TandemSourceApi(
        email=os.getenv("TCONNECT_EMAIL"),
        password=os.getenv("TCONNECT_PASSWORD"),
    )


def get_pump_metadata(api: TandemSourceApi) -> list[dict]:
    """Return metadata for all pumps on the account, sorted oldest-first."""
    metadata = api.pump_event_metadata()
    return sorted(metadata, key=lambda p: p["minDateWithEvents"])


def fetch_pump_events(
    api: TandemSourceApi,
    device_id: int,
    start_date: str,
    end_date: str,
    pump_serial: str,
    chunk_days: int = 30,
) -> list:
    """
    Fetch all events for one pump in date-range chunks.

    Returns a flat list of typed event objects. Continues on chunk failure
    rather than discarding successfully fetched data.

    Args:
        api: Authenticated TandemSourceApi
        device_id: tconnectDeviceId from pump_event_metadata()
        start_date: "YYYY-MM-DD"
        end_date: "YYYY-MM-DD"
        pump_serial: For logging only
        chunk_days: Days per API request (default 30)

    Returns:
        (events, last_successful_end) tuple where events is a flat list
        and last_successful_end is the end date of the last successful chunk.
    """
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

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
            last_successful_end = end_str
            logger.info(
                "Pump %s: chunk %d/%d (%s → %s): %d events",
                pump_serial, i, len(chunks), start_str, end_str, len(chunk_events),
            )
        except Exception:
            failed += 1
            failed_ranges.append(f"{start_str} → {end_str}")
            logger.error(
                "Pump %s: chunk %d/%d (%s → %s) FAILED",
                pump_serial, i, len(chunks), start_str, end_str,
                exc_info=True,
            )

    logger.info(
        "Pump %s: %d/%d chunks fetched, %d failed%s. Total events: %d",
        pump_serial, succeeded, len(chunks), failed,
        f" (ranges: {failed_ranges})" if failed else "",
        len(all_events),
    )

    return all_events, last_successful_end
