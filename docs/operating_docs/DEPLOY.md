# Hosted-shell deploy runbook

Consolidated, ordered runbook for standing up the hosted T1D Engine shell:
Supabase Postgres + the Next.js dashboard (Vercel) + the meal-rise cron worker
(a *separate* Vercel project) + cron-job.org + the Telegram webhook.

The local OSS shell (Streamlit + parquet) needs none of this — it runs against
`data/processed/*.parquet` with no cloud accounts.

Deploy order (each step depends on the one before it):

1. Apply Supabase migrations (0001 → 0002 → 0003)
2. Bootstrap historical data (`scripts/bootstrap_supabase.py`)
3. Deploy the web dashboard Vercel project (Root Directory `apps/web`)
4. Deploy the cron worker Vercel project (Root Directory `.`)
5. Configure cron-job.org to hit the worker every 5 minutes
6. Register the Telegram webhook (`setWebhook`)

---

## 1. Apply migrations

Apply in order against the Supabase project (SQL editor or `psql` over the
**direct** connection, port 5432):

| Migration | Purpose |
|-----------|---------|
| `db/migrations/0001_init.sql` | Initial schema (13 public tables) |
| `db/migrations/0002_supabase_storage_setup.sql` | SupabaseStorage setup + `idle_in_transaction_session_timeout = '5min'` |
| `db/migrations/0003_enable_rls.sql` | Row-Level Security; `auth_required_all` policy per table; `anon` default-deny |

## 2. Bootstrap historical data

One-shot historical parquet → Postgres load. Uses the **direct** connection
(port 5432, `db.<project>.supabase.co`), NOT the pooler. Idempotent
(`ON CONFLICT DO NOTHING`), safe to re-run.

```bash
export SUPABASE_DB_URL="postgresql://postgres:PASSWORD@db.PROJECTREF.supabase.co:5432/postgres"
uv run python scripts/bootstrap_supabase.py            # add --dry-run first
```

## 3. Web dashboard Vercel project

- **Root Directory:** `apps/web`
- **Framework Preset:** Next.js (default; leave as detected)
- Reads `apps/web/vercel.json`; never touches repo-root `vercel.json` or `api/`.

Environment variables (web project):

| Variable | Required | Purpose |
|----------|----------|---------|
| `NEXT_PUBLIC_SUPABASE_URL` | yes | Project URL (`https://PROJECT.supabase.co`); embedded in client bundle |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | yes | Anon key; client bundle (RLS-gated) |
| `SUPABASE_SERVICE_ROLE_KEY` | yes | Server-only; service-role admin reads (bypasses RLS) |
| `SUPABASE_DB_URL` | yes | **Pooler** URL (`*.pooler.supabase.com:6543`) for SQL aggregations (heatmap/TIR/search) |
| `TZ` | yes | IANA tz for calendar-day boundaries (e.g. `America/Los_Angeles`) |
| `DEFAULT_PUMP_SERIAL` | optional | Filter all queries to one pump |
| `USER_CONFIG_PATH` | optional | Override BG-targets JSON (default `apps/web/config/bg-targets.json`) |
| `METRICS_WORKER_URL` | yes | Base URL of the cron-worker project (e.g. `https://<worker-project>.vercel.app`); the `/api/report` route proxies to `…/api/metrics_report` |
| `CRON_SECRET` | yes | Bearer secret the `/api/report` proxy sends to the metrics worker; **must match the worker project's `CRON_SECRET`** |

> Build-time note: `next build` only needs the three Supabase vars present to
> compile; no DB access happens at build time, so public-safe placeholders are
> sufficient for CI builds.

## 4. Cron worker Vercel project (separate project)

Create a **new** Vercel project (do not reuse the web project). Full detail in
`apps/cron_worker/README.md`.

- **Root Directory:** `.` (repository root) — handler at `api/index.py`, config
  at repo-root `vercel.json` (`"framework": null` pins the Python builder).
- `installCommand`: `pip install -r apps/cron_worker/requirements.txt`.
- Serves `/api/meal_rise_cron` (rewritten to `/api/index`), `/api/telegram`, and
  `/api/metrics_report` (the clinical CGM report the web `/api/report` route
  proxies to; bearer-auth via `CRON_SECRET`).

Environment variables (worker project):

| Variable | Required | Purpose |
|----------|----------|---------|
| `CRON_SECRET` | yes | Bearer token; cron-job.org sends `Authorization: Bearer <value>` |
| `SUPABASE_DB_URL` | yes | **Pooler** URL (`*.pooler.supabase.com:6543`) for `SupabaseStorage` |
| `DEXCOM_USERNAME` | yes | Live CGM poll (Dexcom Share) |
| `DEXCOM_PASSWORD` | yes | Live CGM poll |
| `DEXCOM_OUS` | optional | `true` for non-US Dexcom |
| `TELEGRAM_BOT_TOKEN` | yes | Alert + webhook reply delivery |
| `TELEGRAM_CHAT_ID` | yes | Only this chat gets alerts/replies |
| `TELEGRAM_WEBHOOK_SECRET` | yes | Verified on every `/api/telegram` request; must match `setWebhook` secret_token |
| `USER_CONFIG_PATH` | optional | Override `config/user_config.yaml` path |

