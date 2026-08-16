# 2026-08-16 — Nightly Tandem sync restoration (tconnectsync v3 + TZ repair)

Overnight autonomous session (`/remote-control`). Orientation found the
nightly Tandem → Supabase sync had been silently dead for 47 days (last
success 2026-06-30); this entry covers root-causing and fixing it, a second
unrelated bug found while verifying the fix, and the resulting 47-day
backfill. Written as the session's Phase 1 of an 8-phase plan; later phases
get their own dated entries as they land.

## What was broken

Every nightly run since 2026-06-30 failed on the very first API call
(`pump_event_metadata`) with `HTTP 403 "The request is blocked"`. Root
cause: Tandem shipped a breaking internal API change on 2026-06-30 (the
same day as our last success) requiring `tconnectsync>=3.0.0`; we were
pinned to `>=2.3.4`, resolved to `2.3.4`.

The **live 5-minute meal-rise alert loop was unaffected** — it polls Dexcom
Share directly, a separate code path that doesn't use tconnectsync. Alerts
kept firing throughout the outage; only the historical pipeline that feeds
Supabase's `cgm`/`bolus`/etc. tables (and therefore every dashboard beyond
~47 days of live-loop-only data) went stale.

## Fix 1 — tconnectsync v3 migration

- `pyproject.toml`: `tconnectsync>=3.0.0` (resolved 3.0.1).
- `ingestion/client.py::get_pump_metadata`: v3 replaced
  `pump_event_metadata()` with `get_pumper()` (a different BFF endpoint),
  nesting pumps under `.pumps` and renaming `tconnectDeviceId` →
  `assignmentId` (int → UUID string) and `minDateWithEvents`/
  `maxDateWithEvents` → `availableDataRange.start`/`end`. Normalized the new
  shape back to the legacy field names in one place so every downstream
  caller (`scripts/sync_tandem_to_supabase.py`, `scripts/ci_tandem_smoke.py`)
  needed zero changes.
- `ingestion/builders.py`: v3's typed event objects use true camelCase
  field names throughout (`currentGlucoseDisplayValue`, `bolusId`,
  `insulinAmount`, `suspendReasonRaw`, etc.) instead of v2's
  lowercase-with-Raw-suffix convention. Every renamed field was verified
  directly against the installed v3 source
  (`tconnectsync/eventparser/events.py`), not guessed.
- Also fixed a latent `egvTimeStamp` casing bug (code used `egvTimestamp`,
  lowercase s) that silently made `getattr(e, 'egvTimestamp', None)` always
  return `None`, regressing backfilled-CGM `timestamp` to pump-reconnect
  time instead of the documented v3 sensor-time semantics. Confirmed via
  the cached v2.3.4 source that the old casing was correct pre-migration —
  no previously-synced data was affected by this specific defect, but it
  would have silently broken going forward under the new dependency had it
  not been caught. **Bumped `PIPELINE_VERSION` 3→4** to document the
  dependency migration (a structurally different transport/decode path)
  plus this fix.
