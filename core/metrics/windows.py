"""DST-correct local-day windowing + data-sufficiency helpers.

Shared by every clinical metric and the AGP profile. The window convention
is half-open ``[since, until)`` in UTC instants, where the bounds are the
local-midnight instants in the given IANA timezone — so a spring-forward day
spans 23 hours and a fall-back day spans 25 hours. ``active_time`` derives the
expected reading count from the *real* tz-aware span length (not ``days*288``)
so coverage on DST-transition days stays honest.

Core import rules apply: stdlib / pandas / numpy only. The IANA timezone
database is consulted via :mod:`zoneinfo` (stdlib).
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd


def local_day_bounds(date: dt.date, *, tz: str) -> tuple[dt.datetime, dt.datetime]:
    """Return ``(since, until)`` UTC instants bounding one local calendar day.

    ``since`` is the instant of local midnight on ``date``; ``until`` is the
    instant of local midnight on the following day. Both are tz-aware UTC. The
    span is 23h on a spring-forward day and 25h on a fall-back day.
    """
    zone = ZoneInfo(tz)
    start_local = dt.datetime(date.year, date.month, date.day, tzinfo=zone)
    next_day = date + dt.timedelta(days=1)
    end_local = dt.datetime(next_day.year, next_day.month, next_day.day, tzinfo=zone)
    return (
        start_local.astimezone(dt.timezone.utc),
        end_local.astimezone(dt.timezone.utc),
    )


def window_bounds(
    end_date: dt.date, days: int, *, tz: str
) -> tuple[dt.datetime, dt.datetime]:
    """Return ``[since, until)`` UTC instants spanning ``days`` local dates.

    The window is the ``days`` consecutive local calendar dates ending inclusive
    on ``end_date``. ``since`` is local midnight of the first date; ``until`` is
    local midnight of the day after ``end_date``. The total span accounts for
    any DST transitions inside the window.
    """
    if days < 1:
        raise ValueError("days must be >= 1")
    start_date = end_date - dt.timedelta(days=days - 1)
    since, _ = local_day_bounds(start_date, tz=tz)
    _, until = local_day_bounds(end_date, tz=tz)
    return since, until


def active_time(
    cgm: pd.DataFrame,
    since: dt.datetime,
    until: dt.datetime,
    *,
    expected_interval_min: int = 5,
) -> tuple[int, int, float]:
    """Return ``(n_readings, expected, active_pct)`` for ``[since, until)``.

    ``n_readings`` counts CGM rows whose ``timestamp`` falls in the half-open
    window. ``expected`` is the number of readings a fully-covered window would
    contain, derived from the *actual* tz-aware span length divided by
    ``expected_interval_min`` (so a 25h fall-back day expects ~300, not 288).
    ``active_pct`` is ``100 * n / expected`` (0.0 when ``expected`` is 0).

    Naive timestamps are treated as UTC. The ``timestamp`` column may be absent
    or empty, in which case ``n_readings`` is 0.
    """
    span_seconds = (until - since).total_seconds()
    expected = int(round(span_seconds / 60.0 / expected_interval_min))

    n_readings = 0
    if cgm is not None and not cgm.empty and "timestamp" in cgm.columns:
        ts = pd.to_datetime(cgm["timestamp"], utc=True)
        since_utc = pd.Timestamp(since).tz_convert("UTC")
        until_utc = pd.Timestamp(until).tz_convert("UTC")
        mask = (ts >= since_utc) & (ts < until_utc)
        n_readings = int(mask.sum())

    active_pct = 100.0 * n_readings / expected if expected > 0 else 0.0
    return n_readings, expected, active_pct


def meets_sufficiency(
    days_covered: int,
    active_pct: float,
    *,
    min_days: int = 14,
    min_active: float = 70.0,
) -> bool:
    """True when ``days_covered >= min_days`` and ``active_pct >= min_active``.

    Encodes the consensus AGP data-sufficiency gate (>=14 days, >=70% active
    CGM time) used to decide whether GMI/GRI are clinically reportable.
    """
    return days_covered >= min_days and active_pct >= min_active
