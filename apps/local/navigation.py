"""Day navigation helpers for the local dashboard."""

from __future__ import annotations

from datetime import date

import pandas as pd

from core.storage.protocol import Storage


def list_cgm_dates(cgm: pd.DataFrame) -> list[date]:
    """Sorted unique calendar dates with at least one CGM reading."""
    if cgm.empty or "timestamp" not in cgm.columns:
        return []
    dates = pd.to_datetime(cgm["timestamp"]).dt.date.unique()
    return sorted(dates)


def list_cgm_dates_from_storage(storage: Storage) -> list[date]:
    return list_cgm_dates(storage.read_all_table("cgm"))


def shift_day(current: date, delta: int, available: list[date]) -> date:
    """Move ``delta`` positions in ``available`` (-1 prev, +1 next). Clamp at ends."""
    if not available:
        return current
    if current not in available:
        return available[max(0, min(len(available) - 1, 0))]
    idx = available.index(current)
    new_idx = max(0, min(len(available) - 1, idx + delta))
    return available[new_idx]


def nearest_cgm_date(current: date, available: list[date]) -> date:
    """Return ``current`` if present, else closest available date (or ``current``)."""
    if not available:
        return current
    if current in available:
        return current
    # Pick closest by ordinal distance
    return min(available, key=lambda d: abs(d.toordinal() - current.toordinal()))
