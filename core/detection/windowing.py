"""Windowing primitive foundation for CGM time-series analysis.

This module is completely storage-agnostic and side-effect free. It defines
the Anchor and Window structures, and provides functions to slice a continuous
CGM dataframe into sliding or event-anchored intervals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import pandas as pd

DEFAULT_INTERVAL = timedelta(minutes=5)
# An ongoing (open-ended) CGM gap has no recorded end. We treat it as still
# active only while it stays *recent* relative to the window: if its start
# predates the window's own time span by more than this bound, the window's
# present CGM coverage already proves the signal is back, so a never-cleared
# stale `cgm_out_of_range` row must NOT suppress detection forever.
#
# The bound is the maximum trailing span any caller windows over (a few hours)
# with generous headroom — large enough that a genuinely current open gap that
# began just before the window still flags, small enough that an open gap from
# days/weeks/years ago does not.
ONGOING_GAP_RECENCY = timedelta(hours=12)


@dataclass(frozen=True)
class Anchor:
    """An anchor point in the time-series.

    Attributes:
        timestamp: The tz-aware datetime where the window is centered.
        kind: The anchor type (e.g. "live" for M1; "bolus", "sliding", etc.).
    """
    timestamp: datetime
    kind: str

    def __post_init__(self):
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be a datetime object")
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")


@dataclass(frozen=True)
class Window:
    """A sliced interval of CGM data anchored at a specific point.

    Attributes:
        anchor: The Anchor that this window was generated from.
        start: The inclusive start timestamp of this window.
        end: The inclusive end timestamp of this window.
        samples: The sliced DataFrame of CGM readings sorted by timestamp ascending.
        coverage: The fraction of expected readings present (n_present / n_expected).
        has_gap: True if the window overlaps with a known CGM signal gap/dropout.
    """
    anchor: Anchor
    start: datetime
    end: datetime
    samples: pd.DataFrame
    coverage: float
    has_gap: bool

    @property
    def n_samples(self) -> int:
        """Return the number of actual readings in the window."""
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
    """Slice cgm_df around an anchor and check expected sampling coverage.

    Args:
        cgm_df: DataFrame of CGM readings. Must contain a tz-aware 'timestamp'
            column and a 'bg_mgdl' column.
        anchor: The Anchor point to align the slice.
        pre: The duration to look backward from the anchor.
        post: The duration to look forward from the anchor (default 0 for live).
        expected_interval: The expected sampling frequency of the sensor.
        gaps_df: Optional DataFrame of known sensor gaps (cgm_gaps).

    Returns:
        A populated Window object.
    """
    start = anchor.timestamp - pre
    end = anchor.timestamp + post

    # Slicing bounds
    if cgm_df is None or cgm_df.empty or "timestamp" not in cgm_df.columns:
        samples = pd.DataFrame(columns=["timestamp", "bg_mgdl"])
    else:
        # Filter within inclusive bounds
        mask = (cgm_df["timestamp"] >= start) & (cgm_df["timestamp"] <= end)
        samples = cgm_df.loc[mask].sort_values("timestamp").reset_index(drop=True)

    # Coverage calculation
    # n_expected = floor((pre + post) / expected_interval) + 1
    total_span = pre + post
    n_expected = int(total_span.total_seconds() // expected_interval.total_seconds()) + 1
    n_present = len(samples)
    coverage = float(n_present / n_expected) if n_expected > 0 else 0.0

    # Evaluate signal gaps overlap
    window_has_gap = False
    if gaps_df is not None and not gaps_df.empty and "start_ts" in gaps_df.columns:
        for _, row in gaps_df.iterrows():
            g_start = row["start_ts"]
            g_end = row.get("end_ts")
            ongoing = pd.isna(g_end) or bool(row.get("ongoing", False))

            # An ongoing gap has no recorded end. Bounding its end at
            # g_start + ONGOING_GAP_RECENCY (rather than a 10-year horizon)
            # means a stale, never-cleared open gap from long before the
            # window no longer overlaps it — the window's own CGM coverage
            # proves the signal has returned. A genuinely current open gap
            # that started just before/within the window still overlaps.
            if ongoing:
                g_end = g_start + ONGOING_GAP_RECENCY

            # Overlap check: start <= g_end and end >= g_start
            # Ensure timestamps are aligned or both timezone-aware/naive
            try:
                if max(start, g_start) <= min(end, g_end):
                    window_has_gap = True
                    break
            except TypeError:
                # If there's a tz-naive vs tz-aware mismatch, try to align
                # standardizing on the anchor's timezone.
                tz = anchor.timestamp.tzinfo
                s_aligned = start
                e_aligned = end
                gs_aligned = g_start.tz_convert(tz) if g_start.tzinfo else g_start.tz_localize(tz)
                ge_aligned = g_end.tz_convert(tz) if g_end.tzinfo else g_end.tz_localize(tz)
                if max(s_aligned, gs_aligned) <= min(e_aligned, ge_aligned):
                    window_has_gap = True
                    break

    return Window(
        anchor=anchor,
        start=start,
        end=end,
        samples=samples,
        coverage=coverage,
        has_gap=window_has_gap,
    )
