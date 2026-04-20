"""Tests for ingestion/fetch.py — orchestration of fetch → build → store."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from ingestion import fetch

PST = timezone(timedelta(hours=-8))


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_pump(serial, device_id, min_date, max_date):
    return {
        "serialNumber": serial,
        "tconnectDeviceId": device_id,
        "minDateWithEvents": f"{min_date}T00:00:00",
        "maxDateWithEvents": f"{max_date}T00:00:00",
    }


@pytest.fixture
def three_pumps():
    return [
        _make_pump("SN_A", 1, "2023-01-01", "2023-06-30"),
        _make_pump("SN_B", 2, "2024-01-01", "2024-12-31"),
        _make_pump("SN_C", 3, "2025-06-01", "2026-03-01"),
    ]


@pytest.fixture
def patched_fetch(monkeypatch, three_pumps):
    """
    Replace every external collaborator of ingestion.fetch with a MagicMock.
    Returns a SimpleNamespace-like MagicMock for easy inspection.
    """
    container = MagicMock()

    container.get_api = MagicMock(return_value=MagicMock(name="api"))
    container.get_pump_metadata = MagicMock(return_value=three_pumps)
    # Default: each fetch returns no events, last end = None
    container.fetch_pump_events = MagicMock(return_value=([], None))
    container.build_all = MagicMock(return_value={})
    container.save_df = MagicMock()
    container.load_fetch_state = MagicMock(return_value={})
    container.save_fetch_state = MagicMock()

    monkeypatch.setattr(fetch, "get_api", container.get_api)
    monkeypatch.setattr(fetch, "get_pump_metadata", container.get_pump_metadata)
    monkeypatch.setattr(fetch, "fetch_pump_events", container.fetch_pump_events)
    monkeypatch.setattr(fetch, "build_all", container.build_all)
    monkeypatch.setattr(fetch, "save_df", container.save_df)
    monkeypatch.setattr(fetch, "load_fetch_state", container.load_fetch_state)
    monkeypatch.setattr(fetch, "save_fetch_state", container.save_fetch_state)

    return container


def _cgm_df(rows=1, serial="SN_A", start_ts=None):
    """Build a tiny non-empty CGM-like DataFrame with a timestamp column."""
    start_ts = start_ts or datetime(2024, 6, 1, 10, 0, tzinfo=PST)
    return pd.DataFrame({
        "timestamp": [start_ts + timedelta(minutes=5 * i) for i in range(rows)],
        "bg_mgdl": [120 + i for i in range(rows)],
        "pump_serial": [serial] * rows,
        "seqnum": list(range(rows)),
    })


# ---------------------------------------------------------------------------
# run_full_fetch
# ---------------------------------------------------------------------------


class TestRunFullFetch:
    def test_iterates_all_pumps_with_their_full_ranges(self, patched_fetch, three_pumps):
        fetch.run_full_fetch()

        assert patched_fetch.fetch_pump_events.call_count == 3
        calls = patched_fetch.fetch_pump_events.call_args_list
        # Each pump's date range is derived from min/maxDateWithEvents (first 10 chars)
        for call, pump in zip(calls, three_pumps):
            assert call.args[1] == pump["tconnectDeviceId"]
            assert call.args[2] == pump["minDateWithEvents"][:10]
            assert call.args[3] == pump["maxDateWithEvents"][:10]
            assert call.kwargs["pump_serial"] == str(pump["serialNumber"])

    def test_saves_only_non_empty_dataframes(self, patched_fetch):
        """build_all returns a mix of empty and non-empty frames — only
        non-empty ones are passed to save_df."""
        non_empty = _cgm_df(rows=2, serial="SN_A")
        empty = pd.DataFrame()

        # First pump produces real events, others none.
        def _fetch_side_effect(api, device_id, start, end, pump_serial):
            if pump_serial == "SN_A":
                return ([MagicMock()], end)
            return ([], None)

        patched_fetch.fetch_pump_events.side_effect = _fetch_side_effect
        patched_fetch.build_all.return_value = {
            "cgm": non_empty,
            "bolus": empty,
            "basal": empty,
        }

        fetch.run_full_fetch()

        # save_df called exactly once for the non-empty frame
        assert patched_fetch.save_df.call_count == 1
        name, df = patched_fetch.save_df.call_args.args
        assert name == "cgm"
        assert df.equals(non_empty)

    def test_build_all_skipped_when_no_events(self, patched_fetch):
        """_process_pump returns early if events list is empty ⇒ no build_all, no save."""
        patched_fetch.fetch_pump_events.return_value = ([], None)

        fetch.run_full_fetch()

        patched_fetch.build_all.assert_not_called()
        patched_fetch.save_df.assert_not_called()

    def test_updates_fetch_state_per_pump(self, patched_fetch, three_pumps):
        """Each successful pump's last_successful_chunk_end should land in state."""
        non_empty = _cgm_df(rows=1, serial="SN_A")

        def _fetch_side_effect(api, device_id, start, end, pump_serial):
            # Simulate successful fetch with last-chunk end = the requested end date
            return ([MagicMock()], end)

        patched_fetch.fetch_pump_events.side_effect = _fetch_side_effect
        patched_fetch.build_all.return_value = {"cgm": non_empty}

        fetch.run_full_fetch()

        # save_fetch_state is called exactly once at the end of run_full_fetch
        patched_fetch.save_fetch_state.assert_called_once()
        saved_state = patched_fetch.save_fetch_state.call_args.args[0]
        for pump in three_pumps:
            serial = str(pump["serialNumber"])
            assert serial in saved_state
            assert saved_state[serial]["last_successful_chunk_end"] == pump["maxDateWithEvents"][:10]

    def test_save_fetch_state_still_called_when_all_pumps_empty(self, patched_fetch):
        fetch.run_full_fetch()
        patched_fetch.save_fetch_state.assert_called_once()


