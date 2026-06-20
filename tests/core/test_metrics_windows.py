"""Tests for core/metrics/windows.py — DST-correct windowing + sufficiency."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from core.metrics.windows import (
    active_time,
    local_day_bounds,
    meets_sufficiency,
    window_bounds,
)

TZ = "America/Los_Angeles"


def _utc(y, m, d, h, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=dt.timezone.utc)


class TestLocalDayBounds:
    def test_normal_day_is_24h(self):
        since, until = local_day_bounds(dt.date(2026, 6, 16), tz=TZ)
        # PDT = UTC-7 → local midnight is 07:00Z
        assert since == _utc(2026, 6, 16, 7)
        assert until == _utc(2026, 6, 17, 7)
        assert (until - since) == dt.timedelta(hours=24)

    def test_spring_forward_day_is_23h(self):
        # 2026-03-08 DST starts in US → 23-hour local day
        since, until = local_day_bounds(dt.date(2026, 3, 8), tz=TZ)
        # PST=UTC-8 before transition → local midnight 08:00Z;
        # next local midnight is PDT=UTC-7 → 07:00Z next day
        assert since == _utc(2026, 3, 8, 8)
        assert until == _utc(2026, 3, 9, 7)
        assert (until - since) == dt.timedelta(hours=23)

    def test_fall_back_day_is_25h(self):
        # 2026-11-01 DST ends in US → 25-hour local day
        since, until = local_day_bounds(dt.date(2026, 11, 1), tz=TZ)
        assert since == _utc(2026, 11, 1, 7)  # PDT=UTC-7
        assert until == _utc(2026, 11, 2, 8)  # PST=UTC-8
        assert (until - since) == dt.timedelta(hours=25)

    def test_bounds_are_tz_aware_utc(self):
        since, until = local_day_bounds(dt.date(2026, 6, 16), tz=TZ)
        assert since.tzinfo is not None
        assert until.tzinfo is not None


class TestWindowBounds:
    def test_spans_days_ending_inclusive(self):
        since, until = window_bounds(dt.date(2026, 6, 16), days=3, tz=TZ)
        # Window = 2026-06-14, 06-15, 06-16
        assert since == _utc(2026, 6, 14, 7)
        assert until == _utc(2026, 6, 17, 7)

    def test_single_day_equals_local_day_bounds(self):
        wb = window_bounds(dt.date(2026, 6, 16), days=1, tz=TZ)
        ldb = local_day_bounds(dt.date(2026, 6, 16), tz=TZ)
        assert wb == ldb

    def test_window_crossing_dst_has_correct_total_span(self):
        # Window over the spring-forward day: 03-07, 03-08(23h), 03-09
        since, until = window_bounds(dt.date(2026, 3, 9), days=3, tz=TZ)
        assert (until - since) == dt.timedelta(hours=24 + 23 + 24)

    def test_rejects_zero_days(self):
        with pytest.raises(ValueError):
            window_bounds(dt.date(2026, 6, 16), days=0, tz=TZ)


class TestActiveTime:
    def _cgm(self, timestamps):
        return pd.DataFrame({"timestamp": timestamps, "bg_mgdl": [120] * len(timestamps)})

    def test_full_normal_day_5min(self):
        since, until = local_day_bounds(dt.date(2026, 6, 16), tz=TZ)
        ts = pd.date_range(since, until, freq="5min", inclusive="left")
        n, expected, pct = active_time(self._cgm(ts), since, until, expected_interval_min=5)
        assert n == 288
        assert expected == 288
        assert pct == pytest.approx(100.0)

    def test_fall_back_day_expects_300_not_288(self):
        since, until = local_day_bounds(dt.date(2026, 11, 1), tz=TZ)
        n, expected, pct = active_time(
            self._cgm([]), since, until, expected_interval_min=5
        )
        # 25h * 12 readings/h = 300
        assert expected == 300

    def test_spring_forward_day_expects_276(self):
        since, until = local_day_bounds(dt.date(2026, 3, 8), tz=TZ)
        n, expected, pct = active_time(
            self._cgm([]), since, until, expected_interval_min=5
        )
        # 23h * 12 = 276
        assert expected == 276

    def test_half_data_is_50pct(self):
        since, until = local_day_bounds(dt.date(2026, 6, 16), tz=TZ)
        ts = pd.date_range(since, until, freq="5min", inclusive="left")[:144]
        n, expected, pct = active_time(self._cgm(ts), since, until, expected_interval_min=5)
        assert n == 144
        assert pct == pytest.approx(50.0)

    def test_readings_outside_window_excluded(self):
        since, until = local_day_bounds(dt.date(2026, 6, 16), tz=TZ)
        before = since - dt.timedelta(minutes=5)
        at_until = until  # half-open: until itself excluded
        n, expected, pct = active_time(
            self._cgm([before, since, at_until]), since, until, expected_interval_min=5
        )
        assert n == 1  # only `since` counts

    def test_empty_frame_zero_readings(self):
        since, until = local_day_bounds(dt.date(2026, 6, 16), tz=TZ)
        n, expected, pct = active_time(
            pd.DataFrame(columns=["timestamp", "bg_mgdl"]),
            since,
            until,
            expected_interval_min=5,
        )
        assert n == 0
        assert pct == 0.0


class TestMeetsSufficiency:
    def test_meets_when_both_thresholds_met(self):
        assert meets_sufficiency(14, 70.0) is True
        assert meets_sufficiency(20, 95.0) is True

    def test_fails_on_too_few_days(self):
        assert meets_sufficiency(13, 99.0) is False

    def test_fails_on_low_active_pct(self):
        assert meets_sufficiency(30, 69.9) is False

    def test_boundary_inclusive(self):
        assert meets_sufficiency(14, 70.0, min_days=14, min_active=70.0) is True

    def test_custom_thresholds(self):
        assert meets_sufficiency(7, 50.0, min_days=7, min_active=50.0) is True
        assert meets_sufficiency(6, 50.0, min_days=7, min_active=50.0) is False
