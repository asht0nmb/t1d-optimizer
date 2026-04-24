"""Tests for ingestion/enrich.py."""

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

PST = timezone(timedelta(hours=-8))
SERIAL = "TEST123"


def ts(hhmm: str) -> datetime:
    """Build a PST datetime for a given HH:MM on the shared test date."""
    hour, minute = (int(p) for p in hhmm.split(":"))
    return datetime(2026, 3, 19, hour, minute, tzinfo=PST)


# ---------------------------------------------------------------------------
# Helpers — fixtures shared across enrichment tests
# ---------------------------------------------------------------------------

_REQUEST_COLUMNS = [
    "timestamp", "bolus_id", "carbs_g", "bg_mgdl", "iob",
    "bolus_source", "food_insulin", "correction_insulin",
    "total_requested", "pump_serial",
]


def _requests_row(
    *,
    source: str,
    carbs: int = 0,
    food: float = 0.0,
    correction: float = 0.0,
    total: float = 0.0,
    bg: int = 120,
    iob: float = 0.0,
    bolus_id: int = 1,
    ts: datetime | None = None,
) -> pd.DataFrame:
    """Build a single-row requests DataFrame with standard columns."""
    ts = ts or datetime(2026, 3, 19, 12, 0, tzinfo=PST)
    return pd.DataFrame(
        [{
            "timestamp": ts,
            "bolus_id": bolus_id,
            "carbs_g": carbs,
            "bg_mgdl": bg,
            "iob": iob,
            "bolus_source": source,
            "food_insulin": food,
            "correction_insulin": correction,
            "total_requested": total,
            "pump_serial": SERIAL,
        }],
        columns=_REQUEST_COLUMNS,
    )


def _empty_requests() -> pd.DataFrame:
    return pd.DataFrame(columns=_REQUEST_COLUMNS)


# ---------------------------------------------------------------------------
# Task 1.1 — enrich_requests_df
# ---------------------------------------------------------------------------

