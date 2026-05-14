"""Backward-compatibility shim over :class:`core.storage.parquet.ParquetStorage`.

Every public name from the pre-Protocol module is preserved (signature,
return shape, on-disk behavior). Functions delegate to a per-process
cached :class:`ParquetStorage` instance rooted at the module-level
``PROCESSED_DIR``; if a test (or any other caller) reassigns
``PROCESSED_DIR``, the cache rebuilds on the next call.

New code should import :class:`core.storage.protocol.Storage` and
accept it via dependency injection instead of going through this shim.
The shim exists so the existing fetch / view / detection callers and
the bootstrap script keep working unchanged on day one of Phase 1.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from core.storage.parquet import (
    DEDUP_KEYS,
    PARQUET_FILES,
    PIPELINE_VERSION_FILENAME,
    STATE_FILENAME,
    ParquetStorage,
)
from ingestion.pipeline_version import PIPELINE_VERSION

logger = logging.getLogger(__name__)

# ── paths ────────────────────────────────────────────────────────────────────
PROCESSED_DIR = Path("data/processed")
STATE_FILE = PROCESSED_DIR / STATE_FILENAME
PIPELINE_VERSION_FILE = PROCESSED_DIR / PIPELINE_VERSION_FILENAME


# Re-export so existing callers (`ingestion.view_data`,
# `scripts.bootstrap_supabase`, etc.) keep working unmodified.
__all__ = [
    "PROCESSED_DIR",
    "STATE_FILE",
    "PIPELINE_VERSION_FILE",
    "PARQUET_FILES",
    "DEDUP_KEYS",
    "save_df",
    "load_df",
    "load_fetch_state",
    "save_fetch_state",
    "write_pipeline_version",
    "read_pipeline_version",
    "clean_all",
]


# Per-process ParquetStorage cache. Keyed on the *current* PROCESSED_DIR
# so a test that monkey-patches the module global rebuilds the
# storage on the next call rather than keeping a stale instance.
_cached_root: Path | None = None
_cached_storage: ParquetStorage | None = None


def _default_storage() -> ParquetStorage:
    """Return the module's :class:`ParquetStorage` instance.

    Rebuilds the instance whenever the module-level ``PROCESSED_DIR``
    has been reassigned (e.g. by ``monkeypatch.setattr(storage,
    "PROCESSED_DIR", tmp_path)`` in tests).
    """
    global _cached_root, _cached_storage
    current = PROCESSED_DIR
    if _cached_storage is None or _cached_root != current:
        _cached_root = current
        _cached_storage = ParquetStorage(root=current)
    return _cached_storage


# ── dataframe I/O ────────────────────────────────────────────────────────────

def save_df(name: str, new_df: pd.DataFrame) -> None:
    """Append *new_df* to the existing parquet file, dedup, sort, and write back.

    Empty input is a no-op (and does NOT bump the pipeline-version
    sidecar — same as the pre-shim behavior, asserted by
    ``tests/test_storage.py::test_save_df_empty_does_not_write_sidecar``).
    """
    if new_df.empty:
        return
    storage_inst = _default_storage()
    storage_inst.upsert_table(name, new_df)
    storage_inst.set_pipeline_version(PIPELINE_VERSION)


def load_df(name: str) -> pd.DataFrame | None:
    """Load a parquet file by logical name, or return ``None`` if it doesn't exist.

    Notably returns ``None`` (not an empty DataFrame) when the file is
    absent — this is the pre-shim contract every caller relies on.
    """
    path = PROCESSED_DIR / PARQUET_FILES[name]
    if path.exists():
        return pd.read_parquet(path)
    return None


# ── fetch state ──────────────────────────────────────────────────────────────

def load_fetch_state() -> dict:
    """Load fetch state from JSON, or return an empty dict."""
    return _default_storage()._read_legacy_fetch_state()


def save_fetch_state(state: dict) -> None:
    """Persist fetch state to JSON."""
    _default_storage()._write_legacy_fetch_state(state)


# ── pipeline version sidecar ─────────────────────────────────────────────────

def write_pipeline_version(version: int | None = None) -> None:
    """Stamp the processed directory with the pipeline version that wrote it.

    Called from `save_df` on every successful save so the sidecar always
    reflects the version of the code that most recently produced the
    on-disk data.
    """
    if version is None:
        version = PIPELINE_VERSION
    _default_storage().set_pipeline_version(version)


def read_pipeline_version() -> int | None:
    """Return the version recorded in the sidecar, or None if absent/invalid.

    Malformed sidecars (missing key, non-int value, unreadable JSON) are
    treated as "unknown" rather than crashing — the version guard decides
    how to escalate from there.
    """
    return _default_storage().get_pipeline_version()


# ── housekeeping ─────────────────────────────────────────────────────────────

def clean_all() -> None:
    """Delete all parquet files, the fetch-state file, and the version sidecar."""
    _default_storage().clean_all()
