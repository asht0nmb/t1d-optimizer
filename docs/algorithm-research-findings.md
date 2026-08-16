# Algorithm research findings — Phase 7, 2026-08-16

**Branch:** `research/algorithm-improvements` (exploratory, unmerged pending owner review — see "Status" at the bottom)
**Companion notes:** `notes/algorithm-research.md` (the owner's research brief, not checked into `main`)
**Non-goals (binding, unchanged):** no dosing logic, no prediction curves for therapy, no safety caps. This system detects and alerts; it never delivers.

This document is written to double as learning material, not just a changelog — it walks through what was read, what was built, why each design choice was made, and where the honest uncertainty still is.

---

## 1. License discipline — what was actually read

The owner's hard constraint: MIT sources (openaps/oref0, nightscout/trio-oref) can be read *and* ported/adapted with attribution. AGPL-3.0 sources (nightscout/cgm-remote-monitor, the AndroidAPS tree and every fork including autoISF and Tai) can be read for method and reimplemented from documented behavior, but never copied or adapted from.

**What was fetched and read this phase:** `nightscout/trio-oref`, `dev` branch, commit `8282ce71a57d09a160e92ecd2baf28a70c89694d` (fetched via the GitHub API 2026-08-16). Files read in full: `lib/determine-basal/determine-basal.js`, `lib/determine-basal/cob.js`, `lib/glucose-get-last.js`, `lib/iob/calculate.js`, `lib/meal/total.js`, `lib/autotune-prep/categorize.js`, `lib/autotune-prep/index.js`. All MIT-licensed, confirmed via the GitHub API (`license.spdx_id == "MIT"`) and the file-level license header reproduced in each source file. Also read: the `nightscout/trio-algorithm-validator` README (MIT) for Workstream E.

**What was NOT read:** no AGPL source was fetched or read this phase. `notes/algorithm-research.md`'s own description of autoISF's four factors and its dynamic-ISF formula (Workstream D) was treated as the source, plus general public documentation — not the AndroidAPS/autoISF/Tai source trees themselves. `nightscout/cgm-remote-monitor` was not touched. This sentence is the deliverable for that constraint: the AGPL boundary was not crossed.

**Provenance table** — one row per piece of ported/reimplemented logic:

| This repo | Behavior | Source repo | Source file | Commit | License | Relationship |
|---|---|---|---|---|---|---|
| `detection/iob.py::_activity_and_iob_fraction` | Exponential insulin-action curve | nightscout/trio-oref | `lib/iob/calculate.js` (`iobCalcExponential`) | `8282ce71a5` | MIT | **Ported**, attributed. Formula originates further upstream at [LoopKit/Loop#388](https://github.com/LoopKit/Loop/issues/388), cited in the oref source itself. |
| `detection/deviation.py::compute_glucose_deltas` | Elapsed-minutes-normalized delta / short_avgdelta / long_avgdelta with 2.5/17.5/42.5 min bucket boundaries | nightscout/trio-oref | `lib/glucose-get-last.js` | `8282ce71a5` | MIT | **Ported** (formula + bucket boundaries), generalized from "latest reading only" to "every reading" (needed for a batch column, not a live tick). |
| `detection/deviation.py::compute_bgi` | `bgi = -activity * isf * 5` | nightscout/trio-oref | `lib/determine-basal/determine-basal.js` | `8282ce71a5` | MIT | **Ported** formula, unmodified. |
| `detection/episode_boundary.py::track_deviation_trajectory` | Running max/min deviation + decay/rise slope tracking | nightscout/trio-oref | `lib/determine-basal/cob.js` (`detectCarbAbsorption`, the tracking loop only) | `8282ce71a5` | MIT | **Ported** concept and formula; COB/carb-absorption accounting around it was *not* ported (dosing math, out of scope). |
| `detection/iob.py::build_dose_events`, `basal_baseline_rate` | Basal → net-dose decomposition against a rolling-median baseline | — | — | — | — | **Original.** oref's own basal decomposition (`lib/iob/history.js`) assumes an ingested profile rate we don't have; this is a documented approximation, not a port. |
| `detection/deviation_categorize.py::categorize_deviations` | Deviation categorization against `bolus_category`/`rate_source` | nightscout/trio-oref (read for concept only) | `lib/autotune-prep/categorize.js` | `8282ce71a5` | MIT | **Reimplemented**, per the notes' explicit instruction — the categories, thresholds, and join logic are new design work; only the general "walk deviations and bucket them" shape is a family resemblance. See §4. |
| `docs/algorithm-research-findings.md` §7 (Workstream D) | autoISF's four derived factors + dynamic-ISF formula | notes/algorithm-research.md (owner's own notes) + public documentation | — | — | — | **Read-only, no AGPL source fetched.** Treated as a validated feature list per the notes' instruction; not implemented this phase (see §7). |

---

## 2. Answers to the notes' open questions

The notes were explicit: answer these against tconnectsync **v3** (Phase 1's migration), not v2, and against the actual current builders — not assumptions. One important scoping note up front:

> **Branch dependency.** This worktree branches off `main`, which does **not** yet include Phase 1's v3 migration (PR #6, branch `fix/tconnectsync-v3-migration`, still open at the time of this phase). The v3 `ingestion/builders.py` was read directly from that branch (`git show origin/fix/tconnectsync-v3-migration:ingestion/builders.py`) for this analysis — everything below is checked against that version, which is what "current" means per the task brief. All new code in this phase (`detection/iob.py`, `detection/deviation.py`, etc.) takes plain DataFrames with a documented column contract and never imports `ingestion` or `tconnectsync`, so it works unchanged regardless of which PR lands first, and its tests pass on this branch as-is.

### Q1: Does the schema distinguish requested vs. delivered insulin under Control-IQ modulation?

**Bolus side: yes, and it's already exposed (partially used).** `LidBolusCompleted` (v3) carries `insulinDelivered` *and* `insulinRequested` as separate fields — confirmed by reading the field list directly off the installed v3 `tconnectsync` source (`eventparser/events.py`). `ingestion.builders.build_bolus_df` only extracts `insulinDelivered` today; `insulinRequested` is available but currently discarded. Separately, `build_request_df` (from `LidBolusRequestedMsg1/2/3`) already captures the *pre-delivery* requested breakdown (`food_insulin`, `correction_insulin`, `total_requested`, `bolus_source` including `"auto"` for Control-IQ-initiated boluses) — this is the data `bolus_category` is built from and is the more useful "requested" signal in practice, since it distinguishes food from correction, not just total.

**Basal side: yes in the raw event, no in the ingested table — this is the single most important ingestion finding of this phase.** `LidBasalDelivery` (v3) carries four separate rate fields: `commandedRate` (the final, blended, actually-delivered rate), `profileBasalRate` (the user's programmed profile rate), `algorithmRate` (Control-IQ's algorithmic component), and `tempRate` (an active temp rate, if any) — plus `commandedRateSourceRaw`, a categorical flag for which one is currently driving delivery. Confirmed by reading the field list directly off the installed v3 event class. `ingestion.builders.build_basal_df` extracts **only** `commandedRate` (renamed `commanded_rate`, converted to units/hr) and the categorical `rate_source` derived from `commandedRateSourceRaw`. `profileBasalRate`, `algorithmRate`, and `tempRate` are silently dropped at ingestion time even though the pump reports them.

**Practical impact — and a correction to the notes' framing.** The advisor review on this phase caught an overstatement in an earlier draft: BGI (Workstream A) does **not** need the profile/algorithm split — it needs *delivered* insulin only, and `commanded_rate` already **is** the delivered rate. Workstream A is fully implementable today against the existing columns (see §3). The profile-vs-algorithm split matters for a different, narrower thing: correctly separating "the user's basal profile" from "Control-IQ's own decision" for attribution purposes (Workstream C/M4, and `detection/iob.py`'s baseline-rate estimation, which currently substitutes a rolling median because it doesn't have `profileBasalRate`). **Recommended fast-follow, not done this phase:** extend `build_basal_df` to also capture `profile_basal_rate`, `algorithm_rate`, `temp_rate` (three more float columns off data the byte format already parses — no new API calls, no schema risk beyond a `PIPELINE_VERSION` bump), and extend `build_bolus_df` to capture `insulin_requested`. Both are small, mechanical additions to an existing builder, not new ingestion capability.

### Q2: Does tconnectsync expose delivered basal at resolution sufficient to reconstruct net IOB?

**Confirmed structurally: `LidBasalDelivery` is event-driven (rate-source or rate-magnitude changes), not fixed-cadence.** There is no guarantee of a row every 5 minutes — a steady period produces no rows at all. This is workable for IOB reconstruction via the standard "hold the last known rate until superseded" convention, which is exactly what `detection/features.py::_integrate_basal` already does (`duration = next_ts - this_ts`, final row extends to a horizon) and what `detection/iob.py::build_dose_events` mirrors for consistency.

**Not verified against live data — flagged, not guessed.** This worktree has no Supabase credentials (deliberately isolated; see `CLAUDE.md`'s worktree conventions and the owner's own production-data caution). Whether the pump reliably emits a `LidBasalDelivery` row on every algorithmic adjustment, or only above some rate-change threshold, was not checked against real event streams. **Verification query for the owner to run** (read-only, against the existing `basal` table):

```sql
select
  extract(epoch from (timestamp - lag(timestamp) over (order by timestamp))) / 60.0 as gap_minutes
from basal
where pump_serial = '<your pump serial>'
order by timestamp;
-- then: select percentile_cont(0.5) within group (order by gap_minutes), max(gap_minutes) from (...)
```
A median gap well under Control-IQ's ~5-minute evaluation cadence would suggest most adjustments do get logged; a bimodal distribution (many sub-minute gaps, many large gaps) would suggest logging is threshold-gated, which would matter for how much to trust `basal_baseline_rate`'s median during long "no adjustment" stretches.

### Q3: What DIA and peak time to assume?

**`PumpProfile.insulinDuration` (minutes) is confirmed present in the tconnectsync API map** (`docs/operating_docs/tconnectsync_api_map.md`, `PumpProfile` class), reachable via `device_settings_from_guid`. **It is not currently ingested at all** — there is no profile builder in `ingestion/builders.py`, v2 or v3. Fetching it would be new ingestion capability (an additional API call + a new table/column), not a mechanical extension of an existing builder, and was judged out of scope for this exploratory phase (touching `ingestion/` production code on a research branch, the same night as an open, unrelated sync-restoration PR, was avoided deliberately).

`detection/iob.py::IobCurveConfig` defaults to `dia_hours=5.0, peak_minutes=75.0` — the standard oref/Loop "rapid-acting" exponential-curve defaults — **explicitly documented as a placeholder, not this user's real DIA**, with a note in the dataclass docstring that all downstream deviation values are sensitive to this assumption. The notes' own caution stands: even once `insulinDuration` is fetched, Control-IQ's *internal* model may not equal what the profile reports. A sensitivity check (rerun with ±20% DIA and see how much `deviation_5m` moves) is recommended before trusting absolute values; this phase did not run that check (no real multi-day dataset available in this worktree).

**A related, better-than-expected finding:** ISF is directly observable per real bolus. `LidBolusRequestedMsg2.ISF` (mg/dL per unit) is on the raw event, confirmed the same way as the basal fields above, and is likewise not currently extracted by `build_request_df`. `detection/deviation.py::DeviationConfig.isf_mgdl_per_unit` therefore has **no default** — it must be supplied explicitly by the caller — specifically so nobody computes a real deviation series against a silently guessed ISF. Once `build_request_df` captures `ISF`, a piecewise-constant ISF schedule (order the observed values by time, forward-fill between boluses) becomes possible without a config change; this module was written to accept that upgrade path without modification (a caller-supplied ISF, not a hardcoded one).

### Q4: Does oref's 5-minute bucketing survive contact with backfilled Dexcom readings?

**No, and this is fixed in the port, not worked around.** `ingestion.builders.build_cgm_df`'s dedup mask keeps every backfilled row (`cgmDataTypeRaw == 2`) regardless of spacing (`mask = ... | df["backfilled"]`), and backfilled rows are timestamped at the *sensor* read time (`egvTimeStamp`, decoded), not the pump-received time — so a backfilled stretch is neither 5-minute-spaced nor grid-aligned to any epoch. A naive `.diff()` on row order, or an assumption that "row N is 5 minutes after row N-1," would silently compute the wrong per-5-minute rate across every backfilled stretch (which, per `docs/operating_docs/DATA_NOTES_2.md`, happens after every pump-death/reconnect event).

`detection/deviation.py::compute_glucose_deltas` is oref's own fix, ported: every delta is computed as `change_in_bg / actual_elapsed_minutes * 5`, using an elapsed-minutes *window search* (2.5–7.5 / 2.5–17.5 / 17.5–42.5 minute buckets, oref's exact boundaries) rather than positional differencing. This is verified by test (`tests/detection/test_deviation.py::TestComputeGlucoseDeltas::test_irregular_backfilled_spacing_normalizes_correctly`): a 3-minute-spaced pair and a 5-minute-spaced pair with the same *rate* of change produce the same normalized delta.

**Live-data confirmation not performed** (no Supabase access in this worktree, as above). The structural fix is in place regardless of what real backfilled spacing turns out to be — that was the point of porting oref's normalization rather than trusting the grid assumption.

### Q5: Real-time trailing-window-only constraint — unaffected

Unrelated to Workstream A specifically, but worth restating since it's binding: `detection/iob.py`/`detection/deviation.py`/`detection/episode_boundary.py`/`detection/deviation_categorize.py` are all nightly-batch-only, exactly as the notes specified ("this is a nightly-only primitive... M1 stays on raw slope"). None of them are imported by `core/detection/meal_rise.py` or anything in `apps/personal/cron/`. This was verified by construction (new files, no edits to the live path) and by the full test suite passing unchanged (see §8).

---

## 3. Workstream A — BGI/deviation primitive (built, tested)

**Where it lives:** `detection/iob.py` (IOB/activity reconstruction) and `detection/deviation.py` (glucose deltas, BGI, deviation). Both are `detection/`, not `core/detection/` — per `CLAUDE.md`'s package boundary, `core/detection/` is "the shared windowing helper and meal-rise detector (used by the live loop)"; this is nightly-batch-only and belongs at the top level of `detection/` alongside `daily_features`.

**What it needs from `basal_df`/`bolus_df`:** `bolus_df` needs `timestamp` + `insulin_units` (both present today, v2 and v3). `basal_df` needs `timestamp` + `commanded_rate` (both present today). `suspension_df` (`suspend_timestamp`/`resume_timestamp`) is optional but recommended, for the belt-and-braces suspension-zeroing described below. **Crucially, insulin history must start at least `dia_hours` (default 5h) before the first CGM reading you actually want a deviation value for** — see the warm-up policy below.

**The deliverable function:** `detection.deviation.compute_deviation_frame(cgm_df, bolus_df, basal_df, config, suspension_df=None)` returns one row per CGM reading with `delta`, `iob`, `activity`, `bgi`, `deviation_5m`, and `warmed_up`. `deviation_5m = delta - bgi`: positive means BG moved up more than insulin activity alone predicts (carbs, algorithmic basal modulation, stress, noise); negative means it moved down more than predicted (exercise, a stacked dose, noise).

### Design decisions worth understanding

**Warm-up is NaN, never 0 — this is the correctness property the whole module hinges on.** IOB at the first available CGM timestamp is not zero; it is whatever the prior `dia_hours` of insulin delivery left on board. If `compute_iob_activity` started integrating from the edge of whatever history it was given, every deviation value in the first ~5 hours would be *wrong in a specific, misleading direction*: an undercounted IOB makes BGI look smaller than it really is, which makes `deviation_5m` look larger than it really is — a real, ordinary insulin tail would read as a spurious "unexplained rise." Instead, any evaluation timestamp whose `t - dia_hours` predates the earliest available dose history gets `NaN` for `iob`, `activity`, `bgi`, and `deviation_5m`. Tested explicitly (`tests/detection/test_iob.py::TestComputeIobActivity::test_warmup_requires_full_dia_of_history`): the same evaluation timestamp is NaN with a short history and populated once given enough trailing history.

**Basal is decomposed against a rolling-median baseline, not a real profile rate.** Per Q1 above, `profileBasalRate` isn't ingested today. `detection.iob.basal_baseline_rate` estimates a local "what would count as normal" rate as the trailing-`N`-day (default 7) *median* `commanded_rate` — a median specifically because it's robust to short excursions (meal-time boosts, exercise suspensions) that would drag a mean away from "typical." Only the *excursion above/below that baseline* becomes an IOB-contributing synthetic dose (`build_dose_events`), one per basal segment, dated at the segment's start, sized `(commanded_rate - baseline) * duration_hours`. If no basal history exists in the lookback window for a given segment, that segment is **dropped, not assumed zero** — an unknown baseline should not silently become "insulin cancels out here," it should become "we don't know."

**Suspension is handled twice, on purpose.** `LidBasalDelivery` should already emit a `rate_source="suspended"` row (`commanded_rate=0`) when the pump suspends — but that invariant was not verified against real data (no DB access this phase). `_zero_during_suspensions` independently splits/zeros any basal segment overlapping a `suspension_df` window, so "no insulin was actually delivered here" has a second, independent source of truth rather than depending entirely on one field's correctness.

**Deviation is deliberately less than oref's `deviation`.** oref's dosing-facing `deviation` scales by `30/5` (projects 30 minutes ahead, for `eventualBG`), falls back from `minDelta` to `minAvgDelta` to `long_avgdelta` whenever the result goes negative (to avoid under-dosing on noisy negative data), clamps positive deviations to 0 below BG 80, and floors carb impact at a config minimum. All four exist to make dosing decisions safer, and all four bake an asymmetric bias into the number. `deviation_5m` here is the plain, unscaled, unclamped `delta - bgi` — appropriate for a descriptive analytics column, inappropriate (and not built) for anything that would resemble a dosing input. This is a direct application of non-goal #1.

### How this degrades with gaps (asked for explicitly by the notes)

- **CGM gap:** rows simply don't exist for that stretch. The reading immediately after a gap wider than 42.5 minutes gets `NaN` deltas (no neighbor falls in any bucket) — verified by test.
- **Bolus/basal history gap** (a sync outage, a missed pump-history page): this is the dangerous one, and there is currently no in-band signal for it. A missing dose silently undercounts IOB/activity, which pushes BGI toward 0 and `deviation_5m` up — it looks exactly like an unexplained rise, not like missing data. Cross-checking against `ingestion.enrich`'s sync-freshness / gap metadata is a recommended follow-up, not implemented this phase.
- **Basal-history gap specifically:** the rolling-median baseline degrades gracefully as long as *some* basal history exists in the lookback window; a segment with none is dropped from `dose_events` (treated as unknown), not assumed net-zero.

### Tests

`tests/detection/test_iob.py` (18 tests) and `tests/detection/test_deviation.py` (14 tests): curve boundary conditions (t=0, t=end), dose conservation (numeric integration of the activity curve recovers ~1.0 unit), monotonic IOB decay, causal (non-lookahead) baseline computation, suspension segment-splitting, the warm-up NaN property from both directions (short history → NaN, long history → populated), backfill-spacing normalization, and an end-to-end qualitative sanity check (a large bolus with flat observed BG must produce `bgi < 0` and `deviation_5m > 0`, confirming the two sign conventions compose correctly). All 32 pass; full-suite run is 794 passed / 42 skipped / 48 deselected, 0 failures (see §8).

---

## 4. Workstream B — UAM as prior art for M1 (prototype, not wired in)

Read `determine-basal.js`'s UAM path in full before writing anything. Two things stood out:

1. **UAM's own trigger is carbon-blind by design** (`(iob.iob > 2*currentBasal || deviation > 6 || uam)` in `categorize.js`) — it doesn't try to distinguish "meal" from "some other unexplained rise," it just asks "is there more insulin on board, or more rise, than basal alone explains." That posture — sensitive, refuses to classify cause — is close to the notes' description of M1's own "sensitivity over precision" choice. It's validating, not a design change: M1's raw-slope approach and UAM's deviation-based approach are answering the same underlying question with different signals, not different philosophies.

2. **The genuinely reusable piece is the decay-trajectory tracking in `cob.js`**, not the trigger itself. Walking backward through a deviation series, oref tracks a running max and running min, and every time either is updated it records the slope from that extreme back to "now" — a decay rate off the peak, a rise rate off the trough. `determine-basal.js` combines both into a single conservative estimate (`slopeFromDeviations = min(slopeFromMaxDeviation, -slopeFromMinDeviation/3)`) used to predict how much longer to trust a still-elevated UAM signal before assuming it's decayed to nothing.

`detection/episode_boundary.py::track_deviation_trajectory` ports the tracking loop (not the downstream combination — that combination feeds a dosing prediction, out of scope) as a standalone function: given a `deviation_5m` series, it returns the running extremes and their slopes. A rising max with ~0 slope reads as "still building"; a max with strongly negative slope reads as "past peak, winding down"; a trajectory pinned near its min reads as "flat baseline, no episode." This is a genuine candidate primitive for a future episode-boundary definition (where a detected meal-rise "ends," for scoring or for a duration feature) — but **it is not wired into M1 or anything else**. The notes' instruction was to read this before touching M1 again, not to touch it this phase; that's a deliberate design decision for the owner to make with this prototype in hand, informed by real deviation data this module hasn't seen yet (again, no DB access this phase).

14 tests in `tests/detection/test_episode_boundary.py`, covering: rising-then-decaying episode shape, hand-computed slope values, the non-positive/non-negative sign invariants, NaN-row pass-through (a missing reading doesn't reset the trajectory), and flat-baseline zero slopes.

---

## 5. Workstream C — deviation categorization for M4 (built, tested)

The notes were unusually specific here: "Port the categorization. Do not port the fitting... The categorization rules need rewriting against the existing `bolus_category` enrichment rather than a straight port. That rewrite is the actual M4 design work." This section is that rewrite.

### Why oref's categorization can't be ported as-is

`lib/autotune-prep/categorize.js` splits every reading into `CSF` (carb-sensitivity data) / `ISF` / `basal` / `UAM` so Autotune's *fitting* step can propose new profile numbers. The basal/ISF split hinges on `basalBGI = currentBasal * sens / 60 * 5` — "the BG impact the *programmed profile rate alone* would produce" — versus observed `BGI`. When they're close, the reading gets bucketed as basal-tunable data.

That comparison assumes a rig where basal is either the profile rate or a discrete user-set temp rate. Under Control-IQ, `rate_source` on `basal_df` is routinely `"algorithm"` or `"temp_rate_and_algorithm"` — basal is *continuously, algorithmically* modulated. A period where observed insulin activity doesn't match a static profile-only assumption is just as likely to be Control-IQ actively intervening as it is to be "the profile rate is miscalibrated." Porting the basal/ISF split as written would attribute Control-IQ's own real-time decisions to a "user profile" bucket this system doesn't even have a use for (no autotune core — non-goal). This is exactly the AAPS pitfall the notes named up front: "AAPS also defaults to categorizing UAM data as basal, which would poison basal attribution for a user whose pump fires auto-corrections continuously."

### What was built instead

`detection/deviation_categorize.py::categorize_deviations` categorizes each deviation against data this repo already trusts:

- **`meal_explained`** — a food-carrying bolus (`core.bolus_categories.FOOD_CARRYING`) is active nearby (default: within a 180-minute absorption window before, or 20 minutes of late-bolus grace after).
- **`auto_correction_explained`** / **`user_correction_explained`** — a correction-only bolus is active nearby, split by who/what initiated it (Control-IQ vs. the user) since that's a meaningfully different signal.
- **`algorithm_modulated`** — **the AAPS-pitfall carve-out.** No explaining bolus, but `rate_source` shows Control-IQ actively driving basal within the last ~15 minutes. Rather than lumping "insulin activity doesn't match a naive expectation" into a basal-tuning bucket (which this system has no use for), it's labeled as what it almost certainly is: the algorithm doing its job. This category didn't exist in oref's scheme at all — it's the direct answer to the notes' warning.
- **`unexplained_rise`** / **`unexplained_fall`** — no bolus, no recent algorithm activity, deviation still outside the noise band. The closest analog to UAM, but named for what it is rather than what it might be; this system doesn't infer "probably an unannounced meal," it flags a gap.
- **`baseline`** — deviation within the noise band (default ±5 mg/dL) of zero, checked *first* (before any bolus/algorithm lookup) — a near-zero deviation with a bolus nearby means "nothing has happened yet," not "meal explained."

12 tests in `tests/detection/test_deviation_categorize.py`, including the specific AAPS-pitfall regression test (`test_algorithm_modulation_carve_out_not_lumped_into_unexplained`) and a precedence test confirming a logged carb entry outranks nearby algorithm activity when both are present.

This module is a categorizer, not a report — it does not itself implement the M4 "effective insulin sensitivity tracking / Autotune-style observation report" the roadmap describes. It's the labeling layer that report would consume.

---

## 6. Workstream E — is `trio-algorithm-validator` a usable replay harness?

Read the README in full. It's real, well-built, and MIT-licensed — but it answers a different question than M2 needs, and the notes' speculation ("if it is a genuine replay harness it is worth more to M2 than anything in workstreams C or D") does not hold up once you know what it actually does.

**What it is:** `oref-validator`, a Swift CLI, replays a fixed corpus of previously-recorded oref algorithm *inputs* (`determineBasalInput`, `autosens`, etc. — full serialized oref state: glucose entries, treatments, IOB, pump profile, meal data) through **two git revisions of the same Swift `Trio` codebase**, then diffs the two output trees for byte-for-byte equivalence. `compare-branches.py` drives it: checks out each revision, builds, replays, diffs.

**Why it doesn't fit M2 as-is:**
1. It requires a local Swift toolchain and a checked-out `Trio/` repo (the actual Trio iOS app, `@testable import Trio` — a release build won't even link).
2. Its input format is full oref algorithm state, not raw CGM/pump export data. Pointing it at "Supabase-exported frames" would require first building a complete oref-compatible profile + IOB + meal-history serializer — i.e., most of a full oref reimplementation, which is explicitly out of scope (non-goal #1: no dosing/prediction logic).
3. Even with that built, it compares **two code revisions against each other** for exact-output equivalence — it does not score a detector against ground truth. It answers "did this code change alter behavior," not "is this detector's missed-meal call correct." M2's actual need (precision/recall of the meal-rise detector against real bolus/carb outcomes) is already served by `detection/calibration/meal_rise_scoring.py`, which answers exactly that question against this system's own data, with none of the above prerequisites.

**Verdict:** real and correctly identified as existing, but not "worth more to M2 than anything in C or D" — it's not usable for M2 without first building the very oref-input pipeline this project has explicitly decided not to build. Filed here for completeness; not a candidate for near-term investment.

---

## 7. Workstream D — autoISF's feature list (read-only, no code)

Per instruction, this is read-only and no AGPL source was fetched — the source for this section is the owner's own `notes/algorithm-research.md` plus general public documentation, not the autoISF/AAPS tree.

The four derived factors the notes describe — acceleration, deviation from target, postprandial deltas at 5/10/45 minutes, duration stuck at high — are a reasonable validated feature catalog for the M5 window-based suite, and every one of them is buildable from data this system already has or Workstream A now provides:
- **Acceleration** = the second derivative of glucose, i.e. `diff(delta)` over `compute_glucose_deltas`'s output — no new data needed.
- **Deviation from target** — already have `bg_targets.target` in config and raw `bg_mgdl`; trivial.
- **Postprandial deltas at 5/10/45 min** — a windowing operation over `bg_mgdl` anchored on `bolus_category`-tagged meal events, using the existing `core/detection/windowing.py` primitive.
- **Duration stuck at high** — a run-length computation over `bg_mgdl > bg_targets.high`, already partially represented by `time_above_range` in `detection/features.py`.

The dynamic-ISF formula the notes cite (`ISF = 277700 / (BG × TDD)`, blending 7-day average TDD with same-day extrapolation) is cheap and runs on data already stored (bolus + basal totals). It's worth evaluating as a second sensitivity estimate alongside whatever Workstream C/M4's attribution work eventually produces — but that evaluation is exactly the kind of thing that should happen with real multi-week data in hand, not speculatively in this exploratory pass. **No module was built for this workstream** — per the advisor review, a stub module for a read-only, no-porting workstream would be surface without value. This section is the deliverable.

---

## 8. Beyond the five workstreams — what else was found

The owner was explicit that the five workstreams are a starting order, not exhaustive. Two additional searches this phase:

- **Academic literature.** A 2025 PLOS Digital Health systematic review, "On the road to fully automated insulin delivery: A systematic review of meal announcement free algorithms," covers 69 studies (2000–2025) on unannounced-meal detection/compensation across heuristic, ML, and control-theory approaches — a much broader literature than the oref family alone, and a good starting point if M1/M5 work continues toward a more principled detector. A 2019 JAMIA paper, "Automated meal detection from continuous glucose monitor data through simulation and explanation" (Bequette group), is frequently cited in that review and specifically addresses explainability, which fits this system's "detect and alert, never dose" posture better than most control-theory approaches (which are built to feed a dosing decision). The public **OhioT1DM dataset** (CGM + insulin + self-reported meals from 12 pump users) is a plausible future validation/calibration dataset — it's data, not code, so it carries no licensing conflict with this project, unlike the AGPL codebases.
- **LoopKit/Loop and LoopKit/LoopAlgorithm.** Checked their GitHub-reported license: both return `NOASSERTION` (GitHub's license detector could not match a recognized SPDX license to whatever `LICENSE` file is present). Per this project's own conservative posture on license discipline, this was treated as "unclear, do not port" and the source was **not fetched or read** this phase. If Loop's algorithm becomes relevant to a future phase, the license needs to be resolved definitively (reading the actual `LICENSE` file's text, not just the API's classifier) before any code is touched.

---

## 9. What's shipped, what's a follow-up

**Shipped this phase, tested, not wired into anything live:**
- `detection/iob.py` — IOB/activity reconstruction (Workstream A)
- `detection/deviation.py` — BGI/deviation (Workstream A)
- `detection/episode_boundary.py` — deviation-trajectory prototype (Workstream B)
- `detection/deviation_categorize.py` — categorization against `bolus_category`/`rate_source` (Workstream C)
- `tests/detection/test_iob.py`, `test_deviation.py`, `test_episode_boundary.py`, `test_deviation_categorize.py` — 56 new tests, all passing; full suite 794 passed / 42 skipped / 48 deselected, 0 failures.

**Recommended follow-ups, not done this phase (each independently small):**
1. Extend `ingestion.builders.build_basal_df` to also capture `profile_basal_rate`, `algorithm_rate`, `temp_rate` off `LidBasalDelivery` (bytes already parsed, just not surfaced). Bump `PIPELINE_VERSION`.
2. Extend `ingestion.builders.build_bolus_df` to capture `insulin_requested` off `LidBolusCompleted`.
3. Extend `ingestion.builders.build_request_df` to capture `ISF` off `LidBolusRequestedMsg2`, enabling a real (not placeholder) ISF schedule for `detection/deviation.py`.
4. A new profile builder to fetch `PumpProfile.insulinDuration` (and segments) via `device_settings_from_guid` — currently not ingested at all.
5. Run the basal-cadence verification query in §2/Q2 against real Supabase data.
6. A sensitivity analysis of `deviation_5m` under varying DIA/peak assumptions, once real multi-day data is available to run it against.
7. A deliberate, owner-reviewed decision on whether/how `detection/episode_boundary.py`'s trajectory tracking should inform episode-boundary detection for M1/M2 — this phase built and tested the primitive, not the integration.

**Status:** this entire branch is exploratory. Per the task brief, it must not be merged without the owner's own review, since it touches detection-adjacent territory even though nothing here is wired into a live or nightly path yet. `config/user_config.yaml` was not touched; no existing detection threshold was changed.
