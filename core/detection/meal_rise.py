"""Pure storage-agnostic missed-meal (fast-rise) detector.

Computes a robust glycemic rate of rise using the Theil-Sen pairwise estimator
over a trailing window of CGM readings, and evaluates it against a dynamic,
time-of-day weighted threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
import numpy as np
import pandas as pd

from core.detection.windowing import Window


@dataclass(frozen=True)
class MealRiseConfig:
    """Thresholds and window parameters for the missed-meal detector."""
    window_minutes: int
    min_samples: int
    min_coverage: float
    base_slope_mgdl_per_min: float
    start_level_min: int
    start_level_max: int
    meal_windows: tuple[dict[str, Any], ...]
    off_hours_multiplier: float
    refractory_minutes: int
    alert_template: str
    fetch_buffer_minutes: int
    expected_interval_minutes: int
    fetch_readings_padding: int


@dataclass(frozen=True)
class MealRiseDetection:
    """A verified fast glycemic rise event."""
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

    def to_payload(self) -> dict[str, Any]:
        """Convert the detection result into a JSON-serializable dictionary."""
        return {
            "anchor_timestamp": self.anchor_timestamp.isoformat(),
            "slope_mgdl_per_min": float(self.slope_mgdl_per_min),
            "start_level": int(self.start_level),
            "end_level": int(self.end_level),
            "delta": int(self.delta),
            "n_samples": int(self.n_samples),
            "coverage": float(self.coverage),
            "minutes_span": float(self.minutes_span),
            "hour_of_day": int(self.hour_of_day),
            "threshold_used": float(self.threshold_used),
            "time_multiplier": float(self.time_multiplier),
            "glucose_values": [int(g) for g in self.glucose_values],
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
        }


def detect_meal_rise(window: Window, config: MealRiseConfig) -> MealRiseDetection | None:
    """Evaluate a CGM window to detect a sharp, unbolused glucose rise.

    Args:
        window: Sliced CGM window to analyze.
        config: Typed MealRiseConfig containing threshold rules.

    Returns:
        MealRiseDetection if a sharp rise is detected, else None.
    """
    # 1. Guards
    if window.n_samples < config.min_samples:
        return None
    if window.coverage < config.min_coverage:
        return None
    if window.has_gap:
        return None

    # 2. Slope Calculation via Theil-Sen pairwise estimator
    # Extract times in minutes since window start, and values
    first_ts = window.samples["timestamp"].iloc[0]
    times = [(ts - first_ts).total_seconds() / 60.0 for ts in window.samples["timestamp"]]
    values = window.samples["bg_mgdl"].astype(float).tolist()

    n = len(values)
    slopes: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            dt = times[j] - times[i]
            if dt > 0:
                slopes.append((values[j] - values[i]) / dt)

    if not slopes:
        return None

    # Theil-Sen slope is the median of all pairwise slopes
    slope = float(np.median(slopes))

    # 3. Levels
    start_level = int(values[0])
    end_level = int(values[-1])
    delta = end_level - start_level
    minutes_span = float(times[-1] - times[0])

    # 4. Start-level gate
    if not (config.start_level_min <= start_level <= config.start_level_max):
        return None

    # 5. Time Multiplier (based on anchor local hour of day)
    local_dt = window.anchor.timestamp
    # If the anchor lacks localized tz, warn or fallback
    hour_of_day = local_dt.hour

    time_multiplier = config.off_hours_multiplier
    for mw in config.meal_windows:
        sh = int(mw.get("start_hour", 0))
        eh = int(mw.get("end_hour", 0))
        mult = float(mw.get("multiplier", 1.0))
        # Hour range check inclusive (e.g. 6 to 10 covers up to hour 10 inclusive)
        if sh <= hour_of_day <= eh:
            time_multiplier = mult
            break

    # 6. Threshold comparison
    threshold_used = config.base_slope_mgdl_per_min * time_multiplier

    # 7. Check sharp rise threshold
    if slope >= threshold_used:
        return MealRiseDetection(
            anchor_timestamp=window.anchor.timestamp,
            slope_mgdl_per_min=slope,
            start_level=start_level,
            end_level=end_level,
            delta=delta,
            n_samples=n,
            coverage=window.coverage,
            minutes_span=minutes_span,
            hour_of_day=hour_of_day,
            threshold_used=threshold_used,
            time_multiplier=time_multiplier,
            glucose_values=[int(v) for v in values],
            window_start=window.start,
            window_end=window.end,
        )

    return None
