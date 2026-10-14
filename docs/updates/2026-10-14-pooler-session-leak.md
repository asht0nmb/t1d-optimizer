# 2026-10-14: Pooler session leak broke live-loop writes

## Summary

From about 20:55 to 21:09 UTC, every database write from the live meal-rise
alert worker failed with
`psycopg2.errors.ReadOnlySqlTransaction: cannot execute INSERT in a read-only transaction`.
The worker's heartbeat (`fetch_state`, written by `SupabaseStorage.set_fetch_state`)
stopped advancing for about 15 minutes. Alert-claim writes (`record_alert`) would
also have failed, so any meal-rise alert in that window could not have been sent.
No data was lost or corrupted. Reads kept working.

## Root cause

- The worker reaches Postgres through the Supabase **transaction-mode pooler**
  (port 6543). That pooler hands the same server backend to different
  clients one transaction at a time, so any *session-level* state one client
  sets stays on the backend for whoever gets it next.
- A one-off diagnostic client connected through the same pooler and ran
  `conn.set_session(readonly=True, autocommit=True)`. With autocommit on,
  psycopg2 can't put `READ ONLY` on a `BEGIN`, so it runs a session-level
  `SET default_transaction_read_only = on` instead.
- That setting stayed on the pooled backend. When the production worker was
  later given that backend, all of its transactions started read-only by
  default, so every INSERT and UPDATE failed.

## Remediation

The setting was reset on the affected backend, and writes and the heartbeat
recovered right away.

## Fix (this PR)

`SupabaseStorage.__init__` (which covers both `from_pooler_url` and the
caller-managed `conn=` path) now calls `conn.set_session(readonly=False)` on
every connection that is not in autocommit, not closed, and not mid-transaction.

This was checked against the psycopg2 2.9.12 C source; the venv pins 2.9.12:

- `conn_set_session` (`psycopg/connection_int.c`, L1296-1320) sends
  `SET default_transaction_*` only when the connection is, or is becoming,
  autocommit. On a non-autocommit connection it just records the flag
  (L1358-1360) and sends no SQL, so it adds no session state on the pooler.
- `pq_begin_locked` (`psycopg/pqpath.c`, L359-378) then starts every
  transaction with `BEGIN READ WRITE` (`srv_readonly[STATE_OFF]`,
  `connection_int.c` L50-53). An explicit `READ WRITE` on `BEGIN` takes
  precedence over a leaked `default_transaction_read_only = on` for that
  transaction.

Reads run inside the same `READ WRITE` transactions, so they need no separate
guard.

### Autocommit callers: left untouched on purpose

On an autocommit connection, `set_session(readonly=False)` would itself send a
session-level `SET default_transaction_read_only = off`. That is harmless in
value, but it is the same kind of pooler session state this fix is meant to
avoid, and `SET LOCAL` does nothing outside a transaction. The only autocommit
caller is `scripts/sync_tandem_to_supabase.py`, and it uses the **direct**
connection, whose backend is not shared with other clients, so it can't pick up
a setting leaked by someone else. Autocommit connections are therefore skipped,
and this is documented in `_force_read_write_transactions`.

## Prevention

- `tests/core/test_supabase_read_write.py` has unit tests (no database
  needed) for the following:
  - non-autocommit connections, both caller-managed and `from_pooler_url`,
    get `set_session(readonly=False)`;
  - autocommit, closed and mid-transaction connections are left alone.

  It also has an integration test, which skips without `SUPABASE_TEST_URL` and
  refuses production hosts. That test leaks `default_transaction_read_only = on`
  into its own session, shows that a write still succeeds, and then resets the
  setting.
- `tests/test_no_pooler_session_leak.py` is a lint-style test. It fails if any
  git-tracked non-test Python file combines psycopg2 `readonly=True` with
  `autocommit=True`.
- `scripts/replay_meal_rise_live.py` uses `set_session(readonly=True)` *without*
  autocommit, which is safe. It now has a comment explaining why autocommit
  must never be enabled on that connection.
- `CLAUDE.md`'s "Storage abstraction" section now says that clients must never
  issue session-level `SET`s on the transaction pooler.
- Operational rule: diagnostic or ad-hoc read-only sessions should use
  `set_session(readonly=True)` with autocommit **off**, or the direct
  connection. Never use readonly with autocommit on the pooler.
