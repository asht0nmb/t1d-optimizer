"""Tests for ingestion/builders.py — all 6 DataFrame builders."""

import logging
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tconnectsync.eventparser.events import (
    LidAaDailyStatus,
    LidAaPcmChange,
    LidAaUserModeChange,
    LidBasalDelivery,
    LidBolusCompleted,
    LidBolusRequestedMsg1,
    LidBolusRequestedMsg2,
    LidBolusRequestedMsg3,
    LidCannulaFilled,
    LidCgmDataG7,
    LidNewDay,
    LidPumpingResumed,
    LidPumpingSuspended,
)

from ingestion.builders import (
    build_all,
    build_basal_df,
    build_bolus_df,
    build_cgm_df,
    build_events_df,
    build_request_df,
    build_suspension_df,
)

PST = timezone(timedelta(hours=-8))
SERIAL = "TEST123"


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _ts(dt: datetime):
    """Create a mock object whose .datetime property returns *dt*."""
    ts = MagicMock()
    ts.datetime = dt
    return ts


def _cgm(dt: datetime, bg: int, seq: int = 0) -> MagicMock:
    e = MagicMock(spec=LidCgmDataG7)
    e.eventTimestamp = _ts(dt)
    e.currentglucosedisplayvalue = bg
    e.seqNum = seq
    return e


def _bolus_completed(dt: datetime, insulin: float, bolus_id: int) -> MagicMock:
    e = MagicMock(spec=LidBolusCompleted)
    e.eventTimestamp = _ts(dt)
    e.insulindelivered = insulin
    e.bolusid = bolus_id
    return e


def _msg1(dt: datetime, bolus_id: int, carbs: int = 45, bg: int = 180, iob: float = 1.2) -> MagicMock:
    e = MagicMock(spec=LidBolusRequestedMsg1)
    e.eventTimestamp = _ts(dt)
    e.bolusid = bolus_id
    e.carbamount = carbs
    e.BG = bg
    e.IOB = iob
    return e


def _msg2(bolus_id: int, options_raw: int = 0, user_override: int = 0) -> MagicMock:
    e = MagicMock(spec=LidBolusRequestedMsg2)
    e.bolusid = bolus_id
    e.optionsRaw = options_raw
    e.useroverrideRaw = user_override
    return e


def _msg3(bolus_id: int, food: float = 3.0, correction: float = 1.0, total: float = 4.0) -> MagicMock:
    e = MagicMock(spec=LidBolusRequestedMsg3)
    e.bolusid = bolus_id
    e.foodbolussize = food
    e.correctionbolussize = correction
    e.totalbolussize = total
    return e


def _basal(dt: datetime, commanded_rate: int, source_raw: int = 3) -> MagicMock:
    e = MagicMock(spec=LidBasalDelivery)
    e.eventTimestamp = _ts(dt)
    e.commandedRate = commanded_rate
    e.commandedRateSourceRaw = source_raw
    return e


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


def _mode_change(dt: datetime, current: int, previous: int, seq: int = 1) -> MagicMock:
    e = MagicMock(spec=LidAaUserModeChange)
    e.eventTimestamp = _ts(dt)
    e.currentusermodeRaw = current
    e.previoususermodeRaw = previous
    e.requestedactionRaw = 0
    e.exercisetime = 60
    e.seqNum = seq
    return e


def _new_day(dt: datetime, basal_rate: float = 0.8, seq: int = 10) -> MagicMock:
    e = MagicMock(spec=LidNewDay)
    e.eventTimestamp = _ts(dt)
    e.commandedbasalrate = basal_rate
    e.seqNum = seq
    return e


def _cannula(dt: datetime, prime: float = 0.3, seq: int = 20) -> MagicMock:
    e = MagicMock(spec=LidCannulaFilled)
    e.eventTimestamp = _ts(dt)
    e.primesize = prime
    e.seqNum = seq
    return e


# ===========================================================================
# 1. build_cgm_df
# ===========================================================================


