# 2026-10-14 — Live loop observability (production change)

**This changes the live health-data worker.** It edits the Vercel cron worker
(`apps/personal/cron/`) and the Telegram `/status` command
(`apps/personal/telegram/`). Detection logic, thresholds and existing
`meal_rise` config values are untouched.

## HEADLINE FIX: Dexcom timestamps were shifted by the Share UTC offset

`fetch_dexcom_cgm` did `r.datetime.replace(tzinfo=timezone.utc)`. pydexcom
0.5.1 returns a tz-AWARE datetime built from the Share `DT` field
(`Date(<epoch ms><+/-HHMM>)`): the epoch is the true UTC instant and the
offset is only the display timezone (the live account is UTC-07:00).
`.replace()` relabels the wall clock as UTC, shifting every reading 7 h into
the past. Verified on live data: a reading truly 5.0 min old was computed as
425.0 min old, so the 15-min freshness guard skipped EVERY cycle. This is why
`detection_results` / `alerts_sent` have had zero rows since the loop started:
the meal-rise alert has been unable to fire since about late May.

Fix: `astimezone(timezone.utc)` for aware datetimes (naive still treated as
UTC). Audit of the rest of the live path: `normalize_dexcom_readings`
(`tz_convert` on aware timestamps), the `live_health` age computations and the
`/status` reading age all operate on tz-aware values / ISO strings with
offsets, so they were only wrong because of this one conversion. Regression
tests (`tests/detection/test_live_path_dexcom_tz.py`) use REAL
`pydexcom.GlucoseReading` objects with `DT` offsets -0700, -0400 and +0000 and
assert the true instant, a correct reading age, a 120 -> 185 lunch rise firing
end to end, and no false stale alert across 24 h of healthy data. (Without the
fix, my own new stale-CGM alert would have fired falsely forever.)

### PRODUCTION IMPACT of this fix: the live alert starts firing for the first time

Once deployed, meal-rise alerts will reach Telegram for the first time. A
historical replay estimates about **21 alerts/week (~3/day)**. Roughly **16%
are pre-bolused false alarms**, and the live path does not check boluses.
Threshold and volume tuning is the owner's call; **no thresholds were changed**
in this PR. Expect the first alerts within hours of deploy.

## Why (observability)

The August audit (`2026-08-16-live-system-health-audit.md`) found that
`detection_results` and `alerts_sent` had zero rows since the loop started
and that a healthy-looking heartbeat said nothing about whether the loop
could see glucose data. On 2026-10-13/14 Dexcom Share returned no readings
for ~24 h (G7 phone app disconnected) while the `live_cron` heartbeat stayed
fresh the whole time, so both the web `/status` page and Telegram `/status`
reported "Live loop: ok". Nothing told the owner the loop was blind.

Root cause in code: `run_cron()` returned 0 on empty Dexcom data and on a
stale newest reading, and the `finally` block wrote the heartbeat on every
non-raising cycle.

## What changed

### 1. Truthful heartbeat (no DB migration)

The `fetch_state` row `source_id = 'live_cron'` is now read-modify-written
every cycle (`apps/personal/cron/live_health.py`):

- `last_synced_at` (`FetchState.last_fetched_at`) advances **only on a cycle
  that evaluated fresh CGM and completed without error** (newest reading within
  `meal_rise.max_reading_age_minutes`; if windowing/detection raises afterward
  it is not advanced). On `no_data`, `stale` and `error`
  cycles the previous value is carried forward. If the prior state cannot be
  read, nothing is written (a blind write would wipe counters).
- `meta` (jsonb, plain JSON only):

| key | meaning |
|---|---|
| `last_cycle_at` | ISO time of the latest completed cycle (worker liveness) |
| `last_outcome` | `no_data`, `stale`, `insufficient_window`, `no_candidate`, `suppressed_refractory`, `suppressed_duplicate`, `suppressed_claim_lost`, `detected`, `alert_sent`, `error` |
| `last_reading_at` / `last_reading_age_min` | newest Dexcom reading ever seen (carried forward when a cycle sees none) and its age at `last_cycle_at` |
| `outcome_counts` | `{"YYYY-MM-DD": {outcome: n}}` local-day counters, newest 14 days kept |
| `stale_since`, `stale_alert_sent_at`, `recovery_pending`, `fresh_since` | blind-episode state for the alert below (`stale_alert_sent_at` persists across episodes) |

