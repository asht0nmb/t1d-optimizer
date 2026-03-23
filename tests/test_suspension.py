"""Edge-case tests for suspension pairing logic in build_suspension_df."""

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tconnectsync.eventparser.events import LidPumpingResumed, LidPumpingSuspended

from ingestion.builders import build_suspension_df

PST = timezone(timedelta(hours=-8))
SERIAL = "TEST123"


def _ts(dt: datetime):
    ts = MagicMock()
    ts.datetime = dt
    return ts


def _suspend(dt: datetime, reason_raw: int = 0, insulin: int = 200) -> MagicMock:
    e = MagicMock(spec=LidPumpingSuspended)
    e.eventTimestamp = _ts(dt)
    e.suspendreasonRaw = reason_raw
    e.insulinamount = insulin
    return e


def _resume(dt: datetime) -> MagicMock:
    e = MagicMock(spec=LidPumpingResumed)
    e.eventTimestamp = _ts(dt)
    return e


class TestSuspensionEdgeCases:
    def test_multiple_normal_pairs(self):
        """Two consecutive suspend-resume pairs."""
        t0 = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        t1 = t0 + timedelta(minutes=30)
        t2 = t0 + timedelta(hours=2)
        t3 = t2 + timedelta(minutes=15)
        events = [_suspend(t0), _resume(t1), _suspend(t2), _resume(t3)]
        df = build_suspension_df(events, SERIAL)
        assert len(df) == 2
        assert df.iloc[0]["duration_minutes"] == pytest.approx(30.0)
        assert df.iloc[1]["duration_minutes"] == pytest.approx(15.0)
        assert not df.iloc[0]["pairing_suspect"]
        assert not df.iloc[1]["pairing_suspect"]

    def test_resume_then_suspend_resume(self):
        """Leading orphan resume, then a normal pair. Orphan resume is discarded."""
        t0 = datetime(2026, 3, 20, 9, 0, tzinfo=PST)
        t1 = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        t2 = datetime(2026, 3, 20, 10, 20, tzinfo=PST)
        events = [_resume(t0), _suspend(t1), _resume(t2)]
        df = build_suspension_df(events, SERIAL)
        assert len(df) == 1
        assert df.iloc[0]["duration_minutes"] == pytest.approx(20.0)

    def test_triple_suspend_one_resume(self):
        """Three suspends then one resume: first two closed by next suspend, third by resume."""
        t0 = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        t1 = datetime(2026, 3, 20, 10, 10, tzinfo=PST)
        t2 = datetime(2026, 3, 20, 10, 20, tzinfo=PST)
        t3 = datetime(2026, 3, 20, 10, 50, tzinfo=PST)
        events = [_suspend(t0), _suspend(t1), _suspend(t2), _resume(t3)]
        df = build_suspension_df(events, SERIAL)
        assert len(df) == 3
        # First: closed by second suspend
        assert df.iloc[0]["pairing_suspect"] == True
        assert df.iloc[0]["duration_minutes"] == pytest.approx(10.0)
        # Second: closed by third suspend
        assert df.iloc[1]["pairing_suspect"] == True
        assert df.iloc[1]["duration_minutes"] == pytest.approx(10.0)
        # Third: normal resume
        assert df.iloc[2]["pairing_suspect"] == False
        assert df.iloc[2]["duration_minutes"] == pytest.approx(30.0)

    def test_exactly_24h_not_suspect(self):
        """Duration of exactly 24h (1440 min) should NOT be suspect (> check, not >=)."""
        t0 = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        t1 = t0 + timedelta(hours=24)
        events = [_suspend(t0), _resume(t1)]
        df = build_suspension_df(events, SERIAL)
        assert len(df) == 1
        assert df.iloc[0]["pairing_suspect"] == False

    def test_suspend_reason_unknown_raw(self):
        """Unknown suspend reason raw value maps to 'unknown'."""
        t0 = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        t1 = t0 + timedelta(minutes=10)
        events = [_suspend(t0, reason_raw=99), _resume(t1)]
        df = build_suspension_df(events, SERIAL)
        assert df.iloc[0]["suspend_reason"] == "unknown"

    def test_insulin_at_suspend_preserved(self):
        t0 = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        t1 = t0 + timedelta(minutes=10)
        events = [_suspend(t0, insulin=157), _resume(t1)]
        df = build_suspension_df(events, SERIAL)
        assert df.iloc[0]["insulin_at_suspend"] == 157

    def test_empty_events(self):
        df = build_suspension_df([], SERIAL)
        assert df.empty
