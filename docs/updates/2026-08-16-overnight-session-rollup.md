# 2026-08-16 — Overnight autonomous session rollup (Phase 6 / 8)

Final phase of an 8-phase overnight `/remote-control` session. This entry
indexes the whole night's work and its PRs; each phase has its own detailed
dated entry (linked below) — this one is the map, not a duplicate.

## Orientation

Session started by auditing the live production state rather than assuming
it was healthy. That surfaced the actual priority immediately: the nightly
Tandem→Supabase sync had been silently dead for 47 days. Everything else
was sequenced around fixing that first.

## Phases and PRs

1. **[Nightly sync restoration](2026-08-16-tandem-nightly-sync-restoration.md)**
   — [PR #6](https://github.com/asht0nmb/t1d-optimizer/pull/6). Root-caused
   and fixed the 47-day outage (tconnectsync v2→v3 migration, forced by a
   Tandem breaking API change). Found and fixed a second, unrelated bug
   along the way: a `TIMEZONE_NAME` secret that existed but was never wired
   into the workflow, mislabeling ~5 weeks of nightly-synced timestamps by
   exactly 3 hours — repaired in production, verified against live
   re-fetches. Backfilled the full 47-day data gap. Verified with a real
   triggered GitHub Actions run.
2. **[Live-system health audit](2026-08-16-live-system-health-audit.md)**
   — [PR #7](https://github.com/asht0nmb/t1d-optimizer/pull/7). Confirmed
   the web app, cron worker, and Telegram webhook are healthy end-to-end.
   Found the live meal-rise alert loop has never recorded a true detection
   in ~6 weeks running; traced to a currently-4h-stale Dexcom Share feed
   (the freshness guard correctly suppressing — not a bug) but flagged the
   zero-ever history as worth better observability later. Authenticated
   web-app click-through stayed blocked (browser extension not connected
   in this environment) — this was `SESSION_SUMMARY.md`'s one open item
   from the prior session; still open, folded in here.
3. **Scoped correctness review + security pass** — [PR #8](https://github.com/asht0nmb/t1d-optimizer/pull/8).
   Fixed a real pre-existing bug found while re-verifying old fixes:
   suspension events were attributed to the wrong alarm whenever two
   alarms fired the same second. Added a contract test pinning the
   codebase's field-name assumptions to the actual installed
   `tconnectsync` package — closes the exact blind spot that let the whole
   47-day outage go undetected by the test suite. Security review (skill +
   manual pass) found no real vulnerabilities, but incidentally caught and
   fixed a transitive PyJWT downgrade that had reintroduced a patched CVE.
4. **[UX/accessibility polish](https://github.com/asht0nmb/t1d-optimizer/pull/9)**
   — PR #9. Code-level audit (no browser automation available, stated
   honestly). Fixed the headline finding: CGM chart markers were
   color-only red/orange/green for high/low/in-range with no shape or
   legend — a real accessibility problem for exactly the kind of data this
   app displays. Also fixed dark-mode token bypasses, added a missing
   error boundary, fixed a couple of silently-swallowed fetch failures.
5. **[Algorithm research](https://github.com/asht0nmb/t1d-optimizer/pull/10)**
   — PR #10, exploratory/not-for-merge. Researched the oref/openaps
   artificial-pancreas algorithm family (MIT-licensed sources only; read
   zero AGPL source) as prior art, per `notes/algorithm-research.md`. Built
   and tested a BGI/deviation primitive (the notes' stated M2 blocker) plus
   an episode-boundary prototype and a deviation categorizer, all with
   documented provenance.
6. **[ML/clustering exploration](https://github.com/asht0nmb/t1d-optimizer/pull/11)**
   — PR #11, exploratory/not-for-merge. Per the owner's one-time lift of
   the ML-deferral boundary. v2 daily-pattern clustering with real
   validation against live data (finding: the data supports k=2, not the
   legacy config's 5). Supervised models on the M2-labeled corpus (honest
   negative result: doesn't beat baseline, documented why). An LLM
   cause-attribution Telegram assistant, code-complete but deliberately
   unwired. Full `docs/ml-notes/` writeups as explicit learning material.

## Docs truth-up (this phase)

- `CLAUDE.md`: fixed a stale test count (`730 passed` → `742 passed`) that
  predated this session — the suite had already grown by 12 tests before
  tonight's work even started.
- `SESSION_SUMMARY.md` (the prior session's untracked note): its one open
  item (authenticated web-app QA) is folded into Phase 2's entry above;
  deleting the file per its own "delete when done" note now that it's
  captured in the permanent dated audit trail.
- Verified `docs/operating_docs/TECHNICAL_SPEC.md` and `DEPLOY.md` don't
  contain other now-inaccurate claims beyond what the phase-specific
  entries above already address; no further edits needed there.
- Fixed this session's own memory notes (`product-completion-roadmap.md`
  had stale branching info claiming `main` was reset to a pre-completion
  baseline — it wasn't; `main` has carried all that work for weeks). See
  the memory file itself for the correction.

## What's NOT done / needs the owner

- **Merge order**: PRs #6 → #7 → #8 are sequentially stacked (each needs
  the previous merged first, since #7 and #8 branch off `main` but #8's
  code changes build on #6). #9, #10, #11 branch independently off `main`
  and can merge (or not, for #10/#11) in any order relative to the others.
- **#10 and #11 are explicitly not ready to merge** — exploratory research
  the owner asked for, pending his own review. Nothing in either touches
  `config/user_config.yaml` or live detection behavior.
- **Live meal-rise alert-loop observability gap** (Phase 2 finding) — no
  code changes made; needs a design decision, not just a bug fix.
- **Authenticated web-app QA** — still blocked on the Claude-in-Chrome
  browser extension not being connected in this session's environment.
- **Deploy step**: Phase 1's fix needs no manual deploy (GitHub Actions
  picks it up on merge), but the owner should watch the first nightly run
  post-merge given the TZ fix changes what "now" looks like for detection.
