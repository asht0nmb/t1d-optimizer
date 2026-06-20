# 2026-06-11 — OSS hygiene + documentation truth-up (Phases 1–2)

Executed from `docs/superpowers/plans/2026-06-11-phase1-2-oss-hygiene-docs.md`
(design: `docs/superpowers/specs/2026-06-11-product-completion-design.md`).
No Python/TS source changes; one pytest config change.

## Added

- `LICENSE` — MIT, with an informational medical notice (not a medical
  device, never recommends doses).
- `SECURITY.md` — private disclosure via email; deployer notes (env-only
  secrets, RLS posture, CRON_SECRET, data/ privacy).
- `CONTRIBUTING.md` — uv setup, test commands, the binding core/ import
  rules, config/threshold rules, update-doc convention.
- `.github/workflows/tests.yml` — first CI test gate: default pytest
  suite on push/PR to main (setup-uv + `uv sync --frozen`).

## Changed

- `pyproject.toml` — real project description; `addopts` now
  `-m 'not legacy and not integration'` so integration-marked tests are
  excluded from the default suite by the marker itself rather than
  relying on per-test skipifs (review finding).
- `.env.example` (root) — deduplicated keys, fixed `DEXCOM_OUS=false4`
  typo, grouped by shell, placeholder-only values.
- `docs/operating_docs/TECHNICAL_SPEC.md` — full rewrite to the shipped
  v2 system (storage Protocol + RLS, windowing + meal-rise detector, M1
  hardening, M2 calibration scoring, live loop topology, legacy v1
  condensed into a quarantine section, roadmap explicitly marked not
  built). Reviewed line-by-line against the code.
- `CLAUDE.md` — surfaces no longer described as unbuilt; added
  apps/local, apps/web, apps/personal/cron + api/index.py, db/migrations,
  workflows; test counts updated.
- `README.md` — split "Running today" vs "On the roadmap", added local
  OSS quickstart, metrics panel moved to roadmap tense, license footer.

## Suite

576 passed, 42 skipped, 48 deselected (integration tests moved from
skipped to deselected by the addopts change; passed count unchanged).
