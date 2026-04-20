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
def unsorted_pump_metadata():
    """Three pump dicts in a non-chronological order."""
    return [
        {
            "serialNumber": "B_MIDDLE",
            "tconnectDeviceId": 2,
            "minDateWithEvents": "2024-06-01T00:00:00",
            "maxDateWithEvents": "2024-12-31T00:00:00",
        },
        {
            "serialNumber": "C_NEWEST",
            "tconnectDeviceId": 3,
            "minDateWithEvents": "2025-01-15T00:00:00",
            "maxDateWithEvents": "2026-03-01T00:00:00",
        },
        {
            "serialNumber": "A_OLDEST",
            "tconnectDeviceId": 1,
            "minDateWithEvents": "2023-02-10T00:00:00",
            "maxDateWithEvents": "2024-05-20T00:00:00",
        },
    ]


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
    def test_returns_pumps_sorted_oldest_first(self, fake_api, unsorted_pump_metadata):
        fake_api.pump_event_metadata.return_value = unsorted_pump_metadata

        result = client.get_pump_metadata(fake_api)

        serials = [p["serialNumber"] for p in result]
        assert serials == ["A_OLDEST", "B_MIDDLE", "C_NEWEST"]

    def test_calls_pump_event_metadata_once(self, fake_api, unsorted_pump_metadata):
        fake_api.pump_event_metadata.return_value = unsorted_pump_metadata

        client.get_pump_metadata(fake_api)

        fake_api.pump_event_metadata.assert_called_once_with()

    def test_empty_metadata_returns_empty_list(self, fake_api):
        fake_api.pump_event_metadata.return_value = []

        assert client.get_pump_metadata(fake_api) == []


# ---------------------------------------------------------------------------
# fetch_pump_events — chunking semantics
# ---------------------------------------------------------------------------


class TestFetchPumpEventsChunking:
    def test_splits_range_into_correct_chunks(self, fake_api):
        """70 days with chunk_days=30 ⇒ 3 chunks."""
        fake_api.pump_events.return_value = iter([])

        events, last_end = client.fetch_pump_events(
            fake_api,
            device_id=42,
            start_date="2026-01-01",
            end_date="2026-03-12",  # 70 days after 2026-01-01
            pump_serial="SN1",
            chunk_days=30,
        )

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
        events, last_end = client.fetch_pump_events(
            fake_api,
            device_id=1,
            start_date="2026-01-01",
            end_date="2026-01-01",
            pump_serial="SN1",
        )

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

        events, last_end = client.fetch_pump_events(
            fake_api,
            device_id=1,
            start_date="2026-01-01",
            end_date="2026-02-15",
            pump_serial="SN1",
            chunk_days=30,
        )

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
          - last_successful_end reflects the last SUCCESS, not the failure
        """
        ev_a, ev_c = MagicMock(name="event_a"), MagicMock(name="event_c")
        fake_api.pump_events.side_effect = [
            iter([ev_a]),
            RuntimeError("boom"),
            iter([ev_c]),
        ]

        events, last_end = client.fetch_pump_events(
            fake_api,
            device_id=1,
            start_date="2026-01-01",
            end_date="2026-03-12",
            pump_serial="SN1",
            chunk_days=30,
        )

        assert fake_api.pump_events.call_count == 3
        assert events == [ev_a, ev_c]
        # Last success is the third chunk whose end is the clamped end date.
        assert last_end == "2026-03-12"

    def test_last_successful_end_when_final_chunk_fails(self, fake_api):
        """If only the last chunk fails, last_successful_end is the previous chunk's end."""
        ev_a, ev_b = MagicMock(), MagicMock()
        fake_api.pump_events.side_effect = [
            iter([ev_a]),
            iter([ev_b]),
            RuntimeError("boom"),
        ]

        events, last_end = client.fetch_pump_events(
            fake_api,
            device_id=1,
            start_date="2026-01-01",
            end_date="2026-03-12",
            pump_serial="SN1",
            chunk_days=30,
        )

        assert events == [ev_a, ev_b]
        # Second chunk's max_date (30 days after 2026-01-31 = 2026-03-02)
        assert last_end == "2026-03-02"

    def test_all_chunks_fail_returns_empty_events_and_none_last_end(self, fake_api):
        fake_api.pump_events.side_effect = RuntimeError("boom")

        events, last_end = client.fetch_pump_events(
            fake_api,
            device_id=1,
            start_date="2026-01-01",
            end_date="2026-02-15",
            pump_serial="SN1",
            chunk_days=30,
        )

        assert events == []
        assert last_end is None

    def test_first_chunk_fails_last_end_none_until_success(self, fake_api):
        ev_b = MagicMock()
        fake_api.pump_events.side_effect = [
            RuntimeError("boom"),
            iter([ev_b]),
        ]

        events, last_end = client.fetch_pump_events(
            fake_api,
            device_id=1,
            start_date="2026-01-01",
            end_date="2026-02-15",
            pump_serial="SN1",
            chunk_days=30,
        )

        assert events == [ev_b]
        assert last_end == "2026-02-15"
