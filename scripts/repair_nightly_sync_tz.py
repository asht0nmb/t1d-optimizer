"""One-time repair: correct the TIMEZONE_NAME nightly-sync mislabeling.

Background (see docs/updates/2026-08-16-tandem-nightly-sync-restoration.md):
the nightly Tandem->Supabase sync workflow never wired the TIMEZONE_NAME
secret through to the sync script's environment, so tconnectsync silently
defaulted to America/New_York instead of the owner's actual
America/Los_Angeles for every timestamp it decoded. Bootstrap-era data
(loaded locally, correct TIMEZONE_NAME) and the live 5-min alert loop
(separate code path, doesn't use tconnectsync) are unaffected.

Verified empirically (not assumed) by re-fetching known windows from the
live Tandem Source API with the now-fixed workflow and diffing against
what's stored: every row is correctly Pacific-labeled up to and including
bolus_id=1867 / cgm seqnum<510402 (2026-05-31, true time ~13:55 Pacific),
and every row from bolus_id=1868 / cgm seqnum=510402 onward (~14:07 Pacific
the same day) through the last successful nightly run on 2026-06-30 is
mislabeled Eastern-instead-of-Pacific -- a uniform -3 hour offset (PDT is
UTC-7, EDT is UTC-4). No pump clock-change events in the window, so this is
a labeling bug, not a real timezone relocation.

This script corrects every affected timestamp column by adding 3 hours,
scoped to pump_serial='1513861' (the only pump active in this window) and
the empirically-verified boundary. It is provably PK-safe: a uniform shift
preserves distinctness among corrupted rows (no internal collision), and
shifted rows land strictly after BOUNDARY_UTC so they cannot collide with
untouched pre-boundary correct rows. Any eventual overlap with freshly
synced post-outage data (>= 2026-07-01) is benign -- it would mean a
corrected row's true timestamp coincides with a freshly-fetched row for the
same real event, and ON CONFLICT DO NOTHING makes that idempotent.

Usage:
    uv run python scripts/repair_nightly_sync_tz.py            # dry run
    uv run python scripts/repair_nightly_sync_tz.py --apply    # execute

Already applied in production as of 2026-08-16 (verified: bolus_id 1868 and
several other spot-checks across bolus/basal/cgm_gaps in June match a fresh
re-fetch from the live API). NOT safely re-runnable: the WHERE clause is a
range on the *current* stored value, and a +3h shift mostly lands back
inside that same range, so a second --apply would shift already-corrected
rows by another 3 hours. main() refuses to --apply if the known sentinel
row (bolus_id=1868) is already at its corrected value -- see
_already_applied(). This script is kept for the record / as a template for
a similar future repair, not for routine re-execution.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import psycopg2
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

PUMP_SERIAL = "1513861"

# Empirically verified: bolus_id 1867 (stored 2026-05-31 17:55:57 UTC) is the
# last correct row; bolus_id 1868 (stored 2026-05-31 18:07:32 UTC) and cgm
# seqnum 510402 (stored 2026-05-31 18:07:43 UTC) are the first corrupted
# rows, ~11 seconds apart -- the same broken sync run touching every table.
BOUNDARY_UTC = "2026-05-31 18:00:00+00"

# Last successful nightly run was 2026-06-30 (fetch_state heartbeat
# ~09:55 UTC that day); nothing was written after that until this session's
# fix, so this cutoff can never touch a freshly-backfilled row.
CUTOFF_UTC = "2026-07-01 00:00:00+00"

# table -> (primary timestamp column used for the WHERE boundary,
#           [all timestamp columns to shift],
#           is the boundary column part of this table's primary key?)
#
# When the shifted column is part of the PK (basal, suspension, site_issues,
# cgm_gaps), a single-hop `SET ts = ts + 3h` can hit a same-statement unique
# violation: Postgres checks each row's new value against other rows'
# *current* (not-yet-updated) values as it processes the UPDATE, so two rows
# already exactly 3 hours apart in their corrupted state form a swap cycle
# and collide. Basal (a rate-change log, high frequency) hit this in
# practice. The fix is a two-hop shift through a disjoint century-old range:
# hop 1 moves every corrupted row into an offset date range nothing else
# occupies (no collision, since disjoint from both real data and, being a
# uniform shift, from each other); hop 2 moves from that disjoint range to
# the final correct timestamp (also no collision, since nothing else lives
# in the disjoint range to collide with). Tables whose PK doesn't include
# the timestamp (cgm, bolus, requests, events, alarms) have no such risk --
# duplicate timestamps there don't violate anything -- so they stay single-hop.
TABLES: dict[str, tuple[str, list[str], bool]] = {
    "cgm": ("timestamp", ["timestamp", "sensor_timestamp"], False),
    "bolus": ("timestamp", ["timestamp"], False),
    "requests": ("timestamp", ["timestamp"], False),
    "basal": ("timestamp", ["timestamp"], True),
    "suspension": ("suspend_timestamp", ["suspend_timestamp", "resume_timestamp"], True),
    "events": ("timestamp", ["timestamp"], False),
    "alarms": ("timestamp", ["timestamp"], False),
    "site_issues": (
        "first_occlusion_ts",
        ["first_occlusion_ts", "last_occlusion_ts", "resolved_by_site_change_ts"],
        True,
    ),
    "cgm_gaps": ("start_ts", ["start_ts", "end_ts"], True),
}

_DISJOINT_OFFSET = "100 years"

# bolus_id 1868 is the first known-corrupted row (see module docstring). Its
# corrected value is fixed and known; if the DB already shows it, the
# correction has already run and --apply must refuse.
_SENTINEL_BOLUS_ID = 1868
_SENTINEL_CORRECTED_UTC = "2026-05-31 21:07:32+00"


def _already_applied(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT timestamp FROM bolus WHERE pump_serial = %s AND bolus_id = %s",
            (PUMP_SERIAL, _SENTINEL_BOLUS_ID),
        )
        row = cur.fetchone()
    if row is None:
        return False
    return str(row[0]) >= _SENTINEL_CORRECTED_UTC


def repair(conn, *, apply: bool) -> dict[str, int]:
    """Return {table: rows_matched}. Only commits if apply=True."""
    results: dict[str, int] = {}
    with conn.cursor() as cur:
        for table, (boundary_col, shift_cols, pk_has_timestamp) in TABLES.items():
            cur.execute(
                f"SELECT count(*) FROM {table} "
                f"WHERE pump_serial = %s AND {boundary_col} >= %s AND {boundary_col} < %s",
                (PUMP_SERIAL, BOUNDARY_UTC, CUTOFF_UTC),
            )
            (matched,) = cur.fetchone()
            results[table] = matched
            logger.info("%s: %d row(s) in the corrupted window", table, matched)

            if not (apply and matched):
                continue

            if pk_has_timestamp:
                # Hop 1: into a disjoint range (no collision possible).
                hop1 = ", ".join(f"{c} = {c} - INTERVAL '{_DISJOINT_OFFSET}'" for c in shift_cols)
                cur.execute(
                    f"UPDATE {table} SET {hop1} "
                    f"WHERE pump_serial = %s AND {boundary_col} >= %s AND {boundary_col} < %s",
                    (PUMP_SERIAL, BOUNDARY_UTC, CUTOFF_UTC),
                )
                hop1_n = cur.rowcount
                # Hop 2: from the disjoint range to the final correct value.
                # Re-select by the *shifted* boundary window since these rows
                # temporarily sit 100 years in the past.
                shifted_lo = f"('{BOUNDARY_UTC}'::timestamptz - INTERVAL '{_DISJOINT_OFFSET}')"
                shifted_hi = f"('{CUTOFF_UTC}'::timestamptz - INTERVAL '{_DISJOINT_OFFSET}')"
                hop2 = ", ".join(
                    f"{c} = {c} + INTERVAL '{_DISJOINT_OFFSET}' + INTERVAL '3 hours'"
                    for c in shift_cols
                )
                cur.execute(
                    f"UPDATE {table} SET {hop2} "
                    f"WHERE pump_serial = %s AND {boundary_col} >= {shifted_lo} "
                    f"AND {boundary_col} < {shifted_hi}",
                    (PUMP_SERIAL,),
                )
                logger.info("%s: updated %d row(s) (two-hop, PK includes timestamp)", table, cur.rowcount)
                if cur.rowcount != hop1_n:
                    raise RuntimeError(
                        f"{table}: hop1 moved {hop1_n} rows but hop2 only found {cur.rowcount} "
                        "back -- aborting, investigate before re-running"
                    )
            else:
                set_clause = ", ".join(
                    f"{col} = {col} + INTERVAL '3 hours'" for col in shift_cols
                )
                cur.execute(
                    f"UPDATE {table} SET {set_clause} "
                    f"WHERE pump_serial = %s AND {boundary_col} >= %s AND {boundary_col} < %s",
                    (PUMP_SERIAL, BOUNDARY_UTC, CUTOFF_UTC),
                )
                logger.info("%s: updated %d row(s)", table, cur.rowcount)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Execute the correction. Without this flag, only previews row counts.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()

    db_url = os.environ["SUPABASE_DB_URL"]
    conn = psycopg2.connect(db_url)
    conn.autocommit = False

    if args.apply and _already_applied(conn):
        logger.error(
            "Sentinel row (bolus_id=%s) already shows the corrected value -- "
            "this repair has already been applied. Refusing to run --apply "
            "again to avoid double-shifting corrected data. Aborting.",
            _SENTINEL_BOLUS_ID,
        )
        conn.close()
        return 1

    try:
        results = repair(conn, apply=args.apply)
        total = sum(results.values())
        if args.apply:
            conn.commit()
            logger.info("Committed. %d row(s) corrected across %d table(s).", total, len(results))
        else:
            conn.rollback()
            logger.info(
                "Dry run: %d row(s) would be corrected across %d table(s). "
                "Re-run with --apply to execute.",
                total, len(results),
            )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
