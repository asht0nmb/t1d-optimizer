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
from ingestion import redaction, sync_health
from ingestion.client import FetchResult
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


@pytest.fixture(autouse=True)
def _no_real_network_or_state(monkeypatch):
    """Hard guard: no test in this module may reach Telegram, and the global
    redaction registry never leaks between tests."""
    def _boom(*a, **k):
        raise AssertionError("real network call attempted in test")

    monkeypatch.setattr(sync_health.requests, "post", _boom)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    redaction.clear_secrets()
    yield
    redaction.clear_secrets()


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
    container.fetch_pump_events = MagicMock(
        return_value=FetchResult([MagicMock()], "2024-06-29", chunks_total=1)
    )
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
    container.send_telegram = MagicMock(return_value=True)
    monkeypatch.setattr(sync, "send_telegram", container.send_telegram)

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


# ---------------------------------------------------------------------------
# Fail loud + bookmark correctness
# ---------------------------------------------------------------------------


def _saved_payloads(patched_sync) -> list[dict]:
    return [c.args[1].payload for c in patched_sync.storage.set_fetch_state.call_args_list]


def _state(payload: dict) -> FetchState:
    return FetchState(
        source_id="SN_A",
        last_cursor=None,
        last_fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
        payload=payload,
        source_kind="tconnectsync",
    )


class TestFailLoud:
    def test_clean_run_exits_zero(self, patched_sync):
        assert sync.run_sync(dry_run=False) == 0

    def test_any_failed_chunk_exits_nonzero_after_persisting(self, patched_sync):
        patched_sync.fetch_pump_events.return_value = FetchResult(
            [MagicMock()], "2024-06-01", chunks_total=3, chunks_failed=1,
            failed_ranges=["2024-06-01 → 2024-07-01"],
        )

        assert sync.run_sync(dry_run=False) != 0

        # What succeeded was still persisted before the non-zero exit.
        patched_sync.storage.upsert_table.assert_called()
        patched_sync.storage.set_fetch_state.assert_called()
        patched_sync.storage.set_pipeline_version.assert_called_once()

    def test_all_chunks_failed_no_events_exits_nonzero(self, patched_sync):
        patched_sync.fetch_pump_events.return_value = FetchResult(
            [], None, chunks_total=2, chunks_failed=2,
        )

        assert sync.run_sync(dry_run=False) != 0
        patched_sync.storage.set_fetch_state.assert_not_called()

    def test_dry_run_with_failed_chunks_also_nonzero(self, patched_sync):
        patched_sync.fetch_pump_events.return_value = FetchResult(
            [], None, chunks_total=1, chunks_failed=1,
        )
        assert sync.run_sync(dry_run=True) != 0

    def test_failure_in_one_pump_does_not_block_the_other(self, patched_sync):
        patched_sync.get_pump_metadata.return_value = [
            _make_pump("SN_A", 1, "2023-01-01", "2024-06-30"),
            _make_pump("SN_B", 2, "2024-01-01", "2024-12-31"),
        ]
        patched_sync.fetch_pump_events.side_effect = [
            FetchResult([], None, chunks_total=1, chunks_failed=1),
            FetchResult([MagicMock()], "2024-12-30", chunks_total=1),
        ]

        assert sync.run_sync(dry_run=False) != 0

        serials = [c.args[0] for c in patched_sync.storage.set_fetch_state.call_args_list]
        assert serials == ["SN_B"]


