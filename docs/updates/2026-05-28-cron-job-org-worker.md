# 2026-05-28 Cron-Job.org + Vercel Python Worker

## Summary

Moved production meal-rise execution from GitHub Actions schedule to **cron-job.org** triggering a **dedicated Vercel Python project** (`apps/cron_worker`). The Next.js app (`apps/web`) keeps `/api/cron/meal-rise` as health-only.

## Changes

- Added `apps/cron_worker/` with `api/meal_rise_cron.py`, `requirements.txt`, `vercel.json`, README.
- Removed deprecated `apps/web/api/meal_rise_cron.py` (was incompatible with Next.js deploy).
- GitHub Actions `meal-rise-cron.yml`: schedule removed; `workflow_dispatch` only for manual fallback.
- Updated `apps/web/README.md` and health route payload to document cron-job.org + worker URL.
- Tests: `tests/detection/test_meal_rise_cron_handler.py` now loads worker handler module.

## Deploy checklist

1. Vercel project B: Root Directory `apps/cron_worker`, env vars per `apps/cron_worker/README.md`.
2. cron-job.org: GET/POST every 5 min to `https://<worker>/api/meal_rise_cron` with `Authorization: Bearer <CRON_SECRET>`.
3. Verify `401` without auth, `200` + `exit_code` with auth.
4. Confirm Supabase `detection_results` / `alerts_sent` on firing runs.

## Verification (local)

```bash
uv run pytest tests/detection/test_meal_rise_cron_handler.py tests/detection/test_cron_meal_rise.py -q
npm -C apps/web test
```
