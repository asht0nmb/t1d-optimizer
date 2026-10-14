"""Tests for ingestion/client.py — auth, pump metadata, and chunked event fetching."""

from datetime import timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from ingestion import client

PST = timezone(timedelta(hours=-8))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_api():
    """A MagicMock standing in for TandemSourceApi."""
    return MagicMock()


@pytest.fixture
def bff_pumper_response():
    """A get_pumper() response (tconnectsync v3 BFF shape): three pumps in
    non-chronological order, plus one never-uploaded pump with no data
    range."""
    return {
        "pumps": [
            {
                "assignmentId": "uuid-b-middle",
                "serialNumber": "B_MIDDLE",
                "availableDataRange": {
                    "start": "2024-06-01T00:00:00",
                    "end": "2024-12-31T00:00:00",
                },
            },
            {
                "assignmentId": "uuid-c-newest",
                "serialNumber": "C_NEWEST",
                "availableDataRange": {
                    "start": "2025-01-15T00:00:00",
                    "end": "2026-03-01T00:00:00",
                },
            },
            {
                "assignmentId": "uuid-a-oldest",
                "serialNumber": "A_OLDEST",
                "availableDataRange": {
                    "start": "2023-02-10T00:00:00",
                    "end": "2024-05-20T00:00:00",
                },
            },
            {
                "assignmentId": "uuid-never-uploaded",
                "serialNumber": "D_NEVER_UPLOADED",
                "availableDataRange": {"start": None, "end": None},
            },
        ]
    }


# ---------------------------------------------------------------------------
# get_api — authentication
# ---------------------------------------------------------------------------


class TestGetApi:
    def test_reads_env_vars_and_passes_to_api_constructor(self, monkeypatch):
        monkeypatch.setenv("TCONNECT_EMAIL", "user@example.com")
        monkeypatch.setenv("TCONNECT_PASSWORD", "s3cret")

        with patch.object(client, "TandemSourceApi") as fake_ctor, \
             patch.object(client, "load_dotenv") as fake_load_dotenv:
            fake_ctor.return_value = MagicMock(name="api_instance")
            result = client.get_api()

        fake_load_dotenv.assert_called_once()
        fake_ctor.assert_called_once_with(
            email="user@example.com",
            password="s3cret",
        )
        assert result is fake_ctor.return_value

    def test_missing_env_vars_passes_none_through(self, monkeypatch):
        """
        NOTE: potential issue — when TCONNECT_EMAIL/PASSWORD are unset,
        get_api() silently passes ``None`` into TandemSourceApi rather than
        raising a clear configuration error. This test pins the current
        behavior so any future hardening is an intentional change.
        """
        monkeypatch.delenv("TCONNECT_EMAIL", raising=False)
        monkeypatch.delenv("TCONNECT_PASSWORD", raising=False)

        with patch.object(client, "TandemSourceApi") as fake_ctor, \
             patch.object(client, "load_dotenv"):
            fake_ctor.return_value = MagicMock()
            client.get_api()

        fake_ctor.assert_called_once_with(email=None, password=None)


# ---------------------------------------------------------------------------
# get_pump_metadata — sorting
# ---------------------------------------------------------------------------


