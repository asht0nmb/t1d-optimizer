# 2026-06-11 — Automation & observability depth (Phase 5)

## Nightly-sync failure alerting

`tandem-nightly-sync.yml` now sends a Telegram message (same bot/chat as
the live alerts) when the sync job fails, linking the workflow run. The
dashboards silently going stale was previously invisible until someone
noticed old data. The step fails soft (a warning annotation) if Telegram
itself is unreachable. Uses repo secrets already present for the manual
meal-rise fallback workflow.

## Web Status page (`/status`)

Automation health at a glance, session-guarded like every data route:

- **Data recency** — latest `cgm.timestamp` (stale > 26 h: nightly
  cadence + slack).
- **Sync bookmarks** — every `fetch_state` row (`last_synced_at` /
  `updated_at`, stale > 26 h).
- **Live loop** — latest `detection_results.created_at` (badge color
  only; absence of detections is not failure) and latest
  `alerts_sent.fired_at` with its delivery state.

Pure `classifyFreshness` helper (ok/stale/missing, null-safe) is
unit-tested; badges mirror the Alerts page palette.

## Suites

Web: 53 vitest tests across 12 files, `tsc --noEmit` clean. Python suite
unchanged (596 passed). Deploy note: `/status` ships with the next
`apps/web` deploy; no schema changes.
