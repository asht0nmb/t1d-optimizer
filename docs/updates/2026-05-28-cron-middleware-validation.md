# 2026-05-28 Cron Middleware Validation

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
  - valid bearer => health payload describing Python cron handler path
- Investigated live deployed endpoint responses:
  - Both cron endpoints returned `500` with `x-vercel-error: MIDDLEWARE_INVOCATION_FAILED`.
  - `/login` also returned the same 500, indicating global middleware crash risk in deployment.
- Added defensive Supabase middleware env guard:
  - `hasSupabaseMiddlewareEnv()` checks required public Supabase env vars.
  - `updateSession()` now fails open (`NextResponse.next`) when env is missing, preventing full-route outage caused by middleware init failures.
- Added tests for middleware env guard behavior under missing/present env.
- Removed Vercel-managed `crons` config from `apps/web/vercel.json` so Hobby-plan deploys do not fail on schedule limits; cron triggering is now expected from an external scheduler (cron-job.org) hitting `/api/meal_rise_cron` with `Authorization: Bearer <CRON_SECRET>`.

## Follow-up plan

1. Confirm production cron path:
   - Keep Vercel Python cron endpoint (`/api/meal_rise_cron`) and Next route cron endpoint (`/api/cron/meal-rise`) behavior documented so scheduling/auth are unambiguous.
2. Add deploy-time smoke:
   - Add a lightweight CI or post-deploy check that hits cron endpoints with/without auth and verifies status codes.
3. Reduce auth ambiguity:
   - Standardize cron auth validation helper shape between Python and Next handlers (header parsing, explicit error payload).
