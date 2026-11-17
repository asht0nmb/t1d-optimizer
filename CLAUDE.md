<!--
Maintainer notes (stripped before Claude reads this file).
- Keep this file under ~150 lines. It loads into every session, so every line costs context and dilutes the rest.
- Area-specific detail lives in .claude/rules/*.md with `paths:` frontmatter, which loads only when Claude touches matching files.
- Do not put counts, dates of last runs, or production status here. They go stale and caused two truth-up PRs (#23, #29). Put them in a dated docs/updates/ entry.
- Add a line here only when Claude makes the same mistake twice or a review catches something Claude should have known.
- Last restructured 2026-11-03 for the metrics-to-AI direction.
-->

# T1D Engine

Personal data system for a Dexcom CGM and Tandem Control-IQ pump user. It ingests pump and CGM data, cleans it, computes validated glycemic and insulin metrics, and (next) gives those metrics to an LLM that supports diabetes management and lifestyle decisions.

## Current direction (decided 2026-10-29)

- The live missed-meal alert loop (`apps/personal/cron/`, `api/index.py`) is paused. Don't extend it or fix it unless asked.
- The main line of work, in order: data foundation, then validated metrics, then a metric suite, then an AI layer with its own evaluation.
- Correctness of data and metrics matters more than speed or features. A wrong metric flows into everything built on it.

## Commands

```bash
uv sync                                   # Python deps (uv, never pip)
uv sync --group local                     # adds Streamlit/Plotly for the local dashboard
uv run pytest -q                          # default suite (legacy and integration tests excluded)
uv run pytest tests/path/test_x.py::test_name
uv run pytest -m legacy                   # opt-in: detection/legacy tests
uv run python main.py doctor              # check on-disk parquet matches PIPELINE_VERSION
uv run python main.py update              # incremental pump fetch into data/processed/
uv run python main.py check --date YYYY-MM-DD [--view enriched]
uv run python main.py dashboard           # local Streamlit dashboard
```

Web app (`apps/web/`): `npm run lint`, `npx tsc --noEmit`, `npm run test`, `npm run build`. CI runs all four plus pytest.

## Layout

- `ingestion/`: tconnectsync client, event builders, enrichment, log redaction, pipeline version
- `core/`: storage-agnostic library. `core/storage/` (Storage Protocol plus Parquet, Supabase and InMemory backends), `core/schema.py`, `core/metrics/` (glycemic metrics, AGP, risk indices, variability), `core/detection/` (windowing, meal-rise detector)
- `detection/`: typed config, `daily_features`, M2 calibration scoring. `detection/legacy/` is quarantined v1 code
- `apps/local/`: Streamlit dashboard over parquet, no cloud accounts needed
- `apps/web/`: Next.js dashboard on Vercel and Supabase
- `apps/personal/`: paused live loop and the Telegram command handlers
- `db/migrations/`: Supabase schema and RLS
- `scripts/`: CLI tools, nightly sync, CI log gate, Telegram setup
- `config/user_config.yaml`: every threshold and personal parameter
- `docs/decisions/`: one record per data rule or metric definition
- `docs/operating_docs/`: technical spec, data catalog, data issues, deploy runbook
- `docs/updates/`: dated, append-only session write-ups (the audit log)

`data/`, `test_data/` and `research/` are gitignored and exist only on the owner's machine. Never commit data files.

## Rules that apply everywhere

### Privacy (the repo and its Actions logs are public)

- IMPORTANT: never log, print, commit or paste into a PR any pump serial (mask as `…1234`), Tandem or Dexcom account or device ID, UUID, name, date of birth, email, token, or raw API dump.
- Personal health values (therapy settings, dated BG readings, per-day stats) stay out of committed docs, tests and fixtures. Use synthetic data in tests.
- Workflow output goes through `scripts/ci_log_gate.py`, and Python logging goes through `ingestion/redaction.py`. Don't bypass either.
- A committed log forced a full git-history rewrite on 2026-10-14. Commit SHAs cited in docs before that date are stale.

### Health output boundary

- Everything the system produces is an observation about the data. Never write code, prompts or copy that recommends an insulin dose or a specific settings change.
- Nothing in this repo controls or writes to the pump. It only reads.

### Code boundaries

- `core/` may import only stdlib, pandas, numpy, pydantic and typing. It never imports `ingestion/`, `scripts/`, `apps/`, backend SDKs, web frameworks, Telegram or LLM clients.
- Backend SDK imports are allowed only in `core/storage/parquet.py`, `core/storage/supabase.py` and `core/storage/_postgres_converters.py`.
- Callers receive a `Storage` by constructor injection. Code in `core/` never picks a backend.
- Thresholds and personal parameters come from `config/user_config.yaml`, never literals in code.
- Don't extend or import `detection/legacy/` from production code.
- Bump `PIPELINE_VERSION` and add a `PIPELINE_VERSION_CHANGELOG` entry in `ingestion/pipeline_version.py` whenever a builder or enricher changes output schema or timestamp semantics.

## How work is done here

- Agents open PRs. Only the owner merges. Never merge, force-push `main`, or change production config, secrets or deployments.
- Before writing code for a new data rule or metric, there must be a decision record in `docs/decisions/` (template: `docs/decisions/0000-template.md`). If none exists, write the record first and stop for review.
- One decision per PR. Keep PRs small enough to review in about 15 minutes.
- Every metric or cleaning rule gets tests with hand-checkable known answers, taken from its decision record.
- Before calling work done, run the relevant checks and report the actual commands and results. Say plainly what wasn't verified. Don't describe a PR as reviewed unless a separate review actually ran.
- After a substantive change, add a `docs/updates/YYYY-MM-DD-<topic>.md` entry. Never edit earlier entries.
- When something is unclear about the owner's intent or a health-data judgment call, ask rather than guess.

## Reference docs

- `docs/operating_docs/TECHNICAL_SPEC.md`: schema, storage layer and config reference. Its detection and roadmap sections predate the 2026-10-29 direction, so treat them as history.
- `docs/operating_docs/DATA_ISSUES.md`: known data-quality problems. Check it before writing any cleaning rule.
- `docs/operating_docs/DEPLOY.md`: deployment and Telegram setup runbook.