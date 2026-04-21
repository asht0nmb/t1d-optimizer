"""Entry points for the Task 2.6 detection CLI subcommands.

Thin glue between `main.py` and the detection modules. Each function
loads the necessary enriched parquets, invokes the corresponding
detection routine, and prints a human-readable summary.

Usage (invoked by `main.py`):
    uv run python main.py analyze-anomalies --date YYYY-MM-DD
    uv run python main.py analyze-meals --date YYYY-MM-DD
    uv run python main.py cluster-days [--retrain] [--start ...] [--end ...]
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from detection.anomaly import detect_anomalies
from detection.clustering import cluster_days
from detection.config import AppConfig, get_config
from detection.features import daily_features
from detection.meal import detect_meals
from ingestion.storage import PROCESSED_DIR, load_df

__all__ = ["run_anomalies", "run_meals", "run_clustering"]

_CLUSTER_FRAME_NAMES = (
    "cgm",
    "bolus",
    "basal",
    "requests",
    "alarms",
    "suspension",
    "cgm_gaps",
)

_CLUSTERS_PARQUET = PROCESSED_DIR / "daily_clusters.parquet"


def _parse_date(date_str: str) -> date:
    return date.fromisoformat(date_str)


def _slice_to_day(
    df: pd.DataFrame | None,
    day: date,
    config: AppConfig,
    ts_col: str = "timestamp",
) -> pd.DataFrame:
    """Return rows in ``df[ts_col]`` that fall inside ``day`` in ``config.timezone``."""
    if df is None or df.empty or ts_col not in df.columns:
        return df.iloc[0:0] if isinstance(df, pd.DataFrame) else pd.DataFrame()

    tz = ZoneInfo(config.timezone)
    day_start = pd.Timestamp(
        year=day.year, month=day.month, day=day.day, tz=tz
    )
    day_end = day_start + pd.Timedelta(days=1)
    ts = df[ts_col]
    mask = (ts >= day_start) & (ts < day_end)
    return df.loc[mask]


def _require_parquet(name: str) -> pd.DataFrame:
    """Load an enriched parquet or exit(1) with a clear message."""
    df = load_df(name)
    if df is None:
        path = PROCESSED_DIR / f"{name}.parquet"
        print(
            f"error: required parquet not found: {path}. "
            f"Run `uv run python main.py fetch` first.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return df


def run_anomalies(date_str: str) -> None:
    """Load enriched CGM, slice to ``date_str``, run anomaly detection, print."""
    get_config.cache_clear()
    config = get_config()
    target = _parse_date(date_str)

    cgm = _require_parquet("cgm")
    cgm_day = _slice_to_day(cgm, target, config)

    if cgm_day.empty:
        print(f"No CGM readings found for {target}.")
        return

    anomalies = detect_anomalies(cgm_day, config)
    print(f"\nAnomalies for {target}: {len(anomalies)}\n")
    if anomalies.empty:
        print(f"No anomalies detected for {target}.")
        return
    print(anomalies.to_string(index=False))


def run_meals(date_str: str) -> None:
    """Load enriched CGM + requests, slice to ``date_str``, detect missed meals."""
    get_config.cache_clear()
    config = get_config()
    target = _parse_date(date_str)

    cgm = _require_parquet("cgm")
    requests = _require_parquet("requests")

    cgm_day = _slice_to_day(cgm, target, config)
    requests_day = _slice_to_day(requests, target, config)

    if cgm_day.empty:
        print(f"No CGM readings found for {target}.")
        return

    meals = detect_meals(cgm_day, requests_day, config)
    print(f"\nMissed meals for {target}: {len(meals)}\n")
    if meals.empty:
        print(f"No missed meals detected for {target}.")
        return
    print(meals.to_string(index=False))


def _daterange(start: date, end: date) -> list[date]:
    if end < start:
        return []
    n = (end - start).days + 1
    return [start + timedelta(days=i) for i in range(n)]


def _cgm_date_bounds(cgm: pd.DataFrame, config: AppConfig) -> tuple[date, date]:
    """Return (min, max) calendar dates of ``cgm.timestamp`` in config tz."""
    tz = ZoneInfo(config.timezone)
    ts = pd.to_datetime(cgm["timestamp"])
    if ts.dt.tz is None:
        local = ts.dt.tz_localize(tz)
    else:
        local = ts.dt.tz_convert(tz)
    dates = local.dt.date
    return dates.min(), dates.max()


def run_clustering(
    retrain: bool,
    start: str | None,
    end: str | None,
) -> None:
    """Build daily features across the date range and cluster them.

    ``daily_features`` handles per-day slicing internally, so the full
    frames are passed on every iteration (plan §2.4). The fitted model
    is persisted via `detection.clustering.cluster_days`, and the
    resulting assignments are written to
    ``data/processed/daily_clusters.parquet``.
    """
    get_config.cache_clear()
    config = get_config()

    cgm = _require_parquet("cgm")
    frames: dict[str, pd.DataFrame] = {"cgm": cgm}
    for name in _CLUSTER_FRAME_NAMES:
        if name == "cgm":
            continue
        df = load_df(name)
        frames[name] = df if df is not None else pd.DataFrame()

    if cgm.empty:
        print("No CGM data available; nothing to cluster.")
        return

    cgm_min, cgm_max = _cgm_date_bounds(cgm, config)
    start_date = _parse_date(start) if start else cgm_min
    end_date = _parse_date(end) if end else cgm_max

    dates = _daterange(start_date, end_date)
    if not dates:
        print(
            f"No dates to cluster: start={start_date} is after end={end_date}."
        )
        return

    rows: list[dict] = []
    for day in dates:
        feats = daily_features(frames, day, config)
        rows.append(feats)

    features_df = pd.DataFrame(rows)
    if features_df.empty:
        print("No daily features produced; nothing to cluster.")
        return

    result = cluster_days(features_df, config, retrain=retrain)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    result.to_parquet(_CLUSTERS_PARQUET, index=False)

    cluster_sizes = (
        result["cluster_id"].value_counts().sort_index()
        if not result.empty
        else pd.Series(dtype="int64")
    )

    print(f"\nClustered {len(result)} day(s) from {start_date} to {end_date}.")
    if not cluster_sizes.empty:
        print("\nCluster sizes:")
        for cluster_id, count in cluster_sizes.items():
            print(f"  cluster {int(cluster_id)}: {int(count)} day(s)")

    model_dir = Path(config.clustering.model_dir)
    print(f"\nModel saved to: {model_dir}")
    print(f"Assignments written to: {_CLUSTERS_PARQUET}")
