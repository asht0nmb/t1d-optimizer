"""Tests for build_alarm_df — alarm, alert, and CGM alert builder."""

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tconnectsync.eventparser.events import (
    LidAlarmActivated,
    LidAlarmCleared,
    LidAlertActivated,
    LidAlertCleared,
    LidCgmAlertActivatedDex,
    LidCgmAlertClearedDex,
    LidCgmAlertAckDex,
)

from ingestion.builders import build_alarm_df, build_all

PST = timezone(timedelta(hours=-8))
SERIAL = "TEST123"

ALARM_COLUMNS = [
    "timestamp", "category", "action", "alarm_id", "alarm_name",
    "param1", "param2", "seqnum", "pump_serial",
]


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _ts(dt: datetime):
    ts = MagicMock()
    ts.datetime = dt
    return ts


def _alarm_activated(dt, alarm_id_raw=10, alarm_name="SomeAlarm",
                     param1=1.0, param2=2.0, seq=100):
    e = MagicMock(spec=LidAlarmActivated)
    e.eventTimestamp = _ts(dt)
    e.alarmidRaw = alarm_id_raw
    e.seqNum = seq
    # Use a mock for alarmid that has a .name attribute
    aid = MagicMock()
    aid.name = alarm_name
    e.alarmid = aid
    e.param1 = param1
    e.param2 = param2
    return e


def _alarm_cleared(dt, alarm_id_raw=10, alarm_name="SomeAlarm", seq=101):
    e = MagicMock(spec=LidAlarmCleared)
    e.eventTimestamp = _ts(dt)
    e.alarmidRaw = alarm_id_raw
    e.seqNum = seq
    aid = MagicMock()
    aid.name = alarm_name
    e.alarmid = aid
    # Cleared events have no param1/param2
    del e.param1
    del e.param2
    return e


def _alert_activated(dt, alert_id_raw=30, alert_name="SomeAlert",
                     param1=3.0, param2=4.0, seq=200):
    e = MagicMock(spec=LidAlertActivated)
    e.eventTimestamp = _ts(dt)
    e.alertidRaw = alert_id_raw
    e.seqNum = seq
    aid = MagicMock()
    aid.name = alert_name
    e.alertid = aid
    e.param1 = param1
    e.param2 = param2
    return e


def _alert_cleared(dt, alert_id_raw=30, alert_name="SomeAlert", seq=201):
    e = MagicMock(spec=LidAlertCleared)
    e.eventTimestamp = _ts(dt)
    e.alertidRaw = alert_id_raw
    e.seqNum = seq
    aid = MagicMock()
    aid.name = alert_name
    e.alertid = aid
    del e.param1
    del e.param2
    return e


def _cgm_alert_activated(dt, dalert_id_raw=2, dalert_id=None,
                          param1=5.0, param2=6.0, seq=300):
    e = MagicMock(spec=LidCgmAlertActivatedDex)
    e.eventTimestamp = _ts(dt)
    e.dalertidRaw = dalert_id_raw
    e.dalertid = dalert_id
    e.param1 = param1
    e.param2 = param2
    e.seqNum = seq
    return e


def _cgm_alert_cleared(dt, dalert_id_raw=2, dalert_id=None, seq=301):
    e = MagicMock(spec=LidCgmAlertClearedDex)
    e.eventTimestamp = _ts(dt)
    e.dalertidRaw = dalert_id_raw
    e.dalertid = dalert_id
    e.seqNum = seq
    del e.param1
    del e.param2
    return e


def _cgm_alert_ack(dt, dalert_id_raw=2, dalert_id=None, seq=302):
    e = MagicMock(spec=LidCgmAlertAckDex)
    e.eventTimestamp = _ts(dt)
    e.dalertidRaw = dalert_id_raw
    e.dalertid = dalert_id
    e.seqNum = seq
    del e.param1
    del e.param2
    return e


# ===========================================================================
# Tests
# ===========================================================================

