# M1 — Live Missed-Meal (Fast-Rise) Detector — Technical Spec

**Date:** 2026-05-23
**Milestone:** M1 of the V2 development roadmap (`2026-05-23-v2-development-roadmap.md`).
**Goal:** A usable, end-to-end live alert. Dexcom (pydexcom) to a five-minute cron to a pure detector to a Telegram message, writing to `detection_results` and deduping via `alerts_sent`. This is the first shippable, demonstrable piece.

This spec also lays down the **thin windowing primitive** as a foundation (roadmap section 4), since the detector and every later CGM consumer sit on it.

---

## Non-negotiables (carried from the v1 plan and CLAUDE.md)

These override convenience.

1. **Source-agnostic core.** Detection functions take normalized DataFrames (`cgm_df` and, later, others) plus a config object. No knowledge of pydexcom, Supabase, CSV, or Tandem. No I/O in the core.
2. **No hardcoded thresholds.** Every clinically meaningful number lives in `config/user_config.yaml` and is read at runtime. A magic number in source is a review-blocking bug.
3. **Trailing-window only for real time.** The live path must not read any row with `timestamp > now`. The window uses `post = 0`.
4. **Backfill timestamp rule.** Backfilled readings are valid at their sensor time. The live path sees only live readings, so this does not bite M1, but the windowing primitive must not assume contiguous timestamps.
5. **Observation, not prescription.** The alert states what was observed. It never instructs a dose.
6. **No LLM in the live path.** The alert is a template with injected values.
7. **RLS.** The cron uses the `service_role` key (bypasses RLS). Any new migration must `enable row level security`.

---

## Module placement

The detection core is storage-agnostic, so it belongs under `core/`. Proposed:

```
core/detection/windowing.py     # foundation: Anchor, Window, make_window
core/detection/meal_rise.py     # detector: MealRiseConfig, MealRiseDetection, detect_meal_rise
tests/detection/test_windowing.py
tests/detection/test_meal_rise.py
```

The live shell (orchestration, I/O) goes with the personal cloud deployment, proposed `apps/personal/cron/detect_meal_rise.py`.

**Before writing code, confirm the actual tree.** If the repo or CLAUDE.md established a different detection location (for example top-level `detection/`) or a different shell path, conform to it and keep this consistent. Inspect `core/storage/protocol.py`, `core/storage/records.py` (the existing `AlertRecord`), `core/schema.py`, `db/migrations/0001_init.sql` (the `detection_results` and `alerts_sent` shapes), the `AppConfig` loader, and any existing Telegram or pydexcom helper, and reuse them rather than inventing.

---

## Part A — Windowing primitive (foundation)

`core/detection/windowing.py`. Pure, no I/O. This is the small certain core, not a framework.

```python
from dataclasses import dataclass
from datetime import datetime, timedelta
import pandas as pd

DEFAULT_INTERVAL = timedelta(minutes=5)

@dataclass(frozen=True)
class Anchor:
    timestamp: datetime          # tz-aware
    kind: str                    # "live" for M1; the seam for "bolus", "sliding", "wakeup", ...

@dataclass(frozen=True)
class Window:
    anchor: Anchor
    start: datetime
    end: datetime
    samples: pd.DataFrame        # cgm rows in [start, end], sorted by timestamp ascending
    coverage: float              # n_present / n_expected
    has_gap: bool                # overlaps a known cgm_gaps interval (False when gaps_df is None)

    @property
    def n_samples(self) -> int:
        return len(self.samples)

def make_window(
    cgm_df: pd.DataFrame,
    anchor: Anchor,
    pre: timedelta,
    post: timedelta = timedelta(0),
    *,
    expected_interval: timedelta = DEFAULT_INTERVAL,
    gaps_df: pd.DataFrame | None = None,
) -> Window:
    ...
```

Behavior:

- `start = anchor.timestamp - pre`, `end = anchor.timestamp + post`.
- `samples`: rows of `cgm_df` with `start <= timestamp <= end`, sorted ascending. Columns relied on: `timestamp` (tz-aware), `bg_mgdl`.
- `n_expected = floor((pre + post) / expected_interval) + 1`. `coverage = n_present / n_expected`. Allow values slightly above 1; do not clamp silently.
- `has_gap`: if `gaps_df` is provided, `True` when `[start, end]` intersects any `[start_ts, end_ts]` interval; else `False`.
- Empty slice yields `coverage = 0.0`, `n_samples = 0`, and a valid (empty) `samples` frame.

The `post = 0` case sees only the past and is the live-capable case. `post > 0` is retrospective. The stage-one versus stage-two split is just this.

Tests (`test_windowing.py`): inclusive bounds, full-coverage vs sparse vs empty, `has_gap` true and false and with `gaps_df=None`, uneven spacing handled.

---

## Part B — Detector (pure)

`core/detection/meal_rise.py`. Pure function over a `Window` and a config. No `now`, no I/O. Time of day comes from the anchor.

### Config

Add to `config/user_config.yaml`, loaded into a typed `MealRiseConfig` exposed by `AppConfig` (follow the existing AppConfig pattern):

