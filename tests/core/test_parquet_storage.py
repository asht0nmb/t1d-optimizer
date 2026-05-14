"""Parquet-specific behaviors for :class:`core.storage.parquet.ParquetStorage`.

The contract test suite (:mod:`tests.core.test_storage_contract`) covers
every behavior shared with the other implementations. This file holds
the disk-specific invariants:

* sidecar files exist on disk after writes, with the same shape the
  legacy ``ingestion/storage.py`` produced;
* the on-disk parquet filenames match the legacy
  ``PARQUET_FILES`` mapping;
* ``ingestion.version_guard.check_pipeline_version()`` continues to
  return ``None`` after a ``ParquetStorage`` upsert + version write.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from core.storage.parquet import (
    ALERTS_FILENAME,
    DETECTION_FILENAME,
    PARQUET_FILES,
    PIPELINE_VERSION_FILENAME,
    STATE_FILENAME,
    ParquetStorage,
)
from core.storage.records import (
    AlertRecord,
    DetectionResult,
    FetchState,
)

UTC = timezone.utc


def _cgm_df_one(ts: datetime) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "pump_serial": "PUMP-A",
                "seqnum": 1,
                "timestamp": ts,
                "bg_mgdl": 120,
                "backfilled": False,
                "sensor_timestamp": None,
            }
        ]
    )


# ---------------------------------------------------------------------------
# Filename layout
# ---------------------------------------------------------------------------


class TestOnDiskFileLayout:
    def test_upsert_writes_legacy_parquet_filename(self, tmp_path: Path):
        storage = ParquetStorage(root=tmp_path)
        storage.upsert_table(
            "cgm", _cgm_df_one(datetime(2026, 5, 13, tzinfo=UTC))
        )
        # The on-disk file must be the same one the legacy
        # `ingestion.storage` produces, so out-of-band tooling
        # keeps working.
        assert (tmp_path / "cgm.parquet").exists()
        assert (tmp_path / PARQUET_FILES["cgm"]).exists()

    def test_parquet_files_mapping_matches_legacy(self):
        # Sentinel against accidental rename — the legacy callers and
        # the bootstrap script read this dict by name.
        assert PARQUET_FILES == {
            "cgm": "cgm.parquet",
            "bolus": "bolus.parquet",
            "requests": "requests.parquet",
            "basal": "basal.parquet",
            "suspension": "suspension.parquet",
            "events": "events.parquet",
            "alarms": "alarms.parquet",
            "site_issues": "site_issues.parquet",
            "cgm_gaps": "cgm_gaps.parquet",
        }


# ---------------------------------------------------------------------------
# Sidecars
# ---------------------------------------------------------------------------


class TestSidecarsOnDisk:
    def test_set_pipeline_version_writes_sidecar(self, tmp_path: Path):
        storage = ParquetStorage(root=tmp_path)
        storage.set_pipeline_version(3)
        sidecar = tmp_path / PIPELINE_VERSION_FILENAME
        assert sidecar.exists()
        payload = json.loads(sidecar.read_text())
        assert payload["version"] == 3
        # Same key as the legacy sidecar (`written_at`, ISO-8601).
        assert "written_at" in payload
        # Round-trippable.
        datetime.fromisoformat(payload["written_at"])

    def test_set_fetch_state_writes_sidecar(self, tmp_path: Path):
        storage = ParquetStorage(root=tmp_path)
        ts = datetime(2026, 5, 13, tzinfo=UTC)
        storage.set_fetch_state(
            "tandem",
            FetchState("tandem", None, ts, {"last_end": "2026-05-13"}),
        )
        sidecar = tmp_path / STATE_FILENAME
        assert sidecar.exists()
        payload = json.loads(sidecar.read_text())
        assert "tandem" in payload

    def test_record_alert_writes_alerts_parquet(self, tmp_path: Path):
        storage = ParquetStorage(root=tmp_path)
        ts = datetime(2026, 5, 13, tzinfo=UTC)
        storage.record_alert(
            AlertRecord(None, "anomaly_spike", "cgm:1", ts, {"bg": 240})
        )
        assert (tmp_path / ALERTS_FILENAME).exists()

    def test_record_detection_writes_detection_parquet(self, tmp_path: Path):
        storage = ParquetStorage(root=tmp_path)
        ts = datetime(2026, 5, 13, tzinfo=UTC)
        storage.record_detection_result(
            DetectionResult("missed_meal", ts, {"rise": 42}, ts)
        )
        assert (tmp_path / DETECTION_FILENAME).exists()

    def test_clean_all_removes_sidecars(self, tmp_path: Path):
        storage = ParquetStorage(root=tmp_path)
        ts = datetime(2026, 5, 13, tzinfo=UTC)
        storage.upsert_table("cgm", _cgm_df_one(ts))
        storage.set_pipeline_version(3)
        storage.set_fetch_state("tandem", FetchState("tandem", None, ts, {}))
        storage.record_alert(
            AlertRecord(None, "anomaly_spike", "cgm:1", ts, {})
        )
        storage.record_detection_result(
            DetectionResult("missed_meal", ts, {}, ts)
        )

        storage.clean_all()

        assert not (tmp_path / "cgm.parquet").exists()
        assert not (tmp_path / PIPELINE_VERSION_FILENAME).exists()
        assert not (tmp_path / STATE_FILENAME).exists()
        assert not (tmp_path / ALERTS_FILENAME).exists()
        assert not (tmp_path / DETECTION_FILENAME).exists()


# ---------------------------------------------------------------------------
# delete_range: empty-after-delete should preserve the file
# ---------------------------------------------------------------------------


class TestDeleteRangePreservesFile:
    def test_empty_after_delete_writes_empty_frame(self, tmp_path: Path):
        """Per the plan: empty after delete → write empty DF, don't unlink.

        Keeps `version_guard` from seeing the table 'disappear'.
        """
        storage = ParquetStorage(root=tmp_path)
        storage.upsert_table(
            "cgm", _cgm_df_one(datetime(2026, 5, 13, tzinfo=UTC))
        )
        storage.delete_range("cgm", pump_serial="PUMP-A")
        path = tmp_path / "cgm.parquet"
        assert path.exists(), "delete_range must not unlink the parquet file"
        kept = pd.read_parquet(path)
        assert len(kept) == 0


# ---------------------------------------------------------------------------
# version_guard interop
# ---------------------------------------------------------------------------


class TestVersionGuardInterop:
    def test_pipeline_version_v3_clean_under_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """After a ParquetStorage write at the current PIPELINE_VERSION,
        `ingestion.version_guard.check_pipeline_version()` must return
        None (i.e. "in sync"). This is the load-bearing interop test
        between Phase 1 and the existing schema-drift guard."""
        from ingestion import storage as legacy_storage
        from ingestion import version_guard
        from ingestion.pipeline_version import PIPELINE_VERSION

        # Point the legacy module at the same temp root that the
        # ParquetStorage instance writes into, so `version_guard`
        # reads the right sidecar file.
        monkeypatch.setattr(legacy_storage, "PROCESSED_DIR", tmp_path)
        monkeypatch.setattr(
            legacy_storage,
            "PIPELINE_VERSION_FILE",
            tmp_path / PIPELINE_VERSION_FILENAME,
        )
        version_guard.reset_cache()

        storage = ParquetStorage(root=tmp_path)
        storage.upsert_table(
            "cgm", _cgm_df_one(datetime(2026, 5, 13, tzinfo=UTC))
        )
        storage.set_pipeline_version(PIPELINE_VERSION)

        # `legacy_storage.read_pipeline_version` and
        # `version_guard.check_pipeline_version` should both see the
        # version we just wrote and report no mismatch.
        assert legacy_storage.read_pipeline_version() == PIPELINE_VERSION
        assert version_guard.check_pipeline_version() is None