## 5. cron-job.org

- **URL:** `https://<worker-project>.vercel.app/api/meal_rise_cron`
- **Schedule:** every 5 minutes
- **Method:** GET (POST also accepted)
- **Header:** `Authorization: Bearer <CRON_SECRET>`
- **Timeout:** align with the Vercel function max (60s)
- Enable cron-job.org failure notifications so missed invocations are visible.

Smoke test:

```bash
curl -i -H "Authorization: Bearer $CRON_SECRET" \
  "https://<worker-project>.vercel.app/api/meal_rise_cron"
# 401 without auth; 200 {"ok":true,"exit_code":0} on success
```

## 6. Telegram webhook (command bot) + GitHub alert secrets

The command bot (`/today`, `/status`, ...) needs **both** a registered Telegram
webhook and `TELEGRAM_WEBHOOK_SECRET` in the worker's Vercel env. Without the
secret the handler fails closed (every request returns 401); without the
webhook Telegram never calls it. `scripts/setup_telegram_webhook.py` handles
the Telegram side. Secret-store writes (Vercel env, GitHub secrets) are done by
the owner, by hand, in the order below. Run everything from the repo root, with
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` already in `.env`. Secrets are
piped from `.env`, never typed or echoed.

**(a) Generate the webhook secret** (appended to `.env` only if missing; the
value is never printed):

```bash
uv run python scripts/setup_telegram_webhook.py gen-secret
```

**(b) Add it to the worker's Vercel Production env.** The repo root is linked to
the *web* project (`t1d-optimizer`), so link a throwaway directory to the
worker project (`t1d-optimizer-meal-bot`) instead:

```bash
ENV_FILE="$PWD/.env"
WORKDIR="$(mktemp -d)" && cd "$WORKDIR"
vercel link --project t1d-optimizer-meal-bot --yes
grep '^TELEGRAM_WEBHOOK_SECRET=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r\n' \
  | vercel env add TELEGRAM_WEBHOOK_SECRET production --sensitive
```

If the variable already exists, `vercel env rm TELEGRAM_WEBHOOK_SECRET production`
first. Check the worker also has `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
(`vercel env ls production`).

**(c) Redeploy worker production** so the new env var takes effect (still in
`$WORKDIR`):

```bash
vercel ls --prod            # pick the latest Ready production deployment URL
vercel redeploy <that-deployment-url>
```

**(d) Register the webhook, then verify** (back in the repo root):

```bash
cd - >/dev/null; rm -rf "$WORKDIR"
uv run python scripts/setup_telegram_webhook.py register
uv run python scripts/setup_telegram_webhook.py verify
uv run python scripts/setup_telegram_webhook.py verify --live-test   # optional
```

`register` calls `setWebhook` with the worker URL (default
`https://t1d-optimizer-meal-bot.vercel.app/api/telegram`; override with
`--url`), the secret, `allowed_updates=["message"]` and
`drop_pending_updates=true`. `verify` prints the webhook host, pending count and
`last_error`, and expects an unauthenticated POST to the worker to return 401.
`--live-test` sends a correctly signed fake `/help` from `TELEGRAM_CHAT_ID` and
expects `200 {"replied": true}` (you will receive the help message in
Telegram). Finally send `/help` or `/status` to the bot from your phone.

Rollback: `uv run python scripts/setup_telegram_webhook.py delete`.

**(e) GitHub repo secrets for nightly-sync failure alerts.** The nightly sync's
"Notify Telegram on failure" step reads `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID` from repo secrets:

```bash
grep '^TELEGRAM_BOT_TOKEN=' .env | cut -d= -f2- | tr -d '\r\n' \
  | gh secret set TELEGRAM_BOT_TOKEN --repo asht0nmb/t1d-optimizer
grep '^TELEGRAM_CHAT_ID=' .env | cut -d= -f2- | tr -d '\r\n' \
  | gh secret set TELEGRAM_CHAT_ID --repo asht0nmb/t1d-optimizer
gh secret list --repo asht0nmb/t1d-optimizer
```

---

## Connection-model reminders

- **Direct** connection (`db.*.supabase.co:5432`): one-shot bootstrap and the
  nightly GitHub Action sync only.
- **Pooler** (transaction mode, `*.pooler.supabase.com:6543`): everything
  long-running or serverless — `SupabaseStorage` in the worker and the web
  app's SQL aggregations.
- `service_role` / `postgres` bypass RLS; `authenticated` / `anon` are subject
  to it (anon sees zero rows). Server handlers use service-role; client bundles
  use the anon key + Supabase Auth.
