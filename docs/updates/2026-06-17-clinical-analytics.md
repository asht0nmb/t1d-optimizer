# 2026-06-17 — Clinical analytics engine (Workstream A)

Executed from `docs/superpowers/plans/2026-06-17-workstream-a-clinical-analytics.md`
(master plan `docs/superpowers/specs/2026-06-17-refinement-master-plan.md`).

## What shipped

A deterministic, clinically-correct CGM analytics suite in `core/metrics/`
(pure stdlib + numpy + pandas; no scipy/sklearn — core boundary preserved).
Previously the README advertised these metrics with citations but none were
implemented; they are now real and golden-tested.

- `windows.py` — DST-correct local-day windowing + active-time/data-sufficiency
  (Battelino 14-day / 70%-active gate; expected readings from the tz-aware span,
  so 23h/25h DST days are handled, not `days*288`).
- `cgm_metrics.py` — the half-open six-bin partition (TBR2 `<54`, TBR1
  `[54,70)`, TIR `[70,180]`, TAR1 `(180,250]`, TAR2 `>250`) that sums to exactly
  100%; TITR `[70,140]`; mean/median/SD (ddof=1)/CV (+36% stability flag); GMI
  `3.31+0.02392*mean`; eA1c `(mean+46.7)/28.7`. Fixed clinical cut points,
  independent of `bg_targets`.
- `risk_indices.py` — LBGI/HBGI via the Kovatchev transform (BG clamped
  `[20,600]`); GRI `clamp(3*(tbr2+0.8*tbr1) + 1.6*(tar2+0.5*tar1), 0, 100)` with
  its hypo/hyper components for the GRI-grid visual.
- `variability.py` — J-index, MODD, CONGA, and MAGE (documented deterministic
  Baghurst-style turning-point variant, excursions > 1 SD).
- `report.py` — `CgmReport` frozen dataclass + `compute_cgm_report(cgm, *,
  config, window)` orchestrator. Single source of truth; GMI/GRI gated to `None`
  unless the sufficiency gate passes and N≥2; `None` (undefined) vs `0.0`
  (legitimately zero) discipline.

## Golden anchors (hand-verified, asserted)

GMI mean=150→6.898; eA1c mean=150→6.8537; GRI bins (2/5/10/3)→30.8; GRI
all-in-range→0; LBGI/HBGI at 112.5→≈0; J-index (120,30)→22.5. Plus hypothesis
property tests: the five-bin partition sums to 100 for any input; bounds and
monotonicity hold.

## Convergence (kills the 4× TIR duplication)

`apps/local/metrics.py`, `apps/local/chart_prep.py`, `apps/personal/telegram/
digest.py`, and `detection/features.py` now delegate TIR to
`core.metrics.cgm_metrics.time_in_range`; a convergence guard test pins their
agreement on a shared fixture. Detection features keep their ddof=0 per-day std
for clustering.

## AGP rigor

`agp_profile` now defaults to 15-minute buckets with a circular weighted
moving-average smoothing over each percentile curve (pure numpy); the legacy
60-min/unsmoothed path is preserved and pinned by test. The local AGP chart
renders the smoothed profile on a continuous fractional-hour axis.

## Single-source-of-truth decision

Python is canonical. The web shell should call a Python compute endpoint (the
existing Vercel Python worker) for the report rather than re-deriving in SQL —
SQL re-derivation already diverged from Python on day-windowing and basal
integration (fixed in Workstream R). Wiring the web shell to consume `CgmReport`
(and adding the clinical summary tiles to both shells) is Workstream V.

## Suite

710 passed, 42 skipped, 48 deselected. Web unchanged. The clinical math runs on
the owner's real data — values are advisory observations, never dosing advice.