class TestGetPumpMetadata:
    def test_returns_pumps_sorted_oldest_first(self, fake_api, bff_pumper_response):
        fake_api.get_pumper.return_value = bff_pumper_response

        result = client.get_pump_metadata(fake_api)

        serials = [p["serialNumber"] for p in result]
        assert serials == ["A_OLDEST", "B_MIDDLE", "C_NEWEST"]

    def test_calls_get_pumper_once(self, fake_api, bff_pumper_response):
        fake_api.get_pumper.return_value = bff_pumper_response

        client.get_pump_metadata(fake_api)

        fake_api.get_pumper.assert_called_once_with()

    def test_empty_metadata_returns_empty_list(self, fake_api):
        fake_api.get_pumper.return_value = {"pumps": []}

        assert client.get_pump_metadata(fake_api) == []

    def test_missing_pumps_key_returns_empty_list(self, fake_api):
        fake_api.get_pumper.return_value = {}

        assert client.get_pump_metadata(fake_api) == []

    def test_normalizes_v3_field_names_to_legacy_names(self, fake_api, bff_pumper_response):
        """v3's get_pumper() nests pumps and renames tconnectDeviceId ->
        assignmentId, minDateWithEvents/maxDateWithEvents ->
        availableDataRange.start/end. Downstream callers (sync script, CI
        smoke test) still expect the legacy field names, so get_pump_metadata
        must translate."""
        fake_api.get_pumper.return_value = bff_pumper_response

        result = client.get_pump_metadata(fake_api)

        oldest = next(p for p in result if p["serialNumber"] == "A_OLDEST")
        assert oldest["tconnectDeviceId"] == "uuid-a-oldest"
        assert oldest["minDateWithEvents"] == "2023-02-10T00:00:00"
        assert oldest["maxDateWithEvents"] == "2024-05-20T00:00:00"

    def test_skips_pumps_with_no_data_range(self, fake_api, bff_pumper_response):
        """A never-uploaded/retired pump has availableDataRange.start/end ==
        None — there's nothing to sync for it, and the legacy code path never
        had to handle a None minDateWithEvents, so skip it defensively."""
        fake_api.get_pumper.return_value = bff_pumper_response

        result = client.get_pump_metadata(fake_api)

        serials = [p["serialNumber"] for p in result]
        assert "D_NEVER_UPLOADED" not in serials
        assert len(result) == 3


# ---------------------------------------------------------------------------
# fetch_pump_events — chunking semantics
# ---------------------------------------------------------------------------


class TestFetchPumpEventsChunking:
    def test_splits_range_into_correct_chunks(self, fake_api):
        """70 days with chunk_days=30 ⇒ 3 chunks."""
        fake_api.pump_events.return_value = iter([])

        result = client.fetch_pump_events(
            fake_api,
            device_id=42,
            start_date="2026-01-01",
            end_date="2026-03-12",  # 70 days after 2026-01-01
            pump_serial="SN1",
            chunk_days=30,
        )
        events, last_end = result.events, result.last_successful_end

        assert fake_api.pump_events.call_count == 3
        calls = fake_api.pump_events.call_args_list
        assert calls[0].kwargs["min_date"] == "2026-01-01"
        assert calls[0].kwargs["max_date"] == "2026-01-31"
        assert calls[1].kwargs["min_date"] == "2026-01-31"
        assert calls[1].kwargs["max_date"] == "2026-03-02"
        assert calls[2].kwargs["min_date"] == "2026-03-02"
        assert calls[2].kwargs["max_date"] == "2026-03-12"
        assert events == []
        assert last_end == "2026-03-12"

    def test_chunk_boundaries_are_contiguous_and_shared(self, fake_api):
        """
        Pins current behavior: adjacent chunks share their boundary date
        (chunk N's max_date == chunk N+1's min_date). This is end-exclusive
        on the loop condition but the API may or may not treat the
        boundary date as inclusive — storage dedup handles any overlap.
        """
        fake_api.pump_events.return_value = iter([])

        client.fetch_pump_events(
            fake_api,
            device_id=1,
            start_date="2026-01-01",
            end_date="2026-02-15",
            pump_serial="SN1",
            chunk_days=30,
        )

        calls = fake_api.pump_events.call_args_list
        for prev, nxt in zip(calls, calls[1:]):
            assert prev.kwargs["max_date"] == nxt.kwargs["min_date"]

    def test_short_range_single_chunk(self, fake_api):
        fake_api.pump_events.return_value = iter([])

        client.fetch_pump_events(
            fake_api,
            device_id=1,
            start_date="2026-01-01",
            end_date="2026-01-05",
            pump_serial="SN1",
            chunk_days=30,
        )

        assert fake_api.pump_events.call_count == 1
        call = fake_api.pump_events.call_args
        assert call.kwargs["min_date"] == "2026-01-01"
        assert call.kwargs["max_date"] == "2026-01-05"

    def test_zero_length_range_produces_no_calls(self, fake_api):
        """start == end ⇒ no chunks, no API calls, last_end is None."""
        result = client.fetch_pump_events(
            fake_api,
            device_id=1,
            start_date="2026-01-01",
            end_date="2026-01-01",
            pump_serial="SN1",
        )
        events, last_end = result.events, result.last_successful_end

        assert fake_api.pump_events.call_count == 0
        assert events == []
        assert last_end is None

    def test_final_chunk_clamped_to_end(self, fake_api):
        """chunk_days=30 but only 5 days left → last chunk max_date == end."""
        fake_api.pump_events.return_value = iter([])

        client.fetch_pump_events(
            fake_api,
            device_id=1,
            start_date="2026-01-01",
            end_date="2026-02-05",
            pump_serial="SN1",
            chunk_days=30,
        )

        calls = fake_api.pump_events.call_args_list
        assert len(calls) == 2
        assert calls[-1].kwargs["max_date"] == "2026-02-05"


