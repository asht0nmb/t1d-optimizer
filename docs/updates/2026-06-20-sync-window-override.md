# 2026-06-20 — Bounded fetch window for the Tandem → Supabase sync

## Problem

The nightly `scripts/sync_tandem_to_supabase.py` (and any manual run) computes
its fetch window from the `fetch_state` bookmark in Postgres. That table was
**empty** — the historical load came from `bootstrap_supabase.py` (which leaves
`fetch_state` empty by design), and the nightly Action had never completed a
successful write to seed it. With no bookmark, `compute_fetch_window` falls back
to `minDateWithEvents → max`, i.e. **the full 2021→present history every run**.

Two consequences:

1. **The nightly Action kept failing.** Re-pulling ~5 years of pump events and
   upserting ~300k CGM rows under a 60-minute job timeout is a plausible
   stuck loop: it never finishes → never seeds the bookmark → tries the full
   pull again the next night.
2. **`--dry-run` was misleading.** Dry-run never opens the DB, so it can't read
   the bookmark; it always previewed full history and re-fetched everything from
   the API (skipping only the DB write).

## Change (TDD)

Added `--start` / `--end` (ISO-validated `YYYY-MM-DD`) to the sync script. They
override both the full-range fallback and the bookmark:

- `compute_fetch_window(pump, fetch_state, *, start_override, end_override)` —
  overrides take precedence; `end` defaults to the pump's latest event date.
- Threaded through `process_pump` → `run_sync` → `parse_args` → `main`.
- `--dry-run --start <date>` now previews exactly the bounded window (no DB
  needed), so you can confirm the gap before writing.

A successful (non-dry) run still writes `fetch_state`, so a single bounded run
**seeds the bookmark** and the nightly resumes incrementally on its own. Upserts
remain `ON CONFLICT DO NOTHING`, so an overlapping start day is harmless.

7 new tests in `tests/test_sync_tandem_to_supabase.py` (window-override matrix,
dry-run honoring `--start`, arg parsing + invalid-date rejection). Suite:
**737 passed / 42 skipped / 48 deselected**.

## Gap fill executed (owner-approved prod write)

Ran `--start 2026-04-15` against prod Supabase. Inserted: cgm 7,835, basal
12,363, requests 340, bolus 340, alarms 1,364, events 490, suspension 55,
cgm_gaps 83 (skips = the one-day overlap). `fetch_state` for the active pump now
bookmarks `last_successful_chunk_end = 2026-05-28`; pipeline version set to 3.

Supabase is current to **2026-05-28** — the active pump's latest data in Tandem's
cloud. Anything after that has not been uploaded from the pump to Tandem yet
(pump-side, outside this pipeline's reach). The 5 retired pumps returned 0
events (their data predates the start), as expected.

## Follow-ups (owner)

- The nightly Action should now succeed (incremental, fits the timeout). Watch
  the next run; if it still fails it's a separate cause (auth/secrets), not the
  window.
- To pull past 2026-05-28, the pump first has to upload to Tandem.