class TestBuildCgmDf:
    def test_basic_rows(self):
        dt1 = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        dt2 = datetime(2026, 3, 20, 10, 5, tzinfo=PST)
        events = [_cgm(dt1, 150, 1), _cgm(dt2, 160, 2)]
        df = build_cgm_df(events, SERIAL)
        assert len(df) == 2
        assert list(df.columns) == ["timestamp", "bg_mgdl", "pump_serial"]
        assert df.iloc[0]["bg_mgdl"] == 150
        assert df.iloc[1]["bg_mgdl"] == 160

    def test_dedup_on_timestamp(self):
        dt = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        events = [_cgm(dt, 150, 1), _cgm(dt, 155, 2)]
        df = build_cgm_df(events, SERIAL)
        assert len(df) == 1
        assert df.iloc[0]["bg_mgdl"] == 150  # keep="first"

    def test_empty_list(self):
        df = build_cgm_df([], SERIAL)
        assert df.empty
        assert list(df.columns) == ["timestamp", "bg_mgdl", "pump_serial"]

    def test_non_cgm_events_ignored(self):
        events = [_bolus_completed(datetime(2026, 3, 20, 10, 0, tzinfo=PST), 2.0, 1)]
        df = build_cgm_df(events, SERIAL)
        assert df.empty


# ===========================================================================
# 2. build_bolus_df
# ===========================================================================


class TestBuildBolusDf:
    def test_basic_rows(self):
        dt = datetime(2026, 3, 20, 12, 0, tzinfo=PST)
        events = [_bolus_completed(dt, 3.5, 42)]
        df = build_bolus_df(events, SERIAL)
        assert len(df) == 1
        assert df.iloc[0]["insulin_units"] == 3.5
        assert df.iloc[0]["bolus_id"] == 42

    def test_empty_list(self):
        df = build_bolus_df([], SERIAL)
        assert df.empty


# ===========================================================================
# 3. build_request_df
# ===========================================================================


class TestBuildRequestDf:
    def test_full_three_way_join(self):
        dt = datetime(2026, 3, 20, 12, 0, tzinfo=PST)
        events = [_msg1(dt, 1), _msg2(1, options_raw=0, user_override=0), _msg3(1)]
        df = build_request_df(events, SERIAL)
        assert len(df) == 1
        row = df.iloc[0]
        assert row["bolus_source"] == "user"
        assert row["food_insulin"] == 3.0
        assert row["correction_insulin"] == 1.0
        assert row["total_requested"] == 4.0

    def test_msg1_only(self):
        dt = datetime(2026, 3, 20, 12, 0, tzinfo=PST)
        events = [_msg1(dt, 1)]
        df = build_request_df(events, SERIAL)
        assert len(df) == 1
        row = df.iloc[0]
        assert row["bolus_source"] == "unknown"
        assert math.isnan(row["food_insulin"])
        assert math.isnan(row["correction_insulin"])
        assert math.isnan(row["total_requested"])

    def test_msg1_plus_msg2_no_msg3(self):
        dt = datetime(2026, 3, 20, 12, 0, tzinfo=PST)
        events = [_msg1(dt, 1), _msg2(1, options_raw=3)]
        df = build_request_df(events, SERIAL)
        assert len(df) == 1
        row = df.iloc[0]
        assert row["bolus_source"] == "auto"
        assert math.isnan(row["food_insulin"])

    def test_orphan_msg2_msg3_discarded(self):
        """Msg2/Msg3 without matching Msg1 should not appear in output."""
        events = [_msg2(99), _msg3(99)]
        df = build_request_df(events, SERIAL)
        assert df.empty

    def test_carbs_zero_pure_correction(self):
        dt = datetime(2026, 3, 20, 12, 0, tzinfo=PST)
        events = [
            _msg1(dt, 1, carbs=0, bg=250, iob=0.5),
            _msg2(1),
            _msg3(1, food=0.0, correction=2.0, total=2.0),
        ]
        df = build_request_df(events, SERIAL)
        assert df.iloc[0]["carbs_g"] == 0

    def test_bolus_source_auto(self):
        dt = datetime(2026, 3, 20, 12, 0, tzinfo=PST)
        events = [_msg1(dt, 1), _msg2(1, options_raw=3)]
        df = build_request_df(events, SERIAL)
        assert df.iloc[0]["bolus_source"] == "auto"

    def test_bolus_source_auto_6(self):
        dt = datetime(2026, 3, 20, 12, 0, tzinfo=PST)
        events = [_msg1(dt, 1), _msg2(1, options_raw=6)]
        df = build_request_df(events, SERIAL)
        assert df.iloc[0]["bolus_source"] == "auto"

    def test_bolus_source_override(self):
        dt = datetime(2026, 3, 20, 12, 0, tzinfo=PST)
        events = [_msg1(dt, 1), _msg2(1, options_raw=0, user_override=1)]
        df = build_request_df(events, SERIAL)
        assert df.iloc[0]["bolus_source"] == "override"

    def test_bolus_source_user(self):
        dt = datetime(2026, 3, 20, 12, 0, tzinfo=PST)
        events = [_msg1(dt, 1), _msg2(1, options_raw=0, user_override=0)]
        df = build_request_df(events, SERIAL)
        assert df.iloc[0]["bolus_source"] == "user"

    def test_bolus_source_unknown_out_of_range(self):
        dt = datetime(2026, 3, 20, 12, 0, tzinfo=PST)
        events = [_msg1(dt, 1), _msg2(1, options_raw=99)]
        df = build_request_df(events, SERIAL)
        assert df.iloc[0]["bolus_source"] == "unknown"


