# 2026-10-14 - Web dashboard security upgrade (Next 14 -> 16)

Scope: `apps/web/**`, the Node version line of the `web` job in `.github/workflows/tests.yml`, and this note. Branch `security/next-upgrade`.

## Why

`npm audit --omit=dev` on `main` reported 1 critical + 3 high (4 vulnerable packages, 5 advisory-bearing chains). `next@14.2.35` is the last 14.x release, so the 14 line cannot receive any further fixes; every Next advisory published since is unfixable without a major upgrade.

## Target choice

Next 16.4.0 (npm `latest`). Next 15.5.x is still receiving backports, but 15 is the previous major and would force a second migration soon after; the code base is small (12 API routes, ~12 pages) and its auth surface is thin, so going straight to the current major costs little more. No concrete blocker found: Node requirement is `>=20.9`, React 19, TypeScript 5.1+ (repo uses 5.9).

## Versions

| Package | Before | After |
|---|---|---|
| next | 14.2.35 | 16.4.0 |
| react / react-dom | 18.x | 19.3.0 |
| @types/react, @types/react-dom | 18 | 19.3 |
| @types/node | 20 | 24 |
| @supabase/ssr | 0.10.3 | 0.12.7 |
| @supabase/supabase-js | 2.105.x | 2.117.3 |
| eslint | 8 | 9.39 |
| eslint-config-next | 14.2.35 | 16.4.0 |
| vitest (lockfile) | 4.1.6 | 4.1.11 |
| CI Node (web job) | 20 | 22 |