- Test suite blind spot found in the process: `tests/test_builders.py`,
  `test_alarms.py`, `test_suspension.py` used `MagicMock(spec=OldClass)`
  with the *old* field names as fixtures. Plain `spec=` doesn't restrict
  attribute *assignment*, only access on the real class — so the 742-test
  suite stayed green through the entire outage despite every event field
  name being wrong. Fixtures now use the verified v3 names. This is a
  real gap (mocks decoupled from the real dependency's shape) worth a
  dedicated look — noted for Phase 3.
- Verified against the **live Tandem Source API**, not just updated unit
  tests: fetched real pre-outage windows, ran them through the builders,
  and diffed against the same windows already in Supabase from the old v2
  pipeline. Clean once window bounds were aligned (v3's `pump_events`
  windowing is inclusive; naive UTC bound comparison undercounted by
  exactly the boundary-day artifact, not a real discrepancy).
- `.github/workflows/{tandem-nightly-sync,test-tandem-sync}.yml`: see fix 2.

## Fix 2 — TIMEZONE_NAME never wired into the workflow (second bug)

Found while doing the live-API verification above, unrelated to the v3
migration: `.github/workflows/tandem-nightly-sync.yml`'s `env:` block never
referenced the `TIMEZONE_NAME` secret, even though it has existed in repo
secrets since 2026-05-06. tconnectsync silently defaulted to
`America/New_York` instead of the owner's actual `America/Los_Angeles` for
every timestamp it decoded on every GitHub-Actions-run sync.

**Scope, verified empirically (not assumed):**
- Bootstrap-era data (loaded locally with the correct `.env`) is
  **confirmed correct** — two bolus rows (id 1519, 1520, early May) match
  a fresh live re-fetch exactly under Pacific time.
- Binary-searched the exact transition: bolus_id ≤ 1867 and cgm
  seqnum < 510402 (2026-05-31, true local time ~13:55 Pacific) are correct;
  bolus_id 1868 and cgm seqnum 510402 onward (~14:07 Pacific the same day,
  11 seconds apart across two independent tables — the same broken sync
  run) through the last successful nightly run (2026-06-30) are mislabeled
  by exactly **-3 hours** (PDT is UTC-7, EDT is UTC-4).
- Checked `pump_clock_changes()` over the window: 4 clock-change events,
  all pump RTC drift corrections, none suggesting a real relocation.
- `upsert_table` uses `ON CONFLICT ... DO NOTHING`, so re-syncs never
  overwrote an already-present row — the corrupted window is exactly
  bounded by first-insert time, not fuzzy.

**Fix:** added `TIMEZONE_NAME: ${{ secrets.TIMEZONE_NAME }}` to both
workflows' `env:` blocks.

**Repair:** `scripts/repair_nightly_sync_tz.py` (dry-run by default,
`--apply` to execute) shifted every affected timestamp column by +3 hours
across all 9 tables, scoped to the verified boundary
(`2026-05-31 18:00:00 UTC` ≤ ts < `2026-07-01 00:00:00 UTC`). Tables whose
primary key includes the timestamp column (`basal`, `suspension`,
`site_issues`, `cgm_gaps`) needed a two-hop shift through a disjoint
100-year-offset range — a single-hop `UPDATE` hit a real same-statement PK
swap collision on `basal` (two rows already exactly 3 hours apart in their
corrupted state), rolled back cleanly via the transaction, then fixed.
Applied and committed to production Supabase this session (19,029 rows
across 9 tables); verified via fresh re-fetch spot-checks across
bolus/basal/cgm_gaps and multiple dates in June. Hardened against
accidental re-application with a sentinel check.

**Not done:** did not audit whether this same TIMEZONE_NAME gap affected
anything besides the nightly sync (e.g., a one-off manual script run) —
the live loop doesn't use tconnectsync, and bootstrap ran locally, so
nothing else touches this code path today.

## Backfill

Ran `scripts/sync_tandem_to_supabase.py` in four windows from
2026-06-29 through today (2026-08-16), all under the fixed pipeline (v3 +
correct TZ). Supabase now has `cgm`/`bolus`/`basal` data through
2026-08-12 — matching the pump's own `maxDateWithEvents`, i.e. the gap is
fully closed, not just partially filled.

## Verification

- `uv run pytest`: 745 passed, 42 skipped, 48 deselected (was 742/42/48).
- Manually triggered `tandem-nightly-sync.yml` via `workflow_dispatch` on
  the fix branch for real CI evidence (not just local verification) —
  [run 31935528846](https://github.com/asht0nmb/t1d-optimizer/actions/runs/31935528846),
  **success**.

## Follow-ups for later phases

- Phase 3: the alarm/suspension correlation bug found during DATA_ISSUES
  re-verification (`build_suspension_df`'s `alarm_lookup` dict is keyed by
  timestamp only, last-write-wins — when multiple alarms fire at the exact
  same instant, e.g. `BatteryShutdownAlarm` + `ResumePumpAlarm2` both at
  2026-03-19 08:06:18, the suspension's `alarm_name` silently shows
  whichever was last in iteration order instead of the causal alarm).
  Pre-existing, not introduced by tonight's changes.
- Phase 3: the `MagicMock(spec=...)` test-fixture blind spot above.
- Phase 6: fold this session's earlier `SESSION_SUMMARY.md` (web-app QA
  click-through, still open) in with Phase 2's findings.
