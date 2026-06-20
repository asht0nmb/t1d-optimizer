# 2026-06-11 — M2: meal-rise calibration scoring module

## What landed

- `detection/calibration/meal_rise_scoring.py` — retrospective labeling of
  meal-rise detections against bolus context. Pure DataFrame-in /
  dataclass-out, no I/O:
  - `find_meal_rise_instances(cgm_df, config)` slides the production
    detector (`core/detection/meal_rise.py`) across a historical CGM frame
    with refractory de-duplication.
  - `score_instances(detections, requests_df, calib, pump_serial)` labels
    each instance `pre_bolused` / `late_bolused` / `uncovered` by searching
    food-carrying boluses in
    `[rise_start − pre_bolus_lookback, rise_start + late_bolus_lookahead]`,
    and attributes uncovered misses to `user_correction` /
    `auto_correction` / `none` within `correction_lookahead_minutes`.
  - `summarize(scored)` returns label counts + uncovered rate.
- `detection/config.py` — new optional `meal_rise_calibration` config block
  (`MealRiseCalibrationConfig`), defaults applied when absent so older
  configs keep working; all three windows validated > 0.
- `config/user_config.yaml` — documented defaults (30 / 45 / 180 minutes).
- Tests: `tests/detection/test_meal_rise_scoring.py` (scorer behaviour) and
  expanded `tests/test_detection_config.py` (defaults + validation).

## Why

M1 shipped the live alert loop with a placeholder
`base_slope_mgdl_per_min = 1.8`. M2's goal is a labeled historical dataset
of detections so the slope threshold (and start-level gates) can be tuned
against observed pre/late/uncovered rates instead of guesses. This module
is the labeling half; the runner CLI that applies it to
`data/processed/*.parquet` and reports distributions is the follow-up.

## Constraints honored

- Calibration outputs inform **config variables only** — no automatic
  threshold changes. Any retuning lands as a reviewed edit to
  `config/user_config.yaml` with its own dated update doc.
- No ML here: labeling is deterministic window logic. Supervised modeling
  on top of the labeled dataset remains deferred.

## Suite

576 passed, 43 skipped, 47 deselected (legacy) — `uv run pytest -q`.
