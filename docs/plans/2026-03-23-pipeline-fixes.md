# Pipeline Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the 4 must-do pipeline issues identified in DATA_ISSUES.md — build alarms.parquet, fix CGM backfill, filter stale CGM readings, and enrich suspensions with alarm names.

**Architecture:** Each fix modifies `ingestion/builders.py` (add/modify builder functions), `ingestion/storage.py` (register new parquet), and `ingestion/__init__.py` (exports). All fixes are independent and testable in isolation. The alarm builder must land first since suspension enrichment depends on it.

**Tech Stack:** Python 3.12+, pandas, pytest, unittest.mock, tconnectsync event classes

---

### Task 1: Build `alarms.parquet` — new alarm/alert builder

**Files:**
- Modify: `ingestion/builders.py` — add `build_alarm_df()` function and register in `build_all`
- Modify: `ingestion/storage.py` — add `"alarms"` to `PARQUET_FILES` and `DEDUP_KEYS`
- Create: `tests/test_alarms.py` — unit tests for the new builder

**Step 1: Write the failing tests**

Create `tests/test_alarms.py`:

```python
"""Tests for the alarms builder in ingestion/builders.py."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from tconnectsync.eventparser.events import (
    LidAlarmActivated,
    LidAlarmCleared,
    LidAlertActivated,
    LidAlertCleared,
    LidCgmAlertActivatedDex,
    LidCgmAlertClearedDex,
)

from ingestion.builders import build_alarm_df, build_all

PST = timezone(timedelta(hours=-8))
SERIAL = "TEST123"


def _ts(dt: datetime):
    ts = MagicMock()
    ts.datetime = dt
    return ts


def _alarm_activated(dt, alarm_id_raw, param1=0, param2=0, seq=1):
    e = MagicMock(spec=LidAlarmActivated)
    e.eventTimestamp = _ts(dt)
    e.alarmidRaw = alarm_id_raw
    e.alarmid = MagicMock()
    e.alarmid.name = {
        2: "OcclusionAlarm", 3: "PumpResetAlarm", 12: "BatteryShutdownAlarm",
    }.get(alarm_id_raw, f"UnknownAlarm{alarm_id_raw}")
    e.param1 = param1
    e.param2 = param2
    e.seqNum = seq
    return e


def _alarm_cleared(dt, alarm_id_raw, seq=2):
    e = MagicMock(spec=LidAlarmCleared)
    e.eventTimestamp = _ts(dt)
    e.alarmidRaw = alarm_id_raw
    e.alarmid = MagicMock()
    e.alarmid.name = {
        2: "OcclusionAlarm", 12: "BatteryShutdownAlarm",
    }.get(alarm_id_raw, f"UnknownAlarm{alarm_id_raw}")
    e.seqNum = seq
    return e


def _alert_activated(dt, alert_id_raw, param1=0, param2=0, seq=3):
    e = MagicMock(spec=LidAlertActivated)
    e.eventTimestamp = _ts(dt)
    e.alertidRaw = alert_id_raw
    e.alertid = MagicMock()
    e.alertid.name = {
        11: "IncompleteBolusAlert", 50: "DefaultAlert50", 51: "DefaultAlert51",
    }.get(alert_id_raw, f"UnknownAlert{alert_id_raw}")
    e.param1 = param1
    e.param2 = param2
    e.seqNum = seq
    return e


def _alert_cleared(dt, alert_id_raw, seq=4):
    e = MagicMock(spec=LidAlertCleared)
    e.eventTimestamp = _ts(dt)
    e.alertidRaw = alert_id_raw
    e.alertid = MagicMock()
    e.alertid.name = {
        11: "IncompleteBolusAlert", 50: "DefaultAlert50", 51: "DefaultAlert51",
    }.get(alert_id_raw, f"UnknownAlert{alert_id_raw}")
    e.seqNum = seq
    return e


def _cgm_alert_activated(dt, dalert_id_raw, param1=0, param2=0, seq=5):
    e = MagicMock(spec=LidCgmAlertActivatedDex)
    e.eventTimestamp = _ts(dt)
    e.dalertidRaw = dalert_id_raw
    e.dalertid = None  # often unmapped
    e.param1 = param1
    e.param2 = param2
    e.seqNum = seq
    return e


def _cgm_alert_cleared(dt, dalert_id_raw, seq=6):
    e = MagicMock(spec=LidCgmAlertClearedDex)
    e.eventTimestamp = _ts(dt)
    e.dalertidRaw = dalert_id_raw
    e.dalertid = None
    e.seqNum = seq
    return e


class TestBuildAlarmDf:
    def test_alarm_activated(self):
        dt = datetime(2026, 3, 19, 22, 36, 35, tzinfo=PST)
        events = [_alarm_activated(dt, 2, param1=10, param2=20, seq=100)]
        df = build_alarm_df(events, SERIAL)
        assert len(df) == 1
        row = df.iloc[0]
        assert row["category"] == "alarm"
        assert row["action"] == "activated"
        assert row["alarm_id"] == 2
        assert row["alarm_name"] == "OcclusionAlarm"
        assert row["param1"] == 10
        assert row["param2"] == 20
        assert row["seqnum"] == 100

    def test_alarm_cleared(self):
        dt = datetime(2026, 3, 19, 22, 37, 0, tzinfo=PST)
        events = [_alarm_cleared(dt, 2, seq=101)]
        df = build_alarm_df(events, SERIAL)
        assert len(df) == 1
        row = df.iloc[0]
        assert row["category"] == "alarm"
        assert row["action"] == "cleared"
        assert row["alarm_id"] == 2
        # Cleared events have no param1/param2 — should be NaN
        assert row["param1"] != row["param1"]  # NaN check

    def test_alert_activated_with_name_override(self):
        """alertidRaw=50 should map to 'high_bg_alert', not 'DefaultAlert50'."""
        dt = datetime(2026, 3, 19, 15, 0, tzinfo=PST)
        events = [_alert_activated(dt, 50, param1=0, param2=862, seq=200)]
        df = build_alarm_df(events, SERIAL)
        assert len(df) == 1
        assert df.iloc[0]["alarm_name"] == "high_bg_alert"

    def test_alert_51_maps_to_low_bg(self):
        dt = datetime(2026, 3, 19, 15, 0, tzinfo=PST)
        events = [_alert_activated(dt, 51, param1=0, param2=873, seq=201)]
        df = build_alarm_df(events, SERIAL)
        assert df.iloc[0]["alarm_name"] == "low_bg_alert"

    def test_cgm_alert_dalert_mapping(self):
        """dalertidRaw values should map to named CGM alert types."""
        dt = datetime(2026, 3, 19, 12, 0, tzinfo=PST)
        events = [_cgm_alert_activated(dt, 2, param1=200, param2=180, seq=300)]
        df = build_alarm_df(events, SERIAL)
        assert len(df) == 1
        row = df.iloc[0]
        assert row["category"] == "cgm_alert"
        assert row["alarm_name"] == "cgm_high"
        assert row["param1"] == 200
        assert row["param2"] == 180

    def test_cgm_alert_all_dalert_ids(self):
        """All known dalertidRaw values should have names."""
        dt_base = datetime(2026, 3, 19, 12, 0, tzinfo=PST)
        expected_names = {
            1: "cgm_urgent_low",
            2: "cgm_high",
            3: "cgm_low",
            6: "cgm_rise_rate",
            8: "cgm_fall_rate",
            14: "cgm_out_of_range",
        }
        for dalert_id, expected_name in expected_names.items():
            events = [_cgm_alert_activated(dt_base, dalert_id, seq=dalert_id * 100)]
            df = build_alarm_df(events, SERIAL)
            assert df.iloc[0]["alarm_name"] == expected_name, f"dalertidRaw={dalert_id}"

    def test_cgm_alert_cleared(self):
        dt = datetime(2026, 3, 19, 12, 30, tzinfo=PST)
        events = [_cgm_alert_cleared(dt, 14, seq=301)]
        df = build_alarm_df(events, SERIAL)
        assert len(df) == 1
        assert df.iloc[0]["action"] == "cleared"
        assert df.iloc[0]["alarm_name"] == "cgm_out_of_range"

    def test_mixed_events_all_categories(self):
        dt = datetime(2026, 3, 19, 10, 0, tzinfo=PST)
        events = [
            _alarm_activated(dt, 2, seq=1),
            _alert_activated(dt + timedelta(minutes=5), 11, seq=2),
            _cgm_alert_activated(dt + timedelta(minutes=10), 14, seq=3),
        ]
        df = build_alarm_df(events, SERIAL)
        assert len(df) == 3
        assert set(df["category"]) == {"alarm", "alert", "cgm_alert"}

    def test_empty_list(self):
        df = build_alarm_df([], SERIAL)
        assert df.empty
        expected_cols = [
            "timestamp", "category", "action", "alarm_id",
            "alarm_name", "param1", "param2", "seqnum", "pump_serial",
        ]
        assert list(df.columns) == expected_cols

    def test_build_all_includes_alarms(self):
        """build_all should include 'alarms' key."""
        events = [_alarm_activated(datetime(2026, 3, 19, 10, 0, tzinfo=PST), 2, seq=1)]
        result = build_all(events, SERIAL)
        assert "alarms" in result
        assert len(result["alarms"]) == 1
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_alarms.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_alarm_df'`

