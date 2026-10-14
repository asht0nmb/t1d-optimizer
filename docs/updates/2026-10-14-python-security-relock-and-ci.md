# 2026-10-14 - Python security relock + CI action bumps

Stacked on PR #19 (`fix/sync-fail-loud-and-redaction`, tconnectsync 3.0.3).

## Python relock
`pip-audit` against `uv export --all-groups` flagged 144 advisories across 23 packages. Targeted `uv lock --upgrade-package` (no blanket upgrade) took it to 0 known vulnerabilities.

| package | from | to |
|---|---|---|
| anyio | 4.12.1 | 4.14.2 |
| bleach | 6.3.0 | 6.4.0 |
| cryptography | 46.0.5 | 50.0.2 |
| gitpython | 3.1.50 | 3.2.0 |
| idna | 3.11 | 3.20 |
| jupyter-server | 2.17.0 | 2.21.1 |
| jupyterlab | 4.5.6 | 4.6.4 |
| lxml | 6.0.2 | 6.1.3 |
| mistune | 3.2.0 | 3.3.4 |
| nbconvert | 7.17.0 | 7.17.2 |
| notebook | 7.5.5 | 7.6.3 |
| oauthlib | 3.3.1 | 4.0.0 |
| pillow | 12.1.1 | 12.3.0 |
| pygments | 2.19.2 | 2.21.0 |
| pyjwt | 2.13.0 | 2.15.1 |
| pytest | 9.0.2 | 9.1.1 |
| python-multipart | 0.0.29 | 0.0.32 |
| requests | 2.32.5 | 2.34.2 |
| setuptools | 74.1.3 | 84.0.0 |
| soupsieve | 2.8.3 | 3.0.3 |
| starlette | 1.0.0 | 1.8.0 |
| tornado | 6.5.5 | 6.5.10 |
| urllib3 | 2.6.3 | 2.8.0 |

New transitive: jupyter-builder 1.2.3 (via jupyterlab 4.6).

- PyJWT override floor raised `>=2.12.1` -> `>=2.15.0` (tconnectsync still pins `PyJWT==2.8.0`).
- setuptools: the `<75` pin was introduced in the initial "tconnectsync data foundation" commit (dd28968) with no comment or documented reason, and nothing in the repo imports `pkg_resources`/setuptools directly (it is only pulled in transitively by jupyterlab). With no live reason, the pin became `>=83.0.0` (fixes PYSEC-2025-49 and PYSEC-2026-3447). Full suite passes.

## Vercel worker
`requirements.txt` and `apps/cron_worker/requirements.txt`: `requests` 2.32.5 -> 2.34.2 (fixes PYSEC-2026-2275). Other pins had no advisories. No packages added.

## GitHub Actions
checkout v4 -> v7, setup-python v5 -> v7, setup-node v4 -> v7, setup-uv v8.1.0 -> v10.3.0 (setup-uv publishes no floating major tags, so it stays exact-pinned). All run on Node 24.
Breaking-change review: setup-node v5/v6 auto-cache is npm-only and the web job already uses `cache: npm`; setup-python removed only the unused `pip-install` input; setup-uv v9/v10 changed `prune-cache` default and disable auto-cache for `pull_request_target`/`workflow_run`/`release` events (none used here). Schedules, triggers, secrets and job logic untouched.
`ubuntu-latest` moves to Ubuntu 26 on 2026-10-19; left as-is, nothing known to break.

## Verification
`uv lock --check` clean, `uv sync --all-groups` ok, `uv run pytest -q`: 880 passed, 42 skipped, 48 deselected. pip-audit: 144 -> 0.

## Review notes
- setuptools is a new floor, not only removal of the `<75` pin: `>=83.0.0` (resolved 84.0.0), chosen to fix PYSEC-2025-49 and PYSEC-2026-3447.
- `jupyter-builder` 1.2.3 is a new transitive, dev-only dependency of jupyterlab 4.6. It is not in the worker requirements files.
- The new action majors' input schemas were checked only via release notes and a green CI run. A green CI run on this PR, or after rebasing onto main once #19 merges, is the real verification.
