# 2026-05-28 Cron worker Vercel repo-root layout

## Summary

Relocated the meal-rise Vercel Python function to the **repository root** so the worker project can bundle `core/`, `detection/`, `config/`, and `apps/personal/` while the Next.js dashboard project remains scoped to `apps/web` only.

## Changes

- **Handler:** `api/meal_rise_cron.py` at repo root (`class handler(BaseHTTPRequestHandler)`).
- **Config:** repo-root `vercel.json` with `installCommand` → `apps/cron_worker/requirements.txt`, `functions.api/**/*.py`, `excludeFiles` includes `apps/web/**`.
- **Removed:** `apps/cron_worker/api/meal_rise_cron.py`, `apps/cron_worker/vercel.json`.
- **Tests:** `tests/apps/test_cron_worker_deploy_contract.py` (deploy + frontend isolation contracts); handler tests load `api/meal_rise_cron.py`.
- **Docs:** `apps/cron_worker/README.md`, `apps/web/README.md` (docs only under web).

## Vercel dashboard (manual)

| Project | Root Directory | Framework |
|---------|----------------|-----------|
| Dashboard | `apps/web` | Next.js |
| Cron worker | `.` | Other |

## Verification (local)

```bash
uv run pytest tests/apps/test_cron_worker_deploy_contract.py tests/detection/test_meal_rise_cron_handler.py -q
npm -C apps/web test
```

After deploy: worker `401`/`200` on `/api/meal_rise_cron`; dashboard build green on same commit; `/api/cron/meal-rise` health unchanged.

## Post-merge deploy checklist (manual)

**Worker project**

1. Settings → Root Directory: `.` (clear `apps/cron_worker`)
2. Framework Preset: **Other**
3. Deploy; confirm build has no “Unmatched function pattern”
4. `curl -si https://<worker>.vercel.app/api/meal_rise_cron` → `401`
5. `curl -si -H "Authorization: Bearer $CRON_SECRET" https://<worker>.vercel.app/api/meal_rise_cron` → `200` or documented `500`

**Dashboard project (unchanged)**

1. Confirm Root Directory still `apps/web`, Framework **Next.js**
2. Same-commit deployment succeeds
3. `npm -C apps/web test` and `npm -C apps/web run build` (verified locally in this change)
4. Production: `/api/cron/meal-rise` with Bearer → `200`, `mode: health_only`

**CLI (optional):** from repo root, `vercel link` to worker project, then `vercel build`.