**Step 3: Implement `build_alarm_df` in `ingestion/builders.py`**

Add after the events builder (before `build_all`):

```python
# ---------------------------------------------------------------------------
# 7. Alarms, alerts, and CGM alerts
# ---------------------------------------------------------------------------

_CGM_DALERT_MAP = {
    1: "cgm_urgent_low",
    2: "cgm_high",
    3: "cgm_low",
    6: "cgm_rise_rate",
    8: "cgm_fall_rate",
    14: "cgm_out_of_range",
}

_ALERT_NAME_OVERRIDES = {
    50: "high_bg_alert",
    51: "low_bg_alert",
}


def build_alarm_df(events: list, pump_serial: str) -> pd.DataFrame:
    """Build alarm/alert DataFrame from activated/cleared events."""
    rows = []
    for e in events:
        if isinstance(e, LidAlarmActivated):
            rows.append({
                "timestamp": e.eventTimestamp.datetime,
                "category": "alarm",
                "action": "activated",
                "alarm_id": int(e.alarmidRaw),
                "alarm_name": e.alarmid.name if e.alarmid else f"alarm_{e.alarmidRaw}",
                "param1": float(e.param1) if hasattr(e, "param1") else float("nan"),
                "param2": float(e.param2) if hasattr(e, "param2") else float("nan"),
                "seqnum": int(e.seqNum),
                "pump_serial": pump_serial,
            })
        elif isinstance(e, LidAlarmCleared):
            rows.append({
                "timestamp": e.eventTimestamp.datetime,
                "category": "alarm",
                "action": "cleared",
                "alarm_id": int(e.alarmidRaw),
                "alarm_name": e.alarmid.name if e.alarmid else f"alarm_{e.alarmidRaw}",
                "param1": float("nan"),
                "param2": float("nan"),
                "seqnum": int(e.seqNum),
                "pump_serial": pump_serial,
            })
        elif isinstance(e, LidAlertActivated):
            alert_id = int(e.alertidRaw)
            name = _ALERT_NAME_OVERRIDES.get(
                alert_id,
                e.alertid.name if e.alertid else f"alert_{alert_id}",
            )
            rows.append({
                "timestamp": e.eventTimestamp.datetime,
                "category": "alert",
                "action": "activated",
                "alarm_id": alert_id,
                "alarm_name": name,
                "param1": float(e.param1) if hasattr(e, "param1") else float("nan"),
                "param2": float(e.param2) if hasattr(e, "param2") else float("nan"),
                "seqnum": int(e.seqNum),
                "pump_serial": pump_serial,
            })
        elif isinstance(e, LidAlertCleared):
            alert_id = int(e.alertidRaw)
            name = _ALERT_NAME_OVERRIDES.get(
                alert_id,
                e.alertid.name if e.alertid else f"alert_{alert_id}",
            )
            rows.append({
                "timestamp": e.eventTimestamp.datetime,
                "category": "alert",
                "action": "cleared",
                "alarm_id": alert_id,
                "alarm_name": name,
                "param1": float("nan"),
                "param2": float("nan"),
                "seqnum": int(e.seqNum),
                "pump_serial": pump_serial,
            })
        elif isinstance(e, LidCgmAlertActivatedDex):
            dalert_id = int(e.dalertidRaw)
            name = _CGM_DALERT_MAP.get(dalert_id, f"cgm_alert_{dalert_id}")
            rows.append({
                "timestamp": e.eventTimestamp.datetime,
                "category": "cgm_alert",
                "action": "activated",
                "alarm_id": dalert_id,
                "alarm_name": name,
                "param1": float(e.param1) if hasattr(e, "param1") else float("nan"),
                "param2": float(e.param2) if hasattr(e, "param2") else float("nan"),
                "seqnum": int(e.seqNum),
                "pump_serial": pump_serial,
            })
        elif isinstance(e, (LidCgmAlertClearedDex, LidCgmAlertAckDex)):
            dalert_id = int(e.dalertidRaw)
            name = _CGM_DALERT_MAP.get(dalert_id, f"cgm_alert_{dalert_id}")
            action = "cleared" if isinstance(e, LidCgmAlertClearedDex) else "ack"
            rows.append({
                "timestamp": e.eventTimestamp.datetime,
                "category": "cgm_alert",
                "action": action,
                "alarm_id": dalert_id,
                "alarm_name": name,
                "param1": float("nan"),
                "param2": float("nan"),
                "seqnum": int(e.seqNum),
                "pump_serial": pump_serial,
            })

    columns = [
        "timestamp", "category", "action", "alarm_id",
        "alarm_name", "param1", "param2", "seqnum", "pump_serial",
    ]
    df = pd.DataFrame(rows, columns=columns)
    if not df.empty:
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df
```

