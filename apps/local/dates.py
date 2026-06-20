"""Date-window helpers for the local dashboard."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo

MAX_HEATMAP_DAYS = 90


def clamp_heatmap_days(days: int, max_days: int = MAX_HEATMAP_DAYS) -> int:
    """Clamp heatmap range to ``[1, max_days]``."""
    if days < 1:
        return 1
    return min(days, max_days)


def iter_dates_in_window(end: date, days: int) -> list[date]:
    """Inclusive calendar dates from ``end - (days - 1)`` through ``end``."""
    if days < 1:
        raise ValueError("days must be >= 1")
    start = end - timedelta(days=days - 1)
    out: list[date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def date_window_bounds(
    end: date,
    days: int,
    *,
    tz: tzinfo | None = None,
) -> tuple[datetime, datetime]:
    """Return ``(since, until)`` datetimes for ``read_table`` over ``days`` ending on ``end``.

    ``since`` is midnight on the first day; ``until`` is midnight on the day after
  ``end`` (half-open interval).
    """
    if days < 1:
        raise ValueError("days must be >= 1")
    tzinfo = tz or timezone.utc
    start = end - timedelta(days=days - 1)
    since = datetime(start.year, start.month, start.day, tzinfo=tzinfo)
    until = datetime(end.year, end.month, end.day, tzinfo=tzinfo) + timedelta(days=1)
    return since, until
