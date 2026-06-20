"""M2 calibration runner: score historical meal-rise detections, emit a report.

Advisory only — proposes values for existing config variables (notably
``meal_rise.base_slope_mgdl_per_min``); never edits config itself.

Rerun:  uv run python scripts/score_meal_rise.py [--start YYYY-MM-DD]
        [--end YYYY-MM-DD] [--sweep 1.4,1.6,1.8,2.0,2.2]
        [--out-dir data/reports]
Inputs: data/processed/*.parquet (enrichment backfilled in memory).
Output: data/reports/meal_rise_scores_<UTCstamp>.md + .json (gitignored —
        contains personal health data; do not commit).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Make ``import detection...`` work when this script is executed directly
# (``uv run python scripts/score_meal_rise.py``); the project is not
# installed as a package, so the repo root must be on sys.path.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from detection.calibration.meal_rise_scoring import (
    LABEL_LATE,
    LABEL_PRE,
    LABEL_UNCOVERED,
    ScoredInstance,
    find_meal_rise_instances,
    score_instances,
    summarize,
)
from detection.config import AppConfig, get_config


def to_records(scored: list[ScoredInstance]) -> list[dict]:
    """ScoredInstance list → JSON-safe dicts (datetimes to isoformat)."""
    records = []
    for s in scored:
        d = dataclasses.asdict(s)
        for key, val in d.items():
            if isinstance(val, datetime):
                d[key] = val.isoformat()
        records.append(d)
    return records


def build_markdown_report(
    scored: list[ScoredInstance],
    *,
    base_slope: float,
    sweep_rows: list[dict],
    date_range: tuple[str | None, str | None],
) -> str:
    summary = summarize(scored)
    lines = [
        "# Meal-rise calibration report (M2)",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Date range: {date_range[0] or 'all'} → {date_range[1] or 'all'}",
        f"Production base_slope_mgdl_per_min: {base_slope}",
        "",
        "> **Advisory only.** This report proposes values for existing config",
        "> variables. Apply changes by editing `config/user_config.yaml` in a",
        "> reviewed commit with its own dated docs/updates entry.",
        "",
        "## Summary (at production slope)",
        "",
        f"- Total instances: {summary['total']}",
        f"- pre_bolused: {summary['counts'][LABEL_PRE]}",
        f"- late_bolused: {summary['counts'][LABEL_LATE]}",
        f"- uncovered: {summary['counts'][LABEL_UNCOVERED]}",
        f"- uncovered rate: {summary['uncovered_rate']:.1%}",
        f"- uncovered resolutions: {summary['uncovered_resolutions']}",
        "",
        "## Per-hour breakdown",
        "",
        "| Hour | pre_bolused | late_bolused | uncovered |",
        "|------|-------------|--------------|-----------|",
    ]
    by_hour: dict[int, Counter] = {}
    for s in scored:
        by_hour.setdefault(s.hour_of_day, Counter())[s.label] += 1
    for hour in sorted(by_hour):
        c = by_hour[hour]
        lines.append(
            f"| {hour:02d} | {c.get(LABEL_PRE, 0)} | "
            f"{c.get(LABEL_LATE, 0)} | {c.get(LABEL_UNCOVERED, 0)} |"
        )
    if sweep_rows:
        lines += [
            "",
            "## Sensitivity sweep over base_slope_mgdl_per_min",
            "",
            "| base_slope | total | pre_bolused | late_bolused | uncovered | uncovered_rate |",
            "|------------|-------|-------------|--------------|-----------|----------------|",
        ]
        for row in sweep_rows:
            lines.append(
                f"| {row['base_slope']} | {row['total']} | {row['pre_bolused']} | "
                f"{row['late_bolused']} | {row['uncovered']} | "
                f"{row['uncovered_rate']:.1%} |"
            )
    lines.append("")
    return "\n".join(lines)


def _load_enriched_frames(config: AppConfig) -> dict[str, pd.DataFrame]:
    """Isolated for tests; real path reads data/processed parquets."""
    from ingestion.view_data import load_frames  # local: keeps script import-light

    return load_frames("enriched", config)


def _slice_range(df: pd.DataFrame, start: str | None, end: str | None,
                 tz: ZoneInfo) -> pd.DataFrame:
    if df.empty or "timestamp" not in df.columns:
        return df
    out = df
    if start:
        out = out[out["timestamp"] >= pd.Timestamp(start, tz=tz)]
    if end:
        out = out[out["timestamp"] < pd.Timestamp(end, tz=tz)
                  + pd.DateOffset(days=1)]
    return out


def _single_pump_serial(requests: pd.DataFrame) -> str | None:
    if requests.empty or "pump_serial" not in requests.columns:
        return None
    serials = requests["pump_serial"].dropna().unique()
    return str(serials[0]) if len(serials) == 1 else None


def run(*, start: str | None, end: str | None, out_dir: Path,
        sweep: list[float], config: AppConfig | None = None) -> dict:
    if config is None:
        config = get_config()
    tz = ZoneInfo(config.timezone)
    frames = _load_enriched_frames(config)
    cgm = _slice_range(frames.get("cgm", pd.DataFrame()), start, end, tz)
    requests = _slice_range(frames.get("requests", pd.DataFrame()),
                            start, end, tz)

    pump_serial = _single_pump_serial(requests)
    detections = find_meal_rise_instances(cgm, config)
    scored = score_instances(
        detections, requests, config.meal_rise_calibration,
        pump_serial=pump_serial,
    )
    summary = summarize(scored)

    sweep_rows = []
    for slope in sweep:
        print(f"sweep base_slope={slope} ...", flush=True)
        cfg_v = dataclasses.replace(
            config, meal_rise=dataclasses.replace(
                config.meal_rise, base_slope_mgdl_per_min=slope))
        s_detections = find_meal_rise_instances(cgm, cfg_v)
        s_scored = score_instances(
            s_detections, requests, config.meal_rise_calibration,
            pump_serial=pump_serial)
        s = summarize(s_scored)
        sweep_rows.append({
            "base_slope": slope, "total": s["total"],
            "pre_bolused": s["counts"][LABEL_PRE],
            "late_bolused": s["counts"][LABEL_LATE],
            "uncovered": s["counts"][LABEL_UNCOVERED],
            "uncovered_rate": s["uncovered_rate"],
        })

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    md = build_markdown_report(
        scored, base_slope=config.meal_rise.base_slope_mgdl_per_min,
        sweep_rows=sweep_rows, date_range=(start, end))
    (out_dir / f"meal_rise_scores_{stamp}.md").write_text(md)
    (out_dir / f"meal_rise_scores_{stamp}.json").write_text(json.dumps(
        {"summary": summary, "sweep": sweep_rows,
         "records": to_records(scored)}, indent=2))
    return {"summary": summary, "sweep_rows": sweep_rows,
            "scored": scored, "out_dir": out_dir}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score historical meal-rise detections (M2 calibration). "
                    "Advisory output only — never modifies config.")
    parser.add_argument("--start", help="YYYY-MM-DD inclusive (config tz)")
    parser.add_argument("--end", help="YYYY-MM-DD inclusive (config tz)")
    parser.add_argument("--out-dir", default="data/reports", type=Path)
    parser.add_argument(
        "--sweep", default="",
        help="comma-separated base_slope values, e.g. 1.4,1.6,1.8,2.0,2.2")
    args = parser.parse_args()
    sweep = [float(v) for v in args.sweep.split(",") if v.strip()]
    result = run(start=args.start, end=args.end,
                 out_dir=args.out_dir, sweep=sweep)
    s = result["summary"]
    print(f"instances={s['total']} uncovered_rate={s['uncovered_rate']:.1%} "
          f"reports → {result['out_dir']}")


if __name__ == "__main__":
    main()