Then add `"alarms": build_alarm_df(events, pump_serial),` to `build_all`'s result dict.

**Step 4: Register in storage.py**

Add to `PARQUET_FILES`: `"alarms": "alarms.parquet"`
Add to `DEDUP_KEYS`: `"alarms": ["pump_serial", "seqnum"]`

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_alarms.py -v`
Expected: all PASS

**Step 6: Run full test suite**

Run: `uv run pytest -v`
Expected: all pass (including existing `test_build_all_includes_alarms` — update the expected keys set in `test_builders.py::TestBuildAll::test_returns_dict_with_all_keys` to include `"alarms"`)

**Step 7: Commit**

```bash
git add ingestion/builders.py ingestion/storage.py tests/test_alarms.py tests/test_builders.py
git commit -m "feat: add alarms.parquet builder for alarm/alert/cgm_alert events"
```

---

### Task 2: Fix CGM backfill — preserve `cgmDataTypeRaw=2` readings

**Files:**
- Modify: `ingestion/builders.py:71-86` — update `build_cgm_df`
- Modify: `ingestion/storage.py` — update CGM dedup key
- Modify: `tests/test_builders.py` — add backfill tests to `TestBuildCgmDf`

**Step 1: Write the failing tests**

Add to `tests/test_builders.py::TestBuildCgmDf`:

```python
def _cgm_backfill(dt_event, dt_sensor, bg, seq=0, data_type_raw=2):
    """CGM event with backfill fields (cgmDataTypeRaw=2)."""
    e = MagicMock(spec=LidCgmDataG7)
    e.eventTimestamp = _ts(dt_event)
    e.currentglucosedisplayvalue = bg
    e.seqNum = seq
    e.cgmDataTypeRaw = data_type_raw
    e.egvTimestamp = dt_sensor  # raw int/string — builder decodes
    return e