class TestBookmarkContiguity:
    def test_bookmark_stops_before_first_failed_chunk(self, patched_sync):
        # A later chunk succeeded (its events are kept) but the bookmark is
        # only the end of the contiguous successful prefix.
        patched_sync.fetch_pump_events.return_value = FetchResult(
            [MagicMock(), MagicMock()], "2024-05-01", chunks_total=3, chunks_failed=1,
        )
        sync.run_sync(dry_run=False)
        assert _saved_payloads(patched_sync)[-1]["last_successful_chunk_end"] == "2024-05-01"

    def test_first_chunk_failed_keeps_previous_bookmark(self, patched_sync):
        patched_sync.storage.get_fetch_state.return_value = _state(
            {"last_successful_chunk_end": "2024-05-20"}
        )
        patched_sync.fetch_pump_events.return_value = FetchResult(
            [MagicMock()], None, chunks_total=2, chunks_failed=1,
        )

        assert sync.run_sync(dry_run=False) != 0

        payload = _saved_payloads(patched_sync)[-1]
        assert payload["last_successful_chunk_end"] == "2024-05-20"

    def test_first_chunk_failed_no_prior_bookmark_omits_key(self, patched_sync):
        patched_sync.fetch_pump_events.return_value = FetchResult(
            [MagicMock()], None, chunks_total=2, chunks_failed=1,
        )
        sync.run_sync(dry_run=False)
        assert "last_successful_chunk_end" not in _saved_payloads(patched_sync)[-1]

    def test_bookmark_never_moves_backward(self, patched_sync):
        patched_sync.storage.get_fetch_state.return_value = _state(
            {"last_successful_chunk_end": "2024-06-20"}
        )
        patched_sync.fetch_pump_events.return_value = FetchResult(
            [MagicMock()], "2024-05-01", chunks_total=1,
        )
        sync.run_sync(dry_run=False, start="2024-04-01")
        assert _saved_payloads(patched_sync)[-1]["last_successful_chunk_end"] == "2024-06-20"

    def test_bookmark_advances_when_all_ok(self, patched_sync):
        patched_sync.storage.get_fetch_state.return_value = _state(
            {"last_successful_chunk_end": "2024-05-20"}
        )
        sync.run_sync(dry_run=False)
        assert _saved_payloads(patched_sync)[-1]["last_successful_chunk_end"] == "2024-06-29"


# ---------------------------------------------------------------------------
# Staleness alerts
# ---------------------------------------------------------------------------

NOW = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)


def _pump_with_upload(serial: str, max_date: str, hours_ago: float | None) -> dict:
    pump = _make_pump(serial, "uuid-" + serial, "2023-01-01", max_date)
    if hours_ago is not None:
        pump["lastUploadDate"] = (NOW - timedelta(hours=hours_ago)).isoformat()
    return pump


def _cgm_basal_dfs(cgm_hours_ago, basal_hours_ago) -> dict[str, pd.DataFrame]:
    dfs = _enriched_dfs()
    dfs["cgm"] = pd.DataFrame(
        {
            "pump_serial": "SN_AAAA",
            "seqnum": range(len(cgm_hours_ago)),
            "timestamp": [NOW - timedelta(hours=h) for h in cgm_hours_ago],
        }
    )
    dfs["basal"] = pd.DataFrame(
        {
            "pump_serial": "SN_AAAA",
            "rate": 0.8,
            "timestamp": [NOW - timedelta(hours=h) for h in basal_hours_ago],
        }
    )
    return dfs


@pytest.fixture
def at_now(monkeypatch):
    monkeypatch.setattr(sync, "_utcnow", lambda: NOW)


