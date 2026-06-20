# 2026-06-11 — M2 scoring runner CLI (`scripts/score_meal_rise.py`)

## What landed

The orchestrator that `detection/calibration/meal_rise_scoring.py`'s
docstring promised: a CLI that loads historical frames (enrichment
backfilled in memory via `ingestion.view_data.load_frames("enriched")`),
slides the production detector across the CGM history, labels every
instance against bolus context, and writes an advisory calibration report.

```bash
uv run python scripts/score_meal_rise.py \
    [--start YYYY-MM-DD] [--end YYYY-MM-DD] \
    [--sweep 1.4,1.6,1.8,2.0,2.2] [--out-dir data/reports]
```

Outputs `meal_rise_scores_<UTCstamp>.md` (summary, per-hour breakdown,
base-slope sensitivity sweep) and `.json` (full per-instance records)
under `data/reports/` — inside the gitignored `data/` tree, because the
records are personal health data. **Never commit reports.**

## Advisory-only rule (ML deferral)

The report proposes values for existing config variables (notably
`meal_rise.base_slope_mgdl_per_min`). It changes nothing itself. Any
retuning lands as a reviewed edit to `config/user_config.yaml` with its
own dated update entry. Threshold decisions from this output are the
owner's to make.

## Smoke verification (aggregates only)

Bounded run over 2026-02-20 → 2026-03-20 with `--sweep 1.4,1.8,2.2`
completed cleanly: 102 instances at the production slope, uncovered rate
84.3%, with most uncovered instances resolved by Control-IQ
auto-corrections. Interpretation and any tuning are deferred to the owner.
Note for rerunners: a full-history run with a sweep takes minutes (the
runner re-slides the detector once per sweep value).

## Tests

`tests/detection/test_score_meal_rise.py` — report serialization,
markdown sections, orchestration with synthetic frames (monkeypatched
loader), date filtering, sweep behavior. Suite: 583 passed, 42 skipped,
48 deselected.
