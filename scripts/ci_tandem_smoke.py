"""CI smoke test: authenticate against Tandem and fetch a recent slice.

Validates that `tconnectsync` can complete an end-to-end auth + fetch in a
headless environment (GitHub Actions). The point is to confirm the handshake
works in CI before we build the full sync-to-Supabase pipeline on top of it.

Behavior:
    1. Verify required env vars are present (no values are ever echoed).
    2. Authenticate via `ingestion.client.get_api()` (reads env vars).
    3. List pumps and pick the most recent one.
    4. Fetch a 1-day window ending at that pump's last known event date.
    5. Bin events into category DataFrames via `build_all` (no enrichment).
    6. Print summary counts. No persistence. No row-level dumps.

Logging policy (see HARD CONSTRAINTS in the spike task):
    - Credential values are NEVER logged or echoed, anywhere.
    - Pump serial numbers are masked to the trailing 4 characters.
    - Exceptions are logged by *type* only; messages may contain account
      context (e.g. email in error responses) and are intentionally suppressed.

Usage (run via the workflow, not locally):
    uv run python -m scripts.ci_tandem_smoke

Environment variables (required, supplied by the workflow from repo secrets):
    TCONNECT_EMAIL
    TCONNECT_PASSWORD

Exit codes:
    0 — auth + fetch succeeded
    1 — auth, metadata, or fetch failed
    2 — required env vars not set
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("ci_tandem_smoke")


REQUIRED_ENV: tuple[str, ...] = ("TCONNECT_EMAIL", "TCONNECT_PASSWORD")

SUMMARY_FRAMES: tuple[str, ...] = (
    "cgm", "bolus", "requests", "basal", "suspension", "events", "alarms",
)


def _check_env() -> None:
    """Exit early if any credential env var is unset. Never logs values."""
    # In CI, secrets are injected via the workflow `env:` block. Locally, devs
    # may stash creds in a (gitignored) .env file. `load_dotenv` is a no-op if
    # the file doesn't exist, so it's safe to call in both contexts.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        logger.error(
            "Missing required env var(s): %s. "
            "Add them as repository secrets and re-run the workflow.",
            ", ".join(missing),
        )
        sys.exit(2)


def _mask_serial(serial: object) -> str:
    """Return last-4 of a pump serial, suitable for log lines."""
    s = str(serial)
    return f"…{s[-4:]}" if len(s) >= 4 else "…"


def main() -> int:
    _check_env()

    # Defer ingestion imports until after the env check so a missing-secret
    # failure produces a clean diagnostic rather than an import-time stack.
    from ingestion.builders import build_all
    from ingestion.client import fetch_pump_events, get_api, get_pump_metadata

    logger.info("Authenticating against Tandem source API…")
    try:
        api = get_api()
    except Exception as exc:  # noqa: BLE001 — type-only log, see policy above
        logger.error("Authentication failed: %s", type(exc).__name__)
        return 1
    logger.info("Authentication succeeded.")

    try:
        metadata = get_pump_metadata(api)
    except Exception as exc:  # noqa: BLE001
        logger.error("pump_event_metadata failed: %s", type(exc).__name__)
        return 1

    if not metadata:
        logger.error("No pumps found on account; nothing to fetch.")
        return 1
    logger.info("Found %d pump(s) on account.", len(metadata))

    # Most-recent pump (sorted-oldest-first by client.get_pump_metadata).
    # Use that pump's known max date as the window end so the smoke run sees
    # data even on days when no fresh sync has landed yet.
    pump = metadata[-1]
    serial_masked = _mask_serial(pump["serialNumber"])
    device_id = pump["tconnectDeviceId"]
    max_date = pump["maxDateWithEvents"][:10]

    end = date.fromisoformat(max_date)
    start = end - timedelta(days=1)

    logger.info(
        "Selected pump %s — fetching window %s → %s",
        serial_masked, start.isoformat(), end.isoformat(),
    )

    try:
        events, _last_successful_end = fetch_pump_events(
            api,
            device_id,
            start.isoformat(),
            end.isoformat(),
            pump_serial=serial_masked,
            chunk_days=2,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("pump_events fetch failed: %s", type(exc).__name__)
        return 1

    logger.info("Fetched %d raw events.", len(events))

    if not events:
        # Auth + fetch path is healthy; the window simply held nothing.
        # Still a green run — the goal is validating the handshake.
        logger.warning(
            "No events returned in window. Auth + fetch path is healthy; "
            "consider widening the window in a follow-up if this persists."
        )

    # No config → no enrichment. We only need per-category counts for the
    # summary line; enrichment isn't part of what this spike is validating.
    dfs = build_all(events, serial_masked, config=None)

    summary_pairs = [f"{name}={len(dfs[name])}" for name in SUMMARY_FRAMES if name in dfs]
    logger.info("Per-category counts: %s", "  ".join(summary_pairs))

    print()
    print("=" * 60)
    print("Tandem CI smoke result")
    print("=" * 60)
    print(f"  pumps on account     : {len(metadata)}")
    print(f"  selected pump        : {serial_masked}")
    print(f"  window               : {start.isoformat()} → {end.isoformat()}")
    print(f"  total events fetched : {len(events)}")
    for name in SUMMARY_FRAMES:
        if name in dfs:
            print(f"  {name:<20} : {len(dfs[name])}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