# ===========================================================================
# 4. build_basal_df
# ===========================================================================


class TestBuildBasalDf:
    def test_commanded_rate_conversion(self):
        dt = datetime(2026, 3, 20, 14, 0, tzinfo=PST)
        events = [_basal(dt, 1500, source_raw=3)]
        df = build_basal_df(events, SERIAL)
        assert len(df) == 1
        assert df.iloc[0]["commanded_rate"] == pytest.approx(1.5)

    def test_plausibility_range(self):
        """All output rates should be in [0, 10] u/hr for any reasonable input."""
        events = [
            _basal(datetime(2026, 3, 20, 14, i, tzinfo=PST), rate, source_raw=1)
            for i, rate in enumerate([0, 500, 1000, 2000, 5000, 10000])
        ]
        df = build_basal_df(events, SERIAL)
        assert (df["commanded_rate"] >= 0).all()
        assert (df["commanded_rate"] <= 10).all()

    def test_rate_source_mapping(self):
        dt_base = datetime(2026, 3, 20, 14, 0, tzinfo=PST)
        events = [
            _basal(dt_base + timedelta(minutes=i), 1000, source_raw=src)
            for i, src in enumerate([0, 1, 2, 3, 4])
        ]
        df = build_basal_df(events, SERIAL)
        expected = ["suspended", "profile", "temp_rate", "algorithm", "temp_rate_and_algorithm"]
        assert list(df["rate_source"]) == expected

    def test_unknown_rate_source(self):
        dt = datetime(2026, 3, 20, 14, 0, tzinfo=PST)
        events = [_basal(dt, 1000, source_raw=99)]
        df = build_basal_df(events, SERIAL)
        assert df.iloc[0]["rate_source"] == "unknown"

    def test_empty_list(self):
        df = build_basal_df([], SERIAL)
        assert df.empty


# ===========================================================================
# 5. build_suspension_df
# ===========================================================================


