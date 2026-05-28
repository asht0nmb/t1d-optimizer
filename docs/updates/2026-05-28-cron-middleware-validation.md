# 2026-05-28 Cron Stabilization & Middleware Validation

## What I tested

- Ran Python cron tests:
  - `tests/detection/test_cron_meal_rise.py`
  - `tests/detection/test_meal_rise_cron_handler.py`
- Ran web Vitest suite under `apps/web` including cron auth tests.
- Added middleware-focused tests to validate API-route bypass behavior.

## Issue found

- `apps/web/middleware.ts` was applying auth session middleware broadly enough to include `/api/*` paths.
- This creates a risk that cron/API requests get redirected to `/login` before reaching route handlers, including cron endpoints.

## Fix implemented

- Added explicit runtime bypass in `middleware()` for `/api/*` requests via `shouldBypassMiddleware(pathname)`.
- Kept matcher hardening (`api` excluded in matcher) and added direct tests for bypass behavior.

## Verification

- Python cron tests: passing.
- Web tests: passing, including new middleware bypass coverage.

## Additional hardening implemented

- Added Python cron handler defensive error handling:
  - If `run_cron()` raises unexpectedly, handler now returns HTTP 500 with a structured error body instead of bubbling an unhandled exception.
- Expanded Python cron handler tests to cover:
  - non-zero `run_cron()` exit code => HTTP 500
  - raised exception in `run_cron()` => HTTP 500 + error payload
- Added direct Next route tests for `/api/cron/meal-rise` GET:
  - unauthorized path => 401
  - valid bearer => health payload indicating `health_only` mode
- Investigated live deployed endpoint responses:
  - Both cron endpoints returned `500` with `x-vercel-error: MIDDLEWARE_INVOCATION_FAILED`.
  - `/login` also returned the same 500, indicating global middleware crash risk in deployment.
- Added defensive Supabase middleware env guard:
  - `hasSupabaseMiddlewareEnv()` checks required public Supabase env vars.
  - `updateSession()` now fails open (`NextResponse.next`) when env is missing, preventing full-route outage caused by middleware init failures.
- Added tests for middleware env guard behavior under missing/present env.
- Removed Vercel-managed cron config and unsupported function pattern assumptions from `apps/web/vercel.json` to avoid Next.js build failures on Hobby.
- Moved primary cron execution to GitHub Actions (`.github/workflows/meal-rise-cron.yml`) every 5 minutes.
- Updated web route/docs so `/api/cron/meal-rise` is explicitly an authenticated health endpoint, not the production runner.
- Added cron reliability hardening in `apps/personal/cron/detect_meal_rise.py`:
  - `SUPABASE_DB_URL` required by default (Parquet fallback allowed only when `MEAL_RISE_ALLOW_PARQUET_FALLBACK=true` for local tests)
  - partial-success semantics for Telegram delivery failures
  - persisted delivery metadata (`event_ref`, `delivery_stage`, `delivery_attempt`, `telegram_sent`)
  - retry pass with configurable lookback/backoff/max attempts for failed deliveries
- Expanded Python tests for:
  - partial-success outcome on delivery failure
  - retry-after-backoff behavior
  - backoff suppression
  - no-DB safeguard behavior

## Rollout checklist

1. Deploy `main` and confirm `/api/cron/meal-rise` returns `401` without bearer and `200` with bearer.
2. Configure GitHub Actions secrets for `meal-rise-cron.yml`.
3. Manually run one workflow dispatch and verify `detection_results` write path in Supabase.
4. Confirm scheduled runs execute every 5 minutes without Vercel cron dependency.
