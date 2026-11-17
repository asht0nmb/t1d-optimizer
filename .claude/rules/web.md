---
paths:
  - "apps/web/**"
---

# Web dashboard (Next.js 16, React 19, Supabase)

- Run all four checks before calling a web change done: `npm run lint`, `npx tsc --noEmit`, `npm run test`, `npm run build`, all from `apps/web/`.
- Every `/api/*` data route requires a signed-in session through `lib/api/auth.ts`. A new route copies that pattern.
- The request proxy fails closed when Supabase env vars are missing. Keep it that way.
- Auth redirects accept only same-origin relative paths. An open redirect was fixed in #18.
- Metric values shown here must come from, or exactly mirror, the definition in `core/metrics/`. If a TypeScript or SQL mirror exists, a test pins it to the Python output.
- Visual changes can't be verified from a terminal. Say so in the PR and list what the owner should click through.
