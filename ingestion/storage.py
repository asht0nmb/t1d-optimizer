"""Parquet I/O, deduplication, and fetch-state tracking."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ingestion.pipeline_version import PIPELINE_VERSION

logger = logging.getLogger(__name__)

# ── paths ────────────────────────────────────────────────────────────────────
PROCESSED_DIR = Path("data/processed")
STATE_FILE = PROCESSED_DIR / ".fetch_state.json"
PIPELINE_VERSION_FILE = PROCESSED_DIR / ".pipeline_version.json"

PARQUET_FILES: dict[str, str] = {
    "cgm": "cgm.parquet",
    "bolus": "bolus.parquet",
    "requests": "requests.parquet",
    "basal": "basal.parquet",
    "suspension": "suspension.parquet",
    "events": "events.parquet",
    "alarms": "alarms.parquet",
    "site_issues": "site_issues.parquet",
    "cgm_gaps": "cgm_gaps.parquet",
}

DEDUP_KEYS: dict[str, list[str]] = {
    "cgm": ["seqnum", "pump_serial"],
    "bolus": ["bolus_id", "pump_serial"],
    "requests": ["bolus_id", "pump_serial"],
    "basal": ["timestamp", "pump_serial"],
    "suspension": ["suspend_timestamp", "pump_serial"],
    "events": ["pump_serial", "seqnum"],
    "alarms": ["seqnum", "pump_serial"],
    "site_issues": ["first_occlusion_ts", "pump_serial"],
    "cgm_gaps": ["start_ts", "pump_serial"],
}


# ── dataframe I/O ────────────────────────────────────────────────────────────
def save_df(name: str, new_df: pd.DataFrame) -> None:
    """Append *new_df* to the existing parquet file, dedup, sort, and write back."""
    if new_df.empty:
        return

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    parquet_path = PROCESSED_DIR / PARQUET_FILES[name]

    # Load existing data if present
    if parquet_path.exists():
        existing = pd.read_parquet(parquet_path)
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df.copy()

    # Dedup after concat so overlapping re-fetched rows collapse
    combined = combined.drop_duplicates(subset=DEDUP_KEYS[name], keep="first")

    # Sort by the first dedup key (timestamp or equivalent)
    sort_col = DEDUP_KEYS[name][0]
    combined = combined.sort_values(sort_col).reset_index(drop=True)

    combined.to_parquet(parquet_path, index=False)
    logger.info("Saved %s: %d rows → %s", name, len(combined), parquet_path)

    write_pipeline_version()


def load_df(name: str) -> pd.DataFrame | None:
    """Load a parquet file by logical name, or return None if it doesn't exist."""
    parquet_path = PROCESSED_DIR / PARQUET_FILES[name]
    if parquet_path.exists():
        return pd.read_parquet(parquet_path)
    return None


# ── fetch state ──────────────────────────────────────────────────────────────
def load_fetch_state() -> dict:
    """Load fetch state from JSON, or return an empty dict."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_fetch_state(state: dict) -> None:
    """Persist fetch state to JSON."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    logger.info("Fetch state saved → %s", STATE_FILE)


# ── pipeline version sidecar ─────────────────────────────────────────────────
def write_pipeline_version(version: int | None = None) -> None:
    """Stamp the processed directory with the pipeline version that wrote it.

    Called from `save_df` on every successful save so the sidecar always
    reflects the version of the code that most recently produced the
    on-disk data.
    """
    if version is None:
        version = PIPELINE_VERSION
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": version,
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    PIPELINE_VERSION_FILE.write_text(json.dumps(payload, indent=2))


def read_pipeline_version() -> int | None:
    """Return the version recorded in the sidecar, or None if absent/invalid.

    Malformed sidecars (missing key, non-int value, unreadable JSON) are
    treated as "unknown" rather than crashing — the version guard decides
    how to escalate from there.
    """
    if not PIPELINE_VERSION_FILE.exists():
        return None
    try:
        payload = json.loads(PIPELINE_VERSION_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    version = payload.get("version")
    if isinstance(version, int):
        return version
    return None


# ── housekeeping ─────────────────────────────────────────────────────────────
def clean_all() -> None:
    """Delete all parquet files, the fetch-state file, and the version sidecar."""
    for filename in PARQUET_FILES.values():
        path = PROCESSED_DIR / filename
        if path.exists():
            path.unlink()
            logger.info("Deleted %s", path)

    if STATE_FILE.exists():
        STATE_FILE.unlink()
        logger.info("Deleted %s", STATE_FILE)

    if PIPELINE_VERSION_FILE.exists():
        PIPELINE_VERSION_FILE.unlink()
        logger.info("Deleted %s", PIPELINE_VERSION_FILE)
