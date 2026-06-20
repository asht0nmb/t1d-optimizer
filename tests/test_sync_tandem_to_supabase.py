"""Tests for scripts/sync_tandem_to_supabase.py — Tandem → Supabase nightly sync."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.storage.records import FetchState, UpsertResult
from scripts import sync_tandem_to_supabase as sync

PST = timezone(timedelta(hours=-8))
UTC = timezone.utc


def _make_pump(serial: str, device_id: int, min_date: str, max_date: str) -> dict:
    return {
        "serialNumber": serial,
        "tconnectDeviceId": device_id,
        "minDateWithEvents": f"{min_date}T00:00:00",
        "maxDateWithEvents": f"{max_date}T00:00:00",
    }


def _enriched_dfs(serial: str = "SN_A") -> dict[str, pd.DataFrame]:
    ts = datetime(2024, 6, 1, 10, 0, tzinfo=PST)
    requests = pd.DataFrame({
        "pump_serial": [serial],
        "bolus_id": [1],
        "timestamp": [ts],
        "bolus_category": ["user_meal"],
        "override_delta": [float("nan")],
        "total_requested": [2.0],
        "food_insulin": [2.0],
        "correction_insulin": [0.0],
        "carbs_g": [30.0],
        "bolus_source": ["calculated"],
    })
    site_issues = pd.DataFrame({
        "pump_serial": [serial],
        "first_occlusion_ts": [ts],
        "last_occlusion_ts": [ts],
        "occlusion_count": [1],
        "forced_site_change": [False],
    })
    cgm_gaps = pd.DataFrame({
        "pump_serial": [serial],
        "start_ts": [ts],
        "end_ts": [ts + timedelta(hours=1)],
        "duration_minutes": [60],
        "alarm_kind": ["sensor"],
    })
    return {
        "cgm": pd.DataFrame(),
        "bolus": pd.DataFrame(),
        "requests": requests,
        "basal": pd.DataFrame(),
        "suspension": pd.DataFrame(),
        "events": pd.DataFrame(),
        "alarms": pd.DataFrame(),
        "site_issues": site_issues,
        "cgm_gaps": cgm_gaps,
    }


@pytest.fixture
def mock_config() -> dict:
    return {"site_change_detection": {}}


@pytest.fixture
def patched_sync(monkeypatch, mock_config):
    """Replace external collaborators; return a container of mocks."""
    monkeypatch.setenv("TCONNECT_EMAIL", "test@example.com")
    monkeypatch.setenv("TCONNECT_PASSWORD", "secret")

    container = MagicMock()
    container.get_api = MagicMock(return_value=MagicMock(name="api"))
    container.get_pump_metadata = MagicMock(
        return_value=[_make_pump("SN_A", 1, "2023-01-01", "2024-06-30")]
    )
    container.fetch_pump_events = MagicMock(return_value=([MagicMock()], "2024-06-29"))
    container.build_all = MagicMock(return_value=_enriched_dfs())
    container.load_config = MagicMock(return_value=mock_config)

    storage = MagicMock()
    storage.get_fetch_state.return_value = None
    storage.upsert_table.return_value = UpsertResult(
        rows_received=1, rows_inserted=1, rows_skipped=0, elapsed_seconds=0.01,
    )
    mock_conn = MagicMock()

    container.connect_storage = MagicMock(return_value=(storage, mock_conn))
    container.storage = storage
    container.mock_conn = mock_conn

    monkeypatch.setattr(sync, "get_api", container.get_api)
    monkeypatch.setattr(sync, "get_pump_metadata", container.get_pump_metadata)
    monkeypatch.setattr(sync, "fetch_pump_events", container.fetch_pump_events)
    monkeypatch.setattr(sync, "build_all", container.build_all)
    monkeypatch.setattr(sync, "load_config", container.load_config)
    monkeypatch.setattr(sync, "_connect_storage", container.connect_storage)

    return container


class TestEnrichmentBeforeUpsert:
    def test_build_all_receives_non_none_config(self, patched_sync, mock_config):
        sync.run_sync(dry_run=False)

        patched_sync.build_all.assert_called()
        _events, serial, config = patched_sync.build_all.call_args.args
        assert config is mock_config
        assert config is not None

    def test_upsert_requests_includes_bolus_category(self, patched_sync):
        sync.run_sync(dry_run=False)

        upsert_calls = {
            call.args[0]: call.args[1]
            for call in patched_sync.storage.upsert_table.call_args_list
        }
        assert "requests" in upsert_calls
        assert "bolus_category" in upsert_calls["requests"].columns

    def test_upserts_site_issues_and_cgm_gaps(self, patched_sync):
        sync.run_sync(dry_run=False)

        upserted = {call.args[0] for call in patched_sync.storage.upsert_table.call_args_list}
        assert "site_issues" in upserted
        assert "cgm_gaps" in upserted


class TestDryRun:
    def test_dry_run_skips_upsert_and_fetch_state(self, patched_sync):
        sync.run_sync(dry_run=True)

        patched_sync.storage.upsert_table.assert_not_called()
        patched_sync.storage.set_fetch_state.assert_not_called()
        patched_sync.storage.set_pipeline_version.assert_not_called()
        patched_sync.build_all.assert_called()


class TestIncrementalWindow:
    def test_no_fetch_state_uses_full_range(self, patched_sync):
        patched_sync.storage.get_fetch_state.return_value = None

        sync.run_sync(dry_run=False)

        call = patched_sync.fetch_pump_events.call_args
        assert call.args[2] == "2023-01-01"
        assert call.args[3] == "2024-06-30"

    def test_prior_state_overlaps_one_day(self, patched_sync):
        patched_sync.storage.get_fetch_state.return_value = FetchState(
            source_id="SN_A",
            last_cursor=None,
            last_fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
            payload={"last_successful_chunk_end": "2024-06-20"},
            source_kind="tconnectsync",
        )

        sync.run_sync(dry_run=False)

        call = patched_sync.fetch_pump_events.call_args
        assert call.args[2] == "2024-06-19"
        assert call.args[3] == "2024-06-30"


class TestWindowOverride:
    """Explicit --start/--end window bounds (gap-fill without re-pulling history)."""

    def test_start_override_ignores_fetch_state(self):
        pump = _make_pump("SN_A", 1, "2021-11-12", "2026-06-20")
        state = FetchState(
            source_id="SN_A",
            last_cursor=None,
            last_fetched_at=datetime(2024, 1, 1, tzinfo=UTC),
            payload={"last_successful_chunk_end": "2024-06-20"},
            source_kind="tconnectsync",
        )
        start, end = sync.compute_fetch_window(pump, state, start_override="2026-04-15")
        assert start == "2026-04-15"
        assert end == "2026-06-20"

    def test_start_override_with_no_fetch_state(self):
        pump = _make_pump("SN_A", 1, "2021-11-12", "2026-06-20")
        start, _end = sync.compute_fetch_window(pump, None, start_override="2026-04-15")
        assert start == "2026-04-15"

    def test_end_override(self):
        pump = _make_pump("SN_A", 1, "2021-11-12", "2026-06-20")
        _start, end = sync.compute_fetch_window(
            pump, None, start_override="2026-04-15", end_override="2026-05-01"
        )
        assert end == "2026-05-01"

    def test_run_sync_threads_start_to_fetch(self, patched_sync):
        sync.run_sync(dry_run=False, start="2026-04-15")

        call = patched_sync.fetch_pump_events.call_args
        assert call.args[2] == "2026-04-15"

    def test_dry_run_honors_start_override(self, patched_sync):
        # dry-run never opens the DB (no fetch_state), but an explicit --start
        # must still bound the preview window instead of full history.
        sync.run_sync(dry_run=True, start="2026-04-15")

        call = patched_sync.fetch_pump_events.call_args
        assert call.args[2] == "2026-04-15"


class TestArgParsing:
    def test_start_end_parsed(self):
        args = sync.parse_args(["--start", "2026-04-15", "--end", "2026-05-01"])
        assert args.start == "2026-04-15"
        assert args.end == "2026-05-01"

    def test_invalid_start_rejected(self):
        with pytest.raises(SystemExit):
            sync.parse_args(["--start", "not-a-date"])


class TestConnectionHardening:
    """The direct connection must not idle-in-transaction across the long fetch.

    A bookmark SELECT (get_fetch_state, which does not commit) followed by a
    multi-minute tconnectsync fetch left the connection idle-in-transaction past
    idle_in_transaction_session_timeout='5min' (migration 0002), dropping the
    SSL connection mid-sync. autocommit ends each statement's transaction
    immediately; keepalives guard against network-level idle drops.
    """

    def test_connect_storage_sets_autocommit(self, monkeypatch):
        monkeypatch.setenv(
            "SUPABASE_DB_URL",
            "postgresql://u@db.example.supabase.co:5432/postgres",
        )
        fake_conn = MagicMock()
        fake_psycopg2 = MagicMock()
        fake_psycopg2.connect.return_value = fake_conn
        monkeypatch.setitem(sys.modules, "psycopg2", fake_psycopg2)

        _storage, conn = sync._connect_storage()

        assert conn is fake_conn
        assert fake_conn.autocommit is True

    def test_connect_storage_enables_keepalives(self, monkeypatch):
        monkeypatch.setenv(
            "SUPABASE_DB_URL",
            "postgresql://u@db.example.supabase.co:5432/postgres",
        )
        fake_conn = MagicMock()
        fake_psycopg2 = MagicMock()
        fake_psycopg2.connect.return_value = fake_conn
        monkeypatch.setitem(sys.modules, "psycopg2", fake_psycopg2)

        sync._connect_storage()

        kwargs = fake_psycopg2.connect.call_args.kwargs
        assert kwargs.get("keepalives") == 1


class TestOnlySerial:
    def test_only_filters_pumps(self, patched_sync):
        patched_sync.get_pump_metadata.return_value = [
            _make_pump("SN_A", 1, "2023-01-01", "2024-06-30"),
            _make_pump("SN_B", 2, "2024-01-01", "2024-12-31"),
        ]

        sync.run_sync(dry_run=False, only_serial="SN_B")

        assert patched_sync.fetch_pump_events.call_count == 1
        assert patched_sync.fetch_pump_events.call_args.kwargs["pump_serial"] == "SN_B"


class TestPipelineVersion:
    def test_sets_pipeline_version_after_success(self, patched_sync):
        from ingestion.pipeline_version import PIPELINE_VERSION

        sync.run_sync(dry_run=False)

        patched_sync.storage.set_pipeline_version.assert_called_once_with(
            PIPELINE_VERSION
        )


class TestFetchStatePersistence:
    def test_set_fetch_state_after_success(self, patched_sync):
        sync.run_sync(dry_run=False)

        patched_sync.storage.set_fetch_state.assert_called()
        _serial, state = patched_sync.storage.set_fetch_state.call_args.args
        assert state.source_kind == "tconnectsync"
        assert state.payload["last_successful_chunk_end"] == "2024-06-29"


class TestAuthFailure:
    def test_auth_failure_exits_nonzero(self, patched_sync):
        patched_sync.get_api.side_effect = RuntimeError("auth failed")

        assert sync.run_sync(dry_run=False) != 0