# ---------------------------------------------------------------------------
# fetch_pump_events — API call shape
# ---------------------------------------------------------------------------


class TestFetchPumpEventsApiCall:
    def test_passes_fetch_all_event_types_true(self, fake_api):
        fake_api.pump_events.return_value = iter([])

        client.fetch_pump_events(
            fake_api,
            device_id=7,
            start_date="2026-01-01",
            end_date="2026-01-10",
            pump_serial="SN1",
        )

        call = fake_api.pump_events.call_args
        assert call.kwargs["fetch_all_event_types"] is True

    def test_passes_device_id_positionally(self, fake_api):
        fake_api.pump_events.return_value = iter([])

        client.fetch_pump_events(
            fake_api,
            device_id=999,
            start_date="2026-01-01",
            end_date="2026-01-10",
            pump_serial="SN1",
        )

        call = fake_api.pump_events.call_args
        assert call.args[0] == 999

    def test_returns_flat_list_of_events(self, fake_api):
        """Events from multiple chunks are flattened into one list."""
        ev1, ev2, ev3 = MagicMock(), MagicMock(), MagicMock()
        fake_api.pump_events.side_effect = [
            iter([ev1, ev2]),
            iter([ev3]),
        ]

        result = client.fetch_pump_events(
            fake_api,
            device_id=1,
            start_date="2026-01-01",
            end_date="2026-02-15",
            pump_serial="SN1",
            chunk_days=30,
        )
        events, last_end = result.events, result.last_successful_end

        assert events == [ev1, ev2, ev3]
        assert last_end == "2026-02-15"


# ---------------------------------------------------------------------------
# fetch_pump_events — error handling
# ---------------------------------------------------------------------------


