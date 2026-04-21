"""Tests for `detection.features.daily_features`.

Uses LA-aware timestamps (matching ``config.user_config.yaml``) so day
boundaries line up predictably with test fixtures.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from detection.features import daily_features


LA = ZoneInfo("America/Los_Angeles")
DATE = date(2026, 3, 19)  # DST active (UTC-07:00)


# ---------------------------------------------------------------------------
# Frame schemas (match ingestion.builders outputs; tz-aware datetime dtypes
# created via pd.to_datetime + tz_convert as needed).
# ---------------------------------------------------------------------------

_CGM_COLS = [
    "timestamp", "bg_mgdl", "backfilled",
    "sensor_timestamp", "pump_serial", "seqnum",
]
_BOLUS_COLS = ["timestamp", "insulin_units", "bolus_id", "pump_serial"]
_BASAL_COLS = ["timestamp", "commanded_rate", "rate_source", "pump_serial"]
_REQUESTS_COLS = [
    "timestamp", "bolus_id", "carbs_g", "bg_mgdl", "iob",
    "bolus_source", "food_insulin", "correction_insulin",
    "total_requested", "pump_serial", "bolus_category", "override_delta",
]
_ALARMS_COLS = [
    "timestamp", "category", "action", "alarm_id", "alarm_name",
    "param1", "param2", "seqnum", "pump_serial",
]
_SUSPENSION_COLS = [
    "suspend_timestamp", "resume_timestamp", "duration_minutes",
    "suspend_reason", "insulin_at_suspend", "pairing_suspect",
    "pump_serial", "alarm_id", "alarm_name",
]
_CGM_GAPS_COLS = [
    "start_ts", "end_ts", "duration_minutes", "pump_serial", "ongoing",
]


def _empty_cgm() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
            "bg_mgdl": pd.Series(dtype="int64"),
            "backfilled": pd.Series(dtype="bool"),
            "sensor_timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
            "pump_serial": pd.Series(dtype="object"),
            "seqnum": pd.Series(dtype="int64"),
        }
    )[_CGM_COLS]


def _empty_bolus() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
            "insulin_units": pd.Series(dtype="float64"),
            "bolus_id": pd.Series(dtype="int64"),
            "pump_serial": pd.Series(dtype="object"),
        }
    )[_BOLUS_COLS]


def _empty_basal() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
            "commanded_rate": pd.Series(dtype="float64"),
            "rate_source": pd.Series(dtype="object"),
            "pump_serial": pd.Series(dtype="object"),
        }
    )[_BASAL_COLS]


def _empty_requests() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in _REQUESTS_COLS})


def _empty_alarms() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
            "category": pd.Series(dtype="object"),
            "action": pd.Series(dtype="object"),
            "alarm_id": pd.Series(dtype="int64"),
            "alarm_name": pd.Series(dtype="object"),
            "param1": pd.Series(dtype="float64"),
            "param2": pd.Series(dtype="float64"),
            "seqnum": pd.Series(dtype="int64"),
            "pump_serial": pd.Series(dtype="object"),
        }
    )[_ALARMS_COLS]


def _empty_suspension() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "suspend_timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
            "resume_timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
            "duration_minutes": pd.Series(dtype="float64"),
            "suspend_reason": pd.Series(dtype="object"),
            "insulin_at_suspend": pd.Series(dtype="int64"),
            "pairing_suspect": pd.Series(dtype="bool"),
            "pump_serial": pd.Series(dtype="object"),
            "alarm_id": pd.Series(dtype="float64"),
            "alarm_name": pd.Series(dtype="object"),
        }
    )[_SUSPENSION_COLS]


def _empty_cgm_gaps() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "start_ts": pd.Series(dtype="datetime64[ns, UTC]"),
            "end_ts": pd.Series(dtype="datetime64[ns, UTC]"),
            "duration_minutes": pd.Series(dtype="float64"),
            "pump_serial": pd.Series(dtype="object"),
            "ongoing": pd.Series(dtype="bool"),
        }
    )[_CGM_GAPS_COLS]


def _empty_frames_with(**overrides) -> dict:
    frames = {
        "cgm": _empty_cgm(),
        "bolus": _empty_bolus(),
        "basal": _empty_basal(),
        "requests": _empty_requests(),
        "alarms": _empty_alarms(),
        "suspension": _empty_suspension(),
        "cgm_gaps": _empty_cgm_gaps(),
    }
    frames.update(overrides)
    return frames


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _ts(day: date, hour: int, minute: int = 0) -> pd.Timestamp:
    return pd.Timestamp(datetime(day.year, day.month, day.day, hour, minute, tzinfo=LA))


def _flat_day_cgm(bg: int, the_date: date = DATE) -> pd.DataFrame:
    """288 CGM readings at 5-min cadence starting at 00:00 LA."""
    start = _ts(the_date, 0, 0)
    rows = []
    for i in range(288):
        rows.append(
            {
                "timestamp": start + timedelta(minutes=5 * i),
                "bg_mgdl": int(bg),
                "backfilled": False,
                "sensor_timestamp": pd.NaT,
                "pump_serial": "TEST",
                "seqnum": i,
            }
        )
    return pd.DataFrame(rows, columns=_CGM_COLS)


def _mixed_day_cgm(
    counts_by_bg: dict[int, int], the_date: date = DATE
) -> pd.DataFrame:
    """Build 288-reading day where ``counts_by_bg`` maps bg → reading count.

    Readings are emitted in the order given in the dict.
    """
    start = _ts(the_date, 0, 0)
    rows: list[dict] = []
    i = 0
    for bg, cnt in counts_by_bg.items():
        for _ in range(cnt):
            rows.append(
                {
                    "timestamp": start + timedelta(minutes=5 * i),
                    "bg_mgdl": int(bg),
                    "backfilled": False,
                    "sensor_timestamp": pd.NaT,
                    "pump_serial": "TEST",
                    "seqnum": i,
                }
            )
            i += 1
    return pd.DataFrame(rows, columns=_CGM_COLS)


def _boluses(units_list: list[float], the_date: date = DATE) -> pd.DataFrame:
    """Spread N boluses at 10:00, 13:00, 19:00, ... (one slot per entry)."""
    hours = [10, 13, 19, 21, 23]
    rows = []
    for i, u in enumerate(units_list):
        rows.append(
            {
                "timestamp": _ts(the_date, hours[i % len(hours)], 0),
                "insulin_units": float(u),
                "bolus_id": i,
                "pump_serial": "TEST",
            }
        )
    return pd.DataFrame(rows, columns=_BOLUS_COLS)


def _basal_constant_rate(
    rate_u_per_hr: float, the_date: date = DATE
) -> pd.DataFrame:
    """24 basal rows at 00:00, 01:00, ..., 23:00 with a constant rate."""
    rows = []
    for h in range(24):
        rows.append(
            {
                "timestamp": _ts(the_date, h, 0),
                "commanded_rate": float(rate_u_per_hr),
                "rate_source": "profile",
                "pump_serial": "TEST",
            }
        )
    return pd.DataFrame(rows, columns=_BASAL_COLS)


def _requests_frame(rows: list[dict]) -> pd.DataFrame:
    filled = []
    for r in rows:
        base = {
            "bolus_id": 0,
            "carbs_g": 0,
            "bg_mgdl": 0,
            "iob": 0.0,
            "bolus_source": "user",
            "food_insulin": float("nan"),
            "correction_insulin": float("nan"),
            "total_requested": float("nan"),
            "pump_serial": "TEST",
            "override_delta": 0.0,
        }
        base.update(r)
        filled.append(base)
    return pd.DataFrame(filled, columns=_REQUESTS_COLS)


def _cgm_gaps_frame(rows: list[dict]) -> pd.DataFrame:
    filled = []
    for r in rows:
        base = {"pump_serial": "TEST", "ongoing": False, "end_ts": pd.NaT}
        base.update(r)
        filled.append(base)
    return pd.DataFrame(filled, columns=_CGM_GAPS_COLS)


def _alarms_frame(rows: list[dict]) -> pd.DataFrame:
    filled = []
    for i, r in enumerate(rows):
        base = {
            "category": "alarm",
            "alarm_id": 0,
            "alarm_name": "test",
            "param1": float("nan"),
            "param2": float("nan"),
            "seqnum": i,
            "pump_serial": "TEST",
        }
        base.update(r)
        filled.append(base)
    return pd.DataFrame(filled, columns=_ALARMS_COLS)


def _suspension_frame(rows: list[dict]) -> pd.DataFrame:
    filled = []
    for r in rows:
        base = {
            "duration_minutes": float("nan"),
            "suspend_reason": "user",
            "insulin_at_suspend": 0,
            "pairing_suspect": False,
            "pump_serial": "TEST",
            "alarm_id": float("nan"),
            "alarm_name": None,
        }
        base.update(r)
        # Fill duration if not given
        if pd.isna(base["duration_minutes"]) and pd.notna(base["resume_timestamp"]):
            base["duration_minutes"] = (
                base["resume_timestamp"] - base["suspend_timestamp"]
            ).total_seconds() / 60.0
        filled.append(base)
    return pd.DataFrame(filled, columns=_SUSPENSION_COLS)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDailyFeatures:
    def test_perfect_tir_day(self, default_config):
        frames = _empty_frames_with(cgm=_flat_day_cgm(150))
        f = daily_features(frames, DATE, default_config)

        assert f["date"] == DATE
        assert f["tir_70_180"] == 1.0
        assert f["time_below_70"] == 0.0
        assert f["time_above_180"] == 0.0
        assert f["time_above_250"] == 0.0
        assert f["mean_bg"] == 150.0
        assert f["std_bg"] == 0.0
        assert f["cv_bg"] == 0.0

    def test_low_bg_time_below_70(self, default_config):
        frames = _empty_frames_with(cgm=_mixed_day_cgm({60: 100, 150: 188}))
        f = daily_features(frames, DATE, default_config)
        assert f["time_below_70"] == pytest.approx(100 / 288, abs=1e-6)
        assert f["tir_70_180"] == pytest.approx(188 / 288, abs=1e-6)
        assert f["time_above_180"] == 0.0
        assert f["time_above_250"] == 0.0

    def test_above_180_and_250_split(self, default_config):
        # 100 at 200 (above 180, <=250), 80 at 220 (above 180, <=250),
        # 60 at 260 (>250), 48 at 150 (in range).
        frames = _empty_frames_with(
            cgm=_mixed_day_cgm({200: 100, 220: 80, 260: 60, 150: 48})
        )
        f = daily_features(frames, DATE, default_config)
        assert f["time_above_180"] == pytest.approx(180 / 288, abs=1e-6)
        assert f["time_above_250"] == pytest.approx(60 / 288, abs=1e-6)
        assert f["tir_70_180"] == pytest.approx(48 / 288, abs=1e-6)

    def test_total_daily_insulin(self, default_config):
        # 3 boluses summing to 6.5u + constant 1 u/hr basal × 24h = 24u.
        bolus = _boluses([1.5, 2.0, 3.0])
        basal = _basal_constant_rate(1.0)
        frames = _empty_frames_with(
            cgm=_flat_day_cgm(150), bolus=bolus, basal=basal
        )
        f = daily_features(frames, DATE, default_config)
        assert f["total_daily_insulin"] == pytest.approx(6.5 + 24.0, abs=1e-6)
        assert f["basal_bolus_ratio"] == pytest.approx(24.0 / 6.5, abs=1e-6)

    def test_basal_bolus_ratio_nan_when_no_bolus(self, default_config):
        frames = _empty_frames_with(
            cgm=_flat_day_cgm(150), basal=_basal_constant_rate(1.0)
        )
        f = daily_features(frames, DATE, default_config)
        assert pd.isna(f["basal_bolus_ratio"])

    def test_meal_count_excludes_auto_and_correction_only(self, default_config):
        reqs = _requests_frame(
            [
                {
                    "timestamp": _ts(DATE, 8, 0),
                    "bolus_category": "user_meal",
                    "carbs_g": 30,
                },
                {
                    "timestamp": _ts(DATE, 10, 0),
                    "bolus_category": "auto_correction",
                    "carbs_g": 0,
                    "bolus_source": "auto",
                },
                {
                    "timestamp": _ts(DATE, 12, 0),
                    "bolus_category": "user_correction_only",
                    "carbs_g": 0,
                },
                {
                    "timestamp": _ts(DATE, 18, 0),
                    "bolus_category": "user_meal_and_correction",
                    "carbs_g": 45,
                },
                {
                    "timestamp": _ts(DATE, 20, 0),
                    "bolus_category": "override_up",
                    "carbs_g": 15,
                    "bolus_source": "override",
                },
            ]
        )
        frames = _empty_frames_with(cgm=_flat_day_cgm(150), requests=reqs)
        f = daily_features(frames, DATE, default_config)
        assert f["meal_count"] == 3
        assert f["total_carbs_g"] == 90

    def test_overnight_dip(self, default_config):
        # 00:00–02:00 (24 readings at 140), 04:00–06:00 (24 at 100),
        # rest at 150 to keep the rest of TIR computation defined.
        # layout: 0..24 (00:00–02:00) at 140, 24..48 at 150 (02:00–04:00),
        # 48..72 at 100 (04:00–06:00), 72..288 at 150.
        rows_bg = [140] * 24 + [150] * 24 + [100] * 24 + [150] * 216
        start = _ts(DATE, 0, 0)
        rows = []
        for i, bg in enumerate(rows_bg):
            rows.append(
                {
                    "timestamp": start + timedelta(minutes=5 * i),
                    "bg_mgdl": int(bg),
                    "backfilled": False,
                    "sensor_timestamp": pd.NaT,
                    "pump_serial": "TEST",
                    "seqnum": i,
                }
            )
        cgm = pd.DataFrame(rows, columns=_CGM_COLS)
        frames = _empty_frames_with(cgm=cgm)
        f = daily_features(frames, DATE, default_config)
        # overnight_dip = mean(04:00–06:00) - mean(00:00–02:00) = 100 - 140 = -40
        assert f["overnight_dip"] == pytest.approx(-40.0, abs=1e-6)

    def test_mean_postprandial_peak(self, default_config):
        # CGM at 120 leading up to 12:00 then spikes to 180 within 2h, then back down.
        start = _ts(DATE, 0, 0)
        rows = []
        for i in range(288):
            t = start + timedelta(minutes=5 * i)
            if t <= _ts(DATE, 12, 0):
                # includes the bolus timestamp itself → anchor = 120
                bg = 120
            elif t <= _ts(DATE, 13, 0):
                # jumps to 180 shortly after bolus → peak within 2h
                bg = 180
            elif t <= _ts(DATE, 14, 0):
                bg = 150
            else:
                bg = 120
            rows.append(
                {
                    "timestamp": t,
                    "bg_mgdl": int(bg),
                    "backfilled": False,
                    "sensor_timestamp": pd.NaT,
                    "pump_serial": "TEST",
                    "seqnum": i,
                }
            )
        cgm = pd.DataFrame(rows, columns=_CGM_COLS)
        reqs = _requests_frame(
            [
                {
                    "timestamp": _ts(DATE, 12, 0),
                    "bolus_category": "user_meal",
                    "carbs_g": 40,
                }
            ]
        )
        frames = _empty_frames_with(cgm=cgm, requests=reqs)
        f = daily_features(frames, DATE, default_config)
        # anchor at 12:00 = 120; max in [12:00, 14:00] = 180; delta = 60.
        assert f["mean_postprandial_peak"] == pytest.approx(60.0, abs=1e-6)

    def test_mean_postprandial_peak_nan_when_no_meals(self, default_config):
        frames = _empty_frames_with(cgm=_flat_day_cgm(150))
        f = daily_features(frames, DATE, default_config)
        assert pd.isna(f["mean_postprandial_peak"])

    def test_alarm_count_counts_only_activated(self, default_config):
        alarms = _alarms_frame(
            [
                {"timestamp": _ts(DATE, 1, 0), "action": "activated"},
                {"timestamp": _ts(DATE, 1, 5), "action": "cleared"},
                {"timestamp": _ts(DATE, 2, 0), "action": "activated"},
                {"timestamp": _ts(DATE, 2, 5), "action": "ack"},
            ]
        )
        frames = _empty_frames_with(cgm=_flat_day_cgm(150), alarms=alarms)
        f = daily_features(frames, DATE, default_config)
        assert f["alarm_count"] == 2

    def test_suspension_minutes_overlap_and_clip(self, default_config):
        # Episode 1: fully inside day, 30 min.
        # Episode 2: spans midnight from day-1 23:45 to today 00:15, contributes 15 min.
        # Episode 3: spans midnight from today 23:50 to next day 00:20, contributes 10 min.
        sus = _suspension_frame(
            [
                {
                    "suspend_timestamp": _ts(DATE, 10, 0),
                    "resume_timestamp": _ts(DATE, 10, 30),
                },
                {
                    "suspend_timestamp": _ts(
                        date(DATE.year, DATE.month, DATE.day - 1), 23, 45
                    ),
                    "resume_timestamp": _ts(DATE, 0, 15),
                },
                {
                    "suspend_timestamp": _ts(DATE, 23, 50),
                    "resume_timestamp": _ts(
                        date(DATE.year, DATE.month, DATE.day + 1), 0, 20
                    ),
                },
            ]
        )
        frames = _empty_frames_with(cgm=_flat_day_cgm(150), suspension=sus)
        f = daily_features(frames, DATE, default_config)
        assert f["suspension_minutes"] == pytest.approx(30 + 15 + 10, abs=1e-6)

    def test_out_of_range_minutes_overlap(self, default_config):
        gaps = _cgm_gaps_frame(
            [
                # fully inside day: 30 min
                {
                    "start_ts": _ts(DATE, 10, 0),
                    "end_ts": _ts(DATE, 10, 30),
                    "duration_minutes": 30.0,
                },
                # crosses midnight into day: 10 min in day
                {
                    "start_ts": _ts(
                        date(DATE.year, DATE.month, DATE.day - 1), 23, 50
                    ),
                    "end_ts": _ts(DATE, 0, 10),
                    "duration_minutes": 20.0,
                },
            ]
        )
        frames = _empty_frames_with(cgm=_flat_day_cgm(150), cgm_gaps=gaps)
        f = daily_features(frames, DATE, default_config)
        assert f["out_of_range_minutes"] == pytest.approx(30 + 10, abs=1e-6)

    def test_out_of_range_ongoing_treated_as_day_end(self, default_config):
        # ongoing gap starting at 23:00 on DATE: contributes 60 min (23:00–24:00).
        gaps = _cgm_gaps_frame(
            [
                {
                    "start_ts": _ts(DATE, 23, 0),
                    "end_ts": pd.NaT,
                    "duration_minutes": float("nan"),
                    "ongoing": True,
                }
            ]
        )
        frames = _empty_frames_with(cgm=_flat_day_cgm(150), cgm_gaps=gaps)
        f = daily_features(frames, DATE, default_config)
        assert f["out_of_range_minutes"] == pytest.approx(60.0, abs=1e-6)

    def test_empty_frames_defaults(self, default_config):
        frames = _empty_frames_with()
        f = daily_features(frames, DATE, default_config)
        assert f["date"] == DATE
        # Counts / sums → 0
        assert f["meal_count"] == 0
        assert f["total_carbs_g"] == 0
        assert f["alarm_count"] == 0
        assert f["suspension_minutes"] == 0
        assert f["out_of_range_minutes"] == 0
        assert f["total_daily_insulin"] == 0
        # Ratios / means on empty → NaN
        for key in (
            "tir_70_180",
            "time_below_70",
            "time_above_180",
            "time_above_250",
            "mean_bg",
            "std_bg",
            "cv_bg",
            "basal_bolus_ratio",
            "overnight_dip",
            "mean_postprandial_peak",
        ):
            assert pd.isna(f[key]), f"{key} should be NaN on empty frames"

    def test_slicing_excludes_other_days(self, default_config):
        # Same 150-flat day plus readings on day-before and day-after at 60.
        base = _flat_day_cgm(150)
        before = pd.DataFrame(
            [
                {
                    "timestamp": _ts(
                        date(DATE.year, DATE.month, DATE.day - 1), 23, 30
                    ),
                    "bg_mgdl": 60,
                    "backfilled": False,
                    "sensor_timestamp": pd.NaT,
                    "pump_serial": "TEST",
                    "seqnum": -1,
                }
            ],
            columns=_CGM_COLS,
        )
        after = pd.DataFrame(
            [
                {
                    "timestamp": _ts(
                        date(DATE.year, DATE.month, DATE.day + 1), 0, 30
                    ),
                    "bg_mgdl": 60,
                    "backfilled": False,
                    "sensor_timestamp": pd.NaT,
                    "pump_serial": "TEST",
                    "seqnum": 288,
                }
            ],
            columns=_CGM_COLS,
        )
        cgm = pd.concat([before, base, after], ignore_index=True)
        frames = _empty_frames_with(cgm=cgm)
        f = daily_features(frames, DATE, default_config)
        assert f["mean_bg"] == 150.0  # prior/next-day 60s excluded
        assert f["tir_70_180"] == 1.0

    def test_missing_keys_tolerated(self, default_config):
        # Only provide cgm; function must not crash on missing keys.
        f = daily_features({"cgm": _flat_day_cgm(150)}, DATE, default_config)
        assert f["mean_bg"] == 150.0
        assert f["meal_count"] == 0
        assert f["alarm_count"] == 0
        assert pd.isna(f["basal_bolus_ratio"])

    def test_output_schema(self, default_config):
        f = daily_features(_empty_frames_with(), DATE, default_config)
        expected_keys = {
            "date",
            "tir_70_180",
            "time_below_70",
            "time_above_180",
            "time_above_250",
            "mean_bg",
            "std_bg",
            "cv_bg",
            "total_daily_insulin",
            "basal_bolus_ratio",
            "meal_count",
            "total_carbs_g",
            "overnight_dip",
            "mean_postprandial_peak",
            "alarm_count",
            "suspension_minutes",
            "out_of_range_minutes",
        }
        assert set(f.keys()) == expected_keys
