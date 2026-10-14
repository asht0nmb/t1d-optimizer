# 2026-10-14 M1 live-path diagnostic: why has the meal-rise alert never fired?

Status: **advisory analysis. No detection logic, thresholds, config, or live-path code was changed.** A real bug candidate was found and is reported below with a failing (xfail-pinned) test and a proposed fix; fixing it is for the owner of `apps/personal/cron/`.

## 1. The question

The live missed-meal ("meal-rise") Telegram alert has been invoked every 5 minutes since early July 2026 (the `live_cron` heartbeat is fresh as of this session), yet `detection_results` and `alerts_sent` have **zero rows, ever** (verified read-only today: both tables 0 rows). Has the detector ever been *capable* of firing in production? Four candidate explanations:

* (a) the Dexcom Share feed is rarely fresh, so the freshness guard skips nearly every cycle;
* (b) a bug somewhere in the live path;
* (c) the gates suppress nearly everything;
* (d) there were genuinely no qualifying events.

## 2. TL;DR

* **(d) and (c) are effectively ruled out.** Replaying the real detector over 7.5 months of pump CGM as if live, with every gate and the refractory window, yields about **21 alerts/week** (147 alerts across the 6.7 weeks from 2026-07-01 to 2026-08-16 in which the live loop was running). The gates are not suppressing everything (about 1.3% of fresh 5-min ticks produce an alert), and qualifying rises are abundant (the live detector never looks at boluses, it fires on any sustained fast rise).
* **"Zero ever" is therefore a data-path failure, not a detection outcome.** About 22 alerts/week x about 15 weeks (2026-07-01 to today) is on the order of **330 expected alerts versus 0 observed**. That requires the loop to have almost never evaluated a usable window. Which of the remaining causes did it is **undetermined**: (a, persistent) the Share feed was effectively never fresh for 100+ days (the owner confirms the G7 phone app was disconnected for an unknown period), or (b) a bug stopped a fresh feed from being recognised as fresh (including a silent missing-Telegram-config path). The one direct measurement we have (the August audit's 240 min stale) argues *against* the timestamp bug below for a Pacific-time account, so the current lean is (a), with (b) not excluded. Section 8 contains the checks that settle it.
* **A real, deterministic correctness bug exists in the feed-parsing step** (`apps/personal/cron/detect_meal_rise.py:163`): it relabels a timezone-aware pydexcom datetime as UTC, which shifts every reading by the Share `DT` offset. With the production pin (`pydexcom==0.5.1`), any account whose `DT` offset is non-zero has every cycle skipped by the freshness guard. It is pinned by strict-xfail tests and should be fixed regardless, but it is **not shown** to be the cause of the zero (section 5).
* Existing tests could not have caught it: the fake Dexcom reading hands the cron an already-UTC datetime and never touches pydexcom's real parser.

## 3. The live path, end to end (every gate between a fresh reading and an alert)

Entry: cron-job.org every 5 min -> Vercel worker `api/index.py` (bearer `CRON_SECRET`) -> `apps/personal/cron/detect_meal_rise.py::run_cron()`.

| # | Gate / step | Where | Rule (current config) |
|---|---|---|---|
| 0 | Auth, storage, config | `api/index.py::_verify_authorization`; `run_cron` L402-412; `get_storage_connection` L98-120 | `CRON_SECRET`; `SUPABASE_DB_URL` required (else exit 1) |
| 0b | Retry pass for failed Telegram deliveries | `run_cron` L428-439, `retry_failed_alert_deliveries` L236 | runs before fetch; irrelevant while no alerts exist |
| 1 | Fetch + parse Share readings | `fetch_dexcom_cgm` L123-178: count L143-148, API call L151, **timestamp relabel L163 (bug candidate)**, bucket-dedupe L174 | newest `(30+15)//5 + 3 = 12` readings; one per 5-min UTC bucket |
| 1b | Empty feed | L443-445 | returns 0 silently (heartbeat still written) |
| 2 | **Freshness guard** | L456-464 | newest reading age (against injected `now`) must be <= `max_reading_age_minutes` = **15** |
| 3 | Window build | `make_window` L468-473 -> `core/detection/windowing.py` L94-110 | slice `[anchor-30min, anchor]` inclusive; `n_expected = 30//5 + 1 = 7`; coverage = n_present / 7. `gaps_df` is never passed live, so `has_gap` is always False |
| 4 | min_samples | `core/detection/meal_rise.py` L87 | >= **4** readings |
| 5 | min_coverage | L89 | >= **0.7** (i.e. >= 5 of 7; `min_samples` is dominated by this) |
| 6 | has_gap | L91 | inert live |
| 7 | Slope | L94-112 | Theil-Sen (median of all pairwise slopes) over the window |
| 8 | **Start-level bounds** | L121 | first window value in **[70, 250]** mg/dL (inclusive) |
| 9 | **Time-of-day multiplier** | L124-137 | local hour of anchor: 6-10, 11-14, 17-21 -> x**0.7**; everything else (incl. 15-16 and 22-05) -> x**1.3** |
| 10 | **Slope threshold** | L140-143 | `slope >= 1.8 * multiplier` => **1.26** mg/dL/min in meal windows (about +38 mg/dL over 30 min), **2.34** off-hours (about +70 mg/dL) |
| 11 | **Refractory** | `handle_detection_alert` L326 | any `meal_rise` alert in the last **60** min suppresses (wall-clock `sent_at`) |
| 12 | Dedupe by `event_ref` | L330 | `meal_rise:<anchor to the minute>` |
| 13 | **Telegram config present** | L341-343 | token + chat id from env (config yaml values are empty). **Missing => `failed_config`: returns 1 BEFORE any write** |
| 14 | Atomic claim | `record_alert` L355-368 | `ON CONFLICT (alert_kind, event_ref)`; lost race => suppressed |
| 15 | Send | L370 -> `send_telegram_message` L181-197 | `requests.post`, `parse_mode=HTML` |
| 16 | Persist | `record_detection_result` L380-387 | `telegram_sent` true/false recorded either way |
| 17 | Heartbeat | `finally` L520-528 | written on every non-raising cycle, **including** freshness skips, `failed_config`, and empty feeds. So a healthy `live_cron` heartbeat does not mean alerts can fire |

Note the detector never consults boluses. "Missed meal" is inferred only from a fast sustained rise; the alert text just flags "in case a meal went unbolused".

Rows only ever reach `detection_results`/`alerts_sent` after gate 13. Zero rows therefore means no cycle ever got past gates 2-13.

## 4. End-to-end harness (Task 2)

New file `tests/detection/test_meal_rise_live_path_e2e.py` (22 cases). It runs the REAL `run_cron` with:

* `InMemoryStorage` via `get_storage_connection`;
* a fake `Dexcom` class returning **real `pydexcom.glucose_reading.GlucoseReading` objects** built from Share-shaped JSON (`{"Value","Trend","DT":"Date(<ms><offset>)"}`) so the real parser, tz handling and 5-min bucketing run;
* `requests.post` patched, so the real `send_telegram_message` formats URL/payload;
* `now` injected 90 s after the newest reading; per-reading timestamp jitter (+/-20 s).

Scenario: unbolused lunch rise, 120 -> 185 mg/dL over 30 min (about 2.17 mg/dL/min) ending 12:00 PDT.

**Results**

* With `DT` offset `+0000`: **fires**. One Telegram POST to the right URL/chat id, one `detection_results` row (`telegram_sent: true`), one `alerts_sent` claim, heartbeat written, local hour 12, multiplier 0.7. So gates 3-17 work end to end.
* With `DT` offset `-0400`, `-0700`, `-0800`: **does not fire** (0 posts, 0 rows). Pinned as `xfail(strict=True)` (the suite stays green and flips to a failure the moment the bug is fixed). Cause: section 5.
* Gate pins via the real path (offset `+0000` so the bug cannot interfere): same ramp at 03:00 local does **not** fire (2.17 < 2.34), a 1.3x steeper one does; freshness boundary (14 min fires, 16 and 240 do not); coverage (5 of 7 fires, 4 of 7 blocked); start-level bounds (250 fires, 251 and 69 do not); refractory (second qualifying cycle sends nothing); Telegram outage still claims + records; **missing Telegram env => `run_cron` returns 1, both tables stay empty, heartbeat still written** (the second silent-zero path).

**Do existing tests cover this?** Partly. `tests/detection/test_cron_meal_rise.py::test_cron_sharp_rise_breakfast_triggers_alert` runs `run_cron` with an in-memory store and a patched sender, so the downstream half is covered. But its `MockGlucoseReading` carries a hand-built UTC datetime ("pydexcom datetimes are standard naive UTC datetime" in its docstring, which is no longer true for 0.5.1), so it bypasses exactly the step that is broken; it patches `send_telegram_message` rather than the HTTP layer; and it only tests a 100 -> 150 rise inside a meal window. The new file closes those gaps.

Config sensitivity: tests read the current `config/user_config.yaml`. If the owner retunes `base_slope_mgdl_per_min` to roughly 3.1 or above, the literal 120 -> 185 ramp tests need a steeper ramp. The off-hours tests are derived from config and self-adjust.

## 5. Bug candidate: the Share `DT` offset is mishandled

**Location:** `apps/personal/cron/detect_meal_rise.py:163`

```python
dt_utc = r.datetime.replace(tzinfo=timezone.utc)   # assumes naive UTC
```

**Why it is wrong.** pydexcom 0.5.1 (pinned in `uv.lock` and in `apps/cron_worker/requirements.txt`, so this is what the Vercel worker runs) builds `GlucoseReading.datetime` as `datetime.fromtimestamp(epoch_ms/1000, tz=<offset parsed from DT>)`. The Share API returns `DT` such as `Date(1691455258000-0400)` (this exact form is in pydexcom's own README): the epoch is the true UTC instant and the suffix is the display-timezone offset. The result is already a correct, tz-aware instant. `.replace(tzinfo=utc)` keeps the wall clock and swaps the label, which moves the instant by the offset:

| True instant | `DT` offset | Parsed (correct) | After `.replace(tzinfo=utc)` | Apparent age |
|---|---|---|---|---|
| 15:00Z | `-0700` | 08:00-07:00 | 08:00Z | +7 h |
| 15:00Z | `-0400` | 11:00-04:00 | 11:00Z | +4 h |
| 15:00Z | `+0000` | 15:00+00:00 | 15:00Z | 0 |

Consequences for any non-zero offset: (1) every fresh reading looks at least `|offset|` hours old, so the freshness guard (gate 2) skips every single cycle and logs a "stale" warning that looks like a feed problem; (2) the local hour used by the time-of-day multiplier would be wrong even if the guard were bypassed. The cycle exits 0 and the heartbeat is written, so nothing looks broken from outside.

The code comment ("r.datetime is typically in naive UTC") describes pydexcom < 0.5 behaviour.

**Failing test:** `tests/detection/test_meal_rise_live_path_e2e.py` -> `test_unbolused_lunch_rise_fires_end_to_end[dt_offset_-0700_PDT]` and `test_fetch_dexcom_cgm_preserves_the_true_instant[...]` (all `xfail(strict=True)`; run `uv run pytest tests/detection/test_meal_rise_live_path_e2e.py --runxfail` to see them fail).

**Proposed fix** (not applied; verified locally by temporarily applying it: all 22 new tests pass with `--runxfail`, and the existing 18 `test_cron_meal_rise.py` tests still pass; change reverted):

```python
dt = r.datetime
dt_utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
dt_local = dt_utc.astimezone(tz)
```

Then delete the `OFFSET_BUG` markers from the new test file.

**Confidence, honestly stated.** The mechanism is certain (reproduced above with the real parser), and both ingredients were in place during the August audit: the `.replace(tzinfo=utc)` line dates from 2026-05-25/28 (`9c2709d`, `54baf19`), `pydexcom 0.5.1` was locked on 2026-05-25 and pinned in the worker requirements by 2026-06-20. What is *not* known is the `DT` offset this particular Share account reports, and the one direct measurement cuts against the bug being the cause:

* For a Pacific account the offset is -0700 (PDT) or -0800 (PST), so with the bug active no reading could ever look younger than **420-480 min**. The audit measured **240 min**. That is impossible with the bug active at a Pacific offset. It fits (i) a `DT` offset of `+0000` (bug latent for this account; 240 min is then a genuinely stale feed, consistent with the disconnected phone) or (ii) a -0400 offset (unlikely for a Pacific phone, though the account/server timezone is not necessarily the phone's).
* The audit's round "240 (4 hours)" is weak evidence by itself (the write-up may have rounded), so I do not lean on it.

So: the bug is real and worth fixing for any non-zero-offset account, but on current evidence it is an *unconfirmed* cause of the zero, not the likely one.


## 6. Historical replay (Task 3)

### Method

`scripts/replay_meal_rise_live.py` (read-only, advisory, modelled on `scripts/score_meal_rise.py`):

1. Loads pump CGM (`cgm.timestamp`, `bg_mgdl`) and boluses (`requests.timestamp`, `bolus_category`, `carbs_g`) from Supabase in a `readonly=True` session, SELECT only, 2026-01-01 .. 2026-08-16 (the synced pump CGM ends on the evening of 2026-08-16 Pacific). The URL comes from `$SUPABASE_DB_URL` or `--env-file` (parsed, never exported or copied).
2. Steps a virtual clock every 5 min. At each tick it emulates one cron cycle: the newest 12 readings visible (`reading time + latency <= tick`), one per 5-min UTC bucket, newest = anchor; then the **freshness guard**; then the **real** `make_window` + **real** `detect_meal_rise` with the **current** `config/user_config.yaml`; then the **refractory** (60 min since the last *sent* alert). Trailing window only.
3. A mirror classifier attributes each rejection to the first failing gate (in live order). The real detector decides outcomes; the classifier is checked against it on every tick: `mirror_mismatches = 0` in all runs.
4. Alerts are scored with `detection/calibration/meal_rise_scoring.score_instances` and `core/bolus_categories.FOOD_CARRYING`.

### Results (grid phase 0, latency 2 min)

Alerts per week: **21.0** over the whole range (685 alerts, 32.6 weeks); **21.9** in the live period 2026-07-01..08-16 (147 alerts, 6.7 weeks). Monthly alert counts Jan-Jul 64-116 (consistent with the M2 report's roughly 100 candidate rises/month). 88% of alerts fall in meal-window hours.

Candidate windows rejected by each gate (a "tick" = one simulated 5-min cron cycle):

| Gate | Full range | % of fresh ticks | Live period | % of fresh ticks |
|---|---|---|---|---|
| Ticks total | 65,664 | | 13,536 | |
| Freshness (pump gap, **not** Share staleness) | 11,768 | | 2,829 | |
| Fresh ticks evaluated | 53,896 | 100% | 10,707 | 100% |
| min_samples | 197 | 0.4% | 35 | 0.3% |
| min_coverage | 157 | 0.3% | 29 | 0.3% |
| start level < 70 | 2,286 | 4.2% | 268 | 2.5% |
| start level > 250 | 5,616 | 10.4% | 1,236 | 11.5% |
| slope < threshold, meal window | 23,793 | 44.1% | 4,775 | 44.6% |
| slope < threshold, off-hours | 18,346 | 34.0% | 3,589 | 33.5% |
| **Fired (all detector gates passed)** | 3,501 | 6.5% | 775 | 7.2% |
| of which refractory-suppressed | 2,816 | 5.2% | 628 | 5.9% |
| **Alert sent** | **685** | **1.3%** | **147** | **1.4%** |

Slope near-misses: only about 2% of fresh ticks sit at 80-100% of threshold; most slope rejections are far below (ratio < 0.1). A fired rise stays above threshold for a median of **4 consecutive ticks (about 20 min)**, max 17, so a feed outage shorter than that would not make the loop miss an event.

Robustness: results are insensitive to Share latency (0, 2, 6 min -> 686/685/685 full-range alerts) and to the cron grid's phase relative to the sensor (phase offsets 0-4 min -> 685, 685, 686, 687, 686 alerts; 147 in the live period for every phase). Strict precision stays 76.4-76.7% across phases.

Extrapolation: the replay stops at 2026-08-16 (end of synced pump CGM) but the loop has run to today, about 15 weeks since 2026-07-01. At about 22 alerts/week that is about 330 expected alerts versus 0 observed.

### Rough precision against food-carrying boluses

`score_instances` labels each alert vs `FOOD_CARRYING` boluses (`user_meal`, `user_meal_and_correction`, `override_up`) in the window [rise start - 30 min, rise start + 45 min]. I split the "late" label at the alert instant (alert anchor = rise start + 30 min):

| Label (full range, n=685) | n | % |
|---|---|---|
| pre_bolused (bolus before the rise) | 33 | 4.8% |
| late_bolused, bolus already on board at alert time | 77 | 11.2% |
| late_bolused, bolus arrives after the alert | 50 | 7.3% |
| **uncovered (no food bolus in window)** | **525** | **76.6%** |

Of the 525 uncovered: 411 (78%) were followed by a Control-IQ auto-correction, 41 (8%) by a user correction, 73 (14%) by neither. Live period (n=147): uncovered 113 (76.9%), actionable-at-alert-time 84.4%.

* **Strict precision** (uncovered / alerts): **about 77%**.
* **Alert-time-actionable** (uncovered + boluses arriving after the alert): **about 84%**.
* **False alarms by this definition**: pre_bolused + bolus-already-on-board = about 16%.

How to read this: "uncovered" means *no food bolus was logged*, not *the user ate*. It also includes dawn/stress/site-failure rises, post-low rebounds above 70, and meals covered some other way. There is no carb ground truth, so true missed-meal precision is lower than 77% by an unknown amount. Conversely, the high auto-correction share says most uncovered rises were real excursions the pump had to handle. The relevant product observation (a tuning question for the owner, not acted on here): a *working* loop at the current placeholder threshold would ping roughly 3 times a day.

### Caveats

* Pump CGM and Dexcom Share come from the same G7 sensor but differ in latency, timestamping (backfilled readings use sensor time), and availability: the pump receives G7 over Bluetooth directly, while Share needs the phone app connected and online. This replay estimates what the detector would do with a *healthy* feed; it cannot say how often the real feed was healthy. Its "freshness" rejections (18% of ticks) are pump data gaps, not evidence for explanation (a).
* Pump data has about 18-21% of 5-min ticks without a fresh reading (warm-ups, gaps), so even a perfect Share feed would not have beaten the pump's own coverage.
* The refractory in the replay is relative to simulated time; live it uses wall-clock `sent_at` (equivalent in production).

## 7. Weighing the four explanations

| | Verdict | Evidence |
|---|---|---|
| (d) no qualifying events | **Ruled out** | 147 would-be alerts in the live window (about 22/week), about 77% with no food bolus nearby. Zero is not a believable outcome of the detector's own definition. |
| (c) gates suppress everything | **Ruled out** | Same replay: gates pass about 7% of fresh ticks and alert on about 1.4%. Slope is the dominant gate (about 78% of rejections), start-level above 250 next (about 10-12%), both reasonable. Nothing in the gates is close to "always false". |
| (a) Share rarely fresh | **Leading, but only in an extreme persistent form** | Under an IID model with 147 independent events, `P(zero alerts | fraction f of events seen) = (1-f)^147`: f = 5% -> 0.05%; f = 2% -> 5%; f = 1% -> 23%. Staleness comes in bursts, which loosens the IID assumption, but expected alerts are still about f x N regardless: about 330 expected since 2026-07-01 means f must be near zero. Because a rise stays detectable for about 20 min, short outages cannot explain it. The feed must have been essentially never fresh for 100+ days. The owner's report that the G7 phone app was disconnected for an unknown period fits this *if* it spans most of the period, and the audit's 240-min reading shows the feed really was stale on 2026-08-16. |
| (b) bug | **Not excluded; undetermined** | `:163` makes a fresh feed look hours old for any non-zero `DT` offset, but the audit's 240-min measurement is impossible with that bug active at a Pacific offset (minimum 420-480 min), so it is only live if the account's `DT` offset is about -0400. Second silent path: `failed_config` (missing `TELEGRAM_*` env on the worker project) returns before any write while the heartbeat stays healthy; not visible from the database. |

**Classification of "zero ever": not (c) or (d). A data-path failure: (a, persistent) or (b), undetermined; the evidence we have leans (a).** Both are *invisible* in today's telemetry because the heartbeat is written on every non-raising cycle, including skipped ones. Section 8 decides it.

## 8. How to resolve it today (owner-run; I did not call Dexcom or Telegram)

The phone app was reconnected today, so the feed should now be fresh. The first two checks decide (a) versus (b):

1. Vercel logs of the cron worker, newest cycles. Look for `Latest CGM Reading: ... @ N mg/dL` and `Latest CGM reading is stale (X min old`.
   * X about 0-15 and no stale warning: the offset bug is not active for this account and (a) was the cause; alerts should start flowing (expect about 3/day at current thresholds). The bug is still a latent hazard and should be fixed.
   * X about 240+, 420+ or 480+ min (4/7/8 h) with a connected phone and a live sensor: the offset bug is confirmed as the cause.
2. One-off local check of the raw field (uses your own credentials, prints three readings):

```bash
uv run python -c "
from dotenv import load_dotenv; load_dotenv()
import os
from datetime import datetime, timezone
from pydexcom import Dexcom, Region
d = Dexcom(username=os.environ['DEXCOM_USERNAME'], password=os.environ['DEXCOM_PASSWORD'], region=Region.US)
for r in d.get_glucose_readings(max_count=3):
    print(r.json['DT'], '|', r.datetime, '| true age min:', (datetime.now(timezone.utc)-r.datetime).total_seconds()/60)
"
```

   A trailing non-`+0000` offset in `DT` plus a small true age confirms the bug.
3. Check the worker Vercel project has `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` (and `DEXCOM_*`), and cron-job.org history for persistent HTTP 500s (exit code 1 from `failed_config`).

## 9. Recommendations

Aligned with the separate per-cycle outcome-logging work (not duplicated here):

1. **Make every cycle's outcome queryable** (that agent's scope): `alert_sent | suppressed_refractory | no_rise | stale_skip | empty_feed | failed_config | error`. Strongly suggested extra fields on the `stale_skip`/every-cycle record: the computed **age in minutes**, the **raw Share `DT` string (and parsed offset)** of the newest reading, `n_samples`, `coverage`, and for `no_rise` the **slope, threshold, start level, hour**. The raw `DT` would have exposed this bug on the first cycle; slope/threshold make "(c) gates too tight" answerable from data.
2. **Surface a rolling "fresh-cycle fraction" and "last cycle that reached the detector" on `/status`** and alert (once) when no cycle has reached the detector for N hours. The heartbeat alone proves only that the worker ran.
3. **Treat `failed_config` as an error, not a quiet return**: it currently returns 1 without any Telegram or DB trace. At minimum log it into the per-cycle outcome record.
4. **Fix `:163`** (section 5) and drop the `xfail` markers in the same PR. Log the computed age on every cycle (item 1) so a similar timezone mix-up cannot hide; a *negative* age (reading in the future) is an equally clear parse-error signal.
5. **Logic notes for the owner (advisory; nothing changed here):** (i) a 4-hour-plus freshness failure is informational, not a detector failure, so log it differently from a normal "no rise"; (ii) the threshold is a placeholder (see M2); the replay shows 685 alerts over 7.5 months, so threshold or a bolus-aware suppression (e.g. skip when a food bolus landed in the prior 30 min, which the live path does not check at all) are the levers for alert volume. `--sweep`-style analysis is available via `scripts/score_meal_rise.py`.
6. Once logging lands, rerun this replay alongside the live outcome counts to compare expected vs observed alerts/week directly.

## 10. Files

* `tests/detection/test_meal_rise_live_path_e2e.py` - end-to-end harness + gate pins + xfail-pinned bug (22 cases: 16 pass, 6 strict xfail).
* `tests/detection/test_replay_meal_rise_live.py` - unit tests for the replay script (7 cases, synthetic data, no DB).
* `scripts/replay_meal_rise_live.py` - read-only replay.
* This document.

Full suite after these additions: 772 passed, 42 skipped, 48 deselected, 6 xfailed (baseline was 749 passed).

## 11. Rerun commands

```bash
uv sync --all-groups                      # plotly etc. are needed to collect the whole suite

# end-to-end harness (no network, no DB)
uv run pytest tests/detection/test_meal_rise_live_path_e2e.py -q
uv run pytest tests/detection/test_meal_rise_live_path_e2e.py -q --runxfail   # shows the bug failing

# replay (read-only Supabase session; ~70 s per phase)
uv run python scripts/replay_meal_rise_live.py \
    --start 2026-01-01 --end 2026-08-16 --live-since 2026-07-01 \
    --env-file /path/to/.env --phases 0,1,2,3,4 --latency-min 2 \
    --out-dir data/reports      # per-alert detail is written here (gitignored, health data; do not commit)

# full suite
uv run pytest
```

## 12. Method notes (learning material)

* **Why a replay and not just a re-run of M2.** `find_meal_rise_instances` slides the detector over *every reading* and de-duplicates afterwards. The live loop instead evaluates *one anchor per 5-min tick* and only the first alert in each 60-min refractory window is sent. The replay reproduces the live loop's cadence, feed visibility, freshness guard and refractory state, so the output is directly comparable with `alerts_sent`.
* **Theil-Sen intuition.** The detector's slope is the median of all 21 pairwise slopes of a 7-point window, so a spike or a single noisy reading barely moves it, but also a rise that only began in the last 10-15 min of the window has too few ascending pairs to lift the median above threshold: detection lags onset by about 15-20 min, and a rise is typically visible for about 4 consecutive ticks.
* **Why the threshold multiplier matters.** 1.26 vs 2.34 mg/dL/min is the difference between +38 and +70 mg/dL in 30 min. A 120 -> 185 rise (+65) is a textbook post-meal ramp and fires at lunch but is silently ignored at 3 am.
* **Why "zero ever" could hide.** A heartbeat written on every non-raising cycle conflates "the worker ran" with "the detector evaluated data". Three different failure modes (stale feed, mis-parsed timestamps, missing Telegram env) all look identical from the outside; per-cycle outcomes are what separate them.
* **Probability argument.** If each of N independent qualifying events is seen with probability f, `P(0 alerts) = (1-f)^N`. With N = 147 you need f below about 2% to see zero by luck at the 5% level, which is why intermittent staleness is not a credible explanation by itself.
