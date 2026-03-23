"""Orchestrates the full ingestion pipeline: fetch → build → store."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from .client import get_api, get_pump_metadata, fetch_pump_events
from .builders import build_all
from .storage import load_fetch_state, save_fetch_state, save_df, clean_all

logger = logging.getLogger(__name__)


def run_full_fetch() -> None:
    """Fetch ALL data from ALL pumps. Always additive (concat + dedup)."""
    api = get_api()
    metadata = get_pump_metadata(api)
    state = load_fetch_state()

    logger.info("Found %d pumps on account", len(metadata))

    for pump in metadata:
        serial = str(pump["serialNumber"])
        device_id = pump["tconnectDeviceId"]
        start = pump["minDateWithEvents"][:10]
        end = pump["maxDateWithEvents"][:10]

        logger.info("Pump %s: fetching full range %s → %s", serial, start, end)
        _process_pump(api, device_id, serial, start, end, state)

    save_fetch_state(state)
    logger.info("Full fetch complete.")


def run_incremental_fetch() -> None:
    """Fetch only new data since last fetch per pump."""
    api = get_api()
    metadata = get_pump_metadata(api)
    state = load_fetch_state()

    logger.info("Incremental update: %d pumps", len(metadata))

    for pump in metadata:
        serial = str(pump["serialNumber"])
        device_id = pump["tconnectDeviceId"]
        max_date = pump["maxDateWithEvents"][:10]

        pump_state = state.get(serial, {})
        last_end = pump_state.get("last_successful_chunk_end")

        if last_end is None:
            # No state for this pump — do a full fetch
            start = pump["minDateWithEvents"][:10]
            logger.info("Pump %s: no prior state, fetching full range %s → %s", serial, start, max_date)
        else:
            # Overlap by 1 day for safety
            start = (date.fromisoformat(last_end) - timedelta(days=1)).isoformat()
            logger.info("Pump %s: incremental from %s → %s", serial, start, max_date)

        _process_pump(api, device_id, serial, start, max_date, state)

    save_fetch_state(state)
    logger.info("Incremental update complete.")


def _process_pump(
    api,
    device_id: int,
    serial: str,
    start: str,
    end: str,
    state: dict,
) -> None:
    """Fetch events for one pump, build all DataFrames, and save."""
    events, last_successful_end = fetch_pump_events(
        api, device_id, start, end, pump_serial=serial,
    )

    if not events:
        logger.info("Pump %s: no events returned", serial)
        return

    # Build all DataFrames
    dfs = build_all(events, serial)

    for name, df in dfs.items():
        if not df.empty:
            save_df(name, df)
            logger.info("  %s: %d rows saved", name, len(df))
        else:
            logger.info("  %s: 0 rows (skipped)", name)

    # Update fetch state
    if serial not in state:
        state[serial] = {}

    if last_successful_end:
        state[serial]["last_successful_chunk_end"] = last_successful_end

    # Track actual date ranges from the data
    all_timestamps = []
    for df in dfs.values():
        if not df.empty:
            ts_col = "timestamp" if "timestamp" in df.columns else "suspend_timestamp"
            if ts_col in df.columns:
                all_timestamps.extend(df[ts_col].dropna().tolist())

    if all_timestamps:
        state[serial]["actual_min_date"] = str(min(all_timestamps))[:10]
        state[serial]["actual_max_date"] = str(max(all_timestamps))[:10]

    logger.info("Pump %s: processing complete", serial)