class TestEnrichRequestsDf:
    def test_auto_correction_no_food(self):
        from ingestion.enrich import enrich_requests_df

        df = _requests_row(source="auto", carbs=0, food=0.0, correction=1.2, total=1.2)
        out = enrich_requests_df(df)
        assert out.iloc[0]["bolus_category"] == "auto_correction"
        assert pd.isna(out.iloc[0]["override_delta"])

    def test_auto_correction_with_zero_delivered(self):
        from ingestion.enrich import enrich_requests_df

        df = _requests_row(source="auto", carbs=0, food=0.0, correction=0.0, total=0.0)
        out = enrich_requests_df(df)
        assert out.iloc[0]["bolus_category"] == "auto_correction"

    def test_user_meal_only(self):
        from ingestion.enrich import enrich_requests_df

        df = _requests_row(source="user", carbs=40, food=9.5, correction=0.0, total=9.5)
        assert enrich_requests_df(df).iloc[0]["bolus_category"] == "user_meal"

    def test_user_meal_and_correction(self):
        from ingestion.enrich import enrich_requests_df

        df = _requests_row(source="user", carbs=40, food=9.5, correction=1.1, total=10.6)
        assert enrich_requests_df(df).iloc[0]["bolus_category"] == "user_meal_and_correction"

    def test_user_correction_only(self):
        from ingestion.enrich import enrich_requests_df

        df = _requests_row(source="user", carbs=0, food=0.0, correction=1.4, total=1.4)
        assert enrich_requests_df(df).iloc[0]["bolus_category"] == "user_correction_only"

    def test_user_zero_everything_is_unknown(self):
        from ingestion.enrich import enrich_requests_df

        df = _requests_row(source="user", carbs=0, food=0.0, correction=0.0, total=0.0)
        assert enrich_requests_df(df).iloc[0]["bolus_category"] == "unknown"

    def test_override_up(self):
        from ingestion.enrich import enrich_requests_df

        df = _requests_row(source="override", carbs=0, food=0.0, correction=0.2, total=2.5)
        out = enrich_requests_df(df)
        assert out.iloc[0]["bolus_category"] == "override_up"
        assert out.iloc[0]["override_delta"] == pytest.approx(2.3)

    def test_override_down(self):
        from ingestion.enrich import enrich_requests_df

        df = _requests_row(source="override", carbs=30, food=7.0, correction=0.0, total=4.0)
        out = enrich_requests_df(df)
        assert out.iloc[0]["bolus_category"] == "override_down"
        assert out.iloc[0]["override_delta"] == pytest.approx(-3.0)

    def test_override_within_epsilon_falls_back_to_user_meal(self):
        from ingestion.enrich import enrich_requests_df

        df = _requests_row(source="override", carbs=30, food=7.0, correction=0.0, total=7.005)
        out = enrich_requests_df(df)
        assert out.iloc[0]["bolus_category"] == "user_meal"
        # override_delta still populated for override rows (even within epsilon)
        assert out.iloc[0]["override_delta"] == pytest.approx(0.005)

    def test_override_within_epsilon_falls_back_to_correction_only(self):
        from ingestion.enrich import enrich_requests_df

        df = _requests_row(source="override", carbs=0, food=0.0, correction=1.5, total=1.5)
        out = enrich_requests_df(df)
        assert out.iloc[0]["bolus_category"] == "user_correction_only"

    def test_non_override_has_nan_override_delta(self):
        from ingestion.enrich import enrich_requests_df

        df = _requests_row(source="user", carbs=0, food=0.0, correction=1.0, total=1.0)
        assert pd.isna(enrich_requests_df(df).iloc[0]["override_delta"])

    def test_auto_override_delta_is_nan(self):
        from ingestion.enrich import enrich_requests_df

        df = _requests_row(source="auto", carbs=0, food=0.0, correction=1.2, total=1.2)
        assert pd.isna(enrich_requests_df(df).iloc[0]["override_delta"])

    def test_unknown_source_passes_through(self):
        from ingestion.enrich import enrich_requests_df

        df = _requests_row(source="unknown", carbs=0, food=0.0, correction=0.0, total=0.0)
        assert enrich_requests_df(df).iloc[0]["bolus_category"] == "unknown"

    def test_empty_df_preserves_columns(self):
        from ingestion.enrich import enrich_requests_df

        df = _empty_requests()
        out = enrich_requests_df(df)
        assert out.empty
        assert "bolus_category" in out.columns
        assert "override_delta" in out.columns

    def test_multiple_rows_each_categorized(self):
        from ingestion.enrich import enrich_requests_df

        rows = pd.concat([
            _requests_row(source="auto", correction=1.0, total=1.0, bolus_id=1),
            _requests_row(source="user", carbs=40, food=9.0, total=9.0, bolus_id=2),
            _requests_row(source="override", food=5.0, total=7.5, bolus_id=3),
        ], ignore_index=True)
        out = enrich_requests_df(rows)
        assert list(out["bolus_category"]) == [
            "auto_correction", "user_meal", "override_up",
        ]

    def test_nan_inputs_do_not_crash(self):
        from ingestion.enrich import enrich_requests_df

        df = _requests_row(source="user", carbs=30, food=float("nan"), correction=float("nan"), total=float("nan"))
        # Treated as 0; all-zero user row falls through to "unknown".
        out = enrich_requests_df(df)
        assert out.iloc[0]["bolus_category"] == "unknown"


# ---------------------------------------------------------------------------
# enrich_all — module-level orchestrator
# ---------------------------------------------------------------------------

