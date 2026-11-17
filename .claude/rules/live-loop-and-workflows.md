---
paths:
  - "apps/personal/**"
  - "api/**"
  - ".github/workflows/**"
  - "scripts/ci_log_gate.py"
  - "scripts/setup_telegram_webhook.py"
  - "vercel.json"
---

# Live loop, worker and CI workflows

- The live meal-rise loop has been paused since 2026-10-29. Don't add features to it. Fix it only when the owner asks.
- `api/` deploys automatically as the `t1d-optimizer-meal-bot` Vercel project when main changes. Any change here can break production, so the PR has to say so.
- The worker must not log identifiers, including the Dexcom username. Use the redacting logger.
- Every workflow step that uses Tandem or Dexcom credentials runs inside `scripts/ci_log_gate.py`, which blocks output by default. A new workflow of that kind follows the same pattern.
- Secrets come from GitHub or Vercel secret stores. Never echo them, write them to files, or set them yourself. Setting secrets is an owner step documented in `docs/operating_docs/DEPLOY.md`.
- Failure has to be visible. A job that can't do its work exits non-zero so the Telegram failure alert fires.