class TestFetchPumpEventsErrorHandling:
    def test_continues_after_middle_chunk_failure(self, fake_api):
        """
        Three chunks: success, fail, success. Verify:
          - events from successful chunks are returned
          - subsequent chunks ARE attempted (loop does not abort)
          - last_successful_end stops at the contiguous successful prefix
            (chunk 1) so the failed middle chunk is retried next run
        """
        ev_a, ev_c = MagicMock(name="event_a"), MagicMock(name="event_c")
        fake_api.pump_events.side_effect = [
            iter([ev_a]),
            RuntimeError("boom"),
            iter([ev_c]),
        ]

        result = client.fetch_pump_events(
            fake_api,
            device_id=1,
            start_date="2026-01-01",
            end_date="2026-03-12",
            pump_serial="SN1",
            chunk_days=30,
        )
        events, last_end = result.events, result.last_successful_end

        assert fake_api.pump_events.call_count == 3
        assert events == [ev_a, ev_c]
        # Bookmark must NOT jump past the failed middle chunk (chunk 1 ends
        # 2026-01-31); otherwise the gap is never retried.
        assert last_end == "2026-01-31"
        assert result.chunks_failed == 1
        assert result.chunks_total == 3
        assert result.failed_ranges == ["2026-01-31 → 2026-03-02"]

    def test_last_successful_end_when_final_chunk_fails(self, fake_api):
        """If only the last chunk fails, last_successful_end is the previous chunk's end."""
        ev_a, ev_b = MagicMock(), MagicMock()
        fake_api.pump_events.side_effect = [
            iter([ev_a]),
            iter([ev_b]),
            RuntimeError("boom"),
        ]

        result = client.fetch_pump_events(
            fake_api,
            device_id=1,
            start_date="2026-01-01",
            end_date="2026-03-12",
            pump_serial="SN1",
            chunk_days=30,
        )
        events, last_end = result.events, result.last_successful_end

        assert events == [ev_a, ev_b]
        # Second chunk's max_date (30 days after 2026-01-31 = 2026-03-02)
        assert last_end == "2026-03-02"

    def test_all_chunks_fail_returns_empty_events_and_none_last_end(self, fake_api):
        fake_api.pump_events.side_effect = RuntimeError("boom")

        result = client.fetch_pump_events(
            fake_api,
            device_id=1,
            start_date="2026-01-01",
            end_date="2026-02-15",
            pump_serial="SN1",
            chunk_days=30,
        )
        events, last_end = result.events, result.last_successful_end

        assert events == []
        assert last_end is None

    def test_first_chunk_fails_last_end_none_until_success(self, fake_api):
        ev_b = MagicMock()
        fake_api.pump_events.side_effect = [
            RuntimeError("boom"),
            iter([ev_b]),
        ]

        result = client.fetch_pump_events(
            fake_api,
            device_id=1,
            start_date="2026-01-01",
            end_date="2026-02-15",
            pump_serial="SN1",
            chunk_days=30,
        )
        events, last_end = result.events, result.last_successful_end

        assert events == [ev_b]
        # First chunk failed: no contiguous prefix, so no safe bookmark.
        assert last_end is None
        assert result.chunks_failed == 1


class TestFetchResultFailureReporting:
    def test_all_success_reports_zero_failed(self, fake_api):
        fake_api.pump_events.return_value = iter([])
        result = client.fetch_pump_events(
            fake_api, device_id=1, start_date="2026-01-01", end_date="2026-03-12",
            pump_serial="SN1", chunk_days=30,
        )
        assert result.chunks_failed == 0
        assert result.chunks_total == 3
        assert result.failed_ranges == []

    def test_all_fail_counts_every_chunk(self, fake_api):
        fake_api.pump_events.side_effect = RuntimeError("boom")
        result = client.fetch_pump_events(
            fake_api, device_id=1, start_date="2026-01-01", end_date="2026-03-12",
            pump_serial="SN1", chunk_days=30,
        )
        assert result.chunks_failed == 3
        assert result.events == []
        assert result.last_successful_end is None

    def test_failure_after_prefix_keeps_prefix_end_even_if_later_succeeds(self, fake_api):
        # ok, ok, FAIL, ok  -> bookmark = end of chunk 2
        fake_api.pump_events.side_effect = [
            iter([MagicMock()]), iter([MagicMock()]), RuntimeError("x"), iter([MagicMock()]),
        ]
        result = client.fetch_pump_events(
            fake_api, device_id=1, start_date="2026-01-01", end_date="2026-04-20",
            pump_serial="SN1", chunk_days=30,
        )
        assert result.chunks_total == 4
        assert result.chunks_failed == 1
        assert len(result.events) == 3
        assert result.last_successful_end == "2026-03-02"


class TestFetchLogsAreMasked:
    def test_full_serial_never_in_log_records(self, fake_api, caplog):
        full_serial = "90012345678"
        fake_api.pump_events.side_effect = [iter([MagicMock()]), RuntimeError("boom")]
        with caplog.at_level("INFO", logger="ingestion.client"):
            client.fetch_pump_events(
                fake_api, device_id=1, start_date="2026-01-01", end_date="2026-02-15",
                pump_serial=full_serial, chunk_days=30,
            )
        assert caplog.records
        for rec in caplog.records:
            assert full_serial not in rec.getMessage()
        assert any("…5678" in rec.getMessage() for rec in caplog.records)
