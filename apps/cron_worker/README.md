# Meal-rise cron worker (Vercel Python)

Dedicated Vercel project for **cron-job.org** to trigger meal-rise detection every 5 minutes.

The Next.js app (`apps/web`) only exposes `/api/cron/meal-rise` as an authenticated **health** endpoint. Real execution happens on this worker at `/api/meal_rise_cron`.

**Do not confuse with the dashboard project:** the frontend Vercel project uses Root Directory `apps/web` and never reads repo-root `vercel.json` or `api/`.

## Vercel setup (second project)

Create a **new** Vercel project (do not reuse the Next.js `apps/web` project).

1. Import the same GitHub repo in Vercel.
2. Set **Root Directory** to **`.`** (repository root). Do not use `apps/cron_worker` — the handler lives at `api/index.py` and config at repo-root `vercel.json`.
3. **Framework Preset** is pinned to **Other** declaratively via `"framework": null` in repo-root [`vercel.json`](../../vercel.json), which overrides the dashboard setting — so you no longer need to set it by hand. (Background: if the project builds with a Next.js framework context, `vercel.json` `functions` patterns never match the Python files in `api/` and the deploy fails with “Unmatched function pattern”. The `vercel.json` pin prevents that.) Leaving the dashboard Framework Preset on **Other** as well is a harmless belt-and-suspenders.
4. Confirm [`api/index.py`](../../api/index.py) exports `class handler(BaseHTTPRequestHandler)` (Vercel auto-discovers standard `api/index.py` entrypoints).
5. Repo-root [`vercel.json`](../../vercel.json) uses glob `api/**/*.py`. Do not add Python `functions` entries to [`apps/web/vercel.json`](../web/vercel.json).
6. Add environment variables:

| Variable | Required | Purpose |
|----------|----------|---------|
| `CRON_SECRET` | yes | Bearer token; cron-job.org sends `Authorization: Bearer <value>` |
| `SUPABASE_DB_URL` | yes | Pooler URL (`*.pooler.supabase.com:6543`) for `SupabaseStorage` |
| `DEXCOM_USERNAME` / `DEXCOM_PASSWORD` | yes | Live CGM poll |
| `DEXCOM_OUS` | optional | `true` for non-US Dexcom |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | yes | Alert delivery |
| `USER_CONFIG_PATH` | optional | Override path to `user_config.yaml` (default: repo `config/user_config.yaml`) |

7. Deploy. Note the production URL, e.g. `https://t1d-meal-rise-worker.vercel.app`.

### If deploy still fails with “Unmatched function pattern”

- You are on the **worker** project (Root Directory **`.`**), not the dashboard project (`apps/web`).
- Repo-root `vercel.json` includes `"framework": null` (forces the "Other"/Python builder regardless of the dashboard Framework Preset). This is the usual fix for this error.
- Latest `main` includes `class handler(BaseHTTPRequestHandler)` in `api/index.py`.
- `pyproject.toml` has `[tool.vercel] entrypoint = "api.index:handler"` as a fallback.
- `/api/meal_rise_cron` is rewritten to `/api/index` in repo-root `vercel.json` (cron-job.org URL unchanged).
- Temporarily trim repo-root `vercel.json` to only `$schema` + `installCommand` to confirm the function is detected, then re-add `api/**/*.py` + `maxDuration`.

### CLI

Link the worker project from the repository root:

```bash
cd /path/to/t1d-engine
vercel link   # select the worker project, not apps/web
vercel build
```

## cron-job.org

- **URL:** `https://<worker-project>.vercel.app/api/meal_rise_cron`
- **Schedule:** every 5 minutes
- **Method:** GET (or POST; handler accepts both)
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

## Files in this directory

| File | Purpose |
|------|---------|
| `requirements.txt` | Python deps for Vercel `installCommand` |
| `pyproject.toml` | Local/metadata mirror of worker deps |
| `README.md` | This deploy guide |

Handler and `vercel.json` live at the **repository root** (`api/`, `vercel.json`).