Transitive fixes via `npm audit fix --package-lock-only` (plain `npm audit fix` crashes in arborist with "Cannot read properties of null (reading 'edgesOut')" on this tree): nanoid, postcss (now 8.5.23 top-level; Next's nested copy is gone), source-map-js 1.2.2, vite 8.3.4, undici, brace-expansion, @vitest/mocker.

## Audit result

- `npm audit --omit=dev`: before 1 critical / 3 high -> after **0 vulnerabilities**.
- Full `npm audit` (incl. dev): 9 remaining (7 high, 2 moderate), all dev/build-time only: `tailwindcss@3` -> `chokidar`/`braces`/`micromatch`/`fast-glob`/`postcss-nested`/`postcss-selector-parser`, and `eslint-config-next` -> `@next/eslint-plugin-next` -> `fast-glob`. They do not ship in the runtime bundle. npm's only offered remedy is Tailwind 4 (a breaking rewrite), deliberately not bundled into this change.

Advisories resolved (all published, GHSA IDs):

- Critical: GHSA-p293-qw3h-jr36 (RCE, Windows-hosted), GHSA-2xp9-vwfh-vxw4 (RCE, Image Optimization API).
- High (next): GHSA-h25m-26qc-wcjf, GHSA-q4gf-8mx6-v5v3, GHSA-8h8q-6873-q5fj (RSC DoS), GHSA-c4j6-fc7j-m34r (SSRF, WebSocket), GHSA-36qx-fr4f-26g5 (middleware/proxy bypass, Pages i18n), GHSA-m99w-x7hq-7vfj (Server Actions DoS), GHSA-89xv-2m56-2m9x, GHSA-p9j2-gv94-2wf4 (SSRF).
- Moderate/low (next): GHSA-9g9p-9gw9-jx7f, GHSA-3x4c-7xq6-9pq8, GHSA-h64f-5h5j-jqjh (Image Optimizer), GHSA-ggv3-7p47-pfv8 (rewrite smuggling), GHSA-ffhc-5mcf-pf4q, GHSA-gx5p-jg67-6x7h (XSS), GHSA-wfc6-r584-vfw7, GHSA-vfv6-92ff-j949 (RSC cache poisoning), GHSA-3g8h-86w9-wvmq (redirect cache poisoning), GHSA-68g3-v927-f742, GHSA-4633-3j49-mh5q (cache confusion), GHSA-4c39-4ccg-62r3, GHSA-955p-x3mx-jcvp.
- High, transitive: nanoid GHSA-28wg-ghj8-5hjv / GHSA-2v37-7h3g-55p8; postcss GHSA-6g55-p6wh-862q / GHSA-r28c-9q8g-f849 (plus moderate GHSA-qx2v-qp2m-jg93, GHSA-fxqj-rqcc-2cmp); source-map-js GHSA-68fv-2mgg-jv7q.

## Breaking-change adaptations

1. `middleware.ts` -> `proxy.ts` (Next 16 rename; export `middleware` -> `proxy`). Logic unchanged. Note the proxy runs on the Node.js runtime (not Edge). The `shouldBypassMiddleware` helper became `shouldBypassProxy`; `lib/supabase/middleware.ts` keeps its filename (internal helper, no behavioral link to the convention).
2. `next lint` removed in 16: `lint` script is now `eslint .`; `.eslintrc.json` replaced by flat `eslint.config.mjs` (`eslint-config-next/core-web-vitals` + `/typescript`). Lint now also covers `__tests__/` (next lint did not). The new `react-hooks/set-state-in-effect` rule (react-hooks v7) is turned off with a comment: it flagged 11 existing fetch-in-effect / theme-hydration spots; it is React-Compiler guidance, not a correctness issue, and refactoring is out of scope.
3. `experimental.serverComponentsExternalPackages` -> top-level `serverExternalPackages` (still `["pg"]`).
4. Async request APIs: only `app/api/day/[date]/route.ts` used `params`; now `params: Promise<{ date: string }>` and awaited. No page uses `cookies()`/`headers()` synchronously (`lib/supabase/server.ts` already awaited `cookies()`); client pages use `useParams`/`useSearchParams`, unaffected.
5. `@supabase/ssr` 0.12: the `getAll`/`setAll` cookie adapter used by `lib/supabase/server.ts` and `lib/supabase/middleware.ts` is already the supported API; no code change was required, and `getUser()` (server-validated) remains the auth check.
6. `tsconfig.json` was auto-rewritten by `next build` (`jsx: react-jsx`, `target: ES2017`, `.next/dev/types` include). Turbopack is now the default bundler.
7. Test rename: `middleware-matcher.test.ts` -> `proxy-matcher.test.ts`.

No change to `lib/api/auth.ts` logic, `requireSession`, `verifyCronAuth`, the matcher, or the redirect rules.

## Auth preservation evidence

- Existing route-guard backstop (`api-auth.test.ts`) still passes unchanged and enumerates every `app/api/**/route.ts`.
- New `__tests__/auth-enforcement.test.ts` (20 tests) adds behavioral proof: the proxy redirects unauthenticated pages (`/`, `/trends`) to `/login?next=...`, allows `/login` and `/auth/*`, passes signed-in users and bounces them off `/login`, matcher covers pages but not `/api`/static assets; and each discovered API route handler (all 12) returns 401 with no session while never reaching the service-role client; wrong cron bearer -> 401; the `day/[date]` route awaits async params.
- Manual runtime check against `next start` on the production build (placeholder env, no network): `/`, `/trends`, `/day/2026-01-01` -> 307 to `/login?next=...`; `/login` -> 200; all 12 `/api/*` routes (11 session-guarded plus the bearer-guarded cron health route) -> 401 without a session.
- Unchanged pre-existing behavior (intentional, not widened): if `NEXT_PUBLIC_SUPABASE_URL`/`ANON_KEY` are missing, the proxy fails open for pages (API routes still fail closed).

## Gates (from `apps/web`)

`npm ci`, `npm run lint` (0 errors; 1 pre-existing `window.location.href` warning in `AppNav.tsx`), `npx tsc --noEmit`, `npm test` (128 passed, was 108), `next build` with CI placeholder env (27 prerendered/route entries listed; the previous "28" counted differently under Next 14).

## Deploy notes

- Node: Next 16 needs Node >= 20.9. Node 20 is past EOL, so CI moved to 22. In the Vercel project, Settings -> Build & Deployment -> Node.js Version should be 22.x (or 24.x); 20.x works but is EOL. Not changed here. No `engines` field was added on purpose: on Vercel, `engines.node` overrides the project setting.
- A harmless Turbopack "inferred workspace root" warning may appear locally because the repo root also has a `package-lock.json`; Vercel builds from `apps/web`.

## Owner click-test after deploy

1. Signed out, open the site root and a deep link (e.g. a `/day/YYYY-MM-DD` page): should land on `/login`.
2. Request a magic link, click it, confirm you land signed in (exercises `@supabase/ssr` 0.12 cookie handling and `/auth/callback`); reload and confirm the session persists; sign out/in again.
3. Visit each page (Day, Heatmap, Trends, Insulin, AGP, Compare, Search, Alerts, Report, Status) and confirm data and charts render (React 19 + recharts, theme toggle, dark mode).
4. Day view: navigate between dates (route handler `params` change).
5. In a private window, hit `/api/status` directly: expect `{"error":"unauthorized"}` with 401.
6. Confirm the cron worker (separate Vercel project) and Telegram alerts are unaffected (this change does not touch them).
