"""Edge-case tests for suspension pairing logic in build_suspension_df."""

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tconnectsync.eventparser.events import (
    LidAlarmActivated,
    LidPumpingResumed,
    LidPumpingSuspended,
)

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
    e.suspendReasonRaw = reason_raw
    e.insulinAmount = insulin
    return e


def _resume(dt: datetime) -> MagicMock:
    e = MagicMock(spec=LidPumpingResumed)
    e.eventTimestamp = _ts(dt)
    return e


def _alarm(dt: datetime, alarm_id_raw: int, alarm_name: str, seq: int) -> MagicMock:
    e = MagicMock(spec=LidAlarmActivated)
    e.eventTimestamp = _ts(dt)
    e.alarmIdRaw = alarm_id_raw
    e.seqNum = seq
    aid = MagicMock()
    aid.name = alarm_name
    e.alarmId = aid
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

    def test_alarm_collision_at_same_timestamp_picks_lower_seqnum(self):
        """Real-world case (DATA_ISSUES.md #3, 2026-03-19): the pump fires
        the causal alarm (e.g. BatteryShutdownAlarm) and a companion
        ResumePumpAlarm2 at the exact same wall-clock second, one seqnum
        apart -- ResumePumpAlarm2 always fires second. A timestamp-keyed
        dict that just takes "whichever alarm was seen last" can silently
        report the companion alarm as the cause instead of the real one.
        The lower seqnum (first to fire) must win."""
        t0 = datetime(2026, 3, 19, 8, 6, 18, tzinfo=PST)
        t1 = t0 + timedelta(minutes=10)
        events = [
            _alarm(t0, alarm_id_raw=12, alarm_name="BatteryShutdownAlarm", seq=281768),
            _alarm(t0, alarm_id_raw=23, alarm_name="ResumePumpAlarm2", seq=281769),
            _suspend(t0, reason_raw=1),
            _resume(t1),
        ]
        df = build_suspension_df(events, SERIAL)
        assert len(df) == 1
        assert df.iloc[0]["alarm_id"] == 12
        assert df.iloc[0]["alarm_name"] == "BatteryShutdownAlarm"

    def test_alarm_collision_order_independent(self):
        """Same scenario as above but with the alarm events listed in the
        opposite order -- the lower-seqnum alarm must still win regardless
        of iteration order, since the old bug was purely last-write-wins."""
        t0 = datetime(2026, 3, 19, 22, 36, 35, tzinfo=PST)
        t1 = t0 + timedelta(minutes=5)
        events = [
            _alarm(t0, alarm_id_raw=23, alarm_name="ResumePumpAlarm2", seq=283549),
            _alarm(t0, alarm_id_raw=2, alarm_name="OcclusionAlarm", seq=283548),
            _suspend(t0, reason_raw=1),
            _resume(t1),
        ]
        df = build_suspension_df(events, SERIAL)
        assert len(df) == 1
        assert df.iloc[0]["alarm_id"] == 2
        assert df.iloc[0]["alarm_name"] == "OcclusionAlarm"
