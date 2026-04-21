"""Tests for ingestion/enrich.py."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

PST = timezone(timedelta(hours=-8))
SERIAL = "TEST123"


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
