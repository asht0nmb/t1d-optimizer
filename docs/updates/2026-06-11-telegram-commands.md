# 2026-06-11 — Telegram command surface (Phase 6, deterministic)

On-demand Telegram commands answered from stored data. **No LLM** — the
conversational/LLM assistant stays deferred with the rest of the
intelligence layer. Every reply is a deterministic aggregate and carries
"Observations only — not medical advice."; nothing recommends a dose.

## Commands

- `/today`, `/yesterday` — that local calendar day's digest: TIR (from
  `bg_targets`), mean BG, reading count, total bolus units, meal carbs
  (food-carrying bolus categories only), meal-rise alert count.
- `/trends` — TIR for trailing 7/14/30-day windows.
- `/status` — latest CGM timestamp, last meal-rise detection, last alert +
  delivery (mirrors the web `/status` semantics).
- `/help` and anything unrecognized — fixed command list (unknown input is
  never echoed back).

## Architecture

- `apps/personal/telegram/commands.py` — pure update parser
  (`parse_update`); normalizes `/cmd@bot args` → `cmd`, extracts chat id.
- `apps/personal/telegram/digest.py` — pure digest builders over
  DataFrames/scalars; return reply strings. Source-agnostic, no I/O.
- `apps/personal/telegram/handler.py` — `process_webhook` orchestrates:
  verify secret → enforce chat allowlist → (only then) open storage via an
  injected factory → build reply → send. Reuses
  `apps/personal/cron/detect_meal_rise.send_telegram_message`.
- `api/telegram.py` — Vercel Python entrypoint in the existing cron-worker
  project; reachable at `/api/telegram` (no `vercel.json` change — it
  already routes `api/**/*.py`). GET is a health probe; POST is the
  webhook.

## Security

- Telegram's `X-Telegram-Bot-Api-Secret-Token` header is checked with
  `hmac.compare_digest` against `TELEGRAM_WEBHOOK_SECRET`; mismatch → 401.
- Only `TELEGRAM_CHAT_ID` gets replies; any other chat gets a silent 200.
- **Both checks run before a database connection is opened**, so
  unauthenticated traffic cannot exhaust the pooler. A test asserts the
  storage factory is never called for bad-secret or wrong-chat requests.
- Internal errors return 200 with no detail leaked to the chat.

## Manual rollout (owner)

The bot token / chat id already exist as worker env vars. Two new steps:

1. Add a `TELEGRAM_WEBHOOK_SECRET` env var (any long random string) to the
   **cron-worker** Vercel project and redeploy so `api/telegram.py` ships.
2. Register the webhook with Telegram, passing the same secret:

   ```bash
   curl -X POST "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \
     --data-urlencode "url=https://<worker-domain>.vercel.app/api/telegram" \
     --data-urlencode "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
   ```

   Verify with `getWebhookInfo`. To disable: `deleteWebhook`.

No schema changes. Tests: 619 passed, 42 skipped, 48 deselected.