# ---------------------------------------------------------------------------
# run_incremental_fetch
# ---------------------------------------------------------------------------


class TestRunIncrementalFetch:
    def test_uses_prior_state_minus_one_day(self, patched_fetch, three_pumps):
        """When a pump has prior state, start = last_successful_chunk_end - 1 day."""
        patched_fetch.load_fetch_state.return_value = {
            "SN_A": {"last_successful_chunk_end": "2023-06-20"},
            "SN_B": {"last_successful_chunk_end": "2024-12-01"},
            "SN_C": {"last_successful_chunk_end": "2026-02-10"},
        }

        fetch.run_incremental_fetch()

        calls = patched_fetch.fetch_pump_events.call_args_list
        assert len(calls) == 3
        # start should be last_end minus one day, end should be pump's maxDateWithEvents
        expected_starts = ["2023-06-19", "2024-11-30", "2026-02-09"]
        expected_ends = [p["maxDateWithEvents"][:10] for p in three_pumps]
        for call, exp_start, exp_end in zip(calls, expected_starts, expected_ends):
            assert call.args[2] == exp_start
            assert call.args[3] == exp_end

    def test_no_state_falls_back_to_full_range(self, patched_fetch, three_pumps):
        """Pump with no state entry ⇒ start = pump's minDateWithEvents."""
        patched_fetch.load_fetch_state.return_value = {}

        fetch.run_incremental_fetch()

        calls = patched_fetch.fetch_pump_events.call_args_list
        for call, pump in zip(calls, three_pumps):
            assert call.args[2] == pump["minDateWithEvents"][:10]
            assert call.args[3] == pump["maxDateWithEvents"][:10]

    def test_mixed_state_and_no_state(self, patched_fetch, three_pumps):
        """Some pumps have state, others don't — each is treated independently."""
        patched_fetch.load_fetch_state.return_value = {
            "SN_B": {"last_successful_chunk_end": "2024-12-01"},
        }

        fetch.run_incremental_fetch()

        calls = patched_fetch.fetch_pump_events.call_args_list
        # SN_A: no state → full range
        assert calls[0].args[2] == "2023-01-01"
        # SN_B: has state → 2024-12-01 minus 1 day
        assert calls[1].args[2] == "2024-11-30"
        # SN_C: no state → full range
        assert calls[2].args[2] == "2025-06-01"


# ---------------------------------------------------------------------------
# run_day_fetch
# ---------------------------------------------------------------------------


class TestRunDayFetch:
    def test_uses_only_active_pump(self, patched_fetch, three_pumps):
        """run_day_fetch fetches from metadata[-1] (the newest pump) exactly once."""
        patched_fetch.fetch_pump_events.return_value = ([], None)

        fetch.run_day_fetch("2026-02-15")

        assert patched_fetch.fetch_pump_events.call_count == 1
        call = patched_fetch.fetch_pump_events.call_args
        active = three_pumps[-1]
        assert call.args[1] == active["tconnectDeviceId"]
        assert call.kwargs["pump_serial"] == str(active["serialNumber"])

    def test_date_range_is_plus_minus_one_day(self, patched_fetch):
        patched_fetch.fetch_pump_events.return_value = ([], None)

        fetch.run_day_fetch("2026-02-15")

        call = patched_fetch.fetch_pump_events.call_args
        assert call.args[2] == "2026-02-14"
        assert call.args[3] == "2026-02-16"

    def test_does_not_update_fetch_state(self, patched_fetch):
        """Per HANDOFF.md: day fetch never writes fetch state (not even empty)."""
        patched_fetch.fetch_pump_events.return_value = (
            [MagicMock()],
            "2026-02-16",
        )
        patched_fetch.build_all.return_value = {
            "cgm": _cgm_df(rows=1, serial="SN_C"),
        }

        fetch.run_day_fetch("2026-02-15")

        patched_fetch.save_fetch_state.assert_not_called()

    def test_saves_non_empty_dataframes(self, patched_fetch):
        non_empty = _cgm_df(rows=3, serial="SN_C")
        patched_fetch.fetch_pump_events.return_value = ([MagicMock()], "2026-02-16")
        patched_fetch.build_all.return_value = {
            "cgm": non_empty,
            "bolus": pd.DataFrame(),
        }

        fetch.run_day_fetch("2026-02-15")

        assert patched_fetch.save_df.call_count == 1
        name, df = patched_fetch.save_df.call_args.args
        assert name == "cgm"
        assert df.equals(non_empty)

    def test_no_events_skips_build_and_save(self, patched_fetch):
        patched_fetch.fetch_pump_events.return_value = ([], None)

        fetch.run_day_fetch("2026-02-15")

        patched_fetch.build_all.assert_not_called()
        patched_fetch.save_df.assert_not_called()
        patched_fetch.save_fetch_state.assert_not_called()


