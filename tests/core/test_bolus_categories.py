"""The FOOD_CARRYING set is centralized and every non-legacy caller reuses it."""

from __future__ import annotations

from core.bolus_categories import CORRECTION_CATEGORIES, FOOD_CARRYING


def test_food_carrying_values_unchanged() -> None:
    assert FOOD_CARRYING == frozenset(
        {"user_meal", "user_meal_and_correction", "override_up"}
    )


def test_correction_categories_values() -> None:
    assert CORRECTION_CATEGORIES == frozenset(
        {"user_correction_only", "auto_correction"}
    )


def test_callers_reference_the_central_set() -> None:
    """detection + telegram digest must point at the same object, not a copy."""
    from detection.calibration import meal_rise_scoring
    from detection.features import _MEAL_CATEGORIES

    from apps.personal.telegram import digest

    assert meal_rise_scoring.FOOD_CARRYING is FOOD_CARRYING
    assert _MEAL_CATEGORIES is FOOD_CARRYING
    assert digest._FOOD_CARRYING is FOOD_CARRYING