```

Tests:

```python
def test_backfill_preserved_not_deduped(self):
    """Backfilled readings (cgmDataTypeRaw=2) with same eventTimestamp must not dedup to 1."""
    reconnect_time = datetime(2026, 3, 18, 9, 57, 18, tzinfo=PST)
    events = [
        _cgm_backfill(reconnect_time, 1710750000, 200, seq=1, data_type_raw=2),
        _cgm_backfill(reconnect_time, 1710750300, 220, seq=2, data_type_raw=2),
        _cgm_backfill(reconnect_time, 1710750600, 250, seq=3, data_type_raw=2),
    ]
    df = build_cgm_df(events, SERIAL)
    assert len(df) == 3

def test_backfill_column_present(self):
    """Backfilled readings should have backfilled=True."""
    dt = datetime(2026, 3, 18, 9, 57, tzinfo=PST)
    events = [_cgm_backfill(dt, 1710750000, 200, seq=1, data_type_raw=2)]
    df = build_cgm_df(events, SERIAL)
    assert df.iloc[0]["backfilled"] == True

def test_live_reading_not_backfilled(self):
    """Live readings (cgmDataTypeRaw=1) should have backfilled=False."""
    dt = datetime(2026, 3, 18, 10, 0, tzinfo=PST)
    e = _cgm(dt, 150, seq=1)
    e.cgmDataTypeRaw = 1
    events = [e]
    df = build_cgm_df(events, SERIAL)
    assert df.iloc[0]["backfilled"] == False