class TestUploadStalenessAlert:
    def test_stale_upload_sends_specific_alert_exit_zero(self, patched_sync, at_now):
        patched_sync.get_pump_metadata.return_value = [
            _pump_with_upload("SN_AAAA", "2026-10-13", 50)
        ]
        assert sync.run_sync(dry_run=False) == 0
        patched_sync.send_telegram.assert_called_once()
        text = patched_sync.send_telegram.call_args.args[0]
        assert "~50h" in text and "t:connect app" in text

    def test_fresh_upload_sends_nothing(self, patched_sync, at_now):
        patched_sync.get_pump_metadata.return_value = [
            _pump_with_upload("SN_AAAA", "2026-10-13", 2)
        ]
        assert sync.run_sync(dry_run=False) == 0
        patched_sync.send_telegram.assert_not_called()

    def test_telegram_delivery_failure_exits_nonzero(self, patched_sync, at_now):
        patched_sync.get_pump_metadata.return_value = [
            _pump_with_upload("SN_AAAA", "2026-10-13", 50)
        ]
        patched_sync.send_telegram.return_value = False
        assert sync.run_sync(dry_run=False) != 0

    def test_dry_run_never_sends(self, patched_sync, at_now):
        patched_sync.get_pump_metadata.return_value = [
            _pump_with_upload("SN_AAAA", "2026-10-13", 50)
        ]
        assert sync.run_sync(dry_run=True) == 0
        patched_sync.send_telegram.assert_not_called()

    def test_threshold_comes_from_config(self, patched_sync, at_now, mock_config):
        mock_config["sync_health"] = {"max_pump_upload_age_hours": 100}
        patched_sync.get_pump_metadata.return_value = [
            _pump_with_upload("SN_AAAA", "2026-10-13", 50)
        ]
        sync.run_sync(dry_run=False)
        patched_sync.send_telegram.assert_not_called()

    def test_active_pump_is_the_one_with_latest_data(self, patched_sync, at_now):
        # Old pump (stale upload) must not alert; the active pump is fresh.
        patched_sync.get_pump_metadata.return_value = [
            _pump_with_upload("SN_OLD1", "2024-01-01", 5000),
            _pump_with_upload("SN_NEW1", "2026-10-13", 1),
        ]
        sync.run_sync(dry_run=False)
        patched_sync.send_telegram.assert_not_called()

    def test_active_pump_chosen_before_only_filter(self, patched_sync, at_now):
        patched_sync.get_pump_metadata.return_value = [
            _pump_with_upload("SN_OLD1", "2024-01-01", 5000),
            _pump_with_upload("SN_NEW1", "2026-10-13", 1),
        ]
        sync.run_sync(dry_run=False, only_serial="SN_OLD1")
        patched_sync.send_telegram.assert_not_called()

    def test_alert_sent_even_if_sync_crashes(self, patched_sync, at_now):
        patched_sync.get_pump_metadata.return_value = [
            _pump_with_upload("SN_AAAA", "2026-10-13", 50)
        ]
        patched_sync.fetch_pump_events.side_effect = RuntimeError("boom")
        assert sync.run_sync(dry_run=False) != 0
        patched_sync.send_telegram.assert_called_once()

    def test_alert_text_has_no_identifiers(self, patched_sync, at_now):
        pump = _pump_with_upload("SN_AAAA", "2026-10-13", 50)
        patched_sync.get_pump_metadata.return_value = [pump]
        sync.run_sync(dry_run=False)
        text = patched_sync.send_telegram.call_args.args[0]
        assert "SN_AAAA" not in text and pump["tconnectDeviceId"] not in text


class TestPumpCgmGapAlert:
    def test_gap_on_active_pump_alerts(self, patched_sync, at_now):
        patched_sync.get_pump_metadata.return_value = [
            _pump_with_upload("SN_AAAA", "2026-10-13", 1)
        ]
        patched_sync.build_all.return_value = _cgm_basal_dfs([], [30, 20, 10, 0])
        assert sync.run_sync(dry_run=False) == 0
        patched_sync.send_telegram.assert_called_once()
        assert "G7" in patched_sync.send_telegram.call_args.args[0]

    def test_no_gap_no_alert(self, patched_sync, at_now):
        patched_sync.get_pump_metadata.return_value = [
            _pump_with_upload("SN_AAAA", "2026-10-13", 1)
        ]
        patched_sync.build_all.return_value = _cgm_basal_dfs([3, 2, 1, 0], [3, 2, 1, 0])
        sync.run_sync(dry_run=False)
        patched_sync.send_telegram.assert_not_called()

    def test_gap_check_skipped_for_non_active_pump(self, patched_sync, at_now):
        patched_sync.get_pump_metadata.return_value = [
            _pump_with_upload("SN_OLD1", "2024-01-01", 1),
            _pump_with_upload("SN_NEW1", "2026-10-13", 1),
        ]
        old_gap = _cgm_basal_dfs([], [30, 0])
        ok = _cgm_basal_dfs([1, 0], [1, 0])
        patched_sync.build_all.side_effect = [old_gap, ok]
        sync.run_sync(dry_run=False)
        patched_sync.send_telegram.assert_not_called()

    def test_gap_dry_run_does_not_send(self, patched_sync, at_now):
        patched_sync.get_pump_metadata.return_value = [
            _pump_with_upload("SN_AAAA", "2026-10-13", 1)
        ]
        patched_sync.build_all.return_value = _cgm_basal_dfs([], [30, 0])
        assert sync.run_sync(dry_run=True) == 0
        patched_sync.send_telegram.assert_not_called()

    def test_both_alerts_two_messages(self, patched_sync, at_now):
        patched_sync.get_pump_metadata.return_value = [
            _pump_with_upload("SN_AAAA", "2026-10-13", 50)
        ]
        patched_sync.build_all.return_value = _cgm_basal_dfs([], [30, 0])
        sync.run_sync(dry_run=False)
        assert patched_sync.send_telegram.call_count == 2


