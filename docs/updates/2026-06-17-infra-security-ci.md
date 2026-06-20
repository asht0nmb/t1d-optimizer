# 2026-06-17 — Infra / security / CI hardening (Workstream I)

From `docs/superpowers/specs/2026-06-17-refinement-master-plan.md`.

## CI

- `tests.yml` now installs the `local` group (`uv sync --frozen --group local`)
  so the Streamlit-dashboard tests, which import plotly/streamlit, actually run
  in CI — previously they would have errored on import. The `ml` group stays
  optional (unused by tests).
- Added a **web CI job**: Node 20 → `npm ci` → `lint` → `tsc --noEmit` →
  `vitest run` → `next build` (build uses public-safe placeholder env; no DB
  access at build time). A broken dashboard no longer merges green.

## Security

- **Constant-time bearer auth**: `api/index.py` uses `hmac.compare_digest`; the
  web `verifyCronAuth` uses `crypto.timingSafeEqual` with a length guard. Both
  keep the fail-closed-on-empty-secret behavior.
- **Route-guard backstop test**: a vitest scans every `app/api/**/route.ts` and
  asserts each references `requireSession` or `verifyCronAuth`, so a future
  route can't ship unguarded (RLS gives these routes no defense-in-depth).

## Reliability / observability

- **Live-cron heartbeat + failure alert**: the 5-min worker rewrites a
  `live_cron` `fetch_state` row each completed cycle and fires a Telegram alert
  on a cycle exception. The heartbeat is skipped on failure, so its absence is
  the signal. The web `/status` page gains a **"Live loop"** signal at a 15-min
  threshold — the only signal that distinguishes a healthy-but-idle loop from a
  dead one. (Additive to the live worker; no detection-behavior change.)
- **Pinned worker requirements**: `apps/cron_worker/requirements.txt` is pinned
  to exact lock versions so the Vercel worker matches the tested set.

## Config / docs

- `TELEGRAM_WEBHOOK_SECRET` added to the root `.env.example` (it is required by
  `api/telegram.py` and was missing from the template).
- New `docs/operating_docs/DEPLOY.md` — a consolidated hosted-shell runbook:
  migrations → bootstrap → web Vercel project → worker Vercel project →
  cron-job.org → Telegram `setWebhook`, with the full per-surface env-var list.

## Owner rollout

The heartbeat/alert and pinned requirements ship on the next worker deploy; the
`/status` "Live loop" row appears on the next `apps/web` deploy. CI changes take
effect on the next push.

## Suites

Python 723 passed / 42 skipped / 48 deselected; web 62 vitest, `tsc` clean,
`next build` green with placeholders.
