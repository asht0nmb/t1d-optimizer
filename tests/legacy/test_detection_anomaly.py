"""Tests for `detection.legacy.anomaly.detect_anomalies`."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from detection.legacy.anomaly import detect_anomalies

pytestmark = pytest.mark.legacy


PST = timezone(timedelta(hours=-8))

EXPECTED_COLUMNS = [
    "timestamp",
    "anomaly_type",
    "bg_at_event",
    "rate_mgdl_per_min",
    "confidence",
    "is_backfilled_context",
]


def _cgm_series(
    readings,
    start: datetime = datetime(2026, 3, 19, 0, 0, tzinfo=PST),
    step_min: int = 5,
) -> pd.DataFrame:
    """Build a DataFrame matching `ingestion.builders.build_cgm_df` output."""
    rows = []
    for i, bg in enumerate(readings):
        rows.append(
            {
                "timestamp": start + timedelta(minutes=i * step_min),
                "bg_mgdl": int(bg),
                "backfilled": False,
                "sensor_timestamp": None,
                "pump_serial": "TEST",
                "seqnum": i,
            }
        )
    columns = [
        "timestamp",
        "bg_mgdl",
        "backfilled",
        "sensor_timestamp",
        "pump_serial",
        "seqnum",
    ]
    return pd.DataFrame(rows, columns=columns)


class TestSpikeDetection:
    def test_spike_detected_at_crossing(self, default_config):
        # crosses 180 between idx 2 (175) and idx 3 (190)
        df = _cgm_series([120, 150, 175, 190, 210, 220])
        out = detect_anomalies(df, default_config)
        spikes = out[out["anomaly_type"] == "spike"]
        assert len(spikes) == 1
        assert int(spikes.iloc[0]["bg_at_event"]) == 190

    def test_no_spike_when_already_high(self, default_config):
        df = _cgm_series([200, 210, 220, 230])
        out = detect_anomalies(df, default_config)
        assert (out["anomaly_type"] == "spike").sum() == 0

    def test_spike_rate_is_positive(self, default_config):
        df = _cgm_series([120, 150, 175, 190])
        out = detect_anomalies(df, default_config)
        spike = out[out["anomaly_type"] == "spike"].iloc[0]
        # 190 - 175 = 15 over 5 min => 3.0 mg/dL/min
        assert spike["rate_mgdl_per_min"] == pytest.approx(3.0)


class TestDropDetection:
    def test_drop_detected_at_crossing(self, default_config):
        # crosses 70 between idx 2 (75) and idx 3 (65)
        df = _cgm_series([100, 85, 75, 65, 60])
        out = detect_anomalies(df, default_config)
        drops = out[out["anomaly_type"] == "drop"]
        assert len(drops) == 1
        assert int(drops.iloc[0]["bg_at_event"]) == 65

    def test_no_drop_when_already_low(self, default_config):
        df = _cgm_series([65, 60, 55, 50])
        out = detect_anomalies(df, default_config)
        assert (out["anomaly_type"] == "drop").sum() == 0

    def test_drop_rate_is_negative(self, default_config):
        df = _cgm_series([100, 85, 75, 65])
        out = detect_anomalies(df, default_config)
        drop = out[out["anomaly_type"] == "drop"].iloc[0]
        # 65 - 75 = -10 over 5 min => -2.0
        assert drop["rate_mgdl_per_min"] == pytest.approx(-2.0)


class TestFlatlineDetection:
    def test_flatline_detected(self, default_config):
        # 12 contiguous readings with variance well below tolerance (2.0)
        df = _cgm_series([140, 141, 140, 141, 140, 141,
                          140, 141, 140, 141, 140, 141])
        out = detect_anomalies(df, default_config)
        flats = out[out["anomaly_type"] == "flatline"]
        assert len(flats) == 1
        # Flag lands on the LAST reading of the window (idx 11).
        last_ts = df["timestamp"].iloc[-1]
        assert flats.iloc[0]["timestamp"] == last_ts
        assert flats.iloc[0]["rate_mgdl_per_min"] == 0.0

    def test_noisy_series_no_flatline(self, default_config):
        df = _cgm_series([140, 160, 120, 180, 100, 200,
                          140, 160, 120, 180, 100, 200])
        out = detect_anomalies(df, default_config)
        assert (out["anomaly_type"] == "flatline").sum() == 0

    def test_flatline_not_repeated_overlapping(self, default_config):
        # 24 flat readings => 2 non-overlapping windows at most.
        df = _cgm_series([140] * 24)
        out = detect_anomalies(df, default_config)
        flats = out[out["anomaly_type"] == "flatline"]
        assert 1 <= len(flats) <= 2

    def test_flatline_gap_breaks_contiguity(self, default_config):
        # 12 flat readings but with a 15-min gap injected mid-window (>7 min).
        df = _cgm_series([140] * 12)
        # Shift everything from index 6 onward by +10 min so gap @ idx 6 = 15 min.
        df.loc[6:, "timestamp"] = df.loc[6:, "timestamp"] + pd.Timedelta(minutes=10)
        out = detect_anomalies(df, default_config)
        assert (out["anomaly_type"] == "flatline").sum() == 0

    def test_flatline_confidence_in_range(self, default_config):
        df = _cgm_series([140] * 12)
        out = detect_anomalies(df, default_config)
        flat = out[out["anomaly_type"] == "flatline"].iloc[0]
        assert 0.0 <= flat["confidence"] <= 1.0


class TestBackfilledContext:
    def test_backfilled_reading_flagged(self, default_config):
        df = _cgm_series([120, 150, 175, 190, 210])
        df.loc[3, "backfilled"] = True
        out = detect_anomalies(df, default_config)
        row = out[out["bg_at_event"] == 190].iloc[0]
        assert row["is_backfilled_context"] is True or row["is_backfilled_context"] == True  # noqa: E712

    def test_non_backfilled_reading_not_flagged_as_backfilled(self, default_config):
        df = _cgm_series([120, 150, 175, 190, 210])
        out = detect_anomalies(df, default_config)
        row = out[out["bg_at_event"] == 190].iloc[0]
        assert row["is_backfilled_context"] is False or row["is_backfilled_context"] == False  # noqa: E712


class TestEmptyOrInsufficientData:
    def test_empty_df(self, default_config):
        df = _cgm_series([])
        out = detect_anomalies(df, default_config)
        assert out.empty
        for col in EXPECTED_COLUMNS:
            assert col in out.columns

    def test_single_reading_no_anomalies(self, default_config):
        df = _cgm_series([200])
        out = detect_anomalies(df, default_config)
        assert out.empty
        for col in EXPECTED_COLUMNS:
            assert col in out.columns


class TestConfidence:
    def test_spike_confidence_increases_with_magnitude(self, default_config):
        low = _cgm_series([120, 185])
        high = _cgm_series([120, 250])
        out_low = detect_anomalies(low, default_config)
        out_high = detect_anomalies(high, default_config)
        c_low = out_low[out_low["anomaly_type"] == "spike"].iloc[0]["confidence"]
        c_high = out_high[out_high["anomaly_type"] == "spike"].iloc[0]["confidence"]
        assert c_high > c_low

    def test_spike_confidence_in_range(self, default_config):
        df = _cgm_series([120, 500])
        out = detect_anomalies(df, default_config)
        c = out[out["anomaly_type"] == "spike"].iloc[0]["confidence"]
        assert 0.0 <= c <= 1.0


class TestOutputSchema:
    def test_columns_exact(self, default_config):
        df = _cgm_series([120, 150, 175, 190, 210])
        out = detect_anomalies(df, default_config)
        assert list(out.columns) == EXPECTED_COLUMNS
