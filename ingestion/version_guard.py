"""Pipeline staleness detection.

Reads the `.pipeline_version.json` sidecar (written by
`ingestion.storage.save_df`) and compares it against `PIPELINE_VERSION` in
`ingestion.pipeline_version`. Callers get a formatted single-line message
they can print, or `None` when everything is in sync.
"""

from __future__ import annotations

import sys
from typing import Literal

from ingestion import storage
from ingestion.pipeline_version import PIPELINE_VERSION

# Sentinel: `_cache is _UNSET` means "not yet computed this process".
# We can't use `None` since `None` is the valid cached "all clear" result.
_UNSET: object = object()
_cache: object = _UNSET


def reset_cache() -> None:
    """Force the next `check_pipeline_version` call to re-read the sidecar.

    Primarily for tests; real callers rely on the per-process cache so
    multi-command scripts (e.g. `run-all`) don't spam warnings.
    """
    global _cache
    _cache = _UNSET


def _has_any_parquet() -> bool:
    if not storage.PROCESSED_DIR.exists():
        return False
    for filename in storage.PARQUET_FILES.values():
        if (storage.PROCESSED_DIR / filename).exists():
            return True
    return False


def _build_message(on_disk: int | None) -> str | None:
    if on_disk == PIPELINE_VERSION:
        return None

    if on_disk is None:
        on_disk_label = "unknown (unversioned)"
    else:
        on_disk_label = f"v{on_disk}"

    return (
        f"⚠️  PIPELINE VERSION MISMATCH: on-disk data is {on_disk_label}, "
        f"code is v{PIPELINE_VERSION}. Run `uv run python main.py fetch --clean` "
        f"to regenerate data."
    )


def check_pipeline_version() -> str | None:
    """Return a one-line warning if on-disk data is stale, else `None`.

    Cached per process. Call `reset_cache()` to force a re-read (tests).
    """
    global _cache
    if _cache is not _UNSET:
        return _cache  # type: ignore[return-value]

    if not _has_any_parquet():
        _cache = None
        return None

    on_disk = storage.read_pipeline_version()
    _cache = _build_message(on_disk)
    return _cache  # type: ignore[return-value]


def warn_if_stale(stream: Literal["stdout", "stderr"] = "stderr") -> None:
    """Print the staleness warning (if any) to stdout or stderr. Silent when healthy."""
    msg = check_pipeline_version()
    if msg is None:
        return
    target = sys.stderr if stream == "stderr" else sys.stdout
    print(msg, file=target)