class TestEnrichAll:
    def test_enrich_all_applies_requests_enrichment(self):
        from ingestion.enrich import enrich_all

        frames = {
            "requests": _requests_row(source="user", carbs=30, food=7.0, total=7.0),
        }
        out = enrich_all(frames, config={})
        assert "bolus_category" in out["requests"].columns
        assert out["requests"].iloc[0]["bolus_category"] == "user_meal"

    def test_enrich_all_does_not_mutate_input_dict(self):
        from ingestion.enrich import enrich_all

        frames = {"requests": _requests_row(source="user", carbs=30, food=7.0, total=7.0)}
        original_keys = set(frames.keys())
        original_cols = set(frames["requests"].columns)
        enrich_all(frames, config={})
        assert set(frames.keys()) == original_keys
        # Input requests frame should not have the new columns
        assert "bolus_category" not in frames["requests"].columns
        assert set(frames["requests"].columns) == original_cols

    def test_enrich_all_tolerates_missing_requests_frame(self):
        from ingestion.enrich import enrich_all

        out = enrich_all({}, config={})
        assert out == {}


# ---------------------------------------------------------------------------
# Task 1.2 — enrich_events_df
# ---------------------------------------------------------------------------

_EVENT_COLUMNS = [
    "timestamp", "event_type", "event_subtype", "previous_mode",
    "details", "seqnum", "pump_serial",
]

_ALARM_COLUMNS = [
    "timestamp", "category", "action", "alarm_id", "alarm_name",
    "param1", "param2", "seqnum", "pump_serial",
]


def _events_frame(rows: list[dict]) -> pd.DataFrame:
    """Build an events_df with sensible defaults for missing columns."""
    if not rows:
        return pd.DataFrame(columns=_EVENT_COLUMNS)
    filled = []
    for i, r in enumerate(rows):
        filled.append({
            "timestamp": r.get("timestamp"),
            "event_type": r.get("event_type"),
            "event_subtype": r.get("event_subtype"),
            "previous_mode": r.get("previous_mode"),
            "details": r.get("details", json.dumps({})),
            "seqnum": r.get("seqnum", i + 1),
            "pump_serial": r.get("pump_serial", SERIAL),
        })
    return pd.DataFrame(filled, columns=_EVENT_COLUMNS)


def _alarms_frame(rows: list[dict]) -> pd.DataFrame:
    """Build an alarms_df with sensible defaults for missing columns."""
    if not rows:
        return pd.DataFrame(columns=_ALARM_COLUMNS)
    filled = []
    for i, r in enumerate(rows):
        filled.append({
            "timestamp": r.get("timestamp"),
            "category": r.get("category", "alarm"),
            "action": r.get("action", "activated"),
            "alarm_id": r.get("alarm_id", 0),
            "alarm_name": r.get("alarm_name"),
            "param1": r.get("param1", float("nan")),
            "param2": r.get("param2", float("nan")),
            "seqnum": r.get("seqnum", i + 1),
            "pump_serial": r.get("pump_serial", SERIAL),
        })
    return pd.DataFrame(filled, columns=_ALARM_COLUMNS)


