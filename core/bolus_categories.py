"""Canonical bolus-category vocabulary shared across detection + apps.

The ``bolus_category`` enrichment (``ingestion/enrich.py``) tags each bolus
with one of:

* ``user_meal`` — user bolus covering carbs only
* ``user_meal_and_correction`` — user bolus with food + correction
* ``user_correction_only`` — user correction bolus (no carbs)
* ``auto_correction`` — Control-IQ automated correction (no food)
* ``override_up`` / ``override_down`` — user changed the calculated dose

``FOOD_CARRYING`` is the subset that "covers" a meal — the only categories
that count as carb coverage. Corrections are NOT coverage; they are evidence a
meal may have been missed (see ``detection/calibration/meal_rise_scoring.py``).

This is the single source of truth; detection and the Telegram digest import
it rather than re-declaring the literal set.
"""

from __future__ import annotations

# Food-carrying bolus categories: the only ones that cover a meal.
FOOD_CARRYING: frozenset[str] = frozenset(
    {"user_meal", "user_meal_and_correction", "override_up"}
)

# Correction-only categories: evidence of a missed/late meal, not coverage.
CORRECTION_CATEGORIES: frozenset[str] = frozenset(
    {"user_correction_only", "auto_correction"}
)

__all__ = ["FOOD_CARRYING", "CORRECTION_CATEGORIES"]