class TestBuildAlarmDf:
    def test_alarm_activated_basic(self):
        dt = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        events = [_alarm_activated(dt, alarm_id_raw=10, alarm_name="OcclusionAlarm",
                                   param1=1.0, param2=2.0, seq=100)]
        df = build_alarm_df(events, SERIAL)
        assert len(df) == 1
        assert list(df.columns) == ALARM_COLUMNS
        row = df.iloc[0]
        assert row["category"] == "alarm"
        assert row["action"] == "activated"
        assert row["alarm_id"] == 10
        assert row["alarm_name"] == "OcclusionAlarm"
        assert row["param1"] == 1.0
        assert row["param2"] == 2.0
        assert row["seqnum"] == 100
        assert row["pump_serial"] == SERIAL

    def test_alarm_cleared_no_params(self):
        dt = datetime(2026, 3, 20, 10, 5, tzinfo=PST)
        events = [_alarm_cleared(dt, alarm_id_raw=10, alarm_name="OcclusionAlarm", seq=101)]
        df = build_alarm_df(events, SERIAL)
        assert len(df) == 1
        row = df.iloc[0]
        assert row["category"] == "alarm"
        assert row["action"] == "cleared"
        assert math.isnan(row["param1"])
        assert math.isnan(row["param2"])

    def test_alert_name_override_50(self):
        dt = datetime(2026, 3, 20, 11, 0, tzinfo=PST)
        events = [_alert_activated(dt, alert_id_raw=50, alert_name="DefaultAlert50", seq=200)]
        df = build_alarm_df(events, SERIAL)
        assert df.iloc[0]["alarm_name"] == "high_bg_alert"

    def test_alert_name_override_51(self):
        dt = datetime(2026, 3, 20, 11, 0, tzinfo=PST)
        events = [_alert_activated(dt, alert_id_raw=51, alert_name="DefaultAlert51", seq=201)]
        df = build_alarm_df(events, SERIAL)
        assert df.iloc[0]["alarm_name"] == "low_bg_alert"

    def test_alert_cleared_no_params(self):
        dt = datetime(2026, 3, 20, 11, 5, tzinfo=PST)
        events = [_alert_cleared(dt, alert_id_raw=50, alert_name="DefaultAlert50", seq=202)]
        df = build_alarm_df(events, SERIAL)
        assert len(df) == 1
        row = df.iloc[0]
        assert row["category"] == "alert"
        assert row["action"] == "cleared"
        assert row["alarm_name"] == "high_bg_alert"
        assert math.isnan(row["param1"])

    def test_cgm_alert_dalertid_mapping_all(self):
        """All 6 dalertidRaw values should map correctly."""
        dt_base = datetime(2026, 3, 20, 12, 0, tzinfo=PST)
        mapping = {
            1: "cgm_urgent_low",
            2: "cgm_high",
            3: "cgm_low",
            6: "cgm_rise_rate",
            8: "cgm_fall_rate",
            14: "cgm_out_of_range",
        }
        events = [
            _cgm_alert_activated(dt_base + timedelta(minutes=i), dalert_id_raw=raw, seq=300 + i)
            for i, raw in enumerate(mapping.keys())
        ]
        df = build_alarm_df(events, SERIAL)
        assert len(df) == 6
        for i, (raw, expected_name) in enumerate(mapping.items()):
            assert df.iloc[i]["alarm_name"] == expected_name
            assert df.iloc[i]["alarm_id"] == raw
            assert df.iloc[i]["category"] == "cgm_alert"

    def test_cgm_alert_cleared(self):
        dt = datetime(2026, 3, 20, 13, 0, tzinfo=PST)
        events = [_cgm_alert_cleared(dt, dalert_id_raw=2, seq=310)]
        df = build_alarm_df(events, SERIAL)
        assert len(df) == 1
        row = df.iloc[0]
        assert row["category"] == "cgm_alert"
        assert row["action"] == "cleared"
        assert row["alarm_name"] == "cgm_high"
        assert math.isnan(row["param1"])
        assert math.isnan(row["param2"])

    def test_cgm_alert_ack(self):
        dt = datetime(2026, 3, 20, 13, 5, tzinfo=PST)
        events = [_cgm_alert_ack(dt, dalert_id_raw=3, seq=311)]
        df = build_alarm_df(events, SERIAL)
        assert len(df) == 1
        row = df.iloc[0]
        assert row["action"] == "ack"
        assert row["alarm_name"] == "cgm_low"

    def test_mixed_events(self):
        dt = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        events = [
            _alarm_activated(dt, seq=1),
            _alert_activated(dt + timedelta(minutes=1), alert_id_raw=50, seq=2),
            _cgm_alert_activated(dt + timedelta(minutes=2), dalert_id_raw=1, seq=3),
        ]
        df = build_alarm_df(events, SERIAL)
        assert len(df) == 3
        assert set(df["category"]) == {"alarm", "alert", "cgm_alert"}

    def test_empty_list(self):
        df = build_alarm_df([], SERIAL)
        assert df.empty
        assert list(df.columns) == ALARM_COLUMNS

    def test_build_all_includes_alarms_key(self):
        result = build_all([], SERIAL)
        assert "alarms" in result
