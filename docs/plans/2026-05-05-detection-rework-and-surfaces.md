# Plan: Detection Rework + Surfaces

**Date:** 2026-05-05
**Branch:** TBD (new branch off main; v1 detection on `feat/enrichment-detection-v1` to be abandoned, not merged)
**Status:** Scoping

## Context

Current detection engine (v1) was AI-implemented without first-principles design and is being replaced wholesale. This plan covers the rework plus the surfaces it feeds: live alerts (Telegram), dashboard (Next.js), and LLM-driven Telegram pull commands. The architecture is also designed to support a future open-source local-only deployment as a parallel "shell" over the same core library.

## Architecture (decided)

- **Frontend:** Next.js on Vercel
- **Storage + auth:** Supabase Postgres (free tier, ~14 years of headroom)
- **Telegram:** Webhook as Vercel API route (no always-on polling)
- **Live detection loop:** External cron (cron-job.org) hits a Vercel function every 5 min
- **Nightly Tandem sync:** GitHub Actions workflow runs tconnectsync and writes to Supabase. Free, 6-hr timeout (Vercel Hobby's 10s won't cover backfills), built-in run logs. Vercel cron is the fallback if Actions can't authenticate against Tandem in CI.
- **LLM:** DeepSeek API, used only for pull-based Telegram commands, scheduled digests, and dashboard conversation panel. Never on the alert path.
- **Local Python:** No longer load-bearing for the personal deployment. Stays around for ad-hoc historical work, debugging, and one-off backfills. Becomes the basis of the OSS local shell.

## Core / shell split (OSS-friendly architecture)

The system is structured as a storage-agnostic core library plus thin deployment-specific shells. This is the architectural discipline that lets us ship a personal cloud version *and* an open-source local version from one codebase.

**Core (`ingestion/`, `detection/`, future `enrichment/`):**

- Pure functions over normalized DataFrames. Inputs are DataFrames and a config object. Outputs are DataFrames or detection results.
- Imports nothing about Supabase, Vercel, parquet paths, or any specific storage backend.
- Reads/writes go through a `Storage` Protocol (see Infra section).
- Detection v2 (rebuild) follows the same rule that v1 already enforces: source-agnostic, no ingestion imports.

**Personal cloud shell (`apps/web/`, `apps/cron/`, GitHub Actions workflows):**

- Next.js dashboard reading from Supabase
- Vercel cron functions for live detection + Telegram webhook
- GitHub Actions workflow for nightly Tandem sync
- Concrete `SupabaseStorage` implementation of the storage Protocol

**OSS local shell (`apps/local/`, future):**

