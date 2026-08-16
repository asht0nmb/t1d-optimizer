"""Assemble the M2-labeled meal-rise corpus from Supabase, for supervised modeling.

*** RESEARCH / EXPLORATION TOOLING — not the production calibration report.
`scripts/score_meal_rise.py` (existing, unmodified) is the advisory M2
calibration report generator; this script exists purely to produce a
DataFrame suitable for training a classifier, which is a different
downstream consumer with different needs (feature engineering, a
chronological split, persisted-to-parquet output) that don't belong in the
calibration report script. ***

Runtime note: `find_meal_rise_instances` slides the detector across every
CGM reading (see `detection/calibration/meal_rise_scoring.py`). Measured on
this repo's real data: ~0.27ms/row, so even 5 years of CGM history
(~327K rows) finishes in under 2 minutes — timed before writing this
script specifically because a naive read would assume the O(n) sliding
window doesn't scale; empirically here it does.

Date range default mirrors `build_daily_features_dataset.py`'s reasoning
(trailing 18 months) — see that script's docstring for the regime-mixing
rationale, which applies here too: therapy settings (basal rates, carb
ratios) drift over years, and a classifier trained across multiple
regimens is learning a mixture, not one person's current behavior.

Usage:
    uv run python scripts/build_meal_rise_corpus.py [--start YYYY-MM-DD]
        [--end YYYY-MM-DD] [--out data/processed/meal_rise_corpus.parquet]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from core.storage.supabase import SupabaseStorage
from detection.calibration.meal_rise_scoring import find_meal_rise_instances, score_instances
from detection.config import get_config
from detection.supervised import scored_instances_to_frame

_DEFAULT_WINDOW_DAYS = 548  # ~18 months; see module docstring.


def _parse_date(s: str | None):
    return datetime.strptime(s, "%Y-%m-%d").date() if s else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", help="YYYY-MM-DD inclusive (config tz)")
    parser.add_argument("--end", help="YYYY-MM-DD exclusive (config tz)")
    parser.add_argument(
        "--out", default="data/processed/meal_rise_corpus.parquet", type=Path
    )
    parser.add_argument("--db-url-env", default="SUPABASE_DB_URL")
    args = parser.parse_args()

    import os

    from dotenv import load_dotenv

    load_dotenv()
    db_url = os.environ.get(args.db_url_env)
    if not db_url:
        raise SystemExit(f"{args.db_url_env} not set.")

    config = get_config()
    tz = ZoneInfo(config.timezone)
    end_date = _parse_date(args.end) or datetime.now(tz).date()
    start_date = _parse_date(args.start) or (end_date - timedelta(days=_DEFAULT_WINDOW_DAYS))
    since = pd.Timestamp(datetime(start_date.year, start_date.month, start_date.day, tzinfo=tz))
    until = pd.Timestamp(datetime(end_date.year, end_date.month, end_date.day, tzinfo=tz))

    print(f"Loading cgm + requests: {start_date} .. {end_date}", flush=True)
    with SupabaseStorage.from_pooler_url(db_url) as storage:
        cgm = storage.read_table("cgm", since=since, until=until)
        requests = storage.read_table("requests", since=since, until=until)
    print(f"  cgm: {len(cgm)} rows, requests: {len(requests)} rows", flush=True)

    pump_serial = None
    if not requests.empty and "pump_serial" in requests.columns:
        serials = requests["pump_serial"].dropna().unique()
        pump_serial = str(serials[0]) if len(serials) == 1 else None

    print("Sliding detector across CGM history...", flush=True)
    detections = find_meal_rise_instances(cgm, config)
    print(f"  {len(detections)} raw meal-rise instances (post-refractory-dedupe)", flush=True)

    scored = score_instances(
        detections, requests, config.meal_rise_calibration, pump_serial=pump_serial
    )
    corpus = scored_instances_to_frame(scored)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    corpus.to_parquet(args.out, index=False)
    print(f"Wrote {len(corpus)} labeled instances -> {args.out}")
    print(corpus["label"].value_counts())


if __name__ == "__main__":
    main()