class TestBuildSuspensionDf:
    def test_normal_pair(self):
        t0 = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        t1 = datetime(2026, 3, 20, 10, 30, tzinfo=PST)
        events = [_suspend(t0), _resume(t1)]
        df = build_suspension_df(events, SERIAL)
        assert len(df) == 1
        assert df.iloc[0]["duration_minutes"] == pytest.approx(30.0)
        assert df.iloc[0]["pairing_suspect"] == False

    def test_orphan_suspend(self):
        t0 = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        events = [_suspend(t0)]
        df = build_suspension_df(events, SERIAL)
        assert len(df) == 1
        assert pd.isna(df.iloc[0]["resume_timestamp"])
        assert math.isnan(df.iloc[0]["duration_minutes"])

    def test_orphan_resume_ignored(self):
        t0 = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        events = [_resume(t0)]
        df = build_suspension_df(events, SERIAL)
        assert df.empty

    def test_out_of_order_timestamps_sorted(self):
        """Resume before suspend in list, but correct after sort by timestamp."""
        t0 = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        t1 = datetime(2026, 3, 20, 10, 30, tzinfo=PST)
        # Put resume first in the list; builder should sort by timestamp
        events = [_resume(t1), _suspend(t0)]
        df = build_suspension_df(events, SERIAL)
        assert len(df) == 1
        assert df.iloc[0]["duration_minutes"] == pytest.approx(30.0)

    def test_duration_over_24h_suspect(self):
        t0 = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        t1 = t0 + timedelta(hours=25)
        events = [_suspend(t0), _resume(t1)]
        df = build_suspension_df(events, SERIAL)
        assert len(df) == 1
        assert df.iloc[0]["pairing_suspect"] == True

    def test_double_suspend_then_resume(self):
        """Two suspends then one resume: first episode closed at second suspend's time."""
        t0 = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        t1 = datetime(2026, 3, 20, 10, 15, tzinfo=PST)
        t2 = datetime(2026, 3, 20, 10, 45, tzinfo=PST)
        events = [_suspend(t0), _suspend(t1), _resume(t2)]
        df = build_suspension_df(events, SERIAL)
        assert len(df) == 2
        # First episode: closed by the second suspend, pairing_suspect=True
        assert df.iloc[0]["duration_minutes"] == pytest.approx(15.0)
        assert df.iloc[0]["pairing_suspect"] == True
        # Second episode: normal
        assert df.iloc[1]["duration_minutes"] == pytest.approx(30.0)
        assert df.iloc[1]["pairing_suspect"] == False

    def test_suspend_reason_mapping(self):
        t0 = datetime(2026, 3, 20, 10, 0, tzinfo=PST)
        t1 = datetime(2026, 3, 20, 10, 30, tzinfo=PST)
        events = [_suspend(t0, reason_raw=6), _resume(t1)]
        df = build_suspension_df(events, SERIAL)
        assert df.iloc[0]["suspend_reason"] == "plgs_auto"


# ===========================================================================
# 6. build_events_df
# ===========================================================================


class TestBuildEventsDf:
    def test_mode_change(self):
        dt = datetime(2026, 3, 20, 9, 0, tzinfo=PST)
        events = [_mode_change(dt, current=2, previous=0, seq=5)]
        df = build_events_df(events, SERIAL)
        assert len(df) == 1
        assert df.iloc[0]["event_type"] == "mode_change"
        assert df.iloc[0]["event_subtype"] == "exercising"
        assert df.iloc[0]["previous_mode"] == "normal"

    def test_site_change_cannula(self):
        dt = datetime(2026, 3, 20, 9, 0, tzinfo=PST)
        events = [_cannula(dt, prime=0.3, seq=20)]
        df = build_events_df(events, SERIAL)
        assert len(df) == 1
        assert df.iloc[0]["event_type"] == "site_change"
        assert df.iloc[0]["event_subtype"] == "cannula"

    def test_daily_marker(self):
        dt = datetime(2026, 3, 20, 0, 0, tzinfo=PST)
        events = [_new_day(dt, basal_rate=0.8, seq=10)]
        df = build_events_df(events, SERIAL)
        assert len(df) == 1
        assert df.iloc[0]["event_type"] == "daily_marker"
        assert df.iloc[0]["event_subtype"] == "new_day"

    def test_empty_list(self):
        df = build_events_df([], SERIAL)
        assert df.empty


# ===========================================================================
# 7. build_all
# ===========================================================================


class TestBuildAll:
    def test_returns_dict_with_all_keys(self):
        events = [
            _cgm(datetime(2026, 3, 20, 10, 0, tzinfo=PST), 150),
            _bolus_completed(datetime(2026, 3, 20, 12, 0, tzinfo=PST), 3.0, 1),
        ]
        result = build_all(events, SERIAL)
        expected_keys = {"cgm", "bolus", "requests", "basal", "suspension", "events"}
        assert set(result.keys()) == expected_keys

    def test_unknown_event_logged(self, caplog):
        """An event not in _HANDLED_TYPES should produce a warning log."""

        class FakeUnknownEvent:
            pass

        unknown = FakeUnknownEvent()
        unknown.eventTimestamp = _ts(datetime(2026, 3, 20, 10, 0, tzinfo=PST))
        events = [unknown]
        with caplog.at_level(logging.WARNING, logger="ingestion.builders"):
            build_all(events, SERIAL)
        assert any("FakeUnknownEvent" in msg for msg in caplog.messages)
