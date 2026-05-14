# 2026-05-13 — Detection v1 quarantine

## Framing

The May 5 plan (`docs/plans/2026-05-05-detection-rework-and-surfaces.md`) calls for replacing the v1 detection engine — sustained-rise meal detection, threshold-crossing anomalies, KMeans day clustering — with a v2 design that builds on a richer patterns layer. v1 isn't wrong, but it's narrow, and we don't want to keep accreting fixes against algorithms we're about to replace. Rather than delete the v1 code outright we quarantined it under `detection/legacy/`. That preserves the reference implementation (algorithms, tests, configs) so v2 design work can read it, copy patterns, and diff outputs from a notebook. Production code does not import from `detection.legacy.*` and never should; the module README and a Critical Rule in `CLAUDE.md` both spell that out. `detection.features.daily_features` was promoted out of v1 and is now first-class patterns-layer infrastructure for v2.

## File inventory

Moved (via `git mv` so history is preserved):

- `detection/anomaly.py` → `detection/legacy/anomaly.py`
- `detection/meal.py` → `detection/legacy/meal.py`
- `detection/clustering.py` → `detection/legacy/clustering.py`
- `tests/test_detection_anomaly.py` → `tests/legacy/test_detection_anomaly.py`
- `tests/test_detection_meal.py` → `tests/legacy/test_detection_meal.py`
- `tests/test_detection_clustering.py` → `tests/legacy/test_detection_clustering.py`

Created:

- `detection/legacy/__init__.py` (empty; makes `from detection.legacy.meal import detect_meals` work in a notebook)
- `detection/legacy/README.md` (public surface at quarantine + the "do not import from production" rule)
- `tests/legacy/__init__.py` (empty)
- `tests/legacy/conftest.py` (re-exports `default_config` from `tests/conftest.py`)
- `docs/updates/2026-05-13-detection-v1-quarantine.md` (this file)

Deleted entirely:

- `scripts/run_detection.py` — CLI dispatcher for v1 detection; no future use.
- `tests/test_cli_detection.py` — guarded the now-removed subparsers.
- `data/processed/daily_clusters.parquet` — output of `cluster_days`; regenerable from a notebook against legacy if needed.
- `data/models/kmeans_v1.pkl`, `data/models/scaler_v1.pkl`, `data/models/features_v1.json` — clustering artefacts. `data/models/` itself stays (gitignored).
- `main.py` subparsers `analyze-anomalies`, `analyze-meals`, `cluster-days` and their dispatch branches. `fetch` / `update` / `fetch-day` / `check` / `viz` / `doctor` remain.

Edited (touch-light):

- `detection/__init__.py` — docstring rewritten; banner gone, `detection.legacy/` and `daily_features` called out.
- `pyproject.toml` — added `legacy` marker; `addopts = "-m 'not legacy'"` excludes legacy from the default suite while preserving the `integration` marker.
- `config/user_config.yaml` — comment above `meal_detection` flags the `meal_detection` / `anomaly_detection` / `clustering` blocks as legacy-only. Values unchanged.
- `CLAUDE.md` — refreshed Architecture layers, key directories, test counts; replaced HANDOFF guidance with the `docs/updates/` cadence; added the "do not extend `detection/legacy/`" rule.
- `docs/operating_docs/TECHNICAL_SPEC.md` §"Detection Logic" — prepended a dated note pointing readers at `detection/legacy/` and the May 5 plan. No other edits.
- `docs/operating_docs/DATA_CATALOG.md` §4 — same dated note at the top of §4. Column tables unchanged.

Unchanged on purpose:

- `detection/config.py` — `MealDetectionConfig`, `AnomalyDetectionConfig`, `ClusteringConfig` left intact; legacy modules read from them and removing them would break the quarantine.
- `detection/features.py`, `tests/test_detection_features.py`, `tests/test_detection_config.py`.
- The wider docs (`DATA_NOTES*.md`, `DATA_ISSUES.md`, `api_levels.md`, plans) — a full `TECHNICAL_SPEC.md` + `DATA_CATALOG.md` §4 rewrite is deferred until v2's first module ships.

## Test-count delta

Captured via `uv run pytest --collect-only -q`:

| Selection | Before | After |
|---|---|---|
| Default (`uv run pytest -q`) | 401 collected (393 passed, 1 skipped pre-move) | 343 passed, 1 skipped, 47 deselected |
| Legacy (`uv run pytest -m legacy tests/legacy/ -q`) | n/a | 47 passed |

Delta: −47 tests moved to `tests/legacy/` (opt-in via `-m legacy`) and −10 deleted with `test_cli_detection.py`. New default total (343) ≈ previous default minus those 58.

## Public surface change

Before:

```python
from detection import AppConfig, get_config, load_config
from detection.anomaly import detect_anomalies
from detection.meal import detect_meals
from detection.clustering import cluster_days
from detection.features import daily_features
```

After:

```python
from detection import AppConfig, get_config, load_config
from detection.features import daily_features
# Legacy reference, notebook-only — never imported from production code:
from detection.legacy.anomaly import detect_anomalies
from detection.legacy.meal import detect_meals
from detection.legacy.clustering import cluster_days
```

`detection.__all__` is unchanged (still `AppConfig`, `get_config`, `load_config`). `detection.features.daily_features` keeps its top-level path; it's now the patterns-layer foundation v2 will build on rather than a v1-specific module. The three legacy modules continue to import `from detection.config import AppConfig` — that path was preserved deliberately.

## Verification

All commands run from the repo root.

- `uv run pytest -q` — **PASS**. 343 passed, 1 skipped, 47 deselected, 5 warnings in 9.50s.
- `uv run pytest -m legacy tests/legacy/ -q` — **PASS**. 47 passed in 0.87s.
- `uv run python main.py doctor` — **PASS**. `pipeline state: OK`; on-disk pipeline `v3`, 9/9 processed parquet tables present.
- `uv run python main.py check --date 2026-04-14 --view enriched` — **PASS**. Enriched sections (`bolus_category`, `forced_by_alarm`, `site_issues`, `cgm_gaps`) printed without error.
- `uv run python main.py viz --date 2026-04-14 --view enriched` — **PASS** (with `MPLBACKEND=Agg` to render headlessly). Figure built; only the standard `tight_layout` informational warnings.
- `uv run python -c "from detection.legacy.meal import detect_meals; from detection.config import get_config; print(detect_meals.__doc__)"` — **PASS**.
- `uv run python -c "from detection.features import daily_features; print(daily_features.__doc__)"` — **PASS**. Top-level access preserved.
- `uv run python main.py --help` — **PASS**. Subcommands listed: `fetch`, `update`, `fetch-day`, `check`, `viz`, `doctor`. No `analyze-anomalies` / `analyze-meals` / `cluster-days`.

## Follow-up

A full `TECHNICAL_SPEC.md` + `DATA_CATALOG.md` §4 rewrite is **out of scope** for this change. Both docs carry a dated note pointing readers at `detection/legacy/` and the May 5 plan; the full rewrite lands when v2 ships its first module on `main`. At that point we revisit:

- Whether v2 modules live at the top level of `detection/` or under `detection/v2/` (symmetry with `detection/legacy/`).
- Whether the `meal_detection` / `anomaly_detection` / `clustering` blocks in `config/user_config.yaml` get removed (the in-place comment flags them as legacy-only today).
- Deletion of `detection/legacy/` once v2 fully supersedes its functionality.
