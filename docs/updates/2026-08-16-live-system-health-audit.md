# 2026-08-16 — Live-system health audit (Phase 2)

Second phase of the overnight autonomous session (`/remote-control`). Goal:
confirm every live production surface is actually healthy, not just
deployed, now that Phase 1 restored the historical pipeline.

## What's confirmed healthy

- **Web dashboard** (`t1d-optimizer.vercel.app`): `/` → 307 (redirect to
  login, expected), `/login` → 200, `/status` → 307 (session-guarded,
  expected). All 11 `/api/*` data routes (`day/[date]`, `agp`, `alerts`,
  `compare`, `config`, `heatmap`, `insulin`, `report`, `search`, `status`,
  `trends`) → 401: reachable and correctly auth-gated, not 404/500.
- **Cron worker** (`t1d-optimizer-meal-bot.vercel.app`): `/api/meal_rise_cron`
  triggered with the real bearer secret → `200 {"ok": true, "exit_code": 0}`,
  and the `live_cron` heartbeat updated within 10 seconds of the call —
  confirms the deployed worker, its DB connectivity, and its execution path
  all work end-to-end, not just that the endpoint responds.
- **Telegram webhook** (`/api/telegram`): GET → 200 is intentional
  (`api/telegram.py`: "Telegram only POSTs; a GET is a health probe."), not
  a bug — didn't misread this as an auth gap.
- **`/api/metrics_report`** (the `/report` proxy target): 401, correctly
  guarded.

## Real finding: the live meal-rise alert loop has never fired an alert

`alerts_sent` and `detection_results` — the tables written whenever the
live detector actually evaluates a genuine sharp-rise candidate — both have
**zero rows, ever**, despite the live loop having run continuously since
early July (~6 weeks) with a heartbeat landing every ~5 minutes throughout.

Reproduced the exact live cron cycle locally (`apps.personal.cron.detect_meal_rise.run_cron()`,
same code path as the deployed worker) to see why: right now, the latest
Dexcom Share reading is **240 minutes (4 hours) stale**, so the freshness
guard (`max_reading_age_minutes: 15`) correctly refuses to run detection —
this is the code working as designed, not a bug. But it also means: as of
this session, the safety net is not currently active, and I can't rule out
that this kind of gap is a recurring pattern rather than a one-off (the
pump-side `cgm_gaps` table, sourced from a *different* feed — tconnectsync
via the pump, not Dexcom Share via phone — shows only normal short gaps
historically, so it doesn't answer the question either way).

I can't distinguish, from what's currently recorded, between three
explanations: (a) genuinely zero true missed-meal events occurred in 6
weeks of real eating (plausible if boluses are well-timed — a good
outcome), (b) the Dexcom Share feed is chronically stale often enough that
detection rarely gets a fresh-enough window to evaluate, or (c) some other
gate (`start_level_min/max`, time-of-day multiplier, `refractory_minutes`)
is suppressing more than intended. The M2 calibration report (see
[[product-completion-roadmap]] memory) found ~100 candidate rises/month
*retrospectively*, most auto-correction-resolved — consistent with (a), but
not conclusive.

**Recommendation for a later phase (not implemented tonight — this is an
observability gap, not a detection-logic bug, and detection thresholds are
owner-reserved regardless):** log every cron cycle's outcome (not just true
detections) — e.g. `stale_skip` / `no_rise` / `suppressed_refractory` /
`alert_sent` — to a lightweight table or the existing heartbeat payload, so
this exact question is answerable from data instead of an ad-hoc
reproduction like tonight's. Surfacing a rolling stale-CGM-frequency stat
on `/status` would also make Dexcom Share connectivity issues visible
without someone needing to notice zero alerts over weeks.

## Not done: authenticated web-app click-through

The `/day`, `/heatmap`, `/agp`, `/trends`, `/report`, `/insulin`, `/alerts`
pages need an authenticated session to inspect for rendering/data errors —
this was the one item the prior session (`SESSION_SUMMARY.md`) left open,
and it's still open. The Claude-in-Chrome browser extension is not
connected in this autonomous session's environment (`tabs_context_mcp`
returned "Browser extension is not connected"), the same limitation the
prior session hit. Unauthenticated route/API health above is the most that
could be verified from here. Carrying this forward to Phase 6's docs
truth-up rather than leaving it solely in the untracked `SESSION_SUMMARY.md`.
