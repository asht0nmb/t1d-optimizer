# Phase 1–2: OSS Hygiene + Documentation Truth-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the repo open-source ready (LICENSE, CONTRIBUTING, SECURITY, clean env templates, pytest CI) and bring the three top-level docs (TECHNICAL_SPEC.md, CLAUDE.md, README.md) back in line with the shipped v2 system.

**Architecture:** Pure docs/config/CI changes — no Python or TypeScript source changes, no behavior changes. Every task is independently committable. The only executable artifact is the new GitHub Actions test workflow, modeled on the existing `tandem-nightly-sync.yml` conventions (setup-uv@v8.1.0, python-version-file, `uv sync --frozen`).

**Tech Stack:** GitHub Actions, Markdown, uv/pytest.

**Verification used throughout:** `uv run pytest -q` must stay at 576 passed / 43 skipped / 47 deselected (no source changes, so any deviation means something went wrong). YAML files validated with `uv run python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" <file>`.

**Context for the engineer (facts verified 2026-06-11):**
- Repo: Python 3.12+, uv-managed, single `main` branch workflow, conventional commits (`feat:`, `fix:`, `docs:`, `chore:` with scope).
- Tests: 576 default suite, 47 `legacy`-marked opt-in, 34 supabase contract tests skip without `SUPABASE_TEST_URL`.
- Secrets posture is clean: only placeholder `.env.example` files are tracked. Do NOT touch `.env` (live local credentials, gitignored).
- `docs/updates/` is an append-only dated audit log — every substantive change gets an entry.
- Commit footer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: LICENSE (MIT + medical notice)

**Files:**
- Create: `LICENSE`

- [ ] **Step 1: Write `LICENSE`** with exactly:

```
MIT License

Copyright (c) 2026 Ashton Meyer-Bibbins

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

Medical notice (informational, not a license term): T1D Engine is not a
medical device and provides no medical advice. It never recommends insulin
doses. Alerts and analytics are informational and may be wrong, late, or
missing; never rely on this software for treatment decisions. Not affiliated
with Dexcom, Tandem, or any device manufacturer.
```

- [ ] **Step 2: Commit**

```bash
git add LICENSE
git commit -m "docs: add MIT license with medical notice"
```

---

### Task 2: SECURITY.md

**Files:**
- Create: `SECURITY.md`

- [ ] **Step 1: Write `SECURITY.md`** with exactly:

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add SECURITY.md
git commit -m "docs: add security policy"
```

---

### Task 3: CONTRIBUTING.md

**Files:**
- Create: `CONTRIBUTING.md`

- [ ] **Step 1: Write `CONTRIBUTING.md`** with exactly:

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs: add contributing guide"
```

---

### Task 4: Tidy root `.env.example`

**Files:**
- Modify: `.env.example` (root — do NOT touch `apps/web/.env.example` or `.env`)

Current file ends with a sloppy appended block (duplicate `TIMEZONE_NAME`, quoted placeholder values, a `DEXCOM_OUS=false4` typo, missing newline before comment). Replace the entire file with:

- [ ] **Step 1: Rewrite `.env.example`** to exactly:

```
# ── Ingestion (Tandem t:connect via tconnectsync) ──────────────────────
TCONNECT_EMAIL=you@example.com
TCONNECT_PASSWORD=your-password

# Local timezone for ingestion / display (IANA name)
TIMEZONE_NAME=America/Los_Angeles

# ── Supabase (hosted shell only; leave unset for local parquet mode) ──
# Direct connection (port 5432) — one-time bootstrap + nightly sync ONLY.
# Format: postgresql://postgres:PASSWORD@db.PROJECTREF.supabase.co:5432/postgres
SUPABASE_DB_URL=

# ── Live meal-rise loop (hosted shell only) ────────────────────────────
DEXCOM_USERNAME=you@example.com
DEXCOM_PASSWORD=your-password
DEXCOM_OUS=false
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
CRON_SECRET=

# ── Web dashboard (see apps/web/.env.example for the full set) ─────────
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
VERCEL_APP_URL=
```

- [ ] **Step 2: Verify no tooling references the removed duplicate keys**

Run: `git grep -n 'TIMEZONE_NAME' -- '*.py' | head`
Expected: references read the env var by name (unaffected); nothing parses `.env.example` itself.

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "chore: tidy root .env.example (dedupe keys, fix typos, group by shell)"
```

---

### Task 5: GitHub Actions pytest CI

**Files:**
- Create: `.github/workflows/tests.yml`
- Modify: `pyproject.toml:4` (replace placeholder `description = "Add your description here"`)

- [ ] **Step 1: Write `.github/workflows/tests.yml`** with exactly:

```yaml
name: Tests

