"""Tests for `main.py doctor` and `scripts/doctor.py`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from ingestion import storage, version_guard
from scripts.doctor import doctor

PST = timezone(timedelta(hours=-8))


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PROCESSED_DIR", tmp_path)
    monkeypatch.setattr(
        storage, "PIPELINE_VERSION_FILE", tmp_path / ".pipeline_version.json"
    )
    version_guard.reset_cache()
    yield
    version_guard.reset_cache()


def _write_cgm(tmp_path, timestamps):
    df = pd.DataFrame({
        "timestamp": list(timestamps),
        "bg_mgdl": [100 + i for i in range(len(timestamps))],
        "seqnum": list(range(len(timestamps))),
        "pump_serial": ["p1"] * len(timestamps),
    })
    storage.save_df("cgm", df)


# ---------------------------------------------------------------------------
# Version banner
# ---------------------------------------------------------------------------


class TestDoctorVersionBanner:
    def test_reports_current_code_version(self, capsys):
        from ingestion.pipeline_version import PIPELINE_VERSION

        doctor()
        out = capsys.readouterr().out
        assert f"code pipeline version: v{PIPELINE_VERSION}" in out.lower() or \
               f"code: v{PIPELINE_VERSION}" in out.lower() or \
               f"v{PIPELINE_VERSION}" in out

    def test_reports_on_disk_version_when_present(self, capsys, tmp_path):
        storage.write_pipeline_version(1)
        (tmp_path / "cgm.parquet").write_bytes(b"x")
        doctor()
        out = capsys.readouterr().out
        assert "v1" in out

    def test_reports_missing_sidecar(self, capsys, tmp_path):
        (tmp_path / "cgm.parquet").write_bytes(b"x")
        doctor()
        out = capsys.readouterr().out
        assert "unversioned" in out.lower() or "unknown" in out.lower()


# ---------------------------------------------------------------------------
# Staleness recommendation
# ---------------------------------------------------------------------------


class TestDoctorStaleness:
    def test_stale_data_recommends_fetch_clean(self, capsys, tmp_path):
        (tmp_path / "cgm.parquet").write_bytes(b"x")
        storage.write_pipeline_version(1)
        doctor()
        out = capsys.readouterr().out
        assert "fetch --clean" in out
        assert "PIPELINE VERSION MISMATCH" in out

    def test_healthy_pipeline_no_mismatch_line(self, capsys, tmp_path):
        _write_cgm(tmp_path, [datetime(2026, 3, 20, 10, 0, tzinfo=PST)])
        doctor()
        out = capsys.readouterr().out
        assert "PIPELINE VERSION MISMATCH" not in out


# ---------------------------------------------------------------------------
# Missing parquet detection
# ---------------------------------------------------------------------------


class TestDoctorMissingParquets:
    def test_empty_dir_reports_no_processed_data(self, capsys, tmp_path):
        doctor()
        out = capsys.readouterr().out
        assert "no processed data" in out.lower() or "no parquet" in out.lower()


# ---------------------------------------------------------------------------
# Same-second CGM stacking heuristic
# ---------------------------------------------------------------------------


class TestDoctorStackingHeuristic:
    def test_clean_cgm_no_stacking_warning(self, capsys, tmp_path):
        ts = [
            datetime(2026, 3, 20, 10, 0, tzinfo=PST),
            datetime(2026, 3, 20, 10, 5, tzinfo=PST),
            datetime(2026, 3, 20, 10, 10, tzinfo=PST),
        ]
        _write_cgm(tmp_path, ts)
        doctor()
        out = capsys.readouterr().out
        assert "same-second" not in out.lower()
        assert "stack" not in out.lower()

    def test_stacked_cgm_triggers_warning(self, capsys, tmp_path):
        """Multiple CGM readings at the same second → stacking warning.

        Mimics the 2026-03-19 battery-outage burst where backfilled rows
        stamped with eventTimestamp collide at the pump-reconnect second.
        """
        burst_ts = datetime(2026, 3, 20, 12, 3, 16, tzinfo=PST)
        ts = [burst_ts] * 8 + [
            datetime(2026, 3, 20, 12, 10, tzinfo=PST),
            datetime(2026, 3, 20, 12, 15, tzinfo=PST),
        ]
        _write_cgm(tmp_path, ts)
        doctor()
        out = capsys.readouterr().out
        assert "same-second" in out.lower() or "stack" in out.lower()
        assert "fetch --clean" in out


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------


class TestMainRegistersDoctor:
    def test_main_py_registers_doctor(self):
        import subprocess
        import sys
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "main.py", "--help"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        help_text = result.stdout + result.stderr
        assert "doctor" in help_text
