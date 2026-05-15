"""Tests for the typed metadata records used by the Storage Protocol."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from core.storage.records import (
    AlertRecord,
    DetectionResult,
    FetchState,
    UpsertResult,
)


# ---------------------------------------------------------------------------
# UpsertResult
# ---------------------------------------------------------------------------


class TestUpsertResult:
    def test_constructs_with_named_fields(self):
        r = UpsertResult(
            rows_received=10,
            rows_inserted=7,
            rows_skipped=3,
            elapsed_seconds=0.25,
        )
        assert r.rows_received == 10
        assert r.rows_inserted == 7
        assert r.rows_skipped == 3
        assert r.elapsed_seconds == pytest.approx(0.25)

    def test_is_frozen(self):
        r = UpsertResult(0, 0, 0, 0.0)
        with pytest.raises(FrozenInstanceError):
            r.rows_inserted = 1  # type: ignore[misc]

    def test_equality_by_value(self):
        a = UpsertResult(1, 1, 0, 0.1)
        b = UpsertResult(1, 1, 0, 0.1)
        c = UpsertResult(1, 0, 1, 0.1)
        assert a == b
        assert a != c


# ---------------------------------------------------------------------------
# FetchState
# ---------------------------------------------------------------------------


class TestFetchState:
    def test_constructs_with_payload(self):
        ts = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)
        state = FetchState(
            source_id="tandem",
            last_cursor=None,
            last_fetched_at=ts,
            payload={"cgm": {"last_end": "2026-05-13"}},
        )
        assert state.source_id == "tandem"
        assert state.last_cursor is None
        assert state.last_fetched_at == ts
        assert state.payload == {"cgm": {"last_end": "2026-05-13"}}

    def test_is_frozen(self):
        state = FetchState("x", None, None, {})
        with pytest.raises(FrozenInstanceError):
            state.source_id = "y"  # type: ignore[misc]

    def test_round_trip_equality(self):
        a = FetchState("tandem", "abc", None, {"k": 1})
        b = FetchState("tandem", "abc", None, {"k": 1})
        assert a == b

    def test_source_kind_defaults_to_unknown(self):
        """`source_kind` defaults to ``'unknown'`` so callers that pre-date
        the field keep working. Concrete sources (tconnectsync, pydexcom)
        are populated by the connectors, not the storage layer."""
        state = FetchState(
            source_id="tandem",
            last_cursor=None,
            last_fetched_at=None,
        )
        assert state.source_kind == "unknown"

    def test_source_kind_explicit_value(self):
        state = FetchState(
            source_id="tandem",
            last_cursor=None,
            last_fetched_at=None,
            source_kind="tconnectsync",
        )
        assert state.source_kind == "tconnectsync"


# ---------------------------------------------------------------------------
# AlertRecord
# ---------------------------------------------------------------------------


class TestAlertRecord:
    def test_id_optional_until_inserted(self):
        sent_at = datetime(2026, 5, 13, tzinfo=timezone.utc)
        rec = AlertRecord(
            id=None,
            alert_kind="anomaly_spike",
            event_ref="cgm:1234",
            sent_at=sent_at,
            payload={"bg": 240},
        )
        assert rec.id is None
        assert rec.alert_kind == "anomaly_spike"
        assert rec.event_ref == "cgm:1234"
        assert rec.sent_at == sent_at
        assert rec.payload == {"bg": 240}

    def test_event_ref_may_be_none(self):
        sent_at = datetime(2026, 5, 13, tzinfo=timezone.utc)
        rec = AlertRecord(
            id="alert-abc",
            alert_kind="manual_test",
            event_ref=None,
            sent_at=sent_at,
            payload={},
        )
        assert rec.event_ref is None

    def test_is_frozen(self):
        rec = AlertRecord(None, "k", None, datetime(2026, 5, 13, tzinfo=timezone.utc), {})
        with pytest.raises(FrozenInstanceError):
            rec.alert_kind = "other"  # type: ignore[misc]

    def test_pump_serial_defaults_to_none(self):
        """`pump_serial` is nullable in the Postgres ``alerts_sent`` table
        (not every alert kind is pump-scoped). Default ``None`` so existing
        callers that pre-date the field keep working."""
        rec = AlertRecord(
            id=None,
            alert_kind="anomaly_spike",
            event_ref="cgm:1",
            sent_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
            payload={},
        )
        assert rec.pump_serial is None

    def test_pump_serial_explicit_value(self):
        rec = AlertRecord(
            id=None,
            alert_kind="anomaly_spike",
            event_ref="cgm:1",
            sent_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
            payload={},
            pump_serial="PUMP-A",
        )
        assert rec.pump_serial == "PUMP-A"

    def test_delivery_defaults_to_pending(self):
        """`delivery` mirrors the Postgres ``alerts_sent.delivery`` column
        whose default is ``'pending'``."""
        rec = AlertRecord(
            id=None,
            alert_kind="anomaly_spike",
            event_ref="cgm:1",
            sent_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
            payload={},
        )
        assert rec.delivery == "pending"

    def test_delivery_explicit_value(self):
        rec = AlertRecord(
            id=None,
            alert_kind="anomaly_spike",
            event_ref="cgm:1",
            sent_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
            payload={},
            delivery="sent",
        )
        assert rec.delivery == "sent"


# ---------------------------------------------------------------------------
# DetectionResult
# ---------------------------------------------------------------------------


class TestDetectionResult:
    def test_constructs_with_named_fields(self):
        anchor = datetime(2026, 5, 13, 10, 30, tzinfo=timezone.utc)
        created = datetime(2026, 5, 13, 10, 31, tzinfo=timezone.utc)
        rec = DetectionResult(
            kind="missed_meal",
            anchor_timestamp=anchor,
            payload={"bg_rise": 42.0},
            created_at=created,
        )
        assert rec.kind == "missed_meal"
        assert rec.anchor_timestamp == anchor
        assert rec.created_at == created
        assert rec.payload == {"bg_rise": 42.0}

    def test_is_frozen(self):
        anchor = datetime(2026, 5, 13, 10, 30, tzinfo=timezone.utc)
        rec = DetectionResult("k", anchor, {}, anchor)
        with pytest.raises(FrozenInstanceError):
            rec.kind = "x"  # type: ignore[misc]
