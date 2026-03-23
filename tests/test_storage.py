"""Tests for ingestion/storage.py — parquet round-trip, dedup, and cleanup."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from ingestion import storage

PST = timezone(timedelta(hours=-8))


@pytest.fixture(autouse=True)
def _isolate_storage(tmp_path, monkeypatch):
    """Point storage module at a temp directory so tests don't touch real data."""
    monkeypatch.setattr(storage, "PROCESSED_DIR", tmp_path)
    monkeypatch.setattr(storage, "STATE_FILE", tmp_path / ".fetch_state.json")


# ---------------------------------------------------------------------------
# save_df / load_df round-trip
# ---------------------------------------------------------------------------


class TestSaveLoadRoundTrip:
    def test_round_trip(self):
        df = pd.DataFrame({
            "timestamp": [datetime(2026, 3, 20, 10, 0, tzinfo=PST)],
            "bg_mgdl": [150],
            "pump_serial": ["TEST123"],
            "seqnum": [1],
        })
        storage.save_df("cgm", df)
        loaded = storage.load_df("cgm")
        assert loaded is not None
        assert len(loaded) == 1
        assert loaded.iloc[0]["bg_mgdl"] == 150

    def test_dedup_on_overlapping_save(self):
        """Saving twice with overlapping data should collapse duplicates."""
        df1 = pd.DataFrame({
            "timestamp": [
                datetime(2026, 3, 20, 10, 0, tzinfo=PST),
                datetime(2026, 3, 20, 10, 5, tzinfo=PST),
            ],
            "bg_mgdl": [150, 160],
            "pump_serial": ["TEST123", "TEST123"],
            "seqnum": [1, 2],
        })
        df2 = pd.DataFrame({
            "timestamp": [
                datetime(2026, 3, 20, 10, 5, tzinfo=PST),  # overlap
                datetime(2026, 3, 20, 10, 10, tzinfo=PST),
            ],
            "bg_mgdl": [160, 170],
            "pump_serial": ["TEST123", "TEST123"],
            "seqnum": [2, 3],
        })
        storage.save_df("cgm", df1)
        storage.save_df("cgm", df2)
        loaded = storage.load_df("cgm")
        assert loaded is not None
        assert len(loaded) == 3  # seqnum 1, 2, 3

    def test_empty_df_no_file_written(self, tmp_path):
        storage.save_df("cgm", pd.DataFrame())
        parquet_path = tmp_path / storage.PARQUET_FILES["cgm"]
        assert not parquet_path.exists()

    def test_load_nonexistent_returns_none(self):
        assert storage.load_df("cgm") is None


# ---------------------------------------------------------------------------
# clean_all
# ---------------------------------------------------------------------------


class TestCleanAll:
    def test_removes_files(self, tmp_path):
        df = pd.DataFrame({
            "timestamp": [datetime(2026, 3, 20, 10, 0, tzinfo=PST)],
            "bg_mgdl": [150],
            "pump_serial": ["TEST123"],
            "seqnum": [1],
        })
        storage.save_df("cgm", df)
        # Also save fetch state
        storage.save_fetch_state({"last_end": "2026-03-20"})

        # Verify files exist
        assert (tmp_path / storage.PARQUET_FILES["cgm"]).exists()
        assert (tmp_path / ".fetch_state.json").exists()

        storage.clean_all()

        assert not (tmp_path / storage.PARQUET_FILES["cgm"]).exists()
        assert not (tmp_path / ".fetch_state.json").exists()


# ---------------------------------------------------------------------------
# fetch state
# ---------------------------------------------------------------------------


class TestFetchState:
    def test_round_trip(self):
        state = {"last_end": "2026-03-20", "pump_serial": "ABC123"}
        storage.save_fetch_state(state)
        loaded = storage.load_fetch_state()
        assert loaded == state

    def test_empty_when_no_file(self):
        assert storage.load_fetch_state() == {}
