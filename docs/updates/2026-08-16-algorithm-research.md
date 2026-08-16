# 2026-08-16 — Algorithm research (Phase 7): oref/trio-oref prior art for BGI/deviation

**Branch:** `research/algorithm-improvements` (exploratory — see status note below)
**Full writeup:** `docs/algorithm-research-findings.md`

## What this is

Phase 7 of an 8-phase autonomous session, following the owner's research brief `notes/algorithm-research.md`: investigate open-source artificial-pancreas algorithm work (openaps/oref0, nightscout/trio-oref, AndroidAPS/AAPS) for prior art applicable to this system's meal-rise/anomaly detection, under a hard license constraint (MIT sources may be read and ported with attribution; AGPL sources may be read for method only and must be reimplemented, never copied).

## What was built

- `detection/iob.py` — insulin-on-board / activity reconstruction from `basal_df`/`bolus_df`. Ports oref's exponential insulin-action curve (`lib/iob/calculate.js`, MIT, nightscout/trio-oref) with attribution; the basal→dose-event decomposition (rolling-median baseline surrogate, suspension zeroing, warm-up NaN policy) is original code, not ported.
- `detection/deviation.py` — the Workstream A deliverable: BGI and deviation as a per-CGM-reading derived column, nightly-batch-only. Ports oref's delta-normalization scheme (`lib/glucose-get-last.js`) and BGI formula (`determine-basal.js`), both MIT/attributed. Deliberately omits oref's dosing-safety adjustments (30-min projection, negative-deviation fallback ladder, BG<80 clamp, carb-impact floor) since this is a descriptive column, not a dosing input.
- `detection/episode_boundary.py` — Workstream B prototype. Ports oref's deviation max/min slope-tracking loop (`cob.js`, MIT/attributed) as a standalone, tested function. Not wired into M1.
- `detection/deviation_categorize.py` — Workstream C. A from-scratch categorizer (per the notes' explicit instruction, NOT a port) that labels deviations against this repo's existing `bolus_category` enrichment and `rate_source`, including an `algorithm_modulated` category specifically to avoid the AAPS pitfall the notes named: attributing Control-IQ's own continuous basal modulation to a "basal needs tuning" bucket this system has no use for.
- 56 new tests across `tests/detection/test_iob.py`, `test_deviation.py`, `test_episode_boundary.py`, `test_deviation_categorize.py`. Full suite: 794 passed, 42 skipped, 48 deselected, 0 failures.
- README attribution section, per the notes' MIT-vendoring requirement.

## Key findings (see the full doc for detail)

- **Ingestion gap, real and actionable:** `LidBasalDelivery` (v3) carries `profileBasalRate`/`algorithmRate`/`tempRate` alongside `commandedRate`, and `LidBolusCompleted` carries `insulinRequested` alongside `insulinDelivered`, and `LidBolusRequestedMsg2` carries `ISF` — all confirmed present on the raw v3 event classes, none currently extracted by `ingestion/builders.py`. None of these block Workstream A (BGI only needs delivered insulin, which is already captured), but all three are recommended fast-follow ingestion extensions for M4-grade attribution work.
- **Backfilled-CGM 5-minute bucketing does not survive contact with reality** — confirmed structurally (backfilled rows keep sensor-read timestamps, not grid-aligned) and fixed by porting oref's elapsed-minutes normalization rather than assuming fixed spacing.
- **`trio-algorithm-validator` (Workstream E) is real but not usable for M2** without first building most of a full oref-input pipeline (out of scope) — it's a two-revision equivalence diff for the Trio Swift codebase, not a detector-scoring harness against ground truth.
- No AGPL source was read this phase (Workstream D stayed read-only against the owner's own notes, as instructed).
- This worktree has no Supabase credentials, so several structural findings (basal event cadence, backfilled-timestamp distribution) could not be verified against real production data — the doc includes the exact SQL query for the owner to run.

## Status

Exploratory / unmerged. `config/user_config.yaml` was not touched; no existing detection threshold changed. Nothing built this phase is imported by the live cron loop, the nightly sync, or the M1/M2 detection paths — everything is new, standalone, tested modules pending the owner's own review before any integration decision.
