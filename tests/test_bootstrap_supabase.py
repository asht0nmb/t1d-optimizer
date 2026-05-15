"""Unit tests for the parquet → tuple converters in ``core.storage._postgres_converters``.

Pure unit tests: no DB connection, no psycopg2.connect mocking. Each test
exercises either one of the small null-safe scalar helpers, one of the
nine per-table row converters, or an internal-consistency invariant
(CONVERTERS dispatch, COLUMN_SPECS / converter tuple-length, or
TABLE_SPECS PK columns vs. db/migrations/0001_init.sql).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# scripts/ is not a package; mirror the repo's runtime path-shim so the
# test can ``import bootstrap_supabase`` for the TABLE_SPECS list and
# ``_df_to_rows`` helper without installing it.
_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import bootstrap_supabase as boot  # noqa: E402
from core.storage import _postgres_converters as conv  # noqa: E402

# psycopg2 is now a runtime dependency (Task 3 added psycopg2-binary). If it
# is somehow missing in the test env that's a real environment issue worth
# surfacing — skip the affected tests cleanly rather than failing collection.
psycopg2_extras = pytest.importorskip("psycopg2.extras")
Json = psycopg2_extras.Json

PST = timezone(timedelta(hours=-8))


# ─────────────────────────────────────────────────────────────────────────────
# _is_null
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value,expected",
    [
        (None, True),
        (pd.NaT, True),
        (np.nan, True),
        (float("nan"), True),
        (pd.NA, True),
        ("", False),  # empty string is a real value, not null
        ("foo", False),
        (0, False),
        (5, False),
        (False, False),
        (True, False),
        (pd.Timestamp("2026-03-19", tz="UTC"), False),
    ],
)
def test_is_null(value, expected) -> None:
    assert conv._is_null(value) is expected


# ─────────────────────────────────────────────────────────────────────────────
# _ts_or_none
# ─────────────────────────────────────────────────────────────────────────────

def test_ts_or_none_preserves_tz_offset() -> None:
    """tz-aware Timestamp must round-trip the offset, NOT normalize to UTC."""
    ts = pd.Timestamp("2026-03-19 14:30:00", tz="America/Los_Angeles")
    out = conv._ts_or_none(ts)
    assert isinstance(out, datetime)
    # Same wall clock, same offset (PDT = -07:00 on 2026-03-19).
    assert out.year == 2026 and out.month == 3 and out.day == 19
    assert out.hour == 14 and out.minute == 30
    assert out.tzinfo is not None
    assert out.utcoffset() == timedelta(hours=-7)


def test_ts_or_none_passthrough_naive_timestamp() -> None:
    """Naive Timestamp → naive datetime; helper does not fabricate a tz."""
    ts = pd.Timestamp("2026-03-19 14:30:00")
    out = conv._ts_or_none(ts)
    assert isinstance(out, datetime)
    assert out.tzinfo is None


def test_ts_or_none_nat_returns_none() -> None:
    assert conv._ts_or_none(pd.NaT) is None


def test_ts_or_none_none_returns_none() -> None:
    assert conv._ts_or_none(None) is None


# ─────────────────────────────────────────────────────────────────────────────
# _int_or_none
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value,expected",
    [
        (5, 5),
        (0, 0),
        (-3, -3),
        (5.0, 5),  # float-promoted int (e.g. suspension.alarm_id) → int
        (np.nan, None),
        (pd.NA, None),
        (None, None),
        (pd.NaT, None),
    ],
)
def test_int_or_none(value, expected) -> None:
    assert conv._int_or_none(value) == expected


# ─────────────────────────────────────────────────────────────────────────────
# _float_or_none
# ─────────────────────────────────────────────────────────────────────────────

def test_float_or_none_basic() -> None:
    assert conv._float_or_none(2.5) == 2.5
    assert conv._float_or_none(0) == 0.0
    assert conv._float_or_none(-1.25) == -1.25


def test_float_or_none_nulls() -> None:
    assert conv._float_or_none(np.nan) is None
    assert conv._float_or_none(pd.NA) is None
    assert conv._float_or_none(None) is None


def test_float_or_none_does_not_round_bolus_artifact() -> None:
    """The bolus float-promotion artifact 25.000001907348633 must pass
    through verbatim — the helper does NOT round; the column type does."""
    artifact = 25.000001907348633
    assert conv._float_or_none(artifact) == artifact


# ─────────────────────────────────────────────────────────────────────────────
# _bool_or_none
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        (None, None),
        (np.nan, None),
        (pd.NA, None),
    ],
)
def test_bool_or_none(value, expected) -> None:
    """Crucial for events.forced_by_alarm, whose object dtype carries
    True/False/None — never 1/0/NaN."""
    assert conv._bool_or_none(value) is expected


# ─────────────────────────────────────────────────────────────────────────────
# _str_or_none
# ─────────────────────────────────────────────────────────────────────────────

def test_str_or_none_strings_passthrough() -> None:
    assert conv._str_or_none("foo") == "foo"
    # Empty string is a real value (NOT null per _is_null contract).
    assert conv._str_or_none("") == ""


def test_str_or_none_nulls() -> None:
    assert conv._str_or_none(None) is None
    assert conv._str_or_none(np.nan) is None
    assert conv._str_or_none(pd.NA) is None


def test_str_or_none_coerces_numeric_via_str() -> None:
    """Document the implementation: the helper calls ``str(value)`` for any
    non-null input, so a numeric leaks through as its decimal repr. If this
    ever changes, the assertion will fail loudly and force a deliberate
    decision (since converters do rely on the coercion for stringy fields)."""
    assert conv._str_or_none(123) == "123"


# ─────────────────────────────────────────────────────────────────────────────
# _details_to_json
# ─────────────────────────────────────────────────────────────────────────────

def test_details_to_json_valid_payload() -> None:
    out = conv._details_to_json('{"insulin_volume": 240}')
    assert isinstance(out, Json)
    assert out.adapted == {"insulin_volume": 240}


def test_details_to_json_empty_object_string() -> None:
    out = conv._details_to_json("{}")
    assert isinstance(out, Json)
    assert out.adapted == {}


def test_details_to_json_none_defaults_to_empty() -> None:
    out = conv._details_to_json(None)
    assert isinstance(out, Json)
    assert out.adapted == {}


def test_details_to_json_empty_string_defaults_to_empty() -> None:
    out = conv._details_to_json("")
    assert isinstance(out, Json)
    assert out.adapted == {}


def test_details_to_json_nan_defaults_to_empty() -> None:
    out = conv._details_to_json(np.nan)
    assert isinstance(out, Json)
    assert out.adapted == {}


def test_details_to_json_malformed_raises() -> None:
    """Malformed JSON surfaces the json.JSONDecodeError instead of
    silently inserting garbage. The bootstrap relies on a try/except in
    its caller, not on the helper, to decide what to do."""
    with pytest.raises(json.JSONDecodeError):
        conv._details_to_json('{"bad')


# ─────────────────────────────────────────────────────────────────────────────
# Per-table converters
# ─────────────────────────────────────────────────────────────────────────────

def test_cgm_row_live_reading_has_no_sensor_timestamp() -> None:
    row = {
        "pump_serial": "PUMP1",
        "seqnum": 42,
        "timestamp": pd.Timestamp("2026-03-19 12:00:00", tz=PST),
        "bg_mgdl": 142,
        "backfilled": False,
        "sensor_timestamp": None,
    }
    out = conv._cgm_row(row)
    assert out == (
        "PUMP1", 42,
        datetime(2026, 3, 19, 12, 0, tzinfo=PST),
        142, False, None,
    )
    assert len(out) == len(conv.COLUMN_SPECS["cgm"])


def test_cgm_row_backfilled_reading_has_sensor_timestamp() -> None:
    row = {
        "pump_serial": "PUMP1",
        "seqnum": 43,
        "timestamp": pd.Timestamp("2026-03-19 12:05:00", tz=PST),
        "bg_mgdl": 150,
        "backfilled": True,
        "sensor_timestamp": pd.Timestamp("2026-03-19 11:55:00", tz=PST),
    }
    out = conv._cgm_row(row)
    assert out[4] is True
    assert isinstance(out[5], datetime)
    assert out[5].utcoffset() == timedelta(hours=-8)


def test_bolus_row_preserves_float_artifact() -> None:
    row = {
        "pump_serial": "PUMP1",
        "bolus_id": 7,
        "timestamp": pd.Timestamp("2026-03-19 12:00:00", tz=PST),
        "insulin_units": 25.000001907348633,
    }
    out = conv._bolus_row(row)
    assert len(out) == len(conv.COLUMN_SPECS["bolus"])
    assert out[0] == "PUMP1"
    assert out[1] == 7
    assert isinstance(out[2], datetime)
    assert out[3] == 25.000001907348633


def test_requests_row_user_meal_has_no_override_delta() -> None:
    row = {
        "pump_serial": "PUMP1",
        "bolus_id": 11,
        "timestamp": pd.Timestamp("2026-03-19 12:00:00", tz=PST),
        "carbs_g": 30,
        "bg_mgdl": 140,
        "iob": 0.0,
        "bolus_source": "user",
        "food_insulin": 3.0,
        "correction_insulin": 0.0,
        "total_requested": 3.0,
        "bolus_category": "user_meal",
        "override_delta": np.nan,
    }
    out = conv._requests_row(row)
    assert len(out) == len(conv.COLUMN_SPECS["requests"])
    assert out[6] == "user"
    assert out[10] == "user_meal"
    assert out[11] is None  # override_delta NaN → None


def test_requests_row_override_has_signed_delta() -> None:
    row = {
        "pump_serial": "PUMP1",
        "bolus_id": 12,
        "timestamp": pd.Timestamp("2026-03-19 13:00:00", tz=PST),
        "carbs_g": 0,
        "bg_mgdl": 180,
        "iob": 0.5,
        "bolus_source": "override",
        "food_insulin": 0.0,
        "correction_insulin": 1.5,
        "total_requested": 1.5,
        "bolus_category": "override_up",
        "override_delta": 0.5,
    }
    out = conv._requests_row(row)
    assert out[6] == "override"
    assert out[11] == 0.5
    assert isinstance(out[11], float)


def test_basal_row_basic() -> None:
    row = {
        "pump_serial": "PUMP1",
        "timestamp": pd.Timestamp("2026-03-19 12:00:00", tz=PST),
        "commanded_rate": 1.234,
        "rate_source": "algorithm",
    }
    out = conv._basal_row(row)
    assert len(out) == len(conv.COLUMN_SPECS["basal"])
    assert out == ("PUMP1", datetime(2026, 3, 19, 12, 0, tzinfo=PST), 1.234, "algorithm")


def test_suspension_row_closed_with_alarm() -> None:
    row = {
        "pump_serial": "PUMP1",
        "suspend_timestamp": pd.Timestamp("2026-03-19 12:00:00", tz=PST),
        "resume_timestamp": pd.Timestamp("2026-03-19 12:15:00", tz=PST),
        "duration_minutes": 15.0,
        "suspend_reason": "alarm",
        "insulin_at_suspend": 12,
        "pairing_suspect": False,
        "alarm_id": 23.0,  # builder writes float because NaN sentinel forces float dtype
        "alarm_name": "ResumePumpAlarm2",
    }
    out = conv._suspension_row(row)
    assert len(out) == len(conv.COLUMN_SPECS["suspension"])
    assert out[7] == 23
    assert isinstance(out[7], int)  # float-promoted alarm_id → real int
    assert out[2] == datetime(2026, 3, 19, 12, 15, tzinfo=PST)


def test_suspension_row_open_suspend_has_no_resume() -> None:
    row = {
        "pump_serial": "PUMP1",
        "suspend_timestamp": pd.Timestamp("2026-03-19 12:00:00", tz=PST),
        "resume_timestamp": pd.NaT,
        "duration_minutes": np.nan,
        "suspend_reason": "user",
        "insulin_at_suspend": 8,
        "pairing_suspect": False,
        "alarm_id": np.nan,
        "alarm_name": None,
    }
    out = conv._suspension_row(row)
    assert out[2] is None  # NaT → None
    assert out[3] is None  # NaN duration → None
    assert out[7] is None  # NaN alarm_id → None
    assert out[8] is None  # None alarm_name → None


def test_events_row_wraps_details_in_json_and_keeps_forced_flag() -> None:
    row = {
        "pump_serial": "PUMP1",
        "seqnum": 99,
        "timestamp": pd.Timestamp("2026-03-19 09:00:00", tz=PST),
        "event_type": "site_change",
        "event_subtype": "cartridge",
        "previous_mode": None,
        "details": json.dumps({"insulin_volume": 240}),
        "forced_by_alarm": True,
    }
    out = conv._events_row(row)
    assert len(out) == len(conv.COLUMN_SPECS["events"])
    assert isinstance(out[6], Json)
    assert out[6].adapted == {"insulin_volume": 240}
    assert out[7] is True


def test_events_row_forced_by_alarm_none_passes_through() -> None:
    """Non-site_change rows have forced_by_alarm = None on the object dtype
    column. The converter must keep None (not coerce to False)."""
    row = {
        "pump_serial": "PUMP1",
        "seqnum": 100,
        "timestamp": pd.Timestamp("2026-03-19 09:30:00", tz=PST),
        "event_type": "cgm_session",
        "event_subtype": "join",
        "previous_mode": None,
        "details": "{}",
        "forced_by_alarm": None,
    }
    out = conv._events_row(row)
    assert isinstance(out[6], Json)
    assert out[6].adapted == {}
    assert out[7] is None


def test_events_row_forced_by_alarm_false_passes_through() -> None:
    row = {
        "pump_serial": "PUMP1",
        "seqnum": 101,
        "timestamp": pd.Timestamp("2026-03-19 09:45:00", tz=PST),
        "event_type": "site_change",
        "event_subtype": "cannula",
        "previous_mode": None,
        "details": json.dumps({"prime_size": 30}),
        "forced_by_alarm": False,
    }
    out = conv._events_row(row)
    assert out[7] is False


def test_alarms_row_param1_uint32_sentinel_not_truncated() -> None:
    """alarms.param1 sometimes carries the uint32 sentinel 4_294_967_266 —
    bigint in Postgres. The converter must NOT silently truncate."""
    row = {
        "pump_serial": "PUMP1",
        "seqnum": 200,
        "timestamp": pd.Timestamp("2026-03-19 10:00:00", tz=PST),
        "category": "alarm",
        "action": "activated",
        "alarm_id": 17,
        "alarm_name": "OcclusionAlarm",
        "param1": 4_294_967_266,
        "param2": 3.5,
    }
    out = conv._alarms_row(row)
    assert len(out) == len(conv.COLUMN_SPECS["alarms"])
    assert out[7] == 4_294_967_266
    assert isinstance(out[7], int)
    assert out[8] == 3.5


def test_site_issues_row_resolved() -> None:
    row = {
        "pump_serial": "PUMP1",
        "first_occlusion_ts": pd.Timestamp("2026-03-19 08:00:00", tz=PST),
        "last_occlusion_ts": pd.Timestamp("2026-03-19 08:45:00", tz=PST),
        "occlusion_count": 3,
        "resolved_by_site_change_ts": pd.Timestamp("2026-03-19 09:00:00", tz=PST),
        "resolution_delay_minutes": 15.0,
    }
    out = conv._site_issues_row(row)
    assert len(out) == len(conv.COLUMN_SPECS["site_issues"])
    assert isinstance(out[1], datetime)
    assert isinstance(out[4], datetime)
    assert out[5] == 15.0


def test_site_issues_row_unresolved() -> None:
    row = {
        "pump_serial": "PUMP1",
        "first_occlusion_ts": pd.Timestamp("2026-03-19 08:00:00", tz=PST),
        "last_occlusion_ts": pd.Timestamp("2026-03-19 08:45:00", tz=PST),
        "occlusion_count": 3,
        "resolved_by_site_change_ts": pd.NaT,
        "resolution_delay_minutes": np.nan,
    }
    out = conv._site_issues_row(row)
    assert out[4] is None
    assert out[5] is None


def test_cgm_gaps_row_closed_gap() -> None:
    row = {
        "pump_serial": "PUMP1",
        "start_ts": pd.Timestamp("2026-03-19 14:00:00", tz=PST),
        "end_ts": pd.Timestamp("2026-03-19 14:30:00", tz=PST),
        "duration_minutes": 30.0,
        "ongoing": False,
    }
    out = conv._cgm_gaps_row(row)
    assert len(out) == len(conv.COLUMN_SPECS["cgm_gaps"])
    assert isinstance(out[2], datetime)
    assert out[3] == 30.0
    assert out[4] is False


def test_cgm_gaps_row_ongoing_has_no_end_ts() -> None:
    row = {
        "pump_serial": "PUMP1",
        "start_ts": pd.Timestamp("2026-03-19 14:00:00", tz=PST),
        "end_ts": pd.NaT,
        "duration_minutes": np.nan,
        "ongoing": True,
    }
    out = conv._cgm_gaps_row(row)
    assert out[2] is None  # ongoing gap has no end_ts
    assert out[3] is None
    assert out[4] is True


# ─────────────────────────────────────────────────────────────────────────────
# CONVERTERS dispatch invariants
# ─────────────────────────────────────────────────────────────────────────────

def test_converters_dispatch_keys() -> None:
    expected = {
        "cgm", "bolus", "requests", "basal", "suspension",
        "events", "alarms", "site_issues", "cgm_gaps",
    }
    assert set(conv.CONVERTERS.keys()) == expected
    assert len(conv.CONVERTERS) == 9


def test_converters_dispatch_excludes_new_tables() -> None:
    """The 3 tables introduced by migration 0001 but intentionally left
    empty by the bootstrap (alerts_sent, fetch_state, detection_config)
    must NOT have a converter."""
    for forbidden in ("alerts_sent", "fetch_state", "detection_config"):
        assert forbidden not in conv.CONVERTERS


@pytest.mark.parametrize("name", list(conv.COLUMN_SPECS.keys()))
def test_converter_tuple_length_matches_column_spec(name) -> None:
    """For each table, the converter's tuple length must equal the
    declared column count. Without this, INSERT placeholders and values
    desync silently."""
    sample_rows = {
        "cgm": {
            "pump_serial": "P", "seqnum": 1,
            "timestamp": pd.Timestamp("2026-03-19", tz=PST),
            "bg_mgdl": 100, "backfilled": False,
            "sensor_timestamp": None,
        },
        "bolus": {
            "pump_serial": "P", "bolus_id": 1,
            "timestamp": pd.Timestamp("2026-03-19", tz=PST),
            "insulin_units": 1.0,
        },
        "requests": {
            "pump_serial": "P", "bolus_id": 1,
            "timestamp": pd.Timestamp("2026-03-19", tz=PST),
            "carbs_g": 0, "bg_mgdl": 0, "iob": 0.0,
            "bolus_source": "user",
            "food_insulin": 0.0, "correction_insulin": 0.0,
            "total_requested": 0.0,
            "bolus_category": "user_meal",
            "override_delta": np.nan,
        },
        "basal": {
            "pump_serial": "P",
            "timestamp": pd.Timestamp("2026-03-19", tz=PST),
            "commanded_rate": 1.0, "rate_source": "profile",
        },
        "suspension": {
            "pump_serial": "P",
            "suspend_timestamp": pd.Timestamp("2026-03-19", tz=PST),
            "resume_timestamp": pd.NaT,
            "duration_minutes": np.nan,
            "suspend_reason": "user", "insulin_at_suspend": 0,
            "pairing_suspect": False,
            "alarm_id": np.nan, "alarm_name": None,
        },
        "events": {
            "pump_serial": "P", "seqnum": 1,
            "timestamp": pd.Timestamp("2026-03-19", tz=PST),
            "event_type": "site_change", "event_subtype": "cartridge",
            "previous_mode": None, "details": "{}",
            "forced_by_alarm": None,
        },
        "alarms": {
            "pump_serial": "P", "seqnum": 1,
            "timestamp": pd.Timestamp("2026-03-19", tz=PST),
            "category": "alarm", "action": "activated",
            "alarm_id": 1, "alarm_name": "X",
            "param1": None, "param2": np.nan,
        },
        "site_issues": {
            "pump_serial": "P",
            "first_occlusion_ts": pd.Timestamp("2026-03-19", tz=PST),
            "last_occlusion_ts": pd.Timestamp("2026-03-19", tz=PST),
            "occlusion_count": 1,
            "resolved_by_site_change_ts": pd.NaT,
            "resolution_delay_minutes": np.nan,
        },
        "cgm_gaps": {
            "pump_serial": "P",
            "start_ts": pd.Timestamp("2026-03-19", tz=PST),
            "end_ts": pd.NaT, "duration_minutes": np.nan,
            "ongoing": True,
        },
    }
    out = conv.CONVERTERS[name](sample_rows[name])
    assert isinstance(out, tuple)
    assert len(out) == len(conv.COLUMN_SPECS[name])


# ─────────────────────────────────────────────────────────────────────────────
# TABLE_SPECS PK match against the migration SQL
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def migration_sql() -> str:
    path = Path(__file__).resolve().parents[1] / "db" / "migrations" / "0001_init.sql"
    return path.read_text()


@pytest.mark.parametrize("name,pk_cols", boot.TABLE_SPECS)
def test_table_specs_pk_matches_migration(name, pk_cols, migration_sql) -> None:
    """For each (table, pk_cols) entry in TABLE_SPECS, the migration's
    CREATE TABLE block for that table must declare the same PK columns
    (order-sensitive). Catches drift between the bootstrap and the
    schema without needing a real DB."""
    # Match the table's CREATE TABLE block up through the closing `);`.
    block_re = re.compile(
        rf"CREATE TABLE IF NOT EXISTS {re.escape(name)}\s*\((?P<body>.*?)\)\s*;",
        re.DOTALL,
    )
    block = block_re.search(migration_sql)
    assert block is not None, f"no CREATE TABLE block for {name}"
    pk_match = re.search(r"PRIMARY KEY\s*\(([^)]+)\)", block.group("body"))
    assert pk_match is not None, f"no PRIMARY KEY clause for {name}"
    sql_cols = [c.strip() for c in pk_match.group(1).split(",")]
    assert sql_cols == pk_cols, (
        f"PK drift for {name}: bootstrap={pk_cols}, migration={sql_cols}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# _df_to_rows end-to-end smoke
# ─────────────────────────────────────────────────────────────────────────────

def test_df_to_rows_cgm_three_rows() -> None:
    base_ts = pd.Timestamp("2026-03-19 12:00:00", tz=PST)
    df = pd.DataFrame({
        "pump_serial": ["PUMP1"] * 3,
        "seqnum": [1, 2, 3],
        "timestamp": [base_ts, base_ts + pd.Timedelta(minutes=5), base_ts + pd.Timedelta(minutes=10)],
        "bg_mgdl": [120, 130, 140],
        "backfilled": [False, False, True],
        "sensor_timestamp": [None, None, base_ts + pd.Timedelta(minutes=2)],
    })
    rows = boot._df_to_rows("cgm", df)
    assert isinstance(rows, list)
    assert len(rows) == 3
    for row in rows:
        assert isinstance(row, tuple)
        assert len(row) == 6
    # Spot-check values to ensure ordering survived to_dict + tuple round-trip.
    assert rows[0][1] == 1  # seqnum
    assert rows[2][4] is True  # backfilled
    assert isinstance(rows[2][5], datetime)  # sensor_timestamp populated
    assert rows[0][5] is None  # live reading has no sensor_timestamp