# Default pytest suite on every push/PR to main. Legacy-marked and
# supabase-parameterized tests are excluded by default (addopts -m 'not
# legacy'; supabase contract tests skip without SUPABASE_TEST_URL).

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

jobs:
  pytest:
    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v8.1.0
        with:
          enable-cache: true

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version-file: ".python-version"

      - name: Install dependencies
        run: uv sync --frozen

      - name: Run test suite
        env:
          PYTHONUNBUFFERED: "1"
        run: uv run pytest -q
```

- [ ] **Step 2: Validate YAML parses**

Run: `uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/tests.yml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Update pyproject description**

In `pyproject.toml`, change line 4 from `description = "Add your description here"` to:

```toml
description = "Type 1 diabetes data intelligence: CGM + pump ingestion, event detection, live alerts, dashboards"
```

- [ ] **Step 4: Verify suite + lockfile untouched**

Run: `uv run pytest -q 2>&1 | tail -1`
Expected: `576 passed, 43 skipped, 47 deselected, ...`
Run: `git diff --stat uv.lock`
Expected: empty (description change must not churn the lockfile; if it does, run `uv lock` and include it).

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/tests.yml pyproject.toml
git commit -m "ci: run default pytest suite on push/PR to main"
```

---

### Task 6: Rewrite TECHNICAL_SPEC.md to v2 reality

**Files:**
- Modify: `docs/operating_docs/TECHNICAL_SPEC.md` (full rewrite, keep filename)

The current spec (last updated 3/21/26) describes the quarantined v1
detection as active and calls shipped surfaces "Phase 3 / not part of this
version." Rewrite it as the accurate system-of-record. Authoritative
sources to read before writing: `core/detection/meal_rise.py`,
`core/detection/windowing.py`, `detection/config.py`,
`detection/calibration/meal_rise_scoring.py`,
`apps/personal/cron/detect_meal_rise.py`, `core/storage/protocol.py`,
`db/migrations/*.sql`, `docs/updates/2026-05-1*.md` through
`2026-06-11-*.md`, and CLAUDE.md.

- [ ] **Step 1: Rewrite the spec** with this structure (write real prose under each heading from the sources above; keep the existing DATA_CATALOG.md cross-references):
  1. **System overview** — two shells (OSS local: Streamlit+parquet; hosted personal: Next.js+Vercel+Supabase+cron worker+Telegram) around a storage-agnostic `core/`. Update date header to 2026-06-11.
  2. **Data schema** — keep current Source 1/2/3 section, unchanged except wording fixes.
  3. **Storage layer** — `Storage` Protocol, three implementations, contract tests, pooler-vs-direct connection rules, RLS model (condense from CLAUDE.md).
  4. **Detection v2 (active)** — windowing primitive (`make_window`, coverage, gap handling); meal-rise detector (Theil-Sen slope, start-level gate, time-of-day multipliers, freshness guard `max_reading_age_minutes`); M1 hardening summary (idempotent claim-before-send alerts, refractory window, retry w/ backoff, DST-safe bucketing); M2 calibration scoring (label taxonomy pre_bolused/late_bolused/uncovered + correction attribution, config windows 30/45/180).
  5. **Live loop topology** — cron-job.org → Vercel Python worker (`api/index.py`, bearer `CRON_SECRET`) → Dexcom Share fetch → detection → Supabase persist → Telegram. GitHub Actions nightly Tandem sync at 06:00 UTC.
  6. **Daily features** — keep the existing §"Daily Features" text (it is still accurate for `detection/features.py`).
  7. **Detection v1 (legacy, quarantined)** — one short section: algorithms preserved in `detection/legacy/` as reference, not imported by production; link `detection/legacy/README.md`. Move the v1 anomaly/meal/clustering algorithm details OUT of the main flow into this section, condensed.
  8. **Roadmap (deferred)** — episode/pattern/cause layers, supervised models on the M2-labeled dataset, LLM Telegram assistant. Mark explicitly as not built.
  9. **Config** — replace the stale YAML example with the real current `config/user_config.yaml` shape (bg_targets, meal_detection [legacy], anomaly_detection [legacy], clustering [legacy], site_change_detection, meal_rise, meal_rise_calibration, ingestion.timezone), noting which blocks are legacy-only.
  10. **Real-time constraints** — keep (trailing window only; false-positive vs late-alert balance).

- [ ] **Step 2: Verify no stale claims remain**

Run: `grep -n 'Phase 3\|not part of this version\|detection/anomaly.py\|detection/meal.py\|detection/clustering.py' docs/operating_docs/TECHNICAL_SPEC.md`
Expected: no matches outside the legacy section (paths there must say `detection/legacy/...`).

- [ ] **Step 3: Commit**

```bash
git add docs/operating_docs/TECHNICAL_SPEC.md
git commit -m "docs: rewrite TECHNICAL_SPEC to match shipped v2 system"
```

---

### Task 7: Refresh CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the Architecture section** — current text says "surfaces (Telegram / Streamlit / live pydexcom) are not yet built", which is false. Edit the architecture intro and layout list to add:
  - `apps/local/` — Streamlit OSS dashboard (day / heatmap / TIR pages, Plotly).
  - `apps/web/` — Next.js personal dashboard (Vercel + Supabase, Phase A routes).
  - `apps/personal/cron/` — live meal-rise loop (Dexcom poll → detect → Telegram), invoked by the Vercel Python worker at `api/index.py` (separate Vercel project, external cron-job.org scheduler).
  - `db/migrations/` — Supabase schema + RLS.
  - `.github/workflows/` — nightly Tandem sync, manual meal-rise fallback, smoke test, and the test CI added in this phase.
  - In `detection/` bullet: mention `detection/calibration/` (M2 scoring) and `core/detection/` (windowing + meal-rise detector).
- [ ] **Step 2: Fix test counts** — replace both "477" mentions with the current default-suite count (run `uv run pytest -q 2>&1 | tail -1` and use that number).
- [ ] **Step 3: Update the Data Pipeline section** — "Live: pydexcom … **not yet implemented**" is false; rewrite to describe the live loop as shipped.
- [ ] **Step 4: Verify**

Run: `grep -n 'not yet built\|not yet implemented\|477' CLAUDE.md`
Expected: no matches.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: refresh CLAUDE.md to current architecture and test counts"
```

---

### Task 8: README alignment + OSS quickstart

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Separate built from planned in "What it does" (line 22)** — currently one paragraph in present tense including unbuilt features (sensitivity tracker, episode reconstruction, LLM Telegram bot). Rewrite into two short paragraphs: *Running today* (live missed-meal alert loop; nightly Tandem sync; Next.js dashboard with day/heatmap/TIR-trends/insulin/search/compare; local Streamlit dashboard; daily feature panel) and *On the roadmap* (episode reconstruction, pattern clustering, sensitivity tracking, LLM-backed Telegram Q&A and digests). Keep the four-layer table (line 26-33) but add one sentence: only the Event layer is live; Episode/Pattern/Cause are roadmap.
- [ ] **Step 2: Add a Quickstart section** (after "What it does", before "Detection and metrics") with exactly:

````markdown
## Quickstart (local, no cloud accounts)

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). Works with a
Tandem t:connect CSV export or live t:connect credentials.

```bash
uv sync                                  # install
cp .env.example .env                     # add t:connect credentials
uv run python main.py fetch              # pull pump history → data/processed/*.parquet
uv run python main.py doctor             # verify the pipeline state
uv run python main.py dashboard          # Streamlit dashboard at localhost:8501
```

Day-level CLI views: `uv run python main.py check --date 2026-06-01` and
`uv run python main.py viz --date 2026-06-01 --view enriched`.

The hosted shell (Supabase + Vercel + Telegram alerts) is documented in
`docs/operating_docs/TECHNICAL_SPEC.md`.
````

- [ ] **Step 3: Fix the metrics paragraph tense (line 41)** — GRI/GMI/LBGI-HBGI panel is roadmap, not shipped; change "The overnight metric panel computes…" to future/roadmap framing ("will compute"), or fold it into the roadmap paragraph from Step 1. Keep all citations exactly as they are.
- [ ] **Step 4: Add license/contributing footer** — above the final disclaimer line, add: `MIT licensed — see LICENSE (includes a medical notice). Contributions: see CONTRIBUTING.md.`
- [ ] **Step 5: Verify**

Run: `grep -n 'answers questions on demand\|sensitivity tracker estimates' README.md`
Expected: no matches in present-tense "what it does" framing (roadmap phrasing is fine).

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: README — split built vs roadmap, add OSS quickstart"
```

---

### Task 9: Update-doc entry + final verification

**Files:**
- Create: `docs/updates/2026-06-11-oss-hygiene-and-docs-truthup.md`

- [ ] **Step 1: Write the update doc** summarizing Tasks 1–8: what was added (LICENSE/SECURITY/CONTRIBUTING/tests.yml), what was rewritten (TECHNICAL_SPEC, CLAUDE.md, README), and why (OSS readiness; docs described the March system, not the shipped one). Note explicitly: no source changes; suite count unchanged.
- [ ] **Step 2: Full verification**

Run: `uv run pytest -q 2>&1 | tail -1`
Expected: `576 passed, 43 skipped, 47 deselected, ...`
Run: `git status --short`
Expected: only the new update doc untracked/staged; working tree otherwise clean.

- [ ] **Step 3: Commit**

```bash
git add docs/updates/2026-06-11-oss-hygiene-and-docs-truthup.md
git commit -m "docs: dated update entry for OSS hygiene + docs truth-up"
```