# ---------------------------------------------------------------------------
# Redaction wiring
# ---------------------------------------------------------------------------


class TestRedactionWiring:
    def test_serials_registered_and_masked_in_logs(self, patched_sync, caplog):
        serial = "SN_SECRET_12345678"
        patched_sync.get_pump_metadata.return_value = [
            _make_pump(serial, 1, "2023-01-01", "2024-06-30")
        ]
        with caplog.at_level("INFO"):
            sync.run_sync(dry_run=False)
        assert redaction.redact(f"x {serial} y") == "x …5678 y"
        assert all(serial not in r.getMessage() for r in caplog.records)

    def test_add_mask_emitted_in_actions(self, patched_sync, monkeypatch, capsys):
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        serial = "SN_SECRET_12345678"
        patched_sync.get_pump_metadata.return_value = [
            _make_pump(serial, "uuid-device-1", "2023-01-01", "2024-06-30")
        ]
        sync.run_sync(dry_run=True)
        out = capsys.readouterr().out
        assert f"::add-mask::{serial}" in out
        assert "::add-mask::uuid-device-1" in out

    def test_no_add_mask_outside_actions(self, patched_sync, capsys):
        sync.run_sync(dry_run=True)
        assert "::add-mask::" not in capsys.readouterr().out

    def test_only_serial_not_found_logs_masked(self, patched_sync, caplog):
        with caplog.at_level("INFO"):
            rc = sync.run_sync(dry_run=False, only_serial="ABCDEFGH99999")
        assert rc != 0
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "ABCDEFGH99999" not in text
        assert "…9999" in text

    def test_main_installs_redacting_logging(self, monkeypatch):
        called = {}
        monkeypatch.setattr(
            sync.redaction, "configure_logging", lambda **k: called.setdefault("ok", True)
        )
        monkeypatch.setattr(sync, "run_sync", lambda **k: 0)
        assert sync.main(["--dry-run"]) == 0
        assert called.get("ok") is True


class TestPerPumpIsolation:
    def test_crash_in_one_pump_does_not_abort_the_rest(self, patched_sync, at_now):
        patched_sync.get_pump_metadata.return_value = [
            _pump_with_upload("SN_OLD1", "2024-01-01", 1),
            _pump_with_upload("SN_NEW1", "2026-10-13", 1),
        ]
        patched_sync.fetch_pump_events.side_effect = [
            RuntimeError("boom"),
            FetchResult([MagicMock()], "2026-10-12", chunks_total=1),
        ]
        # Active pump (second) has a CGM gap: its alert must still go out.
        patched_sync.build_all.return_value = _cgm_basal_dfs([], [30, 0])

        assert sync.run_sync(dry_run=False) != 0

        serials = [c.args[0] for c in patched_sync.storage.set_fetch_state.call_args_list]
        assert serials == ["SN_NEW1"]
        patched_sync.send_telegram.assert_called_once()
        assert "G7" in patched_sync.send_telegram.call_args.args[0]
        patched_sync.mock_conn.close.assert_called_once()

    def test_crash_log_is_redacted_and_exit_nonzero(self, patched_sync, caplog):
        patched_sync.get_pump_metadata.return_value = [
            _make_pump("SN_SECRET_12345678", 1, "2023-01-01", "2024-06-30")
        ]
        patched_sync.fetch_pump_events.side_effect = RuntimeError("boom SN_SECRET_12345678")
        assert sync.run_sync(dry_run=False) != 0
        formatter = redaction.RedactingFormatter("%(message)s")
        for rec in caplog.records:
            assert "SN_SECRET_12345678" not in formatter.format(rec)