def test_sensor_timestamp_stored_for_backfill(self):
    """Backfilled readings should store egvTimestamp as sensor_timestamp."""
    dt = datetime(2026, 3, 18, 9, 57, tzinfo=PST)
    sensor_ts = 1710750000
    events = [_cgm_backfill(dt, sensor_ts, 200, seq=1, data_type_raw=2)]
    df = build_cgm_df(events, SERIAL)
    assert "sensor_timestamp" in df.columns
    assert df.iloc[0]["sensor_timestamp"] is not None
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_builders.py::TestBuildCgmDf -v`
Expected: FAIL

**Step 3: Update `build_cgm_df` in `ingestion/builders.py`**

Replace the existing function:

```python
def build_cgm_df(events: list, pump_serial: str) -> pd.DataFrame:
    """Build CGM readings DataFrame from G7, Gxb, and FSL2 events.

    Preserves backfilled readings (cgmDataTypeRaw=2) which arrive with the
    same eventTimestamp but different egvTimestamp/seqNum.
    """
    rows = []
    for e in events:
        if isinstance(e, (LidCgmDataG7, LidCgmDataGxb, LidCgmDataFsl2)):
            data_type_raw = getattr(e, "cgmDataTypeRaw", 1)
            is_backfill = data_type_raw == 2

            # For backfilled readings, egvTimestamp is the actual sensor reading time
            sensor_ts = getattr(e, "egvTimestamp", None)

            rows.append({
                "timestamp": e.eventTimestamp.datetime,
                "bg_mgdl": int(e.currentglucosedisplayvalue),
                "backfilled": is_backfill,
                "sensor_timestamp": sensor_ts,
                "pump_serial": pump_serial,
                "seqnum": int(e.seqNum),
            })

    df = pd.DataFrame(rows, columns=[
        "timestamp", "bg_mgdl", "backfilled", "sensor_timestamp", "pump_serial", "seqnum",
    ])
    if not df.empty:
        # Dedup on (seqnum, pump_serial) to preserve backfilled readings
        # that share the same eventTimestamp
        df = df.drop_duplicates(subset=["seqnum", "pump_serial"], keep="first")
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df
```

**Step 4: Update storage.py dedup key**

Change CGM dedup key: `"cgm": ["seqnum", "pump_serial"]`

**Step 5: Update existing CGM mock helper**

Update `_cgm()` in test_builders.py to set `cgmDataTypeRaw=1` and `egvTimestamp=None` on the mock so existing tests pass with the new columns.

**Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_builders.py::TestBuildCgmDf -v`
Expected: all PASS

Run: `uv run pytest -v`
Expected: all pass

**Step 7: Commit**

```bash
git add ingestion/builders.py ingestion/storage.py tests/test_builders.py
git commit -m "fix: preserve backfilled CGM readings (cgmDataTypeRaw=2), recovering 30% of data"
```

---

### Task 3: Filter stale CGM readings — drop readings <60s apart

**Files:**
- Modify: `ingestion/builders.py:71-86` — add stale-reading filter to `build_cgm_df`
- Modify: `tests/test_builders.py` — add stale-reading tests

**Step 1: Write the failing tests**

Add to `tests/test_builders.py::TestBuildCgmDf`:

