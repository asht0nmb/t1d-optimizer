"""Contract tests for the Storage Protocol.

Every behavior described in `core/storage/protocol.py`'s docstrings has
at least one test here. The suite is parameterized over each concrete
implementation; new implementations get their behavior validated
"for free" by adding a fixture branch.

The ``"supabase"`` parameterization runs only when ``SUPABASE_TEST_URL``
is set in the environment, and refuses to run against the production
project (host-pattern denylist) as a defensive belt-and-suspenders.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import pytest

from core.storage.memory import InMemoryStorage
from core.storage.records import (
    AlertInsertResult,
    AlertRecord,
    DetectionResult,
    FetchState,
)

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Implementations parameterized into every contract test.
_STORAGE_IMPLS: list[str] = ["memory", "parquet", "supabase"]

# Hostname substrings that must never appear in SUPABASE_TEST_URL — if the
# env var points at one of these we refuse to run, even though the tests
# clean state between each run. Belt-and-suspenders behind the
# "use a dedicated test project" convention.
#
# Operators MUST populate this tuple with their production project's
# hostname patterns (both direct and pooler) before running the supabase
# suite against any real database. The fixture below fails loud if the
# tuple is empty when ``SUPABASE_TEST_URL`` is set, so an empty list
# can't silently slip past CI.
#
# TODO: externalise via SUPABASE_PROD_HOST_DENYLIST env var once we have
# more than one production project to guard against; the constant
# suffices for the single-project case.
_PROD_HOST_PATTERNS: tuple[str, ...] = (
    # Examples of what to put here once the production project's
    # hostnames are known:
    #     "db.<prod-project-id>.supabase.co",
    #     "aws-0-<prod-region>.pooler.supabase.com",
    # The substring match is intentionally generous; any hostname
    # containing one of these strings triggers the refusal.
)


def _refuse_if_prod(url: str) -> None:
    """Raise :class:`RuntimeError` if ``url`` matches the production project.

    Called by the ``supabase`` fixture branch and by Task 5's defensive
    test that runs even without ``SUPABASE_TEST_URL`` set.
    """
    host = urlparse(url).hostname or ""
    host = host.lower()
    for pat in _PROD_HOST_PATTERNS:
        if pat and pat.lower() in host:
            raise RuntimeError(
                f"SUPABASE_TEST_URL host {host!r} matches production "
                f"pattern {pat!r}; refusing to run against prod."
            )


@pytest.fixture(params=_STORAGE_IMPLS)
def storage(request, tmp_path: Path):
    """Parameterized Storage instance.

    Each contract test runs against every parameter value so behavior
    stays consistent across implementations.
    """
    match request.param:
        case "memory":
            yield InMemoryStorage()
        case "parquet":
            from core.storage.parquet import ParquetStorage
            yield ParquetStorage(root=tmp_path)
        case "supabase":
            url = os.environ.get("SUPABASE_TEST_URL")
            if not url:
                pytest.skip("SUPABASE_TEST_URL not set")
            if not _PROD_HOST_PATTERNS:
                pytest.fail(
                    "_PROD_HOST_PATTERNS in tests/core/test_storage_contract.py "
                    "is empty; populate it with the production project's "
                    "hostname patterns before running the supabase suite. "
                    "Refusing to run with the safety net disabled."
                )
            _refuse_if_prod(url)
            from core.storage.supabase import SupabaseStorage
            s = SupabaseStorage.from_pooler_url(url)
            s.clean_all()
            try:
                yield s
            finally:
                s.clean_all()
                s.close()
        case other:
            raise AssertionError(f"unhandled storage fixture param: {other!r}")


def _cgm_df(rows: list[tuple[str, int, datetime, int, bool]]) -> pd.DataFrame:
    """Build a CGM DataFrame from row tuples.

    Columns mirror the ``cgm.parquet`` schema (and the Postgres
    ``cgm`` table); the test_data is hand-crafted so tests can assert
    on it without depending on real ingestion output.
    """
    return pd.DataFrame(
        [
            {
                "pump_serial": s,
                "seqnum": n,
                "timestamp": t,
                "bg_mgdl": bg,
                "backfilled": b,
                "sensor_timestamp": None,
            }
            for (s, n, t, bg, b) in rows
        ]
    )


# ---------------------------------------------------------------------------
# upsert / read_table / read_all_table
# ---------------------------------------------------------------------------


class TestUpsertAndRead:
    def test_upsert_then_read_window_round_trip(self, storage):
        rows = [
            ("PUMP-A", 1, datetime(2026, 5, 13, 0, 0, tzinfo=UTC), 120, False),
            ("PUMP-A", 2, datetime(2026, 5, 13, 0, 5, tzinfo=UTC), 122, False),
            ("PUMP-A", 3, datetime(2026, 5, 13, 23, 55, tzinfo=UTC), 130, False),
        ]
        result = storage.upsert_table("cgm", _cgm_df(rows))
        assert result.rows_received == 3
        assert result.rows_inserted == 3
        assert result.rows_skipped == 0

        out = storage.read_table(
            "cgm",
            since=datetime(2026, 5, 13, 0, 0, tzinfo=UTC),
            until=datetime(2026, 5, 14, 0, 0, tzinfo=UTC),
        )
        assert len(out) == 3
        assert set(out["seqnum"].tolist()) == {1, 2, 3}

    def test_read_table_window_excludes_until_bound(self, storage):
        rows = [
            ("PUMP-A", 1, datetime(2026, 5, 13, 23, 55, tzinfo=UTC), 130, False),
            ("PUMP-A", 2, datetime(2026, 5, 14, 0, 0, tzinfo=UTC), 131, False),
            ("PUMP-A", 3, datetime(2026, 5, 14, 0, 5, tzinfo=UTC), 132, False),
        ]
        storage.upsert_table("cgm", _cgm_df(rows))
        out = storage.read_table(
            "cgm",
            since=datetime(2026, 5, 13, 0, 0, tzinfo=UTC),
            until=datetime(2026, 5, 14, 0, 0, tzinfo=UTC),
        )
        assert len(out) == 1
        assert int(out.iloc[0]["seqnum"]) == 1

    def test_read_table_pump_serial_filter(self, storage):
        rows = [
            ("PUMP-A", 1, datetime(2026, 5, 13, 0, 0, tzinfo=UTC), 120, False),
            ("PUMP-B", 1, datetime(2026, 5, 13, 0, 5, tzinfo=UTC), 200, False),
        ]
        storage.upsert_table("cgm", _cgm_df(rows))
        out = storage.read_table(
            "cgm",
            since=datetime(2026, 5, 13, 0, 0, tzinfo=UTC),
            until=datetime(2026, 5, 14, 0, 0, tzinfo=UTC),
            pump_serial="PUMP-A",
        )
        assert len(out) == 1
        assert out.iloc[0]["pump_serial"] == "PUMP-A"

    def test_upsert_idempotent_on_pk_conflict(self, storage):
        rows = [
            ("PUMP-A", 1, datetime(2026, 5, 13, 0, 0, tzinfo=UTC), 120, False),
            ("PUMP-A", 2, datetime(2026, 5, 13, 0, 5, tzinfo=UTC), 122, False),
        ]
        storage.upsert_table("cgm", _cgm_df(rows))
        result = storage.upsert_table("cgm", _cgm_df(rows))
        assert result.rows_received == 2
        assert result.rows_inserted == 0
        assert result.rows_skipped == 2

        out = storage.read_table(
            "cgm",
            since=datetime(2026, 5, 13, 0, 0, tzinfo=UTC),
            until=datetime(2026, 5, 14, 0, 0, tzinfo=UTC),
        )
        assert len(out) == 2

    def test_upsert_empty_is_noop(self, storage):
        result = storage.upsert_table("cgm", _cgm_df([]))
        assert result.rows_received == 0
        assert result.rows_inserted == 0
        assert result.rows_skipped == 0

    def test_read_table_requires_since(self, storage):
        with pytest.raises(TypeError):
            storage.read_table(  # type: ignore[call-arg]
                "cgm",
                until=datetime(2026, 5, 13, 0, 0, tzinfo=UTC),
            )

    def test_read_table_requires_until(self, storage):
        with pytest.raises(TypeError):
            storage.read_table(  # type: ignore[call-arg]
                "cgm",
                since=datetime(2026, 5, 13, 0, 0, tzinfo=UTC),
            )

    def test_read_all_table_returns_every_row(self, storage):
        rows = [
            ("PUMP-A", 1, datetime(2020, 1, 1, tzinfo=UTC), 100, False),
            ("PUMP-A", 2, datetime(2026, 5, 13, tzinfo=UTC), 120, False),
            ("PUMP-B", 1, datetime(2030, 12, 31, tzinfo=UTC), 200, False),
        ]
        storage.upsert_table("cgm", _cgm_df(rows))
        out = storage.read_all_table("cgm")
        assert len(out) == 3

    def test_read_all_table_empty_when_no_rows(self, storage):
        out = storage.read_all_table("cgm")
        assert isinstance(out, pd.DataFrame)
        assert len(out) == 0


# ---------------------------------------------------------------------------
# delete_range
# ---------------------------------------------------------------------------


class TestDeleteRange:
    def test_delete_range_without_any_bound_raises(self, storage):
        with pytest.raises(ValueError):
            storage.delete_range("cgm")

    def test_delete_range_with_since_until_removes_window(self, storage):
        rows = [
            ("PUMP-A", 1, datetime(2026, 5, 12, 23, 0, tzinfo=UTC), 110, False),
            ("PUMP-A", 2, datetime(2026, 5, 13, 6, 0, tzinfo=UTC), 120, False),
            ("PUMP-A", 3, datetime(2026, 5, 13, 12, 0, tzinfo=UTC), 130, False),
            ("PUMP-A", 4, datetime(2026, 5, 14, 1, 0, tzinfo=UTC), 140, False),
        ]
        storage.upsert_table("cgm", _cgm_df(rows))
        deleted = storage.delete_range(
            "cgm",
            since=datetime(2026, 5, 13, 0, 0, tzinfo=UTC),
            until=datetime(2026, 5, 14, 0, 0, tzinfo=UTC),
        )
        assert deleted == 2
        out = storage.read_all_table("cgm")
        assert set(out["seqnum"].tolist()) == {1, 4}

    def test_delete_range_scoped_by_pump_serial(self, storage):
        rows = [
            ("PUMP-A", 1, datetime(2026, 5, 13, 0, 0, tzinfo=UTC), 120, False),
            ("PUMP-A", 2, datetime(2026, 5, 13, 0, 5, tzinfo=UTC), 122, False),
            ("PUMP-B", 1, datetime(2026, 5, 13, 0, 0, tzinfo=UTC), 200, False),
            ("PUMP-B", 2, datetime(2026, 5, 13, 0, 5, tzinfo=UTC), 202, False),
        ]
        storage.upsert_table("cgm", _cgm_df(rows))
        deleted = storage.delete_range("cgm", pump_serial="PUMP-A")
        assert deleted == 2
        out = storage.read_all_table("cgm")
        assert len(out) == 2
        assert set(out["pump_serial"].tolist()) == {"PUMP-B"}

    def test_delete_range_only_since(self, storage):
        rows = [
            ("PUMP-A", 1, datetime(2026, 5, 12, 23, 0, tzinfo=UTC), 110, False),
            ("PUMP-A", 2, datetime(2026, 5, 13, 6, 0, tzinfo=UTC), 120, False),
            ("PUMP-A", 3, datetime(2026, 5, 14, 1, 0, tzinfo=UTC), 140, False),
        ]
        storage.upsert_table("cgm", _cgm_df(rows))
        deleted = storage.delete_range(
            "cgm",
            since=datetime(2026, 5, 13, 0, 0, tzinfo=UTC),
        )
        assert deleted == 2
        out = storage.read_all_table("cgm")
        assert set(out["seqnum"].tolist()) == {1}


# ---------------------------------------------------------------------------
# fetch state
# ---------------------------------------------------------------------------


class TestFetchStateProtocol:
    def test_get_missing_returns_none(self, storage):
        assert storage.get_fetch_state("tandem") is None

    def test_round_trip_with_payload(self, storage):
        ts = datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
        state = FetchState(
            source_id="tandem",
            last_cursor="abc",
            last_fetched_at=ts,
            payload={"cgm": {"last_end": "2026-05-13"}},
        )
        storage.set_fetch_state("tandem", state)
        got = storage.get_fetch_state("tandem")
        assert got == state

    def test_set_overwrites_prior(self, storage):
        ts1 = datetime(2026, 5, 13, tzinfo=UTC)
        ts2 = datetime(2026, 5, 14, tzinfo=UTC)
        storage.set_fetch_state(
            "tandem",
            FetchState("tandem", None, ts1, {"a": 1}),
        )
        storage.set_fetch_state(
            "tandem",
            FetchState("tandem", None, ts2, {"a": 2}),
        )
        got = storage.get_fetch_state("tandem")
        assert got is not None
        assert got.last_fetched_at == ts2
        assert got.payload == {"a": 2}

    def test_list_returns_every_source(self, storage):
        ts = datetime(2026, 5, 13, tzinfo=UTC)
        storage.set_fetch_state(
            "tandem", FetchState("tandem", None, ts, {})
        )
        storage.set_fetch_state(
            "pydexcom", FetchState("pydexcom", None, ts, {})
        )
        sources = {s.source_id for s in storage.list_fetch_state()}
        assert sources == {"tandem", "pydexcom"}


# ---------------------------------------------------------------------------
# pipeline version
# ---------------------------------------------------------------------------


class TestPipelineVersionProtocol:
    def test_get_missing_returns_none(self, storage):
        assert storage.get_pipeline_version() is None

    def test_round_trip(self, storage):
        storage.set_pipeline_version(7)
        assert storage.get_pipeline_version() == 7

    def test_set_overwrites_prior(self, storage):
        storage.set_pipeline_version(3)
        storage.set_pipeline_version(4)
        assert storage.get_pipeline_version() == 4


# ---------------------------------------------------------------------------
# alerts (dedup contract)
# ---------------------------------------------------------------------------


class TestAlertsProtocol:
    def _alert(
        self,
        *,
        kind: str = "anomaly_spike",
        event_ref: str | None = "cgm:1234",
        sent_at: datetime | None = None,
        payload: dict | None = None,
    ) -> AlertRecord:
        return AlertRecord(
            id=None,
            alert_kind=kind,
            event_ref=event_ref,
            sent_at=sent_at or datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
            payload=payload or {},
        )

    def test_record_new_event_ref_inserts(self, storage):
        result = storage.record_alert(self._alert(event_ref="cgm:1"))
        assert isinstance(result, AlertInsertResult)
        assert result.inserted is True
        assert result.record.id is not None
        assert result.record.alert_kind == "anomaly_spike"
        assert result.record.event_ref == "cgm:1"

    def test_record_alert_returns_inserted_false_on_duplicate_event_ref(self, storage):
        first = storage.record_alert(self._alert(event_ref="cgm:dup-insert"))
        second = storage.record_alert(
            self._alert(event_ref="cgm:dup-insert", payload={"new": "payload"})
        )
        assert first.inserted is True
        assert second.inserted is False
        assert second.record.id == first.record.id

    def test_record_duplicate_event_ref_returns_existing(self, storage):
        first = storage.record_alert(self._alert(event_ref="cgm:dup"))
        second = storage.record_alert(
            self._alert(event_ref="cgm:dup", payload={"new": "payload"})
        )
        assert second.record.id == first.record.id
        all_alerts = storage.recent_alerts(
            "anomaly_spike", within=timedelta(days=365)
        )
        # Exactly one row across the two record_alert calls.
        matches = [a for a in all_alerts if a.event_ref == "cgm:dup"]
        assert len(matches) == 1

    def test_null_event_ref_does_not_dedup(self, storage):
        # Per the migration COMMENT: rows with NULL event_ref are not
        # deduped (each insert produces a new row).
        storage.record_alert(self._alert(kind="manual", event_ref=None))
        storage.record_alert(self._alert(kind="manual", event_ref=None))
        alerts = storage.recent_alerts(
            "manual", within=timedelta(days=365)
        )
        assert len(alerts) == 2

    def test_find_alert_returns_record(self, storage):
        ref = "cgm:find-me"
        storage.record_alert(self._alert(event_ref=ref))
        got = storage.find_alert("anomaly_spike", ref)
        assert got is not None
        assert got.event_ref == ref

    def test_find_alert_missing_returns_none(self, storage):
        assert storage.find_alert("anomaly_spike", "no:such:ref") is None

    def test_recent_alerts_chronological_order(self, storage):
        base = datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
        for i in range(3):
            storage.record_alert(
                self._alert(
                    kind="anomaly_spike",
                    event_ref=f"cgm:{i}",
                    sent_at=base + timedelta(minutes=i),
                )
            )
        out = storage.recent_alerts(
            "anomaly_spike", within=timedelta(days=365)
        )
        assert len(out) == 3
        ts = [a.sent_at for a in out]
        assert ts == sorted(ts, reverse=True)

    def test_recent_alerts_filters_by_kind(self, storage):
        storage.record_alert(self._alert(kind="anomaly_spike", event_ref="a"))
        storage.record_alert(self._alert(kind="missed_meal", event_ref="b"))
        spikes = storage.recent_alerts(
            "anomaly_spike", within=timedelta(days=365)
        )
        assert {a.event_ref for a in spikes} == {"a"}

    def test_record_alert_rejects_tz_naive_sent_at(self, storage):
        """`sent_at` must be tz-aware — `recent_alerts` compares
        against tz-aware "now" and would crash on naive values."""
        naive = datetime(2026, 5, 13, 12, 0)  # no tzinfo
        rec = AlertRecord(
            id=None,
            alert_kind="anomaly_spike",
            event_ref="cgm:naive",
            sent_at=naive,
            payload={},
        )
        with pytest.raises(ValueError, match="tz-aware"):
            storage.record_alert(rec)


# ---------------------------------------------------------------------------
# detection results
# ---------------------------------------------------------------------------


class TestDetectionResultsProtocol:
    def _det(
        self,
        *,
        kind: str = "missed_meal",
        anchor: datetime | None = None,
        created: datetime | None = None,
        payload: dict | None = None,
    ) -> DetectionResult:
        anchor = anchor or datetime(2026, 5, 13, 10, 0, tzinfo=UTC)
        created = created or datetime(2026, 5, 13, 10, 1, tzinfo=UTC)
        return DetectionResult(
            kind=kind,
            anchor_timestamp=anchor,
            payload=payload or {},
            created_at=created,
        )

    def test_record_and_list_round_trip(self, storage):
        storage.record_detection_result(self._det(payload={"rise": 42}))
        results = storage.list_detection_results()
        assert len(results) == 1
        assert results[0].payload == {"rise": 42}

    def test_list_filters_by_kind(self, storage):
        storage.record_detection_result(self._det(kind="missed_meal"))
        storage.record_detection_result(self._det(kind="anomaly_spike"))
        meals = storage.list_detection_results(kind="missed_meal")
        assert {r.kind for r in meals} == {"missed_meal"}

    def test_list_filters_by_since(self, storage):
        base = datetime(2026, 5, 13, 10, 0, tzinfo=UTC)
        storage.record_detection_result(
            self._det(created=base - timedelta(hours=2))
        )
        storage.record_detection_result(
            self._det(created=base + timedelta(hours=2))
        )
        recent = storage.list_detection_results(since=base)
        assert len(recent) == 1
        assert recent[0].created_at > base

    def test_list_respects_limit(self, storage):
        base = datetime(2026, 5, 13, 10, 0, tzinfo=UTC)
        for i in range(5):
            storage.record_detection_result(
                self._det(created=base + timedelta(minutes=i))
            )
        out = storage.list_detection_results(limit=2)
        assert len(out) == 2


# ---------------------------------------------------------------------------
# clean_all
# ---------------------------------------------------------------------------


class TestCleanAll:
    def test_empties_everything(self, storage):
        ts = datetime(2026, 5, 13, tzinfo=UTC)
        storage.upsert_table(
            "cgm",
            _cgm_df([("PUMP-A", 1, ts, 120, False)]),
        )
        storage.set_fetch_state("tandem", FetchState("tandem", None, ts, {}))
        storage.set_pipeline_version(3)
        storage.record_alert(
            AlertRecord(None, "anomaly_spike", "cgm:1", ts, {})
        )
        storage.record_detection_result(
            DetectionResult("missed_meal", ts, {}, ts)
        )

        storage.clean_all()

        assert len(storage.read_all_table("cgm")) == 0
        assert storage.get_fetch_state("tandem") is None
        assert storage.list_fetch_state() == []
        assert storage.get_pipeline_version() is None
        assert storage.recent_alerts(
            "anomaly_spike", within=timedelta(days=365)
        ) == []
        assert storage.list_detection_results() == []