# ---------------------------------------------------------------------------
# _process_pump (direct)
# ---------------------------------------------------------------------------


class TestProcessPump:
    def test_empty_events_short_circuits(self, patched_fetch):
        """No events returned ⇒ no build_all, no save_df, no state mutation."""
        state = {}
        patched_fetch.fetch_pump_events.return_value = ([], None)

        fetch._process_pump(
            api=MagicMock(),
            device_id=1,
            serial="SN_A",
            start="2023-01-01",
            end="2023-06-30",
            state=state,
        )

        patched_fetch.build_all.assert_not_called()
        patched_fetch.save_df.assert_not_called()
        assert state == {}

    def test_state_updated_with_last_successful_end(self, patched_fetch):
        state = {}
        patched_fetch.fetch_pump_events.return_value = (
            [MagicMock()],
            "2023-06-30",
        )
        patched_fetch.build_all.return_value = {
            "cgm": _cgm_df(rows=1, serial="SN_A"),
        }

        fetch._process_pump(
            api=MagicMock(),
            device_id=1,
            serial="SN_A",
            start="2023-01-01",
            end="2023-06-30",
            state=state,
        )

        assert state["SN_A"]["last_successful_chunk_end"] == "2023-06-30"

    def test_state_actual_min_max_derived_from_timestamps(self, patched_fetch):
        state = {}
        ts0 = datetime(2024, 6, 1, 10, 0, tzinfo=PST)
        ts_last = ts0 + timedelta(minutes=5 * 9)  # 10 rows
        df = _cgm_df(rows=10, serial="SN_A", start_ts=ts0)

        patched_fetch.fetch_pump_events.return_value = ([MagicMock()], "2024-06-30")
        patched_fetch.build_all.return_value = {"cgm": df}

        fetch._process_pump(
            api=MagicMock(),
            device_id=1,
            serial="SN_A",
            start="2024-06-01",
            end="2024-06-30",
            state=state,
        )

        assert state["SN_A"]["actual_min_date"] == str(ts0)[:10]
        assert state["SN_A"]["actual_max_date"] == str(ts_last)[:10]

    def test_saves_only_non_empty_frames(self, patched_fetch):
        state = {}
        non_empty = _cgm_df(rows=2, serial="SN_A")
        patched_fetch.fetch_pump_events.return_value = ([MagicMock()], "2024-06-30")
        patched_fetch.build_all.return_value = {
            "cgm": non_empty,
            "bolus": pd.DataFrame(),
            "basal": pd.DataFrame(),
        }

        fetch._process_pump(
            api=MagicMock(),
            device_id=1,
            serial="SN_A",
            start="2024-06-01",
            end="2024-06-30",
            state=state,
        )

        assert patched_fetch.save_df.call_count == 1
        assert patched_fetch.save_df.call_args.args[0] == "cgm"

    def test_no_last_successful_end_leaves_chunk_key_unset(self, patched_fetch):
        """
        NOTE: potential issue — if events is non-empty but last_successful_end
        is falsy (None / ""), the state entry is created but
        last_successful_chunk_end is never written. Today's data path
        never produces this combination (events only accumulate on success),
        but this test pins the behavior.
        """
        state = {}
        patched_fetch.fetch_pump_events.return_value = (
            [MagicMock()],
            None,  # falsy
        )
        patched_fetch.build_all.return_value = {
            "cgm": _cgm_df(rows=1, serial="SN_A"),
        }

        fetch._process_pump(
            api=MagicMock(),
            device_id=1,
            serial="SN_A",
            start="2024-06-01",
            end="2024-06-30",
            state=state,
        )

        assert "SN_A" in state
        assert "last_successful_chunk_end" not in state["SN_A"]
