"""Core detection package for diabetes data intelligence.

Contains storage-agnostic primitives for time-series slicing and real-time event
detectors.
"""

from __future__ import annotations

from core.detection.windowing import Anchor, Window, make_window
from core.detection.meal_rise import (
    MealRiseConfig,
    MealRiseDetection,
    detect_meal_rise,
)

__all__ = [
    "Anchor",
    "Window",
    "make_window",
    "MealRiseConfig",
    "MealRiseDetection",
    "detect_meal_rise",
]
