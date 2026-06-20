# Workstream A — Clinical Analytics Engine (`core/metrics/`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or executing-plans. Strict TDD per task: write golden/property tests RED, implement GREEN. Checkbox (`- [ ]`) steps.

**Goal:** A deterministic, clinically-correct CGM analytics suite in `core/metrics/`, computed once in Python and consumed identically by both shells, replacing the 4× duplicated TIR logic and the aspirational README metrics with real implementations.

**Architecture:** Pure `stdlib + numpy + pandas + dataclasses` (core import boundary — NO scipy/sklearn). One standalone pure function per metric (independently golden-tested) plus a `compute_cgm_report` orchestrator returning a frozen `CgmReport`. Source-agnostic: takes a CGM DataFrame + config + window; never imports `Storage`.

**Tech stack:** numpy, pandas, pytest, hypothesis (add as a test dep if not present — check pyproject; if adding, put in the dev/test group only).

**Boundary invariant (the #1 correctness lock):** the six bins TBR2 / TBR1 / TIR / TAR1 / TAR2 partition all valid readings with **half-open** edges so they sum to exactly 100%:
- TBR2 = `g < 54`
- TBR1 = `54 <= g < 70`
- TIR  = `70 <= g <= 180`  (consensus range, inclusive both ends)
- TAR1 = `180 < g <= 250`
- TAR2 = `g > 250`
- TITR = `70 <= g <= 140` (overlaps TIR; reported separately, NOT part of the partition)

These cut points (54/70/140/180/250) are **fixed clinical constants, independent of `bg_targets`**. `bg_targets.low/high` default 70/180; when a user customizes them, also report a separate "TIR (config band)" but keep the consensus bins for GMI/GRI/citations.

**Verified baseline:** `uv run pytest -q` → 627 passed, 42 skipped, 48 deselected. Keep green; new tests add to the count.

---

### Task 1: `core/metrics/windows.py` — windowing + sufficiency (TDD)

**Files:** Create `core/metrics/windows.py`, `tests/core/test_metrics_windows.py`.

DST-correct local-day windowing shared by all metrics + AGP. Reuse the half-open `[since, until)` convention from `apps/local/dates.py` but live in core (pure).

- [ ] Tests RED: 
  - `local_day_bounds(date(2026,3,8), tz="America/Los_Angeles")` returns `(2026-03-08T08:00Z, 2026-03-09T07:00Z)` style instants (spring-forward day is 23h); fall-back day (2026-11-01) is 25h.
  - `window_bounds(end_date, days, tz)` returns `[since, until)` spanning `days` local dates ending inclusive on `end_date`.
  - `active_time(cgm, since, until, expected_interval_min=5)` → `(n_readings, expected, active_pct)` where `expected` is computed from the **tz-aware span length** (so a 25h fall-back day expects ~300, not 288), not `days*288`.
  - `meets_sufficiency(days_covered, active_pct, min_days=14, min_active=70.0)` boolean.
- [ ] Implement; full suite green. Commit `feat(metrics): DST-correct windowing + data-sufficiency in core/metrics`.

### Task 2: `core/metrics/cgm_metrics.py` — core panel (TDD, golden + property)

**Files:** Create `core/metrics/cgm_metrics.py`, `tests/core/test_cgm_metrics.py`.

Functions (all pure, operate on a numeric BG array/Series with NaN dropped first):
- `time_in_bands(bg) -> dict` with keys tbr2/tbr1/tir/tar1/tar2/tar_total/tbr_total/titr (percentages 0–100) using the half-open partition above.
- `time_in_range(bg, low, high)` — the configurable-band TIR (this is the single shared TIR that local metrics, features, telegram digest, and web all converge on).
- `mean_bg`, `median_bg`, `sd_bg` (**ddof=1**, requires N≥2 else None), `cv_pct` (=100*sd/mean), `cv_stable` (cv<=36).
- `gmi(mean_mgdl) = 3.31 + 0.02392*mean`.
- `ea1c(mean_mgdl) = (mean + 46.7)/28.7`.

- [ ] **Golden tests RED**:
  - GMI: mean=150 → 6.898 (±1e-3); mean=100→5.702; mean=200→8.094.
  - eA1c: mean=150 → 6.8537 (±1e-3).
  - CV: array `[100,110,120,130,140]`, ddof=1 → assert equals `np.std(...,ddof=1)/np.mean(...)*100`; cv_stable boundary at exactly 36.
  - Bins: construct an array hitting every boundary exactly (53,54,69,70,140,180,181,250,251) and assert each lands in the intended bin and totals sum to 100.
- [ ] **Property tests (hypothesis) RED**: for any non-empty positive BG array, `tbr2+tbr1+tir+tar1+tar2 == 100 ± 1e-9` (partition invariant); all percentages in [0,100]; gmi monotonic in mean.
- [ ] Implement GREEN; full suite. Commit `feat(metrics): core CGM panel (time-in-bands, GMI, eA1c, CV) with golden+property tests`.

### Task 3: `core/metrics/risk_indices.py` — LBGI/HBGI/GRI (TDD, golden)

**Files:** Create `core/metrics/risk_indices.py`, `tests/core/test_risk_indices.py`.

- LBGI/HBGI (Kovatchev), BG clamped to [20,600] before the log:
  ```
  f(g)  = 1.509 * ((ln g)**1.084 - 5.381)
  rl    = 10*f^2 if f<0 else 0 ;  rh = 10*f^2 if f>0 else 0
  LBGI  = mean(rl) ;  HBGI = mean(rh)
  ```
- GRI (Klonoff) from the bins (percentages): `hypo = tbr2 + 0.8*tbr1 ; hyper = tar2 + 0.5*tar1 ; GRI = clamp(3*hypo + 1.6*hyper, 0, 100)`. Return `gri`, `gri_hypo`, `gri_hyper`.

- [ ] **Golden tests RED**:
  - LBGI/HBGI: all readings = 112.5 mg/dL → f≈0 → LBGI≈0, HBGI≈0 (±0.05). A constant-low array and constant-high array → hand-compute `10*f^2` and freeze.
  - GRI: bins tbr2=2,tbr1=5,tar1=10,tar2=3 → hypo=2+0.8*5=6, hyper=3+0.5*10=8, GRI=3*6+1.6*8=30.8. And 100% in-range → GRI=0.
- [ ] **Property RED**: shifting all readings up never decreases HBGI / increases LBGI; GRI in [0,100]; LBGI,HBGI ≥ 0.
- [ ] Implement GREEN; full suite. Commit `feat(metrics): LBGI/HBGI/GRI risk indices with golden tests`.

### Task 4: `core/metrics/report.py` — CgmReport orchestrator (TDD)

**Files:** Create `core/metrics/report.py`, `tests/core/test_cgm_report.py`. Update `core/metrics/__init__.py` to re-export.

- `@dataclass(frozen=True) CgmReport` with all fields from Tasks 1–3 (provenance, panel, central tendency, GMI/eA1c, LBGI/HBGI, GRI+components; advanced fields default None).
- `compute_cgm_report(cgm, *, config, window) -> CgmReport`: windows via Task 1, drops NaN, computes the panel, applies sufficiency gating (`gmi`/`gri` → None when `meets_sufficiency` is False or N<2). `None` (undefined) vs `0.0` (legitimately zero) discipline.

- [ ] Tests RED: full-report on a synthetic ~14-day sufficient frame (assert headline fields); empty frame → all-None, meets_sufficiency False, n=0; single reading → mean defined, sd/cv/gmi/gri None; <14 days → gmi/gri None but lbgi/hbgi computed; DST-transition day handled (active_pct sane).
- [ ] Implement GREEN; full suite. Commit `feat(metrics): CgmReport orchestrator (single source of truth)`.

### Task 5: Converge existing callers onto `core/metrics` (TDD-guarded refactor)

**Files:** `apps/local/metrics.py`, `apps/personal/telegram/digest.py`, `detection/features.py` (TIR portion only), and confirm web `lib/tir.ts` parity (note for Workstream I/V, don't rewrite web here).

- [ ] Replace each local `(bg>=low)&(bg<=high)` TIR reimplementation with a call to `core.metrics.cgm_metrics.time_in_range`. Existing tests for these modules must stay green (they ARE the regression net); add one test asserting all three now return identical values on a shared fixture. `detection/features.py` keeps its per-day ddof=0 std for clustering but its TIR/TAR/TBR should delegate to the shared band logic — verify feature outputs unchanged (golden the existing feature test values).
- [ ] Full suite green. Commit `refactor(metrics): converge TIR callers on core/metrics (kill 4x duplication)`.

### Task 6: AGP rigor upgrade — finer buckets + smoothing (TDD, backward-compatible)

**Files:** `core/metrics/agp.py`, `tests/core/test_agp.py`.

- [ ] Extend `agp_profile(..., bucket_minutes=15, smooth=True, smooth_window_bins=5)`. Default behavior changes to 15-min buckets + **circular weighted moving average** (pure numpy: pad both ends circularly, triangular weights, convolve, trim) applied to each percentile curve. Keep `bucket_minutes=60, smooth=False` reproducing the OLD output for the existing golden test (add an explicit test pinning the legacy path).
- [ ] Tests RED: 96 buckets at 15-min; smoothing reduces bin-to-bin variance vs unsmoothed; circularity (23:45 neighbors 00:00); legacy 60-min/unsmoothed path unchanged.
- [ ] Implement GREEN. Commit `feat(metrics): AGP 15-min buckets + circular smoothing (clinical AGP)`.

### Task 7: variability.py — MODD, CONGA, J-index, MAGE (TDD; MAGE last)

**Files:** Create `core/metrics/variability.py`, `tests/core/test_variability.py`.

- `j_index = 0.001*(mean+sd)^2`; `modd` (mean |g(t)-g(t-24h)| over time-matched points, ≥2 days, None if <2); `conga(n_hours)` (SD of g(t)-g(t-n) differences). Then MAGE via a pinned deterministic Baghurst-2011 variant (excursions whose amplitude > 1 SD; document the algorithm in the docstring).
- [ ] Golden/property tests RED (constant series → MAGE 0/None, J-index = 0.001*mean^2; MODD on a 2-day synthetic with known offset; CONGA on a ramp). Implement GREEN. Wire the four into `compute_cgm_report` (optional, computed when requested). Commit `feat(metrics): variability metrics (MODD/CONGA/J-index/MAGE)`.

### Task 8: README truth-up + update doc

**Files:** `README.md` (clinical-metrics section), `docs/updates/2026-06-17-clinical-analytics.md`.

- [ ] Update README so the GRI/GMI/LBGI/HBGI/TITR/CV panel is described as IMPLEMENTED (it now is), with the module path; keep citations. Write the dated update entry (what shipped, formulas, golden anchors, the single-source-of-truth decision, that web wiring is Workstream V). Commit `docs: clinical analytics implemented (README + update entry)`.
