# 2026-08-16 — Security review (Phase 4)

Fourth phase of the overnight autonomous session (`/remote-control`).
Standard pass: PR-diff review via the `security-review` skill, plus a
manual pass over auth boundaries, RLS, secrets handling, and injection
surfaces across the live system (broader than just tonight's diff).

## PR-diff review (security-review skill, dispatched sub-agents)

No HIGH or MEDIUM confidence findings survived false-positive filtering.

One candidate was found and correctly excluded per the skill's own rules:
`tconnectsync>=3.0.0` (Phase 1's migration) transitively pins
`PyJWT==2.8.0`, downgrading from the previously-resolved 2.12.1 and
reintroducing a patched PyJWT CVE (crit-header validation, fixed in PyJWT
2.12.0) into the OIDC login flow tconnectsync uses against Tandem's
identity provider. Excluded from the formal report because outdated-
third-party-library issues are explicitly out of scope for PR-diff
security review (managed separately) — but real and cheap to fix, so
fixed anyway: added a `[tool.uv] override-dependencies = ["pyjwt>=2.12.1"]`
to `pyproject.toml`, forcing `uv lock` to resolve PyJWT 2.13.0 despite
tconnectsync's exact pin. Verified the tconnectsync/Tandem OIDC login flow
still works against the newer PyJWT (`get_api()` login succeeded live) and
the full test suite stays green (749 passed).

Dynamic SQL construction in `scripts/repair_nightly_sync_tz.py` (the TZ
repair script from Phase 1) was reviewed and ruled not exploitable: every
interpolated value (table/column names, boundary timestamps) traces to
hardcoded module-level constants in the script itself, and `pump_serial`
goes through parameterized `%s` placeholders in every query. Not a
network-facing service, not attacker-reachable.

## Manual pass: auth boundaries, RLS, secrets, injection

- **API route guards:** every `/api/*` data route in `apps/web` calls
  `requireSession()` except `/api/cron/meal-rise`, which correctly uses
  bearer-token auth (`verifyCronAuth`) instead — matches the documented
  design (`lib/api/auth.ts`'s own comment: "the cron health route uses
  bearer-token auth instead of cookies"). No route found unguarded.
- **Constant-time bearer comparison** (`lib/cron/auth.ts`,
  `crypto.timingSafeEqual`) confirmed still in place, not regressed.
- **RLS:** spot-checked `pg_class.relrowsecurity` directly against
  production — all 13 public tables still have RLS enabled. No policy
  changes this session.
- **Telegram webhook:** `api/telegram.py` validates the `secret_token`
  header against `TELEGRAM_WEBHOOK_SECRET` on every request (confirmed via
  source, not just docstring). GET → 200 health-probe response is
  intentional (see Phase 2), not a bypass — Telegram itself only POSTs.
- **Secrets handling:** `.env` is gitignored; GitHub Actions workflows
  reference secrets only via `${{ secrets.* }}` in `env:` blocks (never
  inlined into `run:` shell strings, so no accidental log exposure);
  neither workflow touched tonight triggers on `pull_request`/
  `pull_request_target`, so the new `TIMEZONE_NAME` wiring doesn't open a
  fork-PR secret-exfiltration path.

## Not done

No penetration testing, no dependency-tree-wide CVE scan beyond the one
PyJWT finding surfaced incidentally by the diff review, no review of
Vercel/Supabase account-level access controls (outside this repo's
visibility). Scope matched what the owner asked for: a standard pass, not
a specific worry.
