"""SupabaseStorage-specific behaviors beyond the Protocol contract suite.

The contract suite in :mod:`tests.core.test_storage_contract` already
parameterizes the ``"supabase"`` branch over every shared behavior. This
file holds the Postgres-only invariants:

* The partial unique index on ``(alert_kind, event_ref) WHERE event_ref IS NOT NULL``.
* The connection-ownership protocol (caller-managed vs ``from_pooler_url``).
* ``clean_all`` resets bigserial identities.
* Round-tripping the additive ``AlertRecord.pump_serial`` /
  ``AlertRecord.delivery`` and ``FetchState.source_kind`` fields through
  the real Postgres columns.
* The defensive prod-host denylist used by the contract fixture.

Every test that needs a Postgres connection skips when
``SUPABASE_TEST_URL`` is missing.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pytest

from core.storage.records import AlertRecord, FetchState
from tests.core.test_storage_contract import _PROD_HOST_PATTERNS, _refuse_if_prod

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def supabase_storage():
    """A clean SupabaseStorage instance bound to SUPABASE_TEST_URL.

    Skipped when the env var is missing. Refuses to run against any host
    that matches a production-host pattern. Cleans before and after.
    """
    url = os.environ.get("SUPABASE_TEST_URL")
    if not url:
        pytest.skip("SUPABASE_TEST_URL not set")
    if not _PROD_HOST_PATTERNS:
        pytest.fail(
            "_PROD_HOST_PATTERNS in tests/core/test_storage_contract.py "
            "is empty; populate it before running the supabase suite."
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


def _alert(
    *,
    kind: str = "anomaly_spike",
    event_ref: str | None = "cgm:1",
    sent_at: datetime | None = None,
    payload: dict | None = None,
    pump_serial: str | None = None,
    delivery: str = "pending",
) -> AlertRecord:
    return AlertRecord(
        id=None,
        alert_kind=kind,
        event_ref=event_ref,
        sent_at=sent_at or datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
        payload=payload or {},
        pump_serial=pump_serial,
        delivery=delivery,
    )


# ---------------------------------------------------------------------------
# Partial unique index semantics (alerts_sent)
# ---------------------------------------------------------------------------


class TestPartialUniqueIndex:
    def test_null_event_ref_does_not_dedup(self, supabase_storage):
        """Two record_alert calls with event_ref=None both succeed — the
        partial unique index has ``WHERE event_ref IS NOT NULL`` so null
        rows are NOT covered by it.
        """
        supabase_storage.record_alert(_alert(kind="manual", event_ref=None))
        supabase_storage.record_alert(_alert(kind="manual", event_ref=None))
        alerts = supabase_storage.recent_alerts(
            "manual", within=timedelta(days=365)
        )
        assert len(alerts) == 2

    def test_non_null_event_ref_dedups_to_one_row(self, supabase_storage):
        """Two record_alert calls with the same (alert_kind, event_ref)
        produce one row; second call returns the first record."""
        first = supabase_storage.record_alert(
            _alert(event_ref="cgm:dup", payload={"v": 1})
        )
        second = supabase_storage.record_alert(
            _alert(event_ref="cgm:dup", payload={"v": 2})
        )
        assert second.id == first.id
        alerts = supabase_storage.recent_alerts(
            "anomaly_spike", within=timedelta(days=365)
        )
        matches = [a for a in alerts if a.event_ref == "cgm:dup"]
        assert len(matches) == 1


# ---------------------------------------------------------------------------
# Connection ownership
# ---------------------------------------------------------------------------


class TestConnectionOwnership:
    def test_from_pooler_url_owns_connection(self):
        """``from_pooler_url`` opens its own conn; close() releases it.

        Round-trip: open via from_pooler_url, write a fetch_state row,
        close, reopen on a fresh instance, observe the row survived. The
        closed instance refuses further calls (psycopg2 raises on closed
        conn).
        """
        url = os.environ.get("SUPABASE_TEST_URL")
        if not url:
            pytest.skip("SUPABASE_TEST_URL not set")
        if not _PROD_HOST_PATTERNS:
            pytest.fail("_PROD_HOST_PATTERNS is empty; populate before running.")
        _refuse_if_prod(url)
        from core.storage.supabase import SupabaseStorage

        ts = datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
        first = SupabaseStorage.from_pooler_url(url)
        first.clean_all()
        first.set_fetch_state(
            "tandem", FetchState("tandem", "cursor-x", ts, {"k": 1})
        )
        first.close()

        # Round-trip through a fresh instance.
        second = SupabaseStorage.from_pooler_url(url)
        try:
            got = second.get_fetch_state("tandem")
            assert got is not None
            assert got.last_cursor == "cursor-x"
            assert got.payload == {"k": 1}
        finally:
            second.clean_all()
            second.close()

        # Calls against the closed first instance raise.
        with pytest.raises(psycopg2_interface_error()):
            first.get_fetch_state("tandem")

    def test_caller_managed_conn_is_not_closed(self):
        """``SupabaseStorage(conn=...).__exit__`` is a no-op; the
        connection stays open for the caller's further use."""
        url = os.environ.get("SUPABASE_TEST_URL")
        if not url:
            pytest.skip("SUPABASE_TEST_URL not set")
        if not _PROD_HOST_PATTERNS:
            pytest.fail("_PROD_HOST_PATTERNS is empty; populate before running.")
        _refuse_if_prod(url)
        import psycopg2
        from core.storage.supabase import SupabaseStorage

        conn = psycopg2.connect(url, connect_timeout=10)
        try:
            with SupabaseStorage(conn=conn) as storage:
                storage.clean_all()
            # After the with-block, the conn must still be usable.
            assert conn.closed == 0
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                assert cur.fetchone() == (1,)
        finally:
            conn.close()


