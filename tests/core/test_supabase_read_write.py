"""SupabaseStorage forces READ WRITE transactions (pooler session-leak defence).

Incident 2026-10-14: a diagnostic client on the Supabase transaction-mode
pooler ran ``conn.set_session(readonly=True, autocommit=True)``. In
autocommit mode psycopg2 implements that as a *session-level*
``SET default_transaction_read_only = on``, which persisted on the shared
pooled backend. The live alert worker later drew that backend and every
write failed with ``ReadOnlySqlTransaction``.

Defence: ``SupabaseStorage.__init__`` calls ``conn.set_session(readonly=False)``
on non-autocommit connections. Verified against psycopg2 2.9.12 C source:

* ``conn_set_session`` (connection_int.c L1296-1320) only issues SQL
  (``SET default_transaction_*``) when the connection is, or is becoming,
  autocommit. On a non-autocommit connection it only records
  ``self->readonly`` (L1358-1360) — no statement is sent, so no session
  state is created on the pooler.
* ``pq_begin_locked`` (pqpath.c L359-378) then opens every transaction with
  ``BEGIN READ WRITE`` (``srv_readonly`` connection_int.c L50-53), which
  overrides any leaked ``default_transaction_read_only = on`` for that
  transaction.

Autocommit connections (only ``scripts/sync_tandem_to_supabase.py``, which
uses the DIRECT, unshared connection) are deliberately left untouched: on
them ``set_session`` would itself issue a session-level ``SET``.

The unit tests below need no database. The integration test at the bottom
skips unless ``SUPABASE_TEST_URL`` is set and refuses production hosts.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg2
import psycopg2.extensions
import pytest

from core.storage.records import FetchState
from core.storage.supabase import SupabaseStorage


class _FakeConn:
    """Minimal psycopg2-connection stand-in recording ``set_session`` calls."""

    def __init__(
        self,
        *,
        autocommit: bool = False,
        status: int = psycopg2.extensions.STATUS_READY,
        closed: int = 0,
    ) -> None:
        self.autocommit = autocommit
        self.status = status
        self.closed = closed
        self.set_session_calls: list[dict] = []

    def set_session(self, **kwargs) -> None:
        self.set_session_calls.append(kwargs)

    def close(self) -> None:
        self.closed = 1


class TestForcesReadWrite:
    def test_caller_managed_non_autocommit_conn_is_forced_read_write(self):
        conn = _FakeConn(autocommit=False)
        SupabaseStorage(conn=conn)
        assert conn.set_session_calls == [{"readonly": False}]

    def test_from_pooler_url_conn_is_forced_read_write(self, monkeypatch):
        conn = _FakeConn(autocommit=False)
        monkeypatch.setattr(
            "core.storage.supabase.psycopg2.connect", lambda *a, **k: conn
        )
        storage = SupabaseStorage.from_pooler_url("postgresql://example.invalid/db")
        assert storage._owns_conn is True
        assert conn.set_session_calls == [{"readonly": False}]

    def test_autocommit_conn_is_left_untouched(self):
        # set_session on an autocommit conn would itself emit a
        # session-level SET -- exactly the pooler hazard we are defending
        # against -- so autocommit callers (the nightly sync, on the
        # direct connection) are deliberately not touched.
        conn = _FakeConn(autocommit=True)
        SupabaseStorage(conn=conn)
        assert conn.set_session_calls == []

    def test_conn_mid_transaction_is_left_untouched(self):
        # psycopg2 raises ProgrammingError if set_session is called inside
        # an open transaction; a caller handing over such a conn keeps
        # whatever characteristics that transaction already has.
        conn = _FakeConn(
            autocommit=False, status=psycopg2.extensions.STATUS_IN_TRANSACTION
        )
        SupabaseStorage(conn=conn)
        assert conn.set_session_calls == []

    def test_closed_conn_is_left_untouched(self):
        conn = _FakeConn(autocommit=False, closed=1)
        SupabaseStorage(conn=conn)
        assert conn.set_session_calls == []


# ---------------------------------------------------------------------------
# Integration (skips without SUPABASE_TEST_URL; never production)
# ---------------------------------------------------------------------------


def test_write_succeeds_despite_leaked_read_only_default():
    """Simulate the leak on our own session, then prove a write still lands.

    The leaked setting is applied with raw SQL (so psycopg2 does not know
    about it and will not revert it when autocommit is switched off), on
    the same connection we then hand to SupabaseStorage. Note: against a
    transaction-mode pooler the SET may land on a different backend than
    the later write, so a pass there can be vacuous; the unit tests above
    plus the cited psycopg2 source are the primary proof.
    """
    url = os.environ.get("SUPABASE_TEST_URL")
    if not url:
        pytest.skip("SUPABASE_TEST_URL not set")
    from tests.core.test_storage_contract import _PROD_HOST_PATTERNS, _refuse_if_prod

    if not _PROD_HOST_PATTERNS:
        pytest.fail("_PROD_HOST_PATTERNS is empty; populate before running.")
    _refuse_if_prod(url)

    conn = psycopg2.connect(url, connect_timeout=10)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SET default_transaction_read_only = on")
            cur.execute("SHOW default_transaction_read_only")
            assert cur.fetchone()[0] == "on"
        conn.autocommit = False

        storage = SupabaseStorage(conn=conn)
        storage.clean_all()
        ts = datetime(2026, 10, 14, 21, 0, tzinfo=timezone.utc)
        storage.set_fetch_state(
            "tandem", FetchState("tandem", "rw-cursor", ts, {"k": 1})
        )
        got = storage.get_fetch_state("tandem")
        assert got is not None and got.last_cursor == "rw-cursor"
        storage.clean_all()
    finally:
        try:
            conn.rollback()
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("RESET default_transaction_read_only")
        finally:
            conn.close()