class TestEnrichEventsDf:
    def test_site_change_within_window_tagged_forced(self):
        from ingestion.enrich import enrich_events_df

        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {"timestamp": ts("09:15"), "event_type": "site_change", "event_subtype": "tubing"},
        ])
        out = enrich_events_df(
            events, alarms, {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220}
        )
        assert out.iloc[0]["forced_by_alarm"] == True  # noqa: E712

    def test_site_change_outside_window_not_forced(self):
        from ingestion.enrich import enrich_events_df

        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {"timestamp": ts("11:30"), "event_type": "site_change", "event_subtype": "tubing"},
        ])
        out = enrich_events_df(
            events, alarms, {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220}
        )
        assert out.iloc[0]["forced_by_alarm"] == False  # noqa: E712

    def test_site_change_before_alarm_not_forced(self):
        from ingestion.enrich import enrich_events_df

        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {"timestamp": ts("07:00"), "event_type": "site_change", "event_subtype": "tubing"},
        ])
        out = enrich_events_df(
            events, alarms, {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220}
        )
        assert out.iloc[0]["forced_by_alarm"] == False  # noqa: E712

    def test_non_site_change_has_nan_forced(self):
        from ingestion.enrich import enrich_events_df

        events = _events_frame([
            {"timestamp": ts("10:00"), "event_type": "mode_change", "event_subtype": "exercising"},
        ])
        out = enrich_events_df(
            events, _alarms_frame([]),
            {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220},
        )
        val = out.iloc[0]["forced_by_alarm"]
        assert pd.isna(val) or val is None

    def test_no_battery_shutdown_alarm_all_false(self):
        from ingestion.enrich import enrich_events_df

        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {"timestamp": ts("09:00"), "event_type": "site_change", "event_subtype": "tubing"},
        ])
        out = enrich_events_df(
            events, alarms, {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220}
        )
        assert out.iloc[0]["forced_by_alarm"] == False  # noqa: E712

    def test_no_alarms_frame_at_all(self):
        from ingestion.enrich import enrich_events_df

        events = _events_frame([
            {"timestamp": ts("09:00"), "event_type": "site_change", "event_subtype": "tubing"},
        ])
        out = enrich_events_df(
            events, None,
            {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220},
        )
        assert out.iloc[0]["forced_by_alarm"] == False  # noqa: E712

    def test_multiple_site_changes_some_forced(self):
        from ingestion.enrich import enrich_events_df

        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {"timestamp": ts("09:00"), "event_type": "site_change", "event_subtype": "tubing"},
            {"timestamp": ts("09:05"), "event_type": "site_change", "event_subtype": "cannula"},
            {"timestamp": ts("15:00"), "event_type": "site_change", "event_subtype": "cannula"},
        ])
        out = enrich_events_df(
            events, alarms, {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220}
        )
        assert list(out["forced_by_alarm"]) == [True, True, False]

    # --- cartridge volume override (Step 3b refinement) ---

    def test_cartridge_large_fill_in_window_is_real(self):
        from ingestion.enrich import enrich_events_df

        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {
                "timestamp": ts("09:30"),
                "event_type": "site_change",
                "event_subtype": "cartridge",
                "details": json.dumps({"insulin_volume": 240}),
            },
        ])
        out = enrich_events_df(
            events, alarms, {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220}
        )
        assert out.iloc[0]["forced_by_alarm"] == False  # noqa: E712

    def test_cartridge_small_fill_in_window_is_forced(self):
        from ingestion.enrich import enrich_events_df

        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {
                "timestamp": ts("09:30"),
                "event_type": "site_change",
                "event_subtype": "cartridge",
                "details": json.dumps({"insulin_volume": 180}),
            },
        ])
        out = enrich_events_df(
            events, alarms, {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220}
        )
        assert out.iloc[0]["forced_by_alarm"] == True  # noqa: E712

    def test_cartridge_at_exact_threshold_in_window_is_real(self):
        from ingestion.enrich import enrich_events_df

        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {
                "timestamp": ts("09:30"),
                "event_type": "site_change",
                "event_subtype": "cartridge",
                "details": json.dumps({"insulin_volume": 220}),
            },
        ])
        out = enrich_events_df(
            events, alarms, {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220}
        )
        # >= threshold counts as real
        assert out.iloc[0]["forced_by_alarm"] == False  # noqa: E712

    def test_cartridge_malformed_details_in_window_is_forced(self):
        from ingestion.enrich import enrich_events_df

        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {
                "timestamp": ts("09:30"),
                "event_type": "site_change",
                "event_subtype": "cartridge",
                "details": "{not valid json",
            },
        ])
        out = enrich_events_df(
            events, alarms, {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220}
        )
        assert out.iloc[0]["forced_by_alarm"] == True  # noqa: E712

    def test_cartridge_missing_insulin_volume_in_window_is_forced(self):
        from ingestion.enrich import enrich_events_df

        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {
                "timestamp": ts("09:30"),
                "event_type": "site_change",
                "event_subtype": "cartridge",
                "details": json.dumps({}),
            },
        ])
        out = enrich_events_df(
            events, alarms, {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220}
        )
        assert out.iloc[0]["forced_by_alarm"] == True  # noqa: E712

    def test_cartridge_large_fill_outside_window_still_not_forced(self):
        from ingestion.enrich import enrich_events_df

        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {
                "timestamp": ts("20:00"),
                "event_type": "site_change",
                "event_subtype": "cartridge",
                "details": json.dumps({"insulin_volume": 240}),
            },
        ])
        out = enrich_events_df(
            events, alarms, {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220}
        )
        assert out.iloc[0]["forced_by_alarm"] == False  # noqa: E712

    def test_tubing_in_window_forced_regardless_of_details(self):
        from ingestion.enrich import enrich_events_df

        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {
                "timestamp": ts("09:00"),
                "event_type": "site_change",
                "event_subtype": "tubing",
                "details": json.dumps({"prime_size": 500}),
            },
        ])
        out = enrich_events_df(
            events, alarms, {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220}
        )
        assert out.iloc[0]["forced_by_alarm"] == True  # noqa: E712

    def test_cannula_in_window_forced_regardless_of_details(self):
        from ingestion.enrich import enrich_events_df

        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {
                "timestamp": ts("09:00"),
                "event_type": "site_change",
                "event_subtype": "cannula",
                "details": json.dumps({"prime_size": 300}),
            },
        ])
        out = enrich_events_df(
            events, alarms, {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220}
        )
        assert out.iloc[0]["forced_by_alarm"] == True  # noqa: E712

    def test_empty_events(self):
        from ingestion.enrich import enrich_events_df

        events = _events_frame([])
        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
        ])
        out = enrich_events_df(
            events, alarms, {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220}
        )
        assert out.empty
        assert "forced_by_alarm" in out.columns

    def test_empty_alarms(self):
        from ingestion.enrich import enrich_events_df

        events = _events_frame([
            {"timestamp": ts("09:00"), "event_type": "site_change", "event_subtype": "cartridge",
             "details": json.dumps({"insulin_volume": 180})},
        ])
        out = enrich_events_df(
            events, _alarms_frame([]),
            {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220},
        )
        # No alarms → nothing can be forced, even a small cartridge fill
        assert out.iloc[0]["forced_by_alarm"] == False  # noqa: E712

    def test_only_cleared_battery_alarm_does_not_force(self):
        from ingestion.enrich import enrich_events_df

        alarms = _alarms_frame([
            {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "cleared"},
        ])
        events = _events_frame([
            {"timestamp": ts("09:00"), "event_type": "site_change", "event_subtype": "tubing"},
        ])
        out = enrich_events_df(
            events, alarms, {"forced_window_minutes": 120, "cartridge_real_fill_threshold": 220}
        )
        assert out.iloc[0]["forced_by_alarm"] == False  # noqa: E712

    def test_enrich_all_wires_events_enrichment(self):
        from ingestion.enrich import enrich_all

        frames = {
            "requests": _requests_row(source="user", carbs=30, food=7.0, total=7.0),
            "events": _events_frame([
                {"timestamp": ts("09:00"), "event_type": "site_change", "event_subtype": "tubing"},
            ]),
            "alarms": _alarms_frame([
                {"timestamp": ts("08:06"), "alarm_name": "BatteryShutdownAlarm", "action": "activated"},
            ]),
        }
        config = {
            "site_change_detection": {
                "forced_window_minutes": 120,
                "cartridge_real_fill_threshold": 220,
            }
        }
        out = enrich_all(frames, config=config)
        assert "forced_by_alarm" in out["events"].columns
        assert out["events"].iloc[0]["forced_by_alarm"] == True  # noqa: E712