- Streamlit dashboard reading from local parquet (or SQLite)
- CLI commands for sync (user-driven cron)
- Optional Telegram via long-polling (so localhost users don't need a webhook URL)
- Optional LLM features behind an API key in config
- Single-YAML config for thresholds, meal windows, alert preferences
- Concrete `ParquetStorage` implementation of the storage Protocol
- Onboarding goal: `pip install`, edit one YAML, run one command, dashboard works

**Constraints all shells respect:**

- All storage access goes through the Storage Protocol
- All thresholds and tunables come from config (no hardcoded numbers in core)
- Outputs framed as observations, never therapeutic recommendations
- No automation that affects pump therapy

## Detection rework

- [ ] **Redesign from first principles.** Inputs are enriched parquet/Postgres tables; outputs designed top-down from desired use case, not bottom-up from existing code.
- [ ] **Define output layers explicitly.** Event-level (live alerts), episode-level (grouped narrative), pattern-level (multi-day shifts). Each has different reliability requirements.
- [ ] **CGM-only meal-shape detector.** Rate of rise, duration to peak, starting BG, time of day. No bolus matching on live path (which is why Tandem isn't needed live).
- [ ] **Calibration protocol.** Label historical rises against Tandem bolus data (post-hoc), tune CGM-only thresholds for precision/recall.
- [ ] **Defer ML.** Build deterministic baseline first; ML only justified once labeled data and validation harness exist.
- [ ] **Detection v2 lives in `detection/v2/` (or replaces `detection/`); v1 deprecated.** Add a banner docstring to `detection/__init__.py` so agents don't accidentally extend v1.

## Alert pipeline (live path)

- [ ] pydexcom poll on 5-min cron via Vercel function
- [ ] Detection runs over rolling CGM window pulled from Supabase
- [ ] Alert dedup state in Supabase (no re-firing same event)
- [ ] Pre-templated alert messages with numeric injection (no LLM)
- [ ] Day-1 alert types beyond missed meal: TBD (low/trending-low, prolonged high, flatline, sensor dropout)

## Dashboard (Next.js — personal)

**Phase A (buildable now, no detection dependency):**

- [ ] Day view (port of `daily_viz`: CGM, boluses, basal, alarms, site issues, gaps)
- [ ] BG heatmap (hour-of-day × date, color-coded BG)
- [ ] TIR rolling trends (7/14/30-day stacked bands)
- [ ] Bolus/insulin history (daily totals, basal-bolus split, IOB trace)
- [ ] Search and filter (TIR < X, alarms > N, lows < Y, etc.)
- [ ] Day comparison overlay

**Phase B (gated on detection rework):**

- [ ] Episode timeline (narrative day view)
- [ ] Cluster view (membership + characteristics, only if clustering becomes meaningful)
- [ ] Pattern flags (multi-day shifts surfaced)

**Phase C (LLM):**

- [ ] Conversation panel (free-form chat with data context)
- [ ] Per-day "explain this day" button

## Dashboard (Streamlit — OSS local)

Deferred until personal version is stable. Same view library where possible (matplotlib helpers in `core/viz/` reused by both).

- [ ] Streamlit shell rendering Phase A views from `ParquetStorage`
- [ ] First-run setup wizard (paste tconnectsync creds, paste Telegram bot token if wanted, paste LLM key if wanted, write config)
- [ ] `t1d-engine sync`, `t1d-engine dashboard`, `t1d-engine alert` CLI verbs

## Telegram commands (LLM-backed pull)

- [ ] Webhook bot listening for predefined commands
- [ ] Time-scoped summaries: today / yesterday / this week / this month
- [ ] Explain a moment: `/why HH:MM` or `/explain spike`
- [ ] Compare windows: this week vs last week
- [ ] Endo prep: structured 14-day clinician summary
- [ ] Settings observation: flag what looks off (observations, not prescriptions)
- [ ] Scheduled push: morning briefing, weekly digest

## Infra / data layer

- [ ] **Storage Protocol** (`core/storage/protocol.py` or similar). Methods cover: `read_<table>`, `write_<table>`, `get_fetch_state`, `set_fetch_state`, `read_alerts_sent`, `record_alert_sent`, `read_detection_config`. Protocol/ABC, not a class to inherit from concrete logic.
- [ ] `ParquetStorage` implementation: wraps current `ingestion/storage.py` calls behind the Protocol.
- [ ] `SupabaseStorage` implementation: pgsql via `supabase-py` or direct asyncpg.
- [ ] Supabase project setup, schema port from parquet (cgm, bolus, requests, basal, suspension, events, alarms, site_issues, cgm_gaps).
- [ ] **One-time historical bootstrap:** push existing parquet to Supabase (don't re-fetch from Tandem).
- [ ] **Nightly Tandem sync via GitHub Actions:** workflow + secrets for tconnectsync creds, writes incremental to Supabase. Vercel cron fallback if Actions auth fails.
- [ ] New tables: `alerts_sent` (dedup), `fetch_state` (sync bookmarks), `detection_config` (tunables).
- [ ] Auth via Supabase (single user, but proper auth so the dashboard isn't open to the world).
- [ ] Storage management: not a concern at this volume.

## Open-source readiness (parallel workstream, lower priority)

- [ ] License selection (likely AGPL or similar, matching Nightscout/Loop precedent).
- [ ] Disclaimers ("not medical advice, not FDA-approved, do not change therapy based on output") posted in README, dashboard footer, every Telegram digest, every LLM response.
- [ ] Public repo prep: scrub any private data from history, factor secrets out, write `INSTALL.md` for the local shell.
- [ ] Document the Storage Protocol as the public extension point.

## Open questions

- [ ] Calibration: what's the labeling protocol for "this rise was a meal vs not" against historical Tandem data?
- [ ] Branch from main fresh, or cherry-pick anything from `feat/enrichment-detection-v1` (e.g. enrichment layer is solid, version guard is solid)?
- [ ] Tandem sync cadence: nightly, or twice-daily for fresher dashboard/LLM context?
- [ ] What does the LLM context payload look like, exactly, for a "what's going on today" command? (Affects token budget.)
- [ ] Where does the Storage Protocol physically live? `core/storage/`? New top-level `storage/`? Shapes import paths going forward.