"""`doctor` subcommand — diagnose the health of the processed data layer.

Surfaces three things in one place:

1. Pipeline version banner — what the code is (from
   `ingestion.pipeline_version.PIPELINE_VERSION`) vs. what the sidecar in
   `data/processed/.pipeline_version.json` says was last written.
2. Staleness recommendation — whether `fetch --clean` is needed.
3. Data-quality heuristics — currently only the "same-second CGM stacking"
   signature, which flags the pre-v2 bug where backfilled readings collapsed
   onto the pump-reconnect second.

Typical usage:

    uv run python main.py doctor

`doctor` is read-only and cheap — it never touches the network or
long-running detection; it's safe to run before any CLI to verify the
on-disk state is trustworthy.
"""

from __future__ import annotations

import pandas as pd

from ingestion import storage, version_guard
from ingestion.pipeline_version import (
    PIPELINE_VERSION,
    PIPELINE_VERSION_CHANGELOG,
)


# Burst of ≥ this many CGM rows at the same second is almost always the
# pre-v2 backfill bug, not legitimate dense sampling (Dexcom G6/G7 publish
# at most one reading every 5 minutes; anything > 1 co-timestamped row is
# pathological, but we pick a conservative floor to avoid false positives
# on rare duplicate-seqnum edge cases).
_STACKING_THRESHOLD = 3


def _print_banner() -> None:
    print(f"code pipeline version: v{PIPELINE_VERSION}")
    changelog_entry = PIPELINE_VERSION_CHANGELOG.get(PIPELINE_VERSION, "")
    if changelog_entry:
        print(f"  └─ {changelog_entry}")

    on_disk = storage.read_pipeline_version()
    if on_disk is None:
        if storage.PIPELINE_VERSION_FILE.exists():
            print("on-disk pipeline version: invalid (unreadable sidecar)")
        else:
            print("on-disk pipeline version: unversioned (no sidecar)")
    else:
        print(f"on-disk pipeline version: v{on_disk}")


def _present_parquets() -> list[str]:
    present = []
    for name, filename in storage.PARQUET_FILES.items():
        if (storage.PROCESSED_DIR / filename).exists():
            present.append(name)
    return present


def _check_same_second_stacking() -> str | None:
    """Return a warning message if CGM has same-second row bursts, else None.

    Looks at the full CGM table (not just recent rows): the bug that
    originally motivated this check left permanent artifacts at every
    connectivity gap in the historical dataset, so narrowing the search
    would miss real staleness evidence.

    Unreadable/corrupt parquet is silently ignored — the version-mismatch
    banner above already flags that something is wrong.
    """
    try:
        cgm = storage.load_df("cgm")
    except Exception:
        return None
    if cgm is None or cgm.empty:
        return None
    if "timestamp" not in cgm.columns:
        return None

    timestamps = pd.to_datetime(cgm["timestamp"])
    # `dt.floor` raises on DST-fallback ambiguous times for tz-aware series
    # (e.g. 2023-11-05 01:00–02:00 in US/Eastern). UTC has no DST, so round-trip
    # via UTC to bucket safely; restore the original tz so worst_bucket prints
    # in the user's wall clock.
    original_tz = timestamps.dt.tz
    if original_tz is not None:
        buckets = (
            timestamps.dt.tz_convert("UTC")
            .dt.floor("1s")
            .dt.tz_convert(original_tz)
        )
    else:
        buckets = timestamps.dt.floor("1s")
    counts = buckets.value_counts()
    stacked = counts[counts >= _STACKING_THRESHOLD]
    if stacked.empty:
        return None

    worst_bucket = stacked.idxmax()
    worst_count = int(stacked.max())
    return (
        f"⚠️  same-second CGM stacking detected: {len(stacked)} timestamp(s) "
        f"with ≥{_STACKING_THRESHOLD} readings (worst: {worst_count} rows @ "
        f"{worst_bucket}). This is the signature of the pre-v2 backfill bug. "
        f"Run `uv run python main.py fetch --clean` to regenerate with correct "
        f"sensor-time timestamps."
    )


def doctor() -> None:
    """Emit a health report. Exits with the normal process status (never
    raises on a stale or damaged state — this is a diagnostic, not a gate)."""
    _print_banner()

    present = _present_parquets()
    if not present:
        print("\nno processed data on disk (no parquet files)")
        print("  → run: uv run python main.py fetch")
        return

    print(f"\nprocessed parquet tables present: {len(present)}/{len(storage.PARQUET_FILES)}")
    for name in present:
        print(f"  - {name}")
    missing = set(storage.PARQUET_FILES) - set(present)
    if missing:
        print(f"missing parquet tables: {sorted(missing)}")

    staleness = version_guard.check_pipeline_version()
    if staleness:
        print()
        print(staleness)

    stacking = _check_same_second_stacking()
    if stacking:
        print()
        print(stacking)

    if staleness is None and stacking is None:
        print("\npipeline state: OK")


if __name__ == "__main__":
    doctor()
