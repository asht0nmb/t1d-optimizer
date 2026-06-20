# 2026-06-20 — Nightly Tandem→Supabase sync hardening (retired-pump bookmarks + autocommit)

Follow-up to `2026-06-20-sync-window-override.md`. Running the unscoped sync
surfaced the real, larger reason the nightly GitHub Action keeps failing.

## Root cause

The account has **6 pumps** (1 active, 5 retired). `fetch_state` had **no
bookmark for any of them**. With no bookmark, `compute_fetch_window` falls back
to `minDateWithEvents → max` — a **full-history re-pull for all 5 retired pumps**
(2021 onward) every single run.

Two failure modes compound:

1. **Timeout.** Six full-history pulls + upserts cannot finish inside the
   Action's 60-minute budget.
2. **SSL drop.** `get_fetch_state` issues a SELECT and (by design) does **not**
   commit, so the direct connection sits *idle-in-transaction* through the
   multi-minute fetch and trips `idle_in_transaction_session_timeout='5min'`
   (migration 0002) → `psycopg2.OperationalError: SSL connection has been closed
   unexpectedly` mid-sync. Observed live on the retired pump `…4750` (a ~7-minute
   full-history fetch).

Because the run dies before completing, it never seeds any bookmark → the next
night repeats the full pull. A self-perpetuating loop.

## Fixes

**1. Connection hardening (TDD).** `_connect_storage` now opens the direct
connection with `autocommit=True` and TCP keepalives. Autocommit ends each
statement's transaction immediately, so a read-only helper can never leave the
connection idle-in-transaction across a long fetch; `upsert_table`'s per-chunk
`commit()` calls become harmless no-ops (durability unchanged — it already
committed per chunk). Keepalives guard against network-level idle drops. Two new
tests assert autocommit + keepalives on the opened connection.

**2. Seeded retired-pump bookmarks (one-time, owner-approved prod writes).** Ran
`--only <serial> --start <maxDate-1>` for each of the 5 retired pumps. Each
fetched ~1 day of already-present data (all `inserted=0`, conflict-skipped) and
seeded its `fetch_state` bookmark at the pump's final event date. `fetch_state`
now holds **6/6 pumps**.

## Verification

The unscoped sync — the Action's exact command — now fetches a **1-day window
per pump** (e.g. `…4750: 2022-03-18 → 2022-03-19`, `…3861: 2026-05-30 →
2026-05-31`), 0 failed chunks, **no SSL drop**, and completes in **~13 seconds**
(was: 60-min timeout). Suite: **739 passed / 42 skipped / 48 deselected**.

Supabase remains current to **2026-05-31** (Tandem's ceiling). cgm 308,927 rows,
full 2021→present history intact.

## Follow-ups (owner)

- The next scheduled nightly should now succeed on its own. If it still fails,
  it's an auth/secrets issue (`TCONNECT_*`, `SUPABASE_DB_URL`), not the window.
- Retired pumps will re-fetch their final 1-day window nightly forever (tiny,
  conflict-skipped). A future optimization could skip a pump when its bookmark
  already equals `maxDateWithEvents`, but it isn't necessary for correctness.
