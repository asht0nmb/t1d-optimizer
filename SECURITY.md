# Security Policy

## Reporting a vulnerability

This project handles personal health data (CGM and insulin-pump records).
If you find a vulnerability — especially anything that could expose stored
health data, leak credentials, or fire false/suppressed medical alerts —
please email **ashtonmb@uw.edu** rather than opening a public issue.
You should get a response within a week.

## Scope notes for deployers

- The hosted shell expects secrets only via environment variables
  (`.env` locally, Vercel/GitHub Actions secrets in the cloud). Never
  commit a real `.env`; only `.env.example` templates belong in git.
- Supabase tables are protected by RLS: the `anon` role has no policies
  (zero rows). Client-side code must use the anon key + Supabase Auth;
  server-side code uses `service_role` or the `postgres` role.
- The cron worker endpoint requires a `CRON_SECRET` bearer token.
- Real patient exports under `data/` are gitignored; anonymized fixtures
  live in `test_data/`. Keep it that way in PRs.
