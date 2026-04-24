Plan: Next steps — enrichment visibility in check & viz (+ execution protocol)
Goal
Make enrichment (columns on requests/events, plus site_issues / cgm_gaps tables) visible and inspectable from the same tools people already use (check, viz), while preserving today’s behavior as an explicit “original” mode so comparisons and regressions are easy.

Non-goals (this plan): Telegram, Streamlit, live pydexcom, changing detection algorithms.

Principles
Default stays “original” — uv run python main.py check --date D and viz --date D behave as they do today unless the user opts in to enriched view. That avoids surprise and keeps screenshots/docs stable.
Single source of truth for “is this enriched?” — Reuse the same logic as scripts/run_detection.py: if parquets lack bolus_category / forced_by_alarm / etc., compute enrichment in memory (already partially there post–b2ba092). Factor a small shared helper so check/viz/run_detection don’t duplicate.
Config-driven labels — TIR bands for check should read bg_targets from user_config.yaml (viz already loads YAML; align check if not already).
Tests — Any new CLI flag or code path gets pytest coverage (smoke: “original mode prints X lines”, “enriched mode prints Y section”).
Phase 1 — Shared loading helper (foundation)
Task 1.1 — Extract (or centralize) a function, e.g. ingestion/view_data.py or scripts/data_views.py:

Inputs: optional mode original | enriched, date (for filtering is caller’s job or optional).
Behavior:
original: load_df only; strip enrichment-only columns if present or document that “original” means “no extra sections” while still reading raw parquet (simplest: original = never call enrich, only load; if file on disk is already enriched, either document that true byte-identical original requires old parquets or strip known columns in a documented list).
enriched: load_df → if missing enrichment columns / tables, call enrich_all + build_site_issues_df / build_cgm_gaps_df as in run_detection (same as backfill fix).
Decision to lock in the plan: Prefer original = load-only, no enrich(); if parquets were saved with extra columns, “original” view may still show base columns only (hide enriched columns in print/plot). Enriched = guaranteed via in-memory enrich when missing. Document this in --help.

Task 1.2 — Unit tests for the helper: empty frames, missing files, pre-enriched parquet vs not.

Phase 2 — check (sanity_check)
Task 2.1 — CLI: add to main.py + sanity_check:

uv run python main.py check --date YYYY-MM-DD [--view original|enriched]
Default: --view original (or omit = original).
--view enriched: print additional sections:
Requests: per-row bolus_source + bolus_category + override_delta (when present).
Events (site_change): forced_by_alarm when column exists.
Site issues: rows overlapping the day (filter first_occlusion_ts / window columns per your schema).
CGM gaps: rows overlapping the day (start_ts/end_ts in cgm_gaps).
Keep existing sections unchanged in original mode.
Task 2.2 — Tests: tests/test_sanity_check.py (new) or extend existing — capture stdout with capsys, assert substrings for enriched only when --view enriched.

Phase 3 — viz (daily_viz)
Task 3.1 — CLI:

uv run python main.py viz --date YYYY-MM-DD [--view original|enriched]
Default: original — current panels, current styling (baseline for regression).
Enriched — add optional overlays (keep CGM/bolus/basal structure):
Site change markers: differentiate forced_by_alarm=True (e.g. hatch, muted color, or “(forced)” in label) vs false.
Bolus request annotations (middle panel): small text or color by bolus_category where requests aligns with bolus time (within ±5 min); show override_delta for overrides.
Site issues: vertical band or marker at first_occlusion_ts–last_occlusion_ts when overlapping day (subtle, don’t obscure CGM).
CGM gaps: prefer cgm_gaps spans over re-deriving from raw alarms (single source of truth); keep existing alarm-based OOR shading only in original or merge carefully in enriched to avoid double-drawing (pick one strategy in implementation).
Task 3.2 — Visual regression: document “eyeball checklist” in HANDOFF or plan appendix (not automated pixel tests unless you add matplotlib compare_images later).

Task 3.3 — Tests: optional smoke test that daily_viz with --view enriched runs without exception when given minimal synthetic data (mock plt.show to no-op).

Phase 4 — Docs & polish
Update docs/operating_docs/HANDOFF.md — new flags, behavior table original vs enriched.
Update CLAUDE.md or README snippet — one line on --view.
If DATA_CATALOG.md lists CLI, add check/viz view modes.
Phase 5 — Follow-on (after visibility ships)
Merge branch feat/enrichment-detection-v1 → main after review.
Optional: viz --compare opens two figures side-by-side (original vs enriched) — only if Phase 3 feels cramped; otherwise defer.
Integration test: one day of real parquet, assert enriched check contains bolus_category line.
Execution protocol — subagents & superpowers (mandatory for the executing agent)
The agent must follow the Cursor superpowers skills from the project’s skill paths:

using-superpowers — Before any substantive reply, locate applicable skills (writing-plans already done; use executing-plans for implementation).
executing-plans — Execute the plan task-by-task in order; checkpoint after each phase; do not skip Phase 1.
test-driven-development — For each behavior change: failing test first, then implementation.
verification-before-completion — Run uv run pytest -q and manual check/viz on a known date; no “done” without command output evidence.
systematic-debugging — If tests or plots fail, root-cause before patching.
subagent-driven-development or dispatching-parallel-agents:
Parallel: Task 1 (helper) can run in parallel with doc-only updates only if no file overlap; default sequential to avoid merge conflicts.
Explore subagent: Use explore (readonly) to map all load_df / enrich call sites before editing.
brainstorming — Before large daily_viz layout changes, one short design pass (what overlays, z-order, legend).
finishing-a-development-branch — When phases complete, present merge/PR options.
Explicit rule: Do not use subagents for writing secrets or editing .env.

Acceptance criteria (binary)

 check --date D default output matches pre-change behavior (characteristic lines/sections).

 check --date D --view enriched prints enrichment sections when data supports it.

 viz --date D default matches prior visual intent (same structure).

 viz --date D --view enriched shows at least: forced site-change distinction + one of (bolus_category labels OR site_issues OR cgm_gaps), without breaking original mode.

 uv run pytest -q all green; new tests for new flags.

 HANDOFF (or equivalent) documents both modes.