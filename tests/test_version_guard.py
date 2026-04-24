"""Tests for the pipeline version guard."""

from __future__ import annotations

import pytest

from ingestion import storage, version_guard


@pytest.fixture(autouse=True)
def _isolate_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PROCESSED_DIR", tmp_path)
    monkeypatch.setattr(storage, "STATE_FILE", tmp_path / ".fetch_state.json")
    monkeypatch.setattr(
        storage, "PIPELINE_VERSION_FILE", tmp_path / ".pipeline_version.json"
    )
    version_guard.reset_cache()
    yield
    version_guard.reset_cache()


def _any_parquet(tmp_path):
    """Create a dummy file in PROCESSED_DIR that looks like processed data."""
    (tmp_path / "cgm.parquet").write_bytes(b"fake parquet content")


class TestCheckPipelineVersion:
    def test_no_processed_dir_is_silent(self, tmp_path, monkeypatch):
        """Fresh checkouts with no data shouldn't scream about staleness."""
        missing = tmp_path / "does-not-exist"
        monkeypatch.setattr(storage, "PROCESSED_DIR", missing)
        monkeypatch.setattr(
            storage, "PIPELINE_VERSION_FILE", missing / ".pipeline_version.json"
        )
        assert version_guard.check_pipeline_version() is None

    def test_empty_processed_dir_is_silent(self, tmp_path):
        """Directory exists but has no parquet → nothing to guard against."""
        tmp_path.mkdir(exist_ok=True)
        assert version_guard.check_pipeline_version() is None

    def test_healthy_current_sidecar_is_silent(self, tmp_path):
        _any_parquet(tmp_path)
        storage.write_pipeline_version()
        assert version_guard.check_pipeline_version() is None

    def test_stale_sidecar_returns_warning(self, tmp_path):
        _any_parquet(tmp_path)
        storage.write_pipeline_version(1)

        msg = version_guard.check_pipeline_version()
        assert msg is not None
        assert "PIPELINE VERSION MISMATCH" in msg or "pipeline version" in msg.lower()
        assert "fetch --clean" in msg
        assert "v1" in msg
        from ingestion.pipeline_version import PIPELINE_VERSION

        assert f"v{PIPELINE_VERSION}" in msg

    def test_missing_sidecar_but_parquets_present_returns_warning(self, tmp_path):
        """Parquets exist but no sidecar → pre-versioning data."""
        _any_parquet(tmp_path)
        assert not (tmp_path / ".pipeline_version.json").exists()

        msg = version_guard.check_pipeline_version()
        assert msg is not None
        assert "fetch --clean" in msg
        assert "unknown" in msg.lower() or "unversioned" in msg.lower()

    def test_future_sidecar_returns_warning(self, tmp_path):
        """Data written by a *newer* pipeline than the running code."""
        _any_parquet(tmp_path)
        storage.write_pipeline_version(999)

        msg = version_guard.check_pipeline_version()
        assert msg is not None
        assert "v999" in msg

    def test_cache_only_reads_sidecar_once(self, tmp_path, monkeypatch):
        _any_parquet(tmp_path)
        storage.write_pipeline_version(1)

        call_count = {"n": 0}
        original = storage.read_pipeline_version

        def counting_read():
            call_count["n"] += 1
            return original()

        monkeypatch.setattr(storage, "read_pipeline_version", counting_read)

        first = version_guard.check_pipeline_version()
        second = version_guard.check_pipeline_version()
        assert first == second
        assert call_count["n"] == 1

    def test_reset_cache_forces_reread(self, tmp_path):
        _any_parquet(tmp_path)
        storage.write_pipeline_version(1)
        first = version_guard.check_pipeline_version()
        assert first is not None

        storage.write_pipeline_version()
        assert version_guard.check_pipeline_version() == first

        version_guard.reset_cache()
        assert version_guard.check_pipeline_version() is None


class TestWarnIfStale:
    def test_prints_nothing_when_healthy(self, tmp_path, capsys):
        _any_parquet(tmp_path)
        storage.write_pipeline_version()
        version_guard.warn_if_stale()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_prints_single_line_when_stale(self, tmp_path, capsys):
        _any_parquet(tmp_path)
        storage.write_pipeline_version(1)
        version_guard.warn_if_stale()
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert combined.strip()
        assert "fetch --clean" in combined
