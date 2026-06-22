# 2026-06-22 — Web dashboard: fetch + date-overlay robustness

Follow-up hardening after the `.vercelignore` 404 incident (see
`2026-06-21-cron-worker-vercel-deps` / the deploy fix), so the *class* of error
seen there can't surface again.

## 1. `fetchJson` — no more `Unexpected token '<'`

Every page fetched with `fetch(url).then(r => r.json())` (or `res.json()`)
without checking `r.ok`. When an API route returned a non-JSON response (a
404/500 HTML page, a gateway error), `r.json()` threw
`SyntaxError: Unexpected token '<', "<!DOCTYPE "...` — the exact error seen when
`apps/web` was stripped from the build.

New `apps/web/lib/fetch-json.ts`: `fetchJson<T>(url)` returns `T & { error? }`,
resolving to `{ error }` on a network failure, a non-JSON body, or a non-2xx
response (surfacing the API's own `error` when present). All ~16 call sites
across the pages now use it, so a failed request shows a clean message through
the existing `body.error` handling instead of a cryptic parse crash. 4 unit
tests.

## 2. DayChart site-issue overlay guard

`DayChart.tsx` computed an open-ended site-issue overlay's end as
`new Date(Date.parse(first_occlusion_ts) + 1h).toISOString()` — a `RangeError`
crash if a `site_issues` row ever had a missing/unparseable onset. Extracted a
tested `siteIssueEndTs(first, last)` into `lib/overlays.ts` that returns `null`
for an unparseable onset (the row is then skipped, not rendered as a crash).
3 unit tests.

## Verification

`apps/web`: vitest **100 passed** (was 93), `tsc --noEmit` clean, `next lint`
clean, `next build` green. No API/runtime behaviour change on the happy path —
only failure modes are now graceful.

The earlier root cause (the strip) is already fixed and live; this removes the
fragility that turned a transient API failure into a hard crash.