```yaml
meal_rise:
  window_minutes: 30            # trailing window (pre)
  min_samples: 4
  min_coverage: 0.7
  base_slope_mgdl_per_min: 1.8  # PLACEHOLDER, tuned in M2 against bolus data
  start_level_min: 70           # gate: ignore rises out of a low recovery
  start_level_max: 250          # gate: ignore rises already deep in hyper
  meal_windows:                 # local-time hour ranges; multiplier < 1 lowers the bar
    - {start_hour: 6,  end_hour: 10, multiplier: 0.7}
    - {start_hour: 11, end_hour: 14, multiplier: 0.7}
    - {start_hour: 17, end_hour: 21, multiplier: 0.7}
  off_hours_multiplier: 1.3
  refractory_minutes: 60
  alert_template: "Fast glucose rise: {start} to {end} mg/dL (about {delta} up in {minutes} min). Flagging in case a meal went unbolused."
```

The over-firing is intended. Five to seven CGM-only points cannot reliably separate a meal from any other rise, so the detector is sensitive with the time-of-day weighting as its prior. M2 measures how often it was right.

### Types and function

```python
@dataclass(frozen=True)
class MealRiseDetection:
    anchor_timestamp: datetime
    slope_mgdl_per_min: float
    start_level: int
    end_level: int
    delta: int
    n_samples: int
    coverage: float
    minutes_span: float
    hour_of_day: int
    threshold_used: float
    time_multiplier: float
    glucose_values: list[int]
    window_start: datetime
    window_end: datetime

    def to_payload(self) -> dict: ...   # JSON-safe; becomes detection_results.payload

def detect_meal_rise(window: Window, config: MealRiseConfig) -> MealRiseDetection | None:
    ...
```

Logic, in order:

1. **Guards.** Return `None` if `window.n_samples < min_samples`, or `window.coverage < min_coverage`, or `window.has_gap`. This catches sensor dropout on the live path via coverage.
2. **Slope.** Theil-Sen estimator (median of pairwise slopes) of `bg_mgdl` against minutes since the window start, in mg/dL per minute. Theil-Sen is robust to a single jittery point and cheap at this size. Time-aware x handles missing or unevenly spaced readings.
3. **Levels.** `start_level` = first reading, `end_level` = last, `delta = end_level - start_level`, `minutes_span` from first to last timestamp.
4. **Start-level gate.** Require `start_level_min <= start_level <= start_level_max`, else `None`.
5. **Time multiplier.** From `hour_of_day` (anchor timestamp in local tz): the matching meal window's multiplier, else `off_hours_multiplier`.
6. **Threshold.** `threshold_used = base_slope_mgdl_per_min * time_multiplier`.
7. **Fire.** If `slope >= threshold_used`, return a populated `MealRiseDetection`; else `None`.

Tests (`test_meal_rise.py`): fires on a synthetic breakfast-time rise; no-fire on flat; no-fire on a drift below threshold; no-fire when coverage below `min_coverage`; no-fire when `start_level` is out of the gate (for example a 55 to 80 low-recovery rise); the same slope fires inside a meal window but not off-hours; Theil-Sen unaffected by a single outlier point. Synthetic DataFrames, no network.

---

## Part C — Live shell (orchestration)

`apps/personal/cron/detect_meal_rise.py`. This is where I/O lives. Runs on the five-minute cron, `service_role` key.

Steps:

1. Load `AppConfig` from `config/user_config.yaml`.
2. Fetch recent CGM via pydexcom, last `window_minutes + buffer` (use about 40 minutes). Reuse any existing pydexcom helper.
3. Normalize to `cgm_df`: columns `timestamp` (tz-aware, America/Los_Angeles) and `bg_mgdl`, sorted ascending, deduplicated to one reading per five-minute interval. Live readings only.
4. Build `anchor = Anchor(timestamp=<latest reading ts>, kind="live")`.
5. `window = make_window(cgm_df, anchor, pre=timedelta(minutes=window_minutes), post=timedelta(0))`. No `gaps_df` on the live path; coverage handles dropout.
6. `detection = detect_meal_rise(window, config.meal_rise)`. If `None`, exit cleanly.
7. **Dedup.** Build `event_ref = f"meal_rise:{anchor.timestamp.isoformat(timespec='minutes')}"`. Query the most recent `meal_rise` entry in `alerts_sent`; if one was sent within `refractory_minutes`, exit without sending. Otherwise proceed. The partial-unique `event_ref` index is the race guard: if the insert collides, treat it as already-sent and exit.
8. **Persist.** Write a `detection_results` row: `kind="meal_rise"`, `anchor_timestamp=detection.anchor_timestamp`, `payload=detection.to_payload()`. Write the `alerts_sent` / `AlertRecord` row with `event_ref`. Use the `Storage` Protocol and the existing `AlertRecord`.
9. **Alert.** Format `alert_template` with `start`, `end`, `delta`, `minutes` and send via the existing Telegram helper. No LLM, no dosing language.

Idempotency: a second run over the same latest reading must not double-send or double-write.

---

## Acceptance criteria

- All tests pass under the repo's test-first cycle.
- `core/detection/` has no storage or network imports. Detector and windowing are pure.
- Every threshold is read from `config/user_config.yaml`. No magic numbers in source.
- Live path is trailing-window only; no row with `timestamp > now` is read.
- Re-running the cron over the same latest reading neither double-writes `detection_results` nor double-sends the alert.
- Alert text is observational and carries no LLM call and no dose instruction.
- `detection_results` payload carries enough for M2 to score it against `requests_df` later: slope, levels, span, time of day, and the raw `glucose_values`.

---

## What M1 deliberately excludes

No bolus or pump data in the live path (M2 calibration consumes that nightly). No representation catalog, cohort engine, or any speculative abstraction. No dashboard. No `gaps_df` wiring on the live path.

---