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

## 6. Telegram setWebhook

Point the bot at the worker's `/api/telegram` endpoint with a `secret_token`
matching `TELEGRAM_WEBHOOK_SECRET` (the handler rejects requests without it):

```bash
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  --data-urlencode "url=https://<worker-project>.vercel.app/api/telegram" \
  --data-urlencode "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
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
