"""Pandas-row → Postgres-tuple converters for the 9 data tables.

This module is *Postgres-implementation-only* within ``core/storage/``.
The leading underscore signals the convention: outside callers go
through :class:`core.storage.supabase.SupabaseStorage`, which composes
these converters with the rest of the INSERT machinery. The bootstrap
script (``scripts/bootstrap_supabase.py``) imports them too, because it
shares the exact same pandas → tuple shape; that's the same row of
responsibility.

Three things live here:

* ``COLUMN_SPECS`` — per-table column order. Used both to build the
  ``INSERT INTO {name} ({cols}) VALUES %s`` SQL and to match the order
  of values produced by the converter tuples.
* The small null-safe scalar helpers (``_is_null``, ``_ts_or_none``,
  ``_int_or_none``, ``_float_or_none``, ``_bool_or_none``,
  ``_str_or_none``, ``_details_to_json``). Pandas leaks ``NaN`` / ``NaT``
  into object columns once a column has ever held a null, so the
  converters cannot trust dtype alone — every scalar goes through one of
  these helpers before becoming a Postgres value.
* The per-table ``_<name>_row`` converters and the ``CONVERTERS``
  dispatch dict. Pure functions; the only non-stdlib / non-pandas
  dependency is ``psycopg2.extras.Json`` (only reached by
  ``_details_to_json``).

Idempotency / row-shape invariants are exercised by
``tests/test_bootstrap_supabase.py`` (78 unit tests, no DB required).
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

import pandas as pd

try:  # psycopg2 is a runtime dep but importing it here keeps --dry-run paths working.
    from psycopg2.extras import Json  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised only when psycopg2 is genuinely missing
    Json = None  # type: ignore[assignment]


# Per-table column order used by both the INSERT statement and the row
# converters. Order MUST match the converter tuples below.
COLUMN_SPECS: dict[str, list[str]] = {
    "cgm": [
        "pump_serial", "seqnum", "timestamp",
        "bg_mgdl", "backfilled", "sensor_timestamp",
    ],
    "bolus": [
        "pump_serial", "bolus_id", "timestamp", "insulin_units",
    ],
    "requests": [
        "pump_serial", "bolus_id", "timestamp", "carbs_g", "bg_mgdl", "iob",
        "bolus_source", "food_insulin", "correction_insulin", "total_requested",
        "bolus_category", "override_delta",
    ],
    "basal": [
        "pump_serial", "timestamp", "commanded_rate", "rate_source",
    ],
    "suspension": [
        "pump_serial", "suspend_timestamp", "resume_timestamp", "duration_minutes",
        "suspend_reason", "insulin_at_suspend", "pairing_suspect",
        "alarm_id", "alarm_name",
    ],
    "events": [
        "pump_serial", "seqnum", "timestamp", "event_type", "event_subtype",
        "previous_mode", "details", "forced_by_alarm",
    ],
    "alarms": [
        "pump_serial", "seqnum", "timestamp", "category", "action",
        "alarm_id", "alarm_name", "param1", "param2",
    ],
    "site_issues": [
        "pump_serial", "first_occlusion_ts", "last_occlusion_ts",
        "occlusion_count", "resolved_by_site_change_ts",
        "resolution_delay_minutes",
    ],
    "cgm_gaps": [
        "pump_serial", "start_ts", "end_ts", "duration_minutes", "ongoing",
    ],
}


# ── small null-safe scalar helpers ───────────────────────────────────────────
#
# Pandas leaks NaN/NaT into object columns once a column ever held a null,
# so the converters cannot trust dtype alone. Each helper accepts a single
# scalar (None, NaN, NaT, pd.Timestamp, str, int, float, bool) and returns
# the Postgres-friendly equivalent (None or the cast value).

def _is_null(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _ts_or_none(value: Any) -> Any:
    """Return a tz-aware ``datetime`` (preserving offset) or ``None``."""
    if _is_null(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def _int_or_none(value: Any) -> int | None:
    if _is_null(value):
        return None
    return int(value)


def _float_or_none(value: Any) -> float | None:
    if _is_null(value):
        return None
    return float(value)


def _bool_or_none(value: Any) -> bool | None:
    if _is_null(value):
        return None
    return bool(value)


def _str_or_none(value: Any) -> str | None:
    if _is_null(value):
        return None
    return str(value)


def _details_to_json(value: Any) -> Any:
    """events.details: JSON-encoded text → ``Json(parsed_dict)``.

    Empty / NaN / null defaults to ``Json({})`` because the column is
    NOT NULL and the parquet sometimes carries an empty payload.
    """
    if Json is None:
        # Only reachable from a real insert path; --dry-run skips converters.
        raise RuntimeError("psycopg2 is required to convert events.details")
    if _is_null(value):
        return Json({})
    text = str(value).strip()
    if not text:
        return Json({})
    return Json(json.loads(text))


# ── per-table row converters ─────────────────────────────────────────────────
#
# Each converter accepts a row-shaped Mapping (dict or pd.Series) and
# returns the tuple in COLUMN_SPECS order. Pure functions; no DB or
# psycopg2 dependency except for events (which needs Json).

def _cgm_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _int_or_none(r["seqnum"]),
        _ts_or_none(r["timestamp"]),
        _int_or_none(r["bg_mgdl"]),
        _bool_or_none(r["backfilled"]),
        _ts_or_none(r["sensor_timestamp"]),
    )


def _bolus_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _int_or_none(r["bolus_id"]),
        _ts_or_none(r["timestamp"]),
        _float_or_none(r["insulin_units"]),
    )


def _requests_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _int_or_none(r["bolus_id"]),
        _ts_or_none(r["timestamp"]),
        _int_or_none(r["carbs_g"]),
        _int_or_none(r["bg_mgdl"]),
        _float_or_none(r["iob"]),
        _str_or_none(r["bolus_source"]),
        _float_or_none(r["food_insulin"]),
        _float_or_none(r["correction_insulin"]),
        _float_or_none(r["total_requested"]),
        _str_or_none(r["bolus_category"]),
        _float_or_none(r["override_delta"]),
    )


def _basal_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _ts_or_none(r["timestamp"]),
        _float_or_none(r["commanded_rate"]),
        _str_or_none(r["rate_source"]),
    )


def _suspension_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _ts_or_none(r["suspend_timestamp"]),
        _ts_or_none(r["resume_timestamp"]),
        _float_or_none(r["duration_minutes"]),
        _str_or_none(r["suspend_reason"]),
        _int_or_none(r["insulin_at_suspend"]),
        _bool_or_none(r["pairing_suspect"]),
        _int_or_none(r["alarm_id"]),
        _str_or_none(r["alarm_name"]),
    )


def _events_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _int_or_none(r["seqnum"]),
        _ts_or_none(r["timestamp"]),
        _str_or_none(r["event_type"]),
        _str_or_none(r["event_subtype"]),
        _str_or_none(r["previous_mode"]),
        _details_to_json(r["details"]),
        _bool_or_none(r["forced_by_alarm"]),
    )


def _alarms_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _int_or_none(r["seqnum"]),
        _ts_or_none(r["timestamp"]),
        _str_or_none(r["category"]),
        _str_or_none(r["action"]),
        _int_or_none(r["alarm_id"]),
        _str_or_none(r["alarm_name"]),
        _int_or_none(r["param1"]),
        _float_or_none(r["param2"]),
    )


def _site_issues_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _ts_or_none(r["first_occlusion_ts"]),
        _ts_or_none(r["last_occlusion_ts"]),
        _int_or_none(r["occlusion_count"]),
        _ts_or_none(r["resolved_by_site_change_ts"]),
        _float_or_none(r["resolution_delay_minutes"]),
    )


def _cgm_gaps_row(r: Mapping[str, Any]) -> tuple:
    return (
        _str_or_none(r["pump_serial"]),
        _ts_or_none(r["start_ts"]),
        _ts_or_none(r["end_ts"]),
        _float_or_none(r["duration_minutes"]),
        _bool_or_none(r["ongoing"]),
    )


CONVERTERS: dict[str, Callable[[Mapping[str, Any]], tuple]] = {
    "cgm": _cgm_row,
    "bolus": _bolus_row,
    "requests": _requests_row,
    "basal": _basal_row,
    "suspension": _suspension_row,
    "events": _events_row,
    "alarms": _alarms_row,
    "site_issues": _site_issues_row,
    "cgm_gaps": _cgm_gaps_row,
}
