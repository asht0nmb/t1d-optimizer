# Phase 3: M2 Calibration Runner (`scripts/score_meal_rise.py`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A CLI that runs the M2 meal-rise scoring module over historical parquet data and emits an advisory calibration report (markdown + JSON), including a sensitivity sweep over `base_slope_mgdl_per_min`.

**Architecture:** One new script module `scripts/score_meal_rise.py` (the orchestrator that `detection/calibration/meal_rise_scoring.py`'s docstring already names). Pure report-building functions at module top (testable without I/O), a thin `run()` orchestrator that loads frames via the existing `ingestion.view_data.load_frames("enriched", config)` helper, and an argparse `main()`. Reports are written under `data/reports/` (inside the gitignored `data/` tree — they contain personal health data and must never be committed).

**Tech Stack:** pandas, argparse, `dataclasses.replace` for the sweep, existing `detection/calibration/meal_rise_scoring.py` API: `find_meal_rise_instances(cgm_df, config) -> list[MealRiseDetection]`, `score_instances(detections, requests_df, calib, *, pump_serial=None) -> list[ScoredInstance]`, `summarize(scored) -> dict`.

**Binding constraint (ML deferral):** the report is advisory only. It proposes values for existing config variables; it must never edit `config/user_config.yaml` or any threshold itself, and the report header must say so and include exact rerun instructions.

**Verified API facts (do not rediscover):**
- `ingestion.view_data.load_frames("enriched", config)` returns a dict of DataFrames keyed `cgm`, `requests`, … with enrichment backfilled (`bolus_category` on requests). Missing parquets become empty DataFrames.
- `detection.config.get_config()` → `AppConfig` with `.meal_rise` (`MealRiseConfig`, frozen dataclass with field `base_slope_mgdl_per_min`), `.meal_rise_calibration`, `.timezone`.
- `ScoredInstance` is a frozen dataclass; fields include `label`, `hour_of_day`, datetimes (`anchor_ts`, `rise_start_ts`, …) and optionals (see `detection/calibration/meal_rise_scoring.py:61-82`).
- `cgm` frame columns: `timestamp` (tz-aware UTC), `bg_mgdl`. `requests` has `timestamp`, `bolus_category`, `carbs_g`, and `pump_serial`.
- Suite baseline: 576 passed, 42 skipped, 48 deselected.

---

### Task 1: Report-building pure functions (TDD)

**Files:**
- Create: `scripts/score_meal_rise.py`
- Create: `tests/detection/test_score_meal_rise.py`

- [ ] **Step 1: Write failing tests** in `tests/detection/test_score_meal_rise.py`:

```python
"""Tests for the M2 calibration runner (report building + orchestration)."""
from __future__ import annotations

from datetime import datetime, timezone

from detection.calibration.meal_rise_scoring import ScoredInstance
from scripts.score_meal_rise import build_markdown_report, to_records


def _scored(label="uncovered", hour=12, resolution="none"):
    ts = datetime(2026, 6, 1, hour, 0, tzinfo=timezone.utc)
    return ScoredInstance(
        event_ref=f"meal_rise:{ts.isoformat(timespec='minutes')}",
        pump_serial="123",
        label=label,
        anchor_ts=ts,
        rise_start_ts=ts,
        rise_end_ts=ts,
        start_level=120,
        end_level=170,
        delta=50,
        slope_mgdl_per_min=2.0,
        hour_of_day=hour,
        matched_bolus_ts=None if label == "uncovered" else ts,
        matched_bolus_category=None if label == "uncovered" else "user_meal",
        matched_bolus_carbs=None if label == "uncovered" else 45,
        bolus_delay_min=None if label == "uncovered" else -10.0,
        resolution=resolution if label == "uncovered" else None,
        resolution_ts=None,
        resolution_delay_min=None,
    )


class TestToRecords:
    def test_serializes_datetimes_to_isoformat(self):
        records = to_records([_scored()])
        assert records[0]["anchor_ts"] == "2026-06-01T12:00:00+00:00"
        assert records[0]["label"] == "uncovered"
        assert records[0]["matched_bolus_ts"] is None

    def test_empty_list(self):
        assert to_records([]) == []


class TestBuildMarkdownReport:
    def test_contains_summary_counts_and_advisory_header(self):
        scored = [_scored("pre_bolused", 8), _scored("uncovered", 12),
                  _scored("late_bolused", 19)]
        md = build_markdown_report(
            scored,
            base_slope=1.8,
            sweep_rows=[{"base_slope": 1.8, "total": 3,
                         "pre_bolused": 1, "late_bolused": 1,
                         "uncovered": 1, "uncovered_rate": 1 / 3}],
            date_range=("2026-05-01", "2026-06-01"),
        )
        assert "advisory" in md.lower()
        assert "uncovered" in md
        assert "| 1.8 |" in md            # sweep table row
        assert "Hour" in md               # per-hour breakdown section

    def test_per_hour_breakdown_buckets_by_hour(self):
        md = build_markdown_report(
            [_scored("uncovered", 7), _scored("uncovered", 7),
             _scored("pre_bolused", 12)],
            base_slope=1.8, sweep_rows=[], date_range=(None, None),
        )
        assert "| 07 |" in md and "| 12 |" in md
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/detection/test_score_meal_rise.py -v`
Expected: FAIL — `ModuleNotFoundError`/`ImportError` for `scripts.score_meal_rise` (note: `scripts/` has an `__init__.py`? Check; if not, import as `from scripts.score_meal_rise import ...` works only with one — look at how `tests/` import other scripts, e.g. `grep -rn 'from scripts' tests/ | head`, and follow that pattern. Existing tests import scripts modules, so mirror them.)

- [ ] **Step 3: Implement the pure functions** in `scripts/score_meal_rise.py`:

```python
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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
                f"| {row['late_bolused']} | {row['uncovered']} | "
                f"{row['uncovered_rate']:.1%} |".replace("| |", "|")
            )
    lines.append("")
    return "\n".join(lines)
```

NOTE: the sweep-row f-string above is intentionally shown raw — clean it up
during implementation so each row renders as
`| 1.8 | 41 | 20 | 9 | 12 | 29.3% |` (one cell per column, no doubled pipes).
Write it as a plain single f-string with six cells; the test asserts `"| 1.8 |"`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/detection/test_score_meal_rise.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/score_meal_rise.py tests/detection/test_score_meal_rise.py
git commit -m "feat(calibration): report builders for meal-rise scoring runner"
```

---

### Task 2: Orchestration (`run`) + sweep (TDD)

**Files:**
- Modify: `scripts/score_meal_rise.py`
- Modify: `tests/detection/test_score_meal_rise.py`

- [ ] **Step 1: Add failing tests** (synthetic frames; no parquet I/O — monkeypatch the loader):

```python
import numpy as np
import pandas as pd
import pytest

import scripts.score_meal_rise as runner


def _synthetic_frames():
    # 3 hours of 5-min CGM with one sharp rise at 12:00–12:30 (~3 mg/dL/min).
    ts = pd.date_range("2026-06-01 10:00", periods=36, freq="5min", tz="UTC")
    bg = np.full(36, 110.0)
    rise = slice(24, 31)                      # 12:00 .. 12:30
    bg[rise] = [110, 125, 140, 155, 170, 185, 200]
    bg[31:] = 200.0
    cgm = pd.DataFrame({"timestamp": ts, "bg_mgdl": bg})
    requests = pd.DataFrame(
        {
            "timestamp": [pd.Timestamp("2026-06-01 13:00", tz="UTC")],
            "bolus_category": ["user_correction_only"],
            "carbs_g": [float("nan")],
            "pump_serial": ["881111"],
        }
    )
    return {"cgm": cgm, "requests": requests}


class TestRun:
    def test_run_scores_and_writes_reports(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner, "_load_enriched_frames",
                            lambda config: _synthetic_frames())
        result = runner.run(start=None, end=None, out_dir=tmp_path, sweep=[])
        assert result["summary"]["total"] >= 1
        assert result["summary"]["counts"]["uncovered"] >= 1
        md_files = list(tmp_path.glob("meal_rise_scores_*.md"))
        json_files = list(tmp_path.glob("meal_rise_scores_*.json"))
        assert len(md_files) == 1 and len(json_files) == 1
        payload = json.loads(json_files[0].read_text())
        assert payload["summary"]["total"] == result["summary"]["total"]
        assert payload["records"][0]["label"]

    def test_date_filter_excludes_outside_range(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner, "_load_enriched_frames",
                            lambda config: _synthetic_frames())
        result = runner.run(start="2026-06-02", end=None,
                            out_dir=tmp_path, sweep=[])
        assert result["summary"]["total"] == 0

    def test_sweep_produces_row_per_slope(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner, "_load_enriched_frames",
                            lambda config: _synthetic_frames())
        result = runner.run(start=None, end=None, out_dir=tmp_path,
                            sweep=[1.0, 5.0])
        slopes = [row["base_slope"] for row in result["sweep_rows"]]
        assert slopes == [1.0, 5.0]
        # a 5.0 mg/dL/min threshold should find fewer (likely zero) instances
        assert result["sweep_rows"][1]["total"] <= result["sweep_rows"][0]["total"]
```

(Needs `import json` at test module top.)

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/detection/test_score_meal_rise.py -v` → new tests FAIL (`run`/`_load_enriched_frames` missing).

- [ ] **Step 3: Implement** in `scripts/score_meal_rise.py`:

```python
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
                  + pd.Timedelta(days=1)]
    return out


def _single_pump_serial(requests: pd.DataFrame) -> str | None:
    if requests.empty or "pump_serial" not in requests.columns:
        return None
    serials = requests["pump_serial"].dropna().unique()
    return str(serials[0]) if len(serials) == 1 else None


def run(*, start: str | None, end: str | None, out_dir: Path,
        sweep: list[float]) -> dict:
    config = get_config()
    tz = ZoneInfo(config.timezone)
    frames = _load_enriched_frames(config)
    cgm = _slice_range(frames.get("cgm", pd.DataFrame()), start, end, tz)
    requests = _slice_range(frames.get("requests", pd.DataFrame()),
                            start, end, tz)

    detections = find_meal_rise_instances(cgm, config)
    scored = score_instances(
        detections, requests, config.meal_rise_calibration,
        pump_serial=_single_pump_serial(requests),
    )
    summary = summarize(scored)

    sweep_rows = []
    for slope in sweep:
        cfg_v = dataclasses.replace(
            config, meal_rise=dataclasses.replace(
                config.meal_rise, base_slope_mgdl_per_min=slope))
        s_detections = find_meal_rise_instances(cgm, cfg_v)
        s_scored = score_instances(
            s_detections, requests, config.meal_rise_calibration,
            pump_serial=_single_pump_serial(requests))
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
```

`AppConfig` is a frozen dataclass, so `dataclasses.replace(config, meal_rise=…)` works; verify, and if any non-init field complains, instead build the variant by replacing only `config.meal_rise` and passing the variant `MealRiseConfig` through a small local AppConfig copy — but try plain `dataclasses.replace` first.

- [ ] **Step 4: Run tests** — `uv run pytest tests/detection/test_score_meal_rise.py -v` → all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/score_meal_rise.py tests/detection/test_score_meal_rise.py
git commit -m "feat(calibration): scoring runner orchestration with base-slope sweep"
```

---

### Task 3: CLI entry point + real-data smoke

**Files:**
- Modify: `scripts/score_meal_rise.py`

- [ ] **Step 1: Add `main()`**:

```python
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
```

- [ ] **Step 2: Real-data smoke run** (real parquets exist locally; output goes to gitignored `data/reports/`):

Run: `uv run python scripts/score_meal_rise.py --sweep 1.4,1.8,2.2 2>&1 | tail -3`
Expected: a `instances=… uncovered_rate=… reports → data/reports` line, no traceback. Then `git status --short` must show NO new tracked files (reports are inside gitignored `data/`). If `data/reports/` shows up untracked, STOP and add `data/` gitignore coverage check — do not commit reports.

- [ ] **Step 3: Full suite** — `uv run pytest -q 2>&1 | tail -1` → `583 passed (576+7 new), 42 skipped, 48 deselected` (adjust expected count to the actual new-test total).

- [ ] **Step 4: Commit**

```bash
git add scripts/score_meal_rise.py
git commit -m "feat(calibration): CLI entry point for meal-rise scoring runner"
```

---

### Task 4: Dated update doc

**Files:**
- Create: `docs/updates/2026-06-11-m2-scoring-runner.md`

- [ ] **Step 1: Write the entry**: what the runner does, exact rerun command, where reports land (gitignored), the advisory-only rule (config edits happen only as reviewed commits), and the smoke-run result shape (instance count may be mentioned as an aggregate; do NOT paste per-event health data into the committed doc).
- [ ] **Step 2: Verify clean tree except the doc** — `git status --short`.
- [ ] **Step 3: Commit**

```bash
git add docs/updates/2026-06-11-m2-scoring-runner.md
git commit -m "docs: update entry for M2 scoring runner"
```