def psycopg2_interface_error():
    """Return the psycopg2 exception class that gets raised on a closed conn."""
    import psycopg2
    return psycopg2.InterfaceError


# ---------------------------------------------------------------------------
# clean_all: bigserial identity reset
# ---------------------------------------------------------------------------


class TestCleanAllResetsIdentity:
    def test_clean_all_resets_alerts_sent_identity(self, supabase_storage):
        """``TRUNCATE ... RESTART IDENTITY`` resets the bigserial counter so
        the next insert sees ``id == 1``. Keeps test runs deterministic."""
        ts = datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
        supabase_storage.record_alert(_alert(event_ref="cgm:1", sent_at=ts))
        supabase_storage.clean_all()
        rec = supabase_storage.record_alert(_alert(event_ref="cgm:2", sent_at=ts))
        assert rec.id == "1"


# ---------------------------------------------------------------------------
# Additive record extensions round-trip through Postgres columns
# ---------------------------------------------------------------------------


class TestRecordExtensionsRoundTrip:
    def test_fetch_state_preserves_source_kind_and_payload_keys(
        self, supabase_storage
    ):
        ts = datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
        state = FetchState(
            source_id="tandem",
            last_cursor="abc",
            last_fetched_at=ts,
            payload={"per_event": {"cgm": "2026-05-13"}},
            source_kind="tconnectsync",
        )
        supabase_storage.set_fetch_state("tandem", state)
        got = supabase_storage.get_fetch_state("tandem")
        assert got is not None
        assert got.source_kind == "tconnectsync"
        assert got.last_cursor == "abc"
        assert got.last_fetched_at == ts
        assert got.payload == {"per_event": {"cgm": "2026-05-13"}}

    def test_alerts_sent_preserves_pump_serial_and_delivery(self, supabase_storage):
        ts = datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
        rec = _alert(
            event_ref="cgm:pump-serial-test",
            sent_at=ts,
            pump_serial="PUMP-A",
            delivery="sent",
        )
        inserted = supabase_storage.record_alert(rec)
        assert inserted.pump_serial == "PUMP-A"
        assert inserted.delivery == "sent"

        # Independent re-read via find_alert.
        got = supabase_storage.find_alert(
            "anomaly_spike", "cgm:pump-serial-test"
        )
        assert got is not None
        assert got.pump_serial == "PUMP-A"
        assert got.delivery == "sent"


# ---------------------------------------------------------------------------
# Defensive prod-host denylist
# ---------------------------------------------------------------------------


class TestProdHostDenylist:
    @pytest.mark.parametrize("pattern", _PROD_HOST_PATTERNS or ["unused.example.com"])
    def test_refuse_if_prod_raises_on_match(self, pattern):
        """Calling ``_refuse_if_prod`` with a URL whose host matches a
        production pattern raises :class:`RuntimeError`.

        When ``_PROD_HOST_PATTERNS`` is empty (the initial state until
        we add concrete prod project hostnames), the parametrize uses a
        sentinel that we inject into the local list to exercise the
        match branch.
        """
        # If the real list is empty, parametrize over a synthetic pattern
        # and monkeypatch the module-level constant for this assertion.
        if not _PROD_HOST_PATTERNS:
            from tests.core import test_storage_contract as tc

            original = tc._PROD_HOST_PATTERNS
            tc._PROD_HOST_PATTERNS = (pattern,)
            try:
                url = f"postgres://user:pw@{pattern}:6543/postgres"
                with pytest.raises(RuntimeError, match="production"):
                    _refuse_if_prod(url)
            finally:
                tc._PROD_HOST_PATTERNS = original
        else:
            url = f"postgres://user:pw@{pattern}:6543/postgres"
            with pytest.raises(RuntimeError, match="production"):
                _refuse_if_prod(url)

    def test_refuse_if_prod_allows_test_host(self):
        """Hosts that don't match any prod pattern pass through silently."""
        url = "postgres://user:pw@db.testproject.supabase.co:5432/postgres"
        # Should not raise.
        _refuse_if_prod(url)

    def test_refuse_if_prod_does_not_match_partial_test_url(self):
        """The URL parser correctly extracts the host (sanity check)."""
        parsed = urlparse(
            "postgres://user:pw@db.testproject.supabase.co:5432/postgres"
        )
        assert parsed.hostname == "db.testproject.supabase.co"
