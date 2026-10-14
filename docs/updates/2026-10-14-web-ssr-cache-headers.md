# 2026-10-14 — Web: apply @supabase/ssr cache headers, harden auth callback + proxy

Branch `fix/web-ssr-cache-headers`, stacked on PR #14 (`security/next-upgrade`).
Follow-up to the item flagged in `2026-10-14-web-security-upgrade.md` (§5), plus
two low-severity auth issues found in review of #14.

## 1. `setAll` cache headers (@supabase/ssr 0.12)

Verified against the installed `@supabase/ssr@0.12.7` types
(`dist/main/types.d.ts`, `SetAllCookies`) and the supabase/ssr README/autodocs:
`setAll(cookiesToSet, headers)` receives
`Cache-Control: private, no-cache, no-store, must-revalidate, max-age=0`,
`Expires: 0`, `Pragma: no-cache` **only on the first cookie write per client**;
later calls get `{}`. They must be set on the response carrying the `Set-Cookie`
so a CDN/shared cache never serves one session's tokens to another requester.

| Call site | Change |
|---|---|
| `apps/web/lib/supabase/middleware.ts` (proxy) | Headers accumulated in a closure and re-applied every time `setAll` rebuilds `supabaseResponse` (so a later `{}` call cannot drop them). The `/login` / `/` redirects are unchanged: they never carried the refreshed cookies, so there is no `Set-Cookie` to protect. |
| `apps/web/app/auth/callback/route.ts` | Headers collected in `setAll`, applied to the success redirect and to the post-exchange error redirects. |
| `apps/web/lib/supabase/server.ts` (`createSessionClient`) | **Not applied — no response object.** Server Components cannot set response headers, and `requireSession()` (`lib/api/auth.ts`) returns before the route builds its response; changing its contract would touch 12+ routes. Page navigations are covered because `proxy.ts` refreshes the session first (with headers). Residual gap: `/api/*` bypasses the proxy, so a token refresh inside an API route writes `Set-Cookie` without these explicit headers. Whether Next's default `Cache-Control` for dynamic (cookie-reading) route handlers already covers this was not verified here. Low exposure (signed-in single user, JSON fetched client-side), but a candidate follow-up: have `requireSession` return the pending headers for routes to apply. |

## 2. Open redirect in `/auth/callback`

Previously `NextResponse.redirect(`${origin}${next}`)` with unvalidated `next`;
`next=@evil.com` produced `https://host@evil.com` (→ evil.com). New
`apps/web/lib/auth/safe-next.ts::safeNextUrl` accepts only a single-slash path,
rejects backslashes, control chars/whitespace (the WHATWG parser strips tabs/
newlines and treats `\` as `/`), then resolves with `new URL(next, origin)` and
requires `.origin === origin`; otherwise `/`.

## 3. Proxy now fails CLOSED on missing Supabase env

`updateSession` used to return `NextResponse.next()` when
`NEXT_PUBLIC_SUPABASE_URL` / `_ANON_KEY` were missing — every page served
unauthenticated on a misconfigured deploy. It now redirects non-public pages to
`/login?next=…` (same shape as the unauthenticated branch); `/login` and
`/auth/*` stay reachable. CI/vitest set placeholder values, so unaffected.

## Tests

New `apps/web/__tests__/ssr-cache-headers.test.ts` (fake `createServerClient`
that actually invokes `setAll(cookies, headers)`): headers present when given,
preserved across a second `{}` write, absent when `{}` / never called; callback
success + verifyOtp paths; error/missing-token redirects unchanged; fail-closed
env cases; open-redirect cases (`@evil.com`, `//evil.com`, `/\evil.com`,
`https://evil.com`, `/%2F%2Fevil.com`, tab/newline/space-prefixed,
`javascript:`, raw and encoded) plus normal `/trends` and missing `next`.
10 of the new cases fail against the pre-change code. Existing
`auth-enforcement`, `proxy-matcher`, `supabase-middleware-env` tests unchanged
and passing.

Gates (apps/web): `npm ci`, `npm run lint` (0 errors; 1 pre-existing warning),
`npx tsc --noEmit`, `npx vitest run` (160 passed after review fixes), `next build` with the
placeholder env from `.github/workflows/tests.yml`.

## Review follow-ups (PR #18, approve-with-nits)

1. **`safeNextUrl` dot-segment bypass:** `/.//evil.com`, `/..//evil.com`, and
   `/%2e%2e//evil.com` normalize to pathname `//evil.com`. That is harmless in a
   route handler, but Next's middleware adapter relativizes same-host
   `Location` headers into a protocol-relative `//evil.com`. The fix rejects
   any parsed `url.pathname` that starts with `//`, falling back to `/`.
2. **Segment-bounded public paths:** `isPublicAuthPath` (now exported) matches
   `/login` and `/login/...`, plus `/auth` and `/auth/...`. Previously, the bare
   `startsWith` made `/authors` and `/loginx` public. This also applies in
   fail-closed mode.
3. **Signed-in `/login` bounce (pre-existing):** the redirect to `/` dropped a
   just-rotated session cookie. It now copies `supabaseResponse`'s cookies and
   the accumulated cache headers onto the redirect. With no refresh, the
   redirect is unchanged: no `Set-Cookie` and no cache headers. The
   unauthenticated `/login` redirect still carries nothing, because there is no
   session to keep.

There are 8 new or extended test cases, and all 8 fail against the
pre-follow-up code.
