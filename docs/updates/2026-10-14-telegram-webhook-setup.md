# 2026-10-14: Telegram command bot webhook setup

## Root cause

The Telegram command bot (`/today`, `/status`, ...) never worked. Two
independent causes:

1. No webhook was ever registered with Telegram (`getWebhookInfo` returned an
   empty `url`), so Telegram never called `api/telegram.py`.
2. `TELEGRAM_WEBHOOK_SECRET` was not set in the worker Vercel project
   (`t1d-optimizer-meal-bot`). `verify_secret` fails closed on an empty
   expected value, so every request returned 401 even if a webhook existed.

Related: the GitHub Actions repo secrets `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID` did not exist, so the nightly sync's "Notify Telegram on
failure" step could never send.

## Fix

- Added `scripts/setup_telegram_webhook.py` (`gen-secret`, `register`,
  `verify [--live-test]`, `delete`) with mocked-HTTP tests. Secrets travel only
  in HTTPS bodies/headers and are never printed.
- Rewrote section 6 of `docs/operating_docs/DEPLOY.md` with the ordered owner
  steps: generate secret, pipe it into the worker's Vercel Production env
  (`--sensitive`, via a temp dir linked to the worker project), redeploy,
  register + verify, then set the GitHub secrets via `gh secret set`.

Secret-store writes (Vercel env, GitHub secrets) are performed by the owner;
automation did not write them. Status at time of writing: steps pending owner
execution.
