# 2026-06-21 — Cron worker Vercel deploy: slim deps via root requirements.txt

## Problem

The cron-worker Vercel project (Root Directory `.`) failed to deploy:

```
Error: Total bundle size (654.42 MB) exceeds the ephemeral storage limit (500 MB).
```

The build log gave the real cause:

```
Installing required dependencies from uv.lock...
```

Vercel's Python builder installs from **`uv.lock`** when present — i.e. the FULL
locked project (jupyter, pyarrow ~117 MB, scipy ~81 MB, scikit-learn, matplotlib,
tconnectsync) ≈ 654 MB. It ignored both `apps/cron_worker/requirements.txt` and
the `vercel.json` `installCommand`. Function count is irrelevant: the bundle is
the shared dependency install (confirmed — 3 functions = 654.42 MB, an earlier
2-function consolidation = 654.43 MB, no change). pandas 3.0 does **not** require
pyarrow (it is an optional extra), so the worker doesn't need it; nor does it
need scikit-learn/scipy (core.detection v2 + core.metrics are pure pandas/numpy)
or tconnectsync (it polls Dexcom via pydexcom).

## Fix

- **Root `requirements.txt`** with only the worker runtime (pandas, numpy,
  psycopg2-binary, pydexcom, python-dotenv, pyyaml, requests). When present at
  the root, Vercel's Python builder installs from it (~130 MB).
- **`.vercelignore`** hides `uv.lock` and `pyproject.toml` from the Vercel build
  so the builder falls back to `requirements.txt` instead of the full project
  (plus a few non-runtime dirs to keep the upload small).

Local and CI workflows are unaffected — they use `uv` + `pyproject.toml` +
`uv.lock` and ignore `requirements.txt`. The worker keeps its original **three
separate functions** (`api/index.py` cron, `api/telegram.py`,
`api/metrics_report.py`); with the dependency install slimmed, function count is
no longer a constraint.

TDD: two new assertions in `tests/apps/test_cron_worker_deploy_contract.py` lock
this in — a slim root `requirements.txt` (no pyarrow/scipy/scikit-learn/jupyter/
matplotlib/tconnectsync) and a `.vercelignore` that hides `uv.lock` +
`pyproject.toml`. Suite: **741 passed / 42 skipped / 48 deselected**.

## History note

An earlier attempt consolidated the three `api/` functions into two, on the
mistaken theory that the bundle was per-function. The build log disproved it
(654 → 654). That consolidation was rolled back; this dependency fix is the
actual remedy. Operational details are in memory `cron-worker-vercel-deploy.md`.

## Deploy (owner)

- `vercel --prod` (worker project). Build log should now read `Installing
  dependencies from requirements.txt`, bundle ~130 MB, no 500 MB error.
- Verify: `curl .../api/meal_rise_cron` → 401, `.../api/metrics_report` → 401;
  with `Authorization: Bearer <CRON_SECRET>` → 200.
- Then point cron-job.org at `/api/meal_rise_cron` with the bearer header. (The
  5-min loop must use cron-job.org, not GitHub Actions — scheduled GH runs on
  this repo lag 3.5–6.5 h.)