# ---------------------------------------------------------------------------
# Task 1.3 — build_site_issues_df
# ---------------------------------------------------------------------------

def _empty_events() -> pd.DataFrame:
    return _events_frame([])


def _cfg() -> dict:
    return {
        "occlusion_cluster_window_minutes": 180,
        "min_occlusions_for_cluster": 2,
    }


class TestBuildSiteIssuesDf:
    def test_single_occlusion_not_a_cluster(self):
        from ingestion.enrich import build_site_issues_df

        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        out = build_site_issues_df(alarms, _empty_events(), _cfg())
        assert out.empty

    def test_two_occlusions_within_window_cluster(self):
        from ingestion.enrich import build_site_issues_df

        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("10:45"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        out = build_site_issues_df(alarms, _empty_events(), _cfg())
        assert len(out) == 1
        row = out.iloc[0]
        assert row["occlusion_count"] == 2
        assert row["first_occlusion_ts"] == ts("10:00")
        assert row["last_occlusion_ts"] == ts("10:45")
        assert row["pump_serial"] == SERIAL

    def test_three_occlusions_all_one_cluster(self):
        from ingestion.enrich import build_site_issues_df

        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("11:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("12:30"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        out = build_site_issues_df(alarms, _empty_events(), _cfg())
        assert len(out) == 1
        assert out.iloc[0]["occlusion_count"] == 3

    def test_occlusions_split_into_two_clusters_when_gap_exceeds_window(self):
        from ingestion.enrich import build_site_issues_df

        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("10:30"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("16:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("16:30"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        out = build_site_issues_df(alarms, _empty_events(), _cfg())
        assert len(out) == 2
        assert list(out["occlusion_count"]) == [2, 2]
        assert list(out["first_occlusion_ts"]) == [ts("10:00"), ts("16:00")]
        assert list(out["last_occlusion_ts"]) == [ts("10:30"), ts("16:30")]

    def test_resolution_linked_to_site_change(self):
        from ingestion.enrich import build_site_issues_df

        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("10:30"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {"timestamp": ts("11:00"), "event_type": "site_change", "event_subtype": "cannula"},
        ])
        # Caller passes events_df that has already been through enrich_events_df,
        # so forced_by_alarm is populated (False here: no BatteryShutdownAlarm).
        events["forced_by_alarm"] = False
        out = build_site_issues_df(alarms, events, _cfg())
        assert out.iloc[0]["resolved_by_site_change_ts"] == ts("11:00")
        assert out.iloc[0]["resolution_delay_minutes"] == pytest.approx(30.0)

    def test_forced_site_change_does_not_resolve(self):
        from ingestion.enrich import build_site_issues_df

        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("10:30"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {"timestamp": ts("11:00"), "event_type": "site_change", "event_subtype": "cartridge"},
        ])
        events["forced_by_alarm"] = True
        out = build_site_issues_df(alarms, events, _cfg())
        assert pd.isna(out.iloc[0]["resolved_by_site_change_ts"])
        assert pd.isna(out.iloc[0]["resolution_delay_minutes"])

    def test_forced_skipped_real_later_resolves(self):
        from ingestion.enrich import build_site_issues_df

        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("10:30"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {"timestamp": ts("11:00"), "event_type": "site_change", "event_subtype": "cartridge"},
            {"timestamp": ts("13:00"), "event_type": "site_change", "event_subtype": "cannula"},
        ])
        events["forced_by_alarm"] = [True, False]
        out = build_site_issues_df(alarms, events, _cfg())
        assert out.iloc[0]["resolved_by_site_change_ts"] == ts("13:00")
        assert out.iloc[0]["resolution_delay_minutes"] == pytest.approx(150.0)

    def test_unresolved_cluster_has_nat_resolution(self):
        from ingestion.enrich import build_site_issues_df

        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("10:30"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        out = build_site_issues_df(alarms, _empty_events(), _cfg())
        assert pd.isna(out.iloc[0]["resolved_by_site_change_ts"])
        assert pd.isna(out.iloc[0]["resolution_delay_minutes"])

    def test_empty_alarms_returns_empty_with_schema(self):
        from ingestion.enrich import build_site_issues_df

        out = build_site_issues_df(_alarms_frame([]), _empty_events(), _cfg())
        assert out.empty
        for col in [
            "first_occlusion_ts", "last_occlusion_ts", "occlusion_count",
            "resolved_by_site_change_ts", "resolution_delay_minutes", "pump_serial",
        ]:
            assert col in out.columns

    def test_cleared_occlusions_ignored(self):
        from ingestion.enrich import build_site_issues_df

        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("10:05"), "alarm_name": "OcclusionAlarm", "action": "cleared"},
            {"timestamp": ts("10:30"), "alarm_name": "OcclusionAlarm", "action": "cleared"},
        ])
        out = build_site_issues_df(alarms, _empty_events(), _cfg())
        # Only one activated occlusion → no cluster.
        assert out.empty

    def test_missing_forced_by_alarm_column_treats_all_as_non_forced(self):
        from ingestion.enrich import build_site_issues_df

        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            {"timestamp": ts("10:30"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        events = _events_frame([
            {"timestamp": ts("11:00"), "event_type": "site_change", "event_subtype": "cannula"},
        ])
        # Deliberately no forced_by_alarm column — backward-compat path.
        assert "forced_by_alarm" not in events.columns
        out = build_site_issues_df(alarms, events, _cfg())
        assert out.iloc[0]["resolved_by_site_change_ts"] == ts("11:00")

    def test_enrich_all_attaches_site_issues_frame(self):
        from ingestion.enrich import enrich_all

        frames = {
            "events": _events_frame([
                {"timestamp": ts("11:00"), "event_type": "site_change", "event_subtype": "cannula"},
            ]),
            "alarms": _alarms_frame([
                {"timestamp": ts("10:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
                {"timestamp": ts("10:30"), "alarm_name": "OcclusionAlarm", "action": "activated"},
            ]),
        }
        config = {
            "site_change_detection": {
                "forced_window_minutes": 120,
                "cartridge_real_fill_threshold": 220,
                "occlusion_cluster_window_minutes": 180,
                "min_occlusions_for_cluster": 2,
            }
        }
        out = enrich_all(frames, config=config)
        assert "site_issues" in out
        site_issues = out["site_issues"]
        assert len(site_issues) == 1
        # Events enrichment ran first, so the non-BatteryShutdown site_change
        # is marked forced_by_alarm=False and therefore resolves the cluster.
        assert site_issues.iloc[0]["resolved_by_site_change_ts"] == ts("11:00")


# ---------------------------------------------------------------------------
# Task 1.4 — build_cgm_gaps_df
# ---------------------------------------------------------------------------

class TestBuildCgmGapsDf:
    def test_single_closed_gap(self):
        from ingestion.enrich import build_cgm_gaps_df

        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "category": "cgm_alert",
             "alarm_name": "cgm_out_of_range", "action": "activated"},
            {"timestamp": ts("10:25"), "category": "cgm_alert",
             "alarm_name": "cgm_out_of_range", "action": "cleared"},
        ])
        out = build_cgm_gaps_df(alarms)
        assert len(out) == 1
        row = out.iloc[0]
        assert row["start_ts"] == ts("10:00")
        assert row["end_ts"] == ts("10:25")
        assert row["duration_minutes"] == pytest.approx(25.0)
        assert row["ongoing"] == False  # noqa: E712
        assert row["pump_serial"] == SERIAL

    def test_unclosed_gap_marked_ongoing(self):
        from ingestion.enrich import build_cgm_gaps_df

        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "category": "cgm_alert",
             "alarm_name": "cgm_out_of_range", "action": "activated"},
        ])
        out = build_cgm_gaps_df(alarms)
        assert len(out) == 1
        row = out.iloc[0]
        assert row["start_ts"] == ts("10:00")
        assert pd.isna(row["end_ts"])
        assert pd.isna(row["duration_minutes"])
        assert row["ongoing"] == True  # noqa: E712

    def test_multiple_sequential_gaps(self):
        from ingestion.enrich import build_cgm_gaps_df

        alarms = _alarms_frame([
            {"timestamp": ts("08:00"), "category": "cgm_alert",
             "alarm_name": "cgm_out_of_range", "action": "activated"},
            {"timestamp": ts("08:10"), "category": "cgm_alert",
             "alarm_name": "cgm_out_of_range", "action": "cleared"},
            {"timestamp": ts("14:00"), "category": "cgm_alert",
             "alarm_name": "cgm_out_of_range", "action": "activated"},
            {"timestamp": ts("14:30"), "category": "cgm_alert",
             "alarm_name": "cgm_out_of_range", "action": "cleared"},
        ])
        out = build_cgm_gaps_df(alarms)
        assert len(out) == 2
        assert list(out["start_ts"]) == [ts("08:00"), ts("14:00")]
        assert list(out["end_ts"]) == [ts("08:10"), ts("14:30")]
        assert list(out["duration_minutes"]) == [pytest.approx(10.0), pytest.approx(30.0)]
        assert list(out["ongoing"]) == [False, False]

    def test_double_activated_closes_previous(self, caplog):
        from ingestion.enrich import build_cgm_gaps_df

        alarms = _alarms_frame([
            {"timestamp": ts("08:00"), "category": "cgm_alert",
             "alarm_name": "cgm_out_of_range", "action": "activated"},
            {"timestamp": ts("08:10"), "category": "cgm_alert",
             "alarm_name": "cgm_out_of_range", "action": "activated"},
            {"timestamp": ts("08:30"), "category": "cgm_alert",
             "alarm_name": "cgm_out_of_range", "action": "cleared"},
        ])
        with caplog.at_level("WARNING"):
            out = build_cgm_gaps_df(alarms)
        assert len(out) == 2
        # First gap was force-closed at the second activation's timestamp.
        assert out.iloc[0]["start_ts"] == ts("08:00")
        assert out.iloc[0]["end_ts"] == ts("08:10")
        assert out.iloc[0]["duration_minutes"] == pytest.approx(10.0)
        assert out.iloc[0]["ongoing"] == False  # noqa: E712
        # Second gap was paired normally.
        assert out.iloc[1]["start_ts"] == ts("08:10")
        assert out.iloc[1]["end_ts"] == ts("08:30")
        assert "unpaired" in caplog.text.lower() or "double" in caplog.text.lower()

    def test_ignores_non_cgm_out_of_range(self):
        from ingestion.enrich import build_cgm_gaps_df

        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "category": "cgm_alert",
             "alarm_name": "cgm_high", "action": "activated"},
            {"timestamp": ts("10:05"), "category": "cgm_alert",
             "alarm_name": "cgm_high", "action": "cleared"},
            {"timestamp": ts("11:00"), "alarm_name": "OcclusionAlarm", "action": "activated"},
        ])
        out = build_cgm_gaps_df(alarms)
        assert out.empty

    def test_empty_alarms(self):
        from ingestion.enrich import build_cgm_gaps_df

        out = build_cgm_gaps_df(_alarms_frame([]))
        assert out.empty
        for col in ["start_ts", "end_ts", "duration_minutes", "pump_serial", "ongoing"]:
            assert col in out.columns

    def test_none_alarms_returns_empty(self):
        from ingestion.enrich import build_cgm_gaps_df

        out = build_cgm_gaps_df(None)
        assert out.empty
        for col in ["start_ts", "end_ts", "duration_minutes", "pump_serial", "ongoing"]:
            assert col in out.columns

    def test_cleared_without_activated_logs_and_skips(self, caplog):
        from ingestion.enrich import build_cgm_gaps_df

        alarms = _alarms_frame([
            {"timestamp": ts("10:00"), "category": "cgm_alert",
             "alarm_name": "cgm_out_of_range", "action": "cleared"},
        ])
        with caplog.at_level("WARNING"):
            out = build_cgm_gaps_df(alarms)
        assert out.empty
        assert "unpaired" in caplog.text.lower()

    def test_enrich_all_attaches_cgm_gaps_frame(self):
        from ingestion.enrich import enrich_all

        frames = {
            "alarms": _alarms_frame([
                {"timestamp": ts("10:00"), "category": "cgm_alert",
                 "alarm_name": "cgm_out_of_range", "action": "activated"},
                {"timestamp": ts("10:25"), "category": "cgm_alert",
                 "alarm_name": "cgm_out_of_range", "action": "cleared"},
            ]),
        }
        out = enrich_all(frames, config={})
        assert "cgm_gaps" in out
        gaps = out["cgm_gaps"]
        assert len(gaps) == 1
        assert gaps.iloc[0]["duration_minutes"] == pytest.approx(25.0)
