# Meal-rise cron worker (Vercel Python)

Dedicated Vercel project for **cron-job.org** to trigger meal-rise detection every 5 minutes.

The Next.js app (`apps/web`) only exposes `/api/cron/meal-rise` as an authenticated **health** endpoint. Real execution happens here at `/api/meal_rise_cron`.

## Vercel setup (second project)

1. Import the same GitHub repo in Vercel.
2. Set **Root Directory** to `apps/cron_worker`.
3. Framework preset: **Other** (Python serverless `api/` routes).
4. Add environment variables:

| Variable | Required | Purpose |
|----------|----------|---------|
| `CRON_SECRET` | yes | Bearer token; cron-job.org sends `Authorization: Bearer <value>` |
| `SUPABASE_DB_URL` | yes | Pooler URL (`*.pooler.supabase.com:6543`) for `SupabaseStorage` |
| `DEXCOM_USERNAME` / `DEXCOM_PASSWORD` | yes | Live CGM poll |
| `DEXCOM_OUS` | optional | `true` for non-US Dexcom |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | yes | Alert delivery |
| `USER_CONFIG_PATH` | optional | Override path to `user_config.yaml` (default: repo `config/user_config.yaml`) |

5. Deploy. Note the production URL, e.g. `https://t1d-meal-rise-worker.vercel.app`.

## cron-job.org

- **URL:** `https://<worker-project>.vercel.app/api/meal_rise_cron`
- **Schedule:** every 5 minutes
- **Method:** GET (or POST; handler accepts Vercel invocation)
- **Headers:** `Authorization: Bearer <CRON_SECRET>`
- **Timeout:** align with Vercel function max (60s on Hobby configurable limit)

Enable failure notifications in cron-job.org so missed invocations are visible.

## Manual test

```bash
curl -i -H "Authorization: Bearer $CRON_SECRET" \
  "https://<worker-project>.vercel.app/api/meal_rise_cron"
```

Expected:

- `401` without auth
- `200` with `{"ok":true,"exit_code":0}` on success (or `500` with `exit_code` non-zero / `cron_execution_failed` on errors)

## Local smoke (repo root)

```bash
export CRON_SECRET=your-secret
export SUPABASE_DB_URL=...
# ... other vars ...
uv run python -m apps.personal.cron.detect_meal_rise
```
