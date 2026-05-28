# 2026-05-28 Web Date Input Alignment

## Problem

Analysis views in `apps/web` were using input defaults and query windows that could drift from actual data coverage:

- Frontend date inputs used hardcoded or current-date defaults.
- Aggregation SQL for trends/insulin/search was anchored to `CURRENT_DATE`.
- API date parameters expected strict `YYYY-MM-DD` even when callers supplied ISO datetimes.

With historical or delayed datasets, these mismatches produced empty/invalid results and made analysis pages hard to use.

## Changes made

- Added date coercion helper in `apps/web/lib/dates.ts`:
  - `coerceDateParam(value)` accepts either `YYYY-MM-DD` or ISO datetime and normalizes to `YYYY-MM-DD`.
- Added tests in `apps/web/__tests__/dates.test.ts` covering coercion behavior.
- Updated API routes to normalize/validate dates consistently:
  - `apps/web/app/api/day/[date]/route.ts`
  - `apps/web/app/api/compare/route.ts`
  - `apps/web/app/api/heatmap/route.ts` (also validates `from <= to`).
- Added CGM data-bound lookup:
  - `apps/web/lib/queries/date-bounds.ts` computes `min_date`/`max_date` in configured timezone.
  - `apps/web/app/api/config/route.ts` now returns `date_bounds` with `bg_targets` and `timezone`.
- Added data-anchored query window helper:
  - `apps/web/lib/queries/window-anchor.ts` resolves latest available CGM day and derives window starts.
- Reworked analysis SQL windows to anchor on latest available CGM day (not wall-clock `CURRENT_DATE`):
  - `apps/web/lib/queries/trends.ts`
  - `apps/web/lib/queries/insulin.ts`
  - `apps/web/lib/queries/search.ts`
- Updated date-driven UI defaults to use backend `date_bounds` when available:
  - `apps/web/app/page.tsx`
  - `apps/web/app/compare/page.tsx`
  - `apps/web/app/heatmap/page.tsx`
- Extended API contracts with config/date-bound types in `apps/web/lib/types/api.ts`.

## Verification

- Ran web tests: `npm run test` in `apps/web`.
- Result: all tests passing (`7` files, `23` tests).