```python
def test_stale_reading_dropped(self):
    """Two readings <60s apart: keep first, drop second."""
    dt1 = datetime(2026, 3, 19, 12, 3, 15, tzinfo=PST)
    dt2 = datetime(2026, 3, 19, 12, 3, 16, tzinfo=PST)  # 1 second later
    e1 = _cgm(dt1, 344, seq=1)
    e2 = _cgm(dt2, 136, seq=2)  # stale cached value
    df = build_cgm_df([e1, e2], SERIAL)
    assert len(df) == 1
    assert df.iloc[0]["bg_mgdl"] == 344

def test_normal_5min_spacing_preserved(self):
    """Readings 5 minutes apart should both be kept."""
    dt1 = datetime(2026, 3, 19, 12, 0, 0, tzinfo=PST)
    dt2 = datetime(2026, 3, 19, 12, 5, 0, tzinfo=PST)
    e1 = _cgm(dt1, 150, seq=1)
    e2 = _cgm(dt2, 155, seq=2)
    df = build_cgm_df([e1, e2], SERIAL)
    assert len(df) == 2

def test_exactly_60s_kept(self):
    """Readings exactly 60s apart should both be kept (filter is <60s)."""
    dt1 = datetime(2026, 3, 19, 12, 0, 0, tzinfo=PST)
    dt2 = datetime(2026, 3, 19, 12, 1, 0, tzinfo=PST)
    e1 = _cgm(dt1, 150, seq=1)
    e2 = _cgm(dt2, 155, seq=2)
    df = build_cgm_df([e1, e2], SERIAL)
    assert len(df) == 2
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_builders.py::TestBuildCgmDf::test_stale_reading_dropped -v`
Expected: FAIL (returns 2 rows instead of 1)

**Step 3: Add stale-reading filter to `build_cgm_df`**

After dedup and sort, add:

```python
    if not df.empty and len(df) > 1:
        # Drop stale readings: if two readings are <60s apart, keep the first
        time_diff = df["timestamp"].diff()
        mask = time_diff.isna() | (time_diff >= pd.Timedelta(seconds=60))
        df = df[mask].reset_index(drop=True)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_builders.py::TestBuildCgmDf -v`
Expected: all PASS

**Step 5: Commit**

```bash
git add ingestion/builders.py tests/test_builders.py
git commit -m "fix: filter stale CGM readings (<60s apart) on sensor reconnection"
```

---

### Task 4: Enrich suspensions with alarm name — timestamp match

**Files:**
- Modify: `ingestion/builders.py:228-300` — update `build_suspension_df` to accept alarm events and cross-reference
- Modify: `ingestion/builders.py` `build_all` — pass alarm events to suspension builder
- Modify: `tests/test_builders.py` and `tests/test_suspension.py` — add enrichment tests

**Step 1: Write the failing tests**

Add to `tests/test_builders.py::TestBuildSuspensionDf`:

```python
def test_alarm_enrichment_by_timestamp(self):
    """Suspension with reason=alarm should be enriched with specific alarm name."""
    t0 = datetime(2026, 3, 19, 22, 36, 35, tzinfo=PST)
    t1 = datetime(2026, 3, 19, 22, 37, 0, tzinfo=PST)
    alarm = _alarm_activated(t0, 2)  # OcclusionAlarm at same timestamp
    events = [_suspend(t0, reason_raw=1), _resume(t1), alarm]
    df = build_suspension_df(events, SERIAL)
    assert len(df) == 1
    assert df.iloc[0]["alarm_id"] == 2
    assert df.iloc[0]["alarm_name"] == "OcclusionAlarm"

def test_no_alarm_at_timestamp_yields_nan(self):
    """Suspension with reason=alarm but no matching alarm event."""
    t0 = datetime(2026, 3, 19, 22, 36, 35, tzinfo=PST)
    t1 = datetime(2026, 3, 19, 22, 37, 0, tzinfo=PST)
    events = [_suspend(t0, reason_raw=1), _resume(t1)]
    df = build_suspension_df(events, SERIAL)
    assert len(df) == 1
    assert df.iloc[0]["alarm_id"] != df.iloc[0]["alarm_id"]  # NaN

def test_user_suspend_no_alarm_enrichment(self):
    """Suspension with reason=user should not be enriched even if alarm at same time."""
    t0 = datetime(2026, 3, 19, 10, 0, tzinfo=PST)
    t1 = datetime(2026, 3, 19, 10, 30, tzinfo=PST)
    alarm = _alarm_activated(t0, 2)
    events = [_suspend(t0, reason_raw=0), _resume(t1), alarm]
    df = build_suspension_df(events, SERIAL)
    assert df.iloc[0]["alarm_id"] != df.iloc[0]["alarm_id"]  # NaN — user suspend
```

