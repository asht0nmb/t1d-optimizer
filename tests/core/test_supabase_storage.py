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

import pandas as pd
import psycopg2
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
        assert first.inserted is True
        assert second.inserted is False
        assert second.record.id == first.record.id
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
        assert rec.record.id == "1"


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
        assert inserted.inserted is True
        assert inserted.record.pump_serial == "PUMP-A"
        assert inserted.record.delivery == "sent"

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


# ---------------------------------------------------------------------------
# Row-Level Security (migration 0003_enable_rls)
# ---------------------------------------------------------------------------


def test_rls_denies_anon(supabase_storage):
    """The ``anon`` Postgres role sees zero rows and can't write either.

    Migration ``0003_enable_rls.sql`` enables RLS on all 13 public
    tables and grants a single permissive ``FOR ALL TO authenticated``
    policy each. The ``anon`` role has no policy, so default-deny
    applies (no permissive policy means zero visible rows on SELECT and
    an insufficient-privilege error on INSERT / UPDATE / DELETE).

    The test reuses the postgres-role pooler connection that the
    ``supabase_storage`` fixture already opens and switches to the
    ``anon`` role via ``SET LOCAL ROLE`` — the same role-switching
    mechanism PostgREST/Supabase use internally when they accept an
    anon-key JWT. (Future per-row policies like
    ``USING (user_id = auth.uid())`` would additionally require setting
    ``request.jwt.claims`` GUCs; the current policies do not reference
    JWT claims so the bare role switch suffices.)

    Two representative tables are checked — one data table (``cgm``)
    and one metadata table (``alerts_sent``) — rather than enumerating
    all 13. The test exists to prove the policy *applies* (and applies
    to both reads and writes); the SQL migration is what guarantees
    coverage, and the ``get_advisors`` MCP check at apply time confirms
    there are no ``rls_disabled`` warnings left.

    Skips automatically when ``SUPABASE_TEST_URL`` is unset (via the
    fixture). The role switch is wrapped in ``try`` / ``finally`` so a
    failed assertion never leaves the shared connection stuck as
    ``anon`` for subsequent tests.
    """
    ts = datetime(2026, 5, 13, 12, 0, tzinfo=UTC)

    cgm_df = pd.DataFrame(
        [
            {
                "pump_serial": "PUMP-A",
                "seqnum": 1,
                "timestamp": ts,
                "bg_mgdl": 120,
                "backfilled": False,
                "sensor_timestamp": None,
            }
        ]
    )
    supabase_storage.upsert_table("cgm", cgm_df)
    supabase_storage.record_alert(
        _alert(kind="rls_smoke", event_ref="rls_smoke:1", sent_at=ts)
    )

    # Sanity: postgres role sees the seeded data — proves the assertions
    # below are testing RLS, not an empty database.
    pg_cgm = supabase_storage.read_table(
        "cgm",
        since=ts - timedelta(hours=1),
        until=ts + timedelta(hours=1),
    )
    assert len(pg_cgm) == 1, (
        "Seeded cgm row not visible to postgres role; test setup is broken, "
        "not RLS."
    )
    pg_alerts = supabase_storage.recent_alerts(
        "rls_smoke", within=timedelta(days=1)
    )
    assert len(pg_alerts) == 1, "Seeded alerts_sent row not visible to postgres role."

    conn = supabase_storage._conn
    anon_insert_error: Exception | None = None
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE anon")
            cur.execute("SELECT count(*) FROM cgm")
            anon_cgm = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM alerts_sent")
            anon_alerts = cur.fetchone()[0]
        conn.rollback()

        # Anon must also be denied writes. Run in a fresh transaction so
        # the failed INSERT doesn't poison the outer one.
        with conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE anon")
            try:
                cur.execute(
                    "INSERT INTO cgm "
                    "(pump_serial, seqnum, timestamp, bg_mgdl, backfilled) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    ("PUMP-X", 99, ts, 100, False),
                )
            except psycopg2.Error as exc:
                anon_insert_error = exc
        conn.rollback()
    finally:
        # SET LOCAL is scoped to the current transaction, so the rollbacks
        # above already drop the role. The explicit RESET ROLE is belt-
        # and-suspenders in case an earlier statement implicitly committed.
        with conn.cursor() as cur:
            cur.execute("RESET ROLE")
        conn.rollback()

    assert anon_cgm == 0, (
        f"RLS not enforced: anon role sees {anon_cgm} cgm rows (expected 0). "
        "Apply db/migrations/0003_enable_rls.sql to this project."
    )
    assert anon_alerts == 0, (
        f"RLS not enforced: anon role sees {anon_alerts} alerts_sent rows "
        "(expected 0). Apply db/migrations/0003_enable_rls.sql to this project."
    )
    assert anon_insert_error is not None, (
        "RLS not enforced for writes: anon role INSERT into cgm succeeded. "
        "Expected an InsufficientPrivilege / RLS violation."
    )
