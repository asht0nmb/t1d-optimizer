"""Build a one-row-per-day feature dataset from Supabase, for ML exploration.

*** Research/exploration tooling — not part of the production pipeline. ***
Lives in `scripts/` (not `detection/`) specifically because it needs to talk
to a concrete storage backend (`SupabaseStorage`, which wraps `psycopg2`),
and `detection/` is not allowed to import backend SDKs (see CLAUDE.md's
`core/` package boundary — the same rule extends informally to `detection/`,
which stays DataFrame-in/DataFrame-out and source-agnostic). `detection/`
modules (`daily_features`, and the new `detection/clustering.py`) never know
where their input DataFrames came from; this script is the only place that
knows it's Supabase.

WHY A SEPARATE ASSEMBLY STEP AT ALL?
-------------------------------------
`detection.features.daily_features(frames, date, config)` computes features
for exactly one day, given the 7 normalized frames already sliced to roughly
that day's data. It's deliberately a pure, single-day function — easy to
unit test, easy to reason about, and it's also what a future real-time
"which pattern am I in today" feature would call. But clustering needs many
rows (one per historical day) to find structure across days. Something has
to (a) pull enough raw history from storage, (b) loop over every calendar
day in range, and (c) stitch the per-day dicts into a DataFrame. That's all
this script does — no feature logic lives here, only I/O and looping.

WHY QUERY SUPABASE DIRECTLY INSTEAD OF GOING THROUGH `ingestion/`?
--------------------------------------------------------------------
`ingestion.view_data.load_frames` only reads local parquet files
(`data/processed/*.parquet`), which do not exist in this environment — the
Aug 2026 sync work (see docs/updates) landed the data in Supabase, not on
disk. Querying `SupabaseStorage` directly (via the `Storage` Protocol) is
also the architecturally "correct" direction per CLAUDE.md: "New downstream
code... takes a Storage via DI from the start." This script is that new
downstream code.

CONNECTION MODE
----------------
This is a short one-shot analytical read (not a long-lived server, not a
bulk write). Per CLAUDE.md's storage guidance, that means the transaction
pooler (`SupabaseStorage.from_pooler_url`), used as a context manager so the
connection is always closed. `SUPABASE_DB_URL` in this repo's `.env` happens
to already point at the pooler host (`*.pooler.supabase.com:6543`), which is
what makes this safe; if a user's `.env` instead holds the direct connection
string (`db.*.supabase.co:5432`, meant only for the nightly sync job and
`bootstrap_supabase.py`), this script will still *work* against it, but
shouldn't be left running as anything long-lived against that host.

DATE RANGE DEFAULT
--------------------
Full-history clustering would mix pump/CGM eras (the owner's Supabase CGM
history spans late 2021 to today) and, more importantly, mixes therapy
regimens (basal rates, carb ratios, insulin-to-carb settings all get
retuned over years by an endo) that have nothing to do with "what kind of
day was this." A day from a 2022 regimen and a day from a 2026 regimen can
look different in the feature space for reasons that have nothing to do
with recurring behavioral/physiological *patterns* — which is what
clustering is trying to find. Defaulting to a trailing window (18 months)
is a bias/variance tradeoff: long enough for `n_clusters` clusters to each
get a reasonable sample of days, short enough to plausibly reflect one
"regimen era." Override with `--start`/`--end` to analyze a different
window or the full history; the tradeoff is discussed in
`docs/ml-notes/clustering.md`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from core.schema import TABLES
from core.storage.supabase import SupabaseStorage
from detection.config import AppConfig, get_config
from detection.features import daily_features

# The 7 frames `daily_features` reads from (see detection/features.py).
# `bolus` is fetched too (not consumed by daily_features today, but cheap
# to carry and useful for downstream M2 corpus building in the same run).
_FRAME_TABLES = ("cgm", "requests", "basal", "alarms", "suspension", "cgm_gaps", "bolus")

# Expected CGM readings per day at 5-minute cadence. Used only to compute
# `cgm_reading_count` / coverage — NOT fed into daily_features, and not a
# config value because it's a physical constant of the sensor cadence, not
# a personal threshold.
_EXPECTED_READINGS_PER_DAY = 288

_DEFAULT_WINDOW_DAYS = 548  # ~18 months; see module docstring.


def load_frames_from_supabase(
    db_url: str, since: datetime, until: datetime
) -> dict[str, pd.DataFrame]:
    """Pull the 7 frames `daily_features` needs, windowed to [since, until).

    One connection, opened and closed via the context manager — this is a
    short-lived analytical read, not a long-lived server process.
    """
    frames: dict[str, pd.DataFrame] = {}
    with SupabaseStorage.from_pooler_url(db_url) as storage:
        for name in _FRAME_TABLES:
            assert name in TABLES, f"unexpected table name {name!r}"
            frames[name] = storage.read_table(name, since=since, until=until)
    return frames


def _date_range(start: _date, end: _date):
    d = start
    while d < end:
        yield d
        d += timedelta(days=1)


def build_dataset(
    frames: dict[str, pd.DataFrame], config: AppConfig, start: _date, end: _date
) -> pd.DataFrame:
    """Loop every calendar day in `[start, end)` and assemble one row each.

    Adds `cgm_reading_count` (raw count, not in `daily_features`'s
    contract) so callers can apply their own coverage filter before
    clustering — see `detection/clustering.py`'s `filter_low_coverage_days`.
    """
    tz = ZoneInfo(config.timezone)
    cgm = frames.get("cgm", pd.DataFrame())
    rows = []
    for d in _date_range(start, end):
        row = daily_features(frames, d, config)
        day_start = pd.Timestamp(datetime(d.year, d.month, d.day, tzinfo=tz))
        day_end = day_start + pd.Timedelta(days=1)
        if cgm is not None and not cgm.empty and "timestamp" in cgm.columns:
            ts = cgm["timestamp"]
            count = int(((ts >= day_start) & (ts < day_end)).sum())
        else:
            count = 0
        row["cgm_reading_count"] = count
        row["cgm_coverage_fraction"] = count / _EXPECTED_READINGS_PER_DAY
        rows.append(row)
    return pd.DataFrame(rows)


def _parse_date(s: str | None) -> _date | None:
    return datetime.strptime(s, "%Y-%m-%d").date() if s else None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a one-row-per-day feature dataset from Supabase "
        "for clustering / ML exploration (research tooling, not production)."
    )
    parser.add_argument("--start", help="YYYY-MM-DD inclusive (config tz). Default: end - 18mo.")
    parser.add_argument("--end", help="YYYY-MM-DD exclusive (config tz). Default: today.")
    parser.add_argument(
        "--out", default="data/processed/daily_features.parquet", type=Path,
        help="Output parquet path (gitignored; contains personal health aggregates).",
    )
    parser.add_argument(
        "--db-url-env", default="SUPABASE_DB_URL",
        help="Env var holding the Postgres connection string.",
    )
    args = parser.parse_args()

    import os

    from dotenv import load_dotenv

    load_dotenv()  # no-op if .env is absent; mirrors scripts/bootstrap_supabase.py
    db_url = os.environ.get(args.db_url_env)
    if not db_url:
        raise SystemExit(
            f"{args.db_url_env} not set. This script reads real Supabase "
            f"data; set it in .env (see .env.example) or export it."
        )

    config = get_config()
    tz = ZoneInfo(config.timezone)
    end = _parse_date(args.end) or datetime.now(tz).date()
    start = _parse_date(args.start) or (end - timedelta(days=_DEFAULT_WINDOW_DAYS))

    since = pd.Timestamp(datetime(start.year, start.month, start.day, tzinfo=tz))
    until = pd.Timestamp(datetime(end.year, end.month, end.day, tzinfo=tz))

    print(f"Loading frames from Supabase: {start} .. {end} ({args.db_url_env})", flush=True)
    frames = load_frames_from_supabase(db_url, since, until)
    for name, df in frames.items():
        print(f"  {name}: {len(df)} rows", flush=True)

    print(f"Assembling daily features for {(end - start).days} days...", flush=True)
    dataset = build_dataset(frames, config, start, end)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_parquet(args.out, index=False)
    print(f"Wrote {len(dataset)} rows -> {args.out}")


if __name__ == "__main__":
    main()