The mock helper `_alarm_activated` needs to be in test_builders.py (import from test_alarms.py or duplicate the helper).

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_builders.py::TestBuildSuspensionDf::test_alarm_enrichment_by_timestamp -v`
Expected: FAIL — `alarm_id` not in columns

**Step 3: Update `build_suspension_df`**

Modify `build_suspension_df` to also scan for `LidAlarmActivated` events and build a timestamp→alarm lookup. When creating an episode with `suspend_reason == "alarm"`, check the lookup:

```python
def build_suspension_df(events: list, pump_serial: str) -> pd.DataFrame:
    """Build suspension episode DataFrame by pairing suspend/resume events.

    Also enriches alarm-caused suspensions with the specific alarm name by
    timestamp-matching against LidAlarmActivated events.
    """
    # Build alarm lookup: timestamp → (alarmidRaw, alarm_name)
    alarm_lookup: dict[datetime, tuple[int, str]] = {}
    for e in events:
        if isinstance(e, LidAlarmActivated):
            ts = e.eventTimestamp.datetime
            name = e.alarmid.name if e.alarmid else f"alarm_{e.alarmidRaw}"
            alarm_lookup[ts] = (int(e.alarmidRaw), name)

    # ... existing suspend/resume pairing logic unchanged ...

    # When building each episode dict, add alarm_id and alarm_name:
    # If suspend_reason == "alarm" and timestamp matches an alarm:
    #   alarm_id = matched alarm id, alarm_name = matched name
    # Else:
    #   alarm_id = float("nan"), alarm_name = None
```

Add `"alarm_id"` and `"alarm_name"` to the columns list.

**Step 4: Update existing suspension tests**

All existing tests that check column counts or specific column access need updating to account for the two new columns. The existing tests pass `alarm_id=NaN` and `alarm_name=None` since none of them have `reason_raw=1` with matching alarm events.

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_builders.py::TestBuildSuspensionDf -v`
Run: `uv run pytest tests/test_suspension.py -v`
Expected: all PASS

**Step 6: Run full test suite**

Run: `uv run pytest -v`
Expected: all pass

**Step 7: Commit**

```bash
git add ingestion/builders.py tests/test_builders.py tests/test_suspension.py
git commit -m "feat: enrich alarm-caused suspensions with specific alarm name via timestamp match"
```

---

### Task 5: Housekeeping — add USB events to `_HANDLED_TYPES`

**Files:**
- Modify: `ingestion/builders.py` — add `LidUsbConnected`, `LidUsbDisconnected` to imports and `_HANDLED_TYPES`

**Step 1: Check if the event classes exist in tconnectsync**

Run: `uv run python -c "from tconnectsync.eventparser.events import LidUsbConnected, LidUsbDisconnected; print('ok')"`

**Step 2: Add to imports and `_HANDLED_TYPES`**

If the import works, add them. If not, they may be `RawEvent` subclasses — check and handle accordingly.

**Step 3: Run full test suite**

Run: `uv run pytest -v`
Expected: all pass

**Step 4: Commit**

```bash
git add ingestion/builders.py
git commit -m "chore: suppress warnings for USB connect/disconnect events"
```

---

### Task 6: Verification — re-fetch and validate with viz

**Step 1: Wipe and re-fetch a verified day**

Run: `uv run python main.py fetch-day --date 2026-03-19`

**Step 2: Run sanity check**

Run: `uv run python main.py check --date 2026-03-19`

Verify:
- CGM count is higher than before (backfilled readings recovered)
- No stale 1-second-apart readings in CGM trace
- Alarms section now shows alarm/alert data
- Suspensions show specific alarm names instead of generic "alarm"

**Step 3: Run viz**

Run: `uv run python main.py viz --date 2026-03-19`

Visual check: no false data points from stale readings, backfilled gap data visible.
