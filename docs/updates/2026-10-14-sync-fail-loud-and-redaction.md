# 2026-10-14 - Nightly sync: fail loud, stale-data alerts, log redaction

**This is a production change.** It alters the nightly Tandem to Supabase sync
(`.github/workflows/tandem-nightly-sync.yml` ->
`scripts/sync_tandem_to_supabase.py`), which writes the owner's live health
data. Review before merging; do not merge unattended.

## Why

The nightly Action reported green every night while syncing nothing:

- **Aug 24 - Sep 30:** Tandem had no new uploads (the phone app was closed), so
  every run re-fetched the same window and inserted 0 rows.
- **From Oct 1:** Tandem began rejecting the `eventIds` query parameter
  (HTTP 400). Every fetch chunk failed, `ingestion/client.py` swallowed each
  chunk error, and `run_sync` still returned 0, so the workflow's
  `if: failure()` Telegram alert never fired.
- Separately, the pump stopped receiving CGM readings around Aug 17 (sensor
  unpaired from the pump) while basal delivery kept logging, and nothing alerted.
- The repo is public, so Actions logs are public. The logs printed full pump
  serials, pumper/device UUIDs, and tconnectsync's login line
  `Decoded JWT: {...}`, which carries the account holder's name, email and
  account ids.

## What changed

1. **tconnectsync >= 3.0.3** (upstream "Required update", renames `eventIds` to
   `eventCodes`). Only `tconnectsync` was relocked; the PyJWT override is intact.
   Note: 3.0.3 sends an empty `eventCodes=` on the fetch-all path. This was
   verified working against the live API.
2. **Bookmark correctness.** `fetch_pump_events` now returns a `FetchResult`
   (events, contiguous-prefix end, chunks total/failed, failed ranges). The
   `fetch_state` bookmark advances only through the contiguous prefix of
   successful chunks. Events from later chunks are still upserted (idempotent),
   but the bookmark stays before the first failed chunk so it is retried. If the
   first chunk fails, the previous bookmark is kept (never dropped, which would
   cause a full-history refetch) and the bookmark never moves backward.
3. **Fail loud.** `run_sync` exits 1 if any chunk failed for any pump, after
   persisting everything that succeeded. `scripts/ci_tandem_smoke.py` exits 1
   on zero events or any failed chunk.
4. **Data-staleness alerts** (Telegram, sent from Python, text built only from
   durations - no serials, UUIDs or names):
   - *Upload staleness:* the active pump's (latest data range) `lastUploadDate`
     is older than `max_pump_upload_age_hours`: "Tandem hasn't received a pump
     upload in ~Xh - is the t:connect app open on your phone?"
   - *Pump CGM gap:* the fetched window has basal deliveries but the trailing
     stretch has no CGM readings for >= `max_pump_cgm_gap_hours`: "Pump logged
     insulin delivery but no CGM readings for ~Xh - check your G7 is paired to
     the pump." (Mid-window gaps that later resumed are not re-alerted; they are
     already recorded in `cgm_gaps`.)
5. **Log redaction** (`ingestion/redaction.py`), installed on every handler of
   every logger (including `tconnectsync`) by both entrypoints:
   - serials masked to the last 4 characters (`...1234`);
   - UUIDs, email addresses, `pumperId=` / `pump-logs/<id>` URL segments, the
     Telegram bot token, `eyJ...` JWT strings, the whole `Decoded JWT: {...}`
     payload and JWT claim keys (`given_name`, `family_name`, `name`, `email`,
     `sub`, `sid`, `accountId`, `pumperId`, ...);
   - runtime-learned values: pump serials, assignment ids, pumper/account ids,
     and the account holder's first/last/full name (read from the login claims
     and `get_pumper()` at runtime, never hardcoded);
   - applied to the formatted message, record args, and traceback text; uncaught
     exceptions go through a redacting `sys.excepthook`.
   - **Primary control for the login JWT line:** the `tconnectsync` logger is
     held at WARNING or higher (set before login in `get_api()` and in both
     entrypoints), because login happens before any value is known. Value and
     pattern redaction is the second layer.
   - Under `GITHUB_ACTIONS=true` only, `::add-mask::` is emitted for serials,
     assignment/pumper/account ids and the name values. Never emitted locally.
6. **`scripts/repair_nightly_sync_tz.py`** now takes a required `--serial`
   (or `REPAIR_PUMP_SERIAL`); no serial is stored in source. The argument is
   validated before any DB connection.
7. **Workflow:** the sync step now receives `TELEGRAM_BOT_TOKEN` /
   `TELEGRAM_CHAT_ID` from secrets (needed for the specific alerts). No cron,
   action-version, or enable/disable changes. No `-v` / DEBUG flags anywhere;
   identity values come from `secrets.*` (pinned by `tests/test_workflow_hygiene.py`).

## Exit-code and alert policy

| Condition | Python Telegram | Exit | Generic workflow alert |
|---|---|---|---|
| Clean run | none | 0 | no |
| Stale upload and/or CGM gap, alert delivered | specific message | 0 | no (avoids double alert) |
| Staleness alert could not be delivered (no token / API error) | none | 1 | yes (fallback) |
| Any failed fetch chunk, auth/metadata error, crash | none | 1 | yes |
| Stale upload AND failed chunks | specific message | 1 | yes (two causes, two messages) |

`--dry-run` never sends Telegram; it logs what it would send. The upload
staleness alert is sent before the fetch, so it still goes out if the sync
later crashes. Because the job runs nightly, a persistent condition re-alerts
once per night by design.

## New config keys (`config/user_config.yaml`, last block, owner-editable)

```yaml
sync_health:
  max_pump_upload_age_hours: 24   # stale-upload alert threshold
  max_pump_cgm_gap_hours: 6       # pump CGM-gap alert threshold
```

Both must be positive numbers; defaults apply if the block is removed. The
typed model `SyncHealthConfig` is `AppConfig.sync_health` in
`detection/config.py` (optional block, trailing defaulted field).

## After merge: what to expect and watch

- **The first nightly backfills.** The stored bookmark is stuck at about Aug 21,
  so the first run fetches Aug 21 through now (several 30-day chunks) and
  upserts a large batch. This is expected; it may take noticeably longer than a
  normal run (the job timeout is 60 minutes).
- The first run will probably also send the **pump CGM gap** alert if the sensor
  is still not paired to the pump, and the **upload staleness** alert if the
  phone app has not synced. Those are real conditions, not bugs.
- Watch the next few runs for: exit status, the `N/M chunks fetched, K failed`
  log line (K should be 0), and that the dashboard's latest-data date advances.
  A red run now means a real problem and will page Telegram.
- Pump CGM readings must be restored on the device side (re-pair the G7 to the
  pump); this change only makes the condition visible.
- Historical Actions logs from before this change are being removed separately.

## Verification

- Full pytest: 877 passed (baseline 749); new tests cover bookmark
  contiguity, fail-loud exit codes, staleness/CGM-gap checks, Telegram sending
  (mocked), redaction (message, args, traceback, names, JWTs, logger level),
  workflow hygiene and the repair-script CLI.
- Live dry-run (read-only, `--dry-run --only <active pump>` over Aug 21-22,
  1/1 chunks, 0 failed): zero occurrences of any real serial, UUID, email, name,
  account id, `eyJ` token or `Decoded JWT` text in the captured output.
