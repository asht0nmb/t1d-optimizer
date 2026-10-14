# 2026-10-14 - Web /status: worker liveness vs CGM flow

Stacked on PR #14 (`security/next-upgrade`); depends on the `live_cron`
semantics introduced by PR #15 (`fix/live-loop-observability`). Read-only
dashboard change: no migration, no worker change, auth/session guard untouched.

## Why

After #15, `fetch_state.last_synced_at` for `source_id='live_cron'` advances
only when the live loop evaluated FRESH CGM. The old "Live loop" row read that
column, so it could no longer tell "worker dead" from "Dexcom blind". #15's
write-up listed this web follow-up.

## What changed (`apps/web`)

- `lib/queries/status.ts`: `fetch_state` SELECT now also returns `meta` and a
  UTC-ISO copy of `last_synced_at` (for correct age maths; the existing
  local-time text is kept for the generic sync rows). `RawFetchState` gains
  both fields.
- `lib/live-loop.ts` (new, pure, no pg import): maps the row to two signals,
  15-minute threshold each.
  - **Live loop (worker)**: `meta.last_cycle_at`; if absent or unparseable
    (legacy rows) falls back to `last_synced_at` and says "legacy heartbeat".
  - **Live CGM**: `last_synced_at`; detail = `last: <last_outcome> · reading N
    min old · today: <outcome_counts for the local day>`.
  - `meta` may be null, `{"last_cursor": null}` (current production row) or
    malformed: every field is type-checked, bad fields are dropped.
- `__tests__/live-loop.test.ts`: missing/malformed meta, stale worker, fresh
  worker + stale CGM, all healthy (with counts), boundary.
- The status page component is unchanged; it renders signals generically.

## Caveats

- Until #15 is deployed, the production row has no `last_cycle_at`, so both
  rows track `last_synced_at` (worker row labelled legacy).
- The "today" key uses the dashboard timezone; the worker keys counts by its
  configured local timezone. They match for this single-user setup.

## Review follow-ups

- Outcome-count day keys come from the worker (`config.timezone`), while the
  web timezone is `process.env.TZ` (default America/Los_Angeles); they can
  differ. The page now shows the newest worker day key as-is
  (`counts 2026-10-14: ...`) instead of recomputing "today".
- `classifyFreshness` now treats timestamps >5 min in the future as "stale"
  (warning badge). To keep that from misfiring on naive local-time strings,
  all freshness signals (CGM, detection, sync rows) classify the UTC ISO value
  and only display the local text.