Outcome notes: `detected` = rise found and claimed but Telegram delivery
failed; `alert_sent` = rise found and delivered; a missing Telegram config on
a detection is recorded as `error` (the cycle still returns 1 as before).
`insufficient_window` is derived from the same guards `detect_meal_rise`
applies (`min_samples`, `min_coverage`, `has_gap`) without modifying it.
Per-cycle outcomes are deliberately NOT written to `detection_results`: the
web "Last detection" signal and Telegram `/status` use `MAX(created_at)` over
that table, and cycle logs there would make both look healthy.

Consequence for existing readers: the web `/status` "Live loop" row reads
`last_synced_at` with a 15-min threshold, so it now goes stale whenever fresh
CGM stops arriving (Dexcom blind, sensor warmup/swap, phone disconnected).
That is the truthful signal for "the loop can detect meals", but it can no
longer distinguish "worker dead" from "Dexcom blind". See the web follow-up
below. The Postgres `updated_at` column still bumps on every write.

### 2. Stale-CGM Telegram alert

When the newest Dexcom reading is more than `cgm_stale_alert_minutes` old (or
none has been seen) the worker sends one Telegram message
("No Dexcom readings for ~Xmin - is the G7 app connected and Share turned
on? ..."). Messages contain only a minute count - no glucose values, no
usernames. De-duplication lives in `meta`:

- **Persist-then-send.** The marker (`stale_alert_sent_at`,
  `recovery_pending`) is written to storage BEFORE the message is sent, and if
  that write fails nothing is sent. Chosen because it fails toward fewer
  messages: a flaky database can at worst lose an alert, never repeat one every
  5-minute cycle (the first version sent-then-persisted; a simulated run of 29
  failed writes produced 17 messages). If the send itself fails, the marker is
  reverted best-effort so the next cycle retries; if that revert write also
  fails the alert is lost, not repeated.
- **Cross-episode hysteresis.** `stale_alert_sent_at` is kept across episodes;
  no new alert (in this or a later episode) is sent within
  `cgm_stale_realert_hours` (12) of the previous one.
- **Sustained recovery.** An alerted episode is only closed, and the
  one-line recovery note sent, after `cgm_recovery_fresh_minutes` (30) of
  UNBROKEN fresh cycles (`fresh_since` streak, reset by any non-fresh cycle).
  With sparse readings (e.g. one every 70-90 min, just over the 60-min
  threshold) the episode simply stays open, so the owner gets about 2
  messages per 24h (alert at ~1h, again at ~13h) instead of an alert plus a
  recovery for every gap (previously ~40/24h). A real 3-day outage still
  re-alerts every 12h (6 alerts). The recovery note is best-effort (the
  episode is closed before it is sent; a lost note is not retried).
- Short gaps that never alerted clear silently, and an episode suppressed by
  the 12h guard gets no recovery note.
- **Fetch errors are not G7 problems.** If the Dexcom fetch raises (auth,
  network) the cycle is recorded as `error` with no stale alert and no episode
  change, because "is the G7 connected?" would be wrong advice; the existing
  worker-error Telegram message covers it.

Limit: this runs inside the worker, so it cannot fire if the worker itself is
dead. That case is still covered by the cron-job.org failure notifications
(DEPLOY.md section 5) and by `/status`.

Known limitation: the heartbeat is a non-atomic read-modify-write. If two
cycles ever overlapped (cron-job.org retries, a slow cycle exceeding 5 min),
both could read the same prior state and both send a stale alert, and one
write would overwrite the other's counters. Acceptable for a single-user
system on a 5-minute cadence; persist-then-send narrows the window to the
read-to-write gap.

### 3. Telegram `/status`

Adds a "Live loop" section: last cycle age (with a "worker may be down" flag
past `worker_cycle_stale_minutes`), last Dexcom reading age (flagged stale past
`max_reading_age_minutes`), last outcome, and today's (local-day) outcome
counts. Missing row / malformed meta degrade to a plain message.

### 4. Config (new block at the end of `config/user_config.yaml`)

```yaml
live_health:
  cgm_stale_alert_minutes: 60     # alert if no fresh reading for MORE than this
  cgm_stale_realert_hours: 12     # min gap between ANY two stale alerts
  worker_cycle_stale_minutes: 15  # /status "worker may be down" threshold
  outcome_counter_days: 14        # days of per-day counters kept in meta
  cgm_recovery_fresh_minutes: 30  # unbroken fresh CGM before recovery note
```

All keys are optional with the defaults shown (the block may be absent, e.g.
when `USER_CONFIG_PATH` points elsewhere); invalid values fall back to the
defaults with a logged warning. Parsed from `AppConfig.raw` by
`LiveHealthConfig`, deliberately not added to `AppConfig` (keeps
`detection/config.py` free of merge conflicts with concurrent work).

### 5. Testability

`run_cron()` gained optional injection kwargs (`storage`, `config`,
`fetch_cgm`, `send`); `api/index.py`'s bare `run_cron()` is unchanged. New
tests use `InMemoryStorage`, a fake fetcher and a fake sender and scrub
credentials / patch real I/O to raise. Note: `load_dotenv()` at import walks
parent directories, so inside a git worktree under the main checkout the real
`.env` is loaded into `os.environ` for the whole pytest session; new tests
defend against this explicitly.

One existing test changed on purpose:
`test_cron_failure_skips_heartbeat_and_alerts` previously asserted no
heartbeat row after a failing cycle; it now asserts `last_synced_at` was NOT
advanced and `last_outcome == "error"`.

## Deploy (production)

- Target: the **cron-worker Vercel project** (`t1d-optimizer-meal-bot`, Root
  Directory `.`), not the dashboard project. No new dependencies, no
  migration, `requirements.txt` / `uv.lock` untouched.
- Redeploy: `DEPLOY.md` and `apps/cron_worker/README.md` do not state a deploy
  trigger. The project was created by importing the GitHub repo, so Vercel's
  Git integration should auto-deploy the production branch on merge to `main`;
  confirm in the Vercel dashboard, otherwise deploy manually
  (`vercel deploy --prod` from the repo root). This PR is not merged by the
  author.
- First cycle after deploy overwrites the legacy heartbeat (no meta) with the
  new shape; counters start from zero.

## Watch after deploy

0. **First real meal-rise alerts**: expect ~3/day (see headline); some will be pre-bolused false alarms.
1. Telegram `/status` within ~10 min: "Live loop" shows a recent last cycle
   and an outcome other than `error`.
2. `SELECT last_synced_at, meta FROM fetch_state WHERE source_id='live_cron'`
   - `meta.last_outcome` should be `no_candidate` / `insufficient_window`
   while the G7 is connected; `last_synced_at` within ~15 min.
3. The web "Live loop" badge tracks fresh CGM (see caveat above). Expect it
   to go stale during a sensor warmup/swap; that is by design until the web
   follow-up lands.
4. If the G7 is still disconnected you should receive exactly one "No Dexcom
   readings" alert, then at most one every 12 h, then one recovery note after
   30 min of continuous fresh data.
5. Worker-error Telegram messages still fire every failing cycle (pre-existing
   behaviour, not changed here).

## Web follow-up (not done here; `apps/web` is owned by another change)

To show worker liveness and CGM freshness as separate signals in
`apps/web/lib/queries/status.ts`: add `meta` to the `fetch_state` SELECT and to
`RawFetchState`; emit "Live loop" from `meta.last_cycle_at` (15-min
threshold) and a new "Live CGM" signal from `last_synced_at`, with
`detail = meta.last_outcome`.
