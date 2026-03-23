"""Parquet I/O, deduplication, and fetch-state tracking."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# ── paths ────────────────────────────────────────────────────────────────────
PROCESSED_DIR = Path("data/processed")
STATE_FILE = PROCESSED_DIR / ".fetch_state.json"

PARQUET_FILES: dict[str, str] = {
    "cgm": "cgm.parquet",
    "bolus": "bolus.parquet",
    "requests": "requests.parquet",
    "basal": "basal.parquet",
    "suspension": "suspension.parquet",
    "events": "events.parquet",
    "alarms": "alarms.parquet",
}

DEDUP_KEYS: dict[str, list[str]] = {
    "cgm": ["seqnum", "pump_serial"],
    "bolus": ["bolus_id", "pump_serial"],
    "requests": ["bolus_id", "pump_serial"],
    "basal": ["timestamp", "pump_serial"],
    "suspension": ["suspend_timestamp", "pump_serial"],
    "events": ["pump_serial", "seqnum"],
    "alarms": ["pump_serial", "seqnum"],
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


# ── housekeeping ─────────────────────────────────────────────────────────────
def clean_all() -> None:
    """Delete all parquet files and the fetch-state file."""
    for filename in PARQUET_FILES.values():
        path = PROCESSED_DIR / filename
        if path.exists():
            path.unlink()
            logger.info("Deleted %s", path)

    if STATE_FILE.exists():
        STATE_FILE.unlink()
        logger.info("Deleted %s", STATE_FILE)
