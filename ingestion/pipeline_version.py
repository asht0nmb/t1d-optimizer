"""Pipeline output-schema version.

Bumped by hand whenever a builder or enricher in this package changes its
output schema or timestamp semantics in a way that invalidates
previously-written parquets in `data/processed/`. Every `fetch` / `fetch-day`
run embeds this version in `data/processed/.pipeline_version.json`; CLIs
read that sidecar and warn when it's behind the code.

When bumping:
    1. Add a new key-value entry to `PIPELINE_VERSION_CHANGELOG` describing
       *why* the bump invalidates prior parquets.
    2. Bump `PIPELINE_VERSION` to match.
    3. Run the full test suite and update any integration fixtures that
       depended on the old schema.
    4. Recommend `uv run python main.py fetch --clean` in the PR description
       so reviewers know downstream data needs to be regenerated.

This is a semantic signal, not a schema hash. Cosmetic refactors (renaming a
helper, tightening types without changing output) do **not** bump.
"""

from __future__ import annotations

PIPELINE_VERSION: int = 3

PIPELINE_VERSION_CHANGELOG: dict[int, str] = {
    1: (
        "Initial tconnectsync ingestion. Backfilled CGM rows were stamped "
        "with `eventTimestamp` (pump-received burst time), causing all "
        "post-outage readings to stack at the reconnection second."
    ),
    2: (
        "`35041db` — intended to use `egvTimestamp` (sensor time) as the "
        "primary `timestamp` for backfilled CGM rows, plus add the enrichment "
        "layer. Stillborn: the fix called `egv_ts.datetime`, but upstream "
        "`tconnectsync` types `egvTimestamp` as a raw `int # sec`, so any "
        "fetch that hit a backfilled row crashed with `AttributeError`. "
        "No v2 parquet ever made it to disk; bumping to v3 is the actual "
        "shipped behavior. Listed here only so the changelog remains "
        "contiguous."
    ),
    3: (
        "Decode `egvTimestamp` as `int` seconds since `TANDEM_EPOCH` "
        "(2008-01-01) and re-label into the user's TZ — mirrors upstream "
        "`process_cgm_reading.timestamp_for`. Backfilled CGM rows now land "
        "at their real sensor wall-clock instead of the pump-reconnect "
        "second; `eventTimestamp` is preserved in `sensor_timestamp`. "
        "Includes the enrichment layer originally introduced for v2 "
        "(`bolus_category`, `override_delta`, `forced_by_alarm`, "
        "`site_issues.parquet`, `cgm_gaps.parquet`)."
    ),
}
