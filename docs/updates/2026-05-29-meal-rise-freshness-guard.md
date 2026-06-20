# 2026-05-29 Meal-rise freshness guard

## Summary

Added a data-freshness guard to the live meal-rise cron so it no longer evaluates
stale CGM. Motivation: a live run observed the latest Dexcom Share reading was
~18h old (no active sensor session), yet the detector still built a window around
that stale anchor. Without a guard, a long-past rise in an old Share window could
fire a "fast glucose rise" alert.

## Changes

- **Config:** new `meal_rise.max_reading_age_minutes` (default `15`) in
  `config/user_config.yaml`, `core.detection.meal_rise.MealRiseConfig`, and
  `detection.config._parse_meal_rise` (validated `> 0`). Tunable, not hardcoded.
- **Cron (`apps/personal/cron/detect_meal_rise.py`):** `run_cron` now takes a
  keyword-only `now` (defaults to `datetime.now(timezone.utc)`, injectable for
  tests). After fetching CGM, if `now - latest_ts > max_reading_age_minutes` it
  logs a warning and returns `0` (clean skip, not an error). `now` is also
  threaded into the retry pass so the run has a single notion of "now".
- **Tests:** `test_cron_skips_stale_reading` (stale → no alert/records) and
  `test_cron_fresh_reading_still_fires` (guard doesn't over-skip);
  `test_detection_config` gains a default-when-absent test and a `> 0`
  validation test. Existing `run_cron` tests now pass an explicit fresh `now`
  so they keep exercising no-rise / firing / refractory rather than being
  short-circuited by the guard.

## Verification

- `uv run pytest` → 561 passed, 43 skipped, 47 legacy-deselected.
- Live run against real Dexcom Share: `Latest CGM reading is stale (1088.0 min
  old > 15 min max); skipping detection.` → exit 0 (previously it proceeded to
  evaluate the stale window).

## Notes / follow-ups (not done here)

- `min_samples=4` remains dominated by the coverage gate (`0.7 * 7 = 5` readings);
  left as-is.
- `Window.has_gap` is inert in the live path (`run_cron` passes no `gaps_df`, and
  Dexcom Share has no `cgm_gaps`); coverage is the only live signal-quality gate.
- `base_slope_mgdl_per_min` is still the placeholder pending **M2** calibration.
