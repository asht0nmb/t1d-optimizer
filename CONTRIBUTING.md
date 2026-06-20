# Contributing

T1D Engine is a personal project first, an OSS project second. PRs and
issues are welcome, but scope is conservative — open an issue before
building anything large.

## Dev setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                 # install dependencies
uv run pytest           # default suite (legacy tests deselected)
uv run pytest -m legacy # opt-in: quarantined v1 detection tests
```

There is no real-data requirement: tests run against anonymized fixtures
in `test_data/` and in-memory storage.

## Rules that PRs are reviewed against

- **`core/` stays storage-agnostic.** It may import stdlib, pandas,
  numpy, pydantic, typing only. Backend SDKs (psycopg2, parquet I/O) are
  allowed only in `core/storage/parquet.py`, `core/storage/supabase.py`,
  and `core/storage/_postgres_converters.py`. Shells inject a `Storage`
  implementation; core never picks a backend.
- **No hardcoded thresholds or personal parameters.** Everything tunable
  lives in `config/user_config.yaml` and is read through
  `detection.config.get_config()`.
- **Real-time detection uses trailing windows only** — no future BG
  context.
- **Do not extend or import `detection/legacy/`** from production code.
- **Bump `ingestion.pipeline_version.PIPELINE_VERSION`** (with a
  changelog entry) when a builder/enricher changes output schema or
  timestamp semantics.
- **Never commit real patient data or a real `.env`.** `data/` and
  `.env*` (except `.env.example`) are gitignored for a reason.
- Substantive changes add a dated `docs/updates/YYYY-MM-DD-*.md` entry —
  the append-only audit trail.

## Commit style

Conventional commits (`feat(scope): …`, `fix: …`, `docs: …`). Keep
commits focused; the test suite must pass on every commit.
