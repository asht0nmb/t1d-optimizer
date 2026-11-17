---
paths:
  - "core/metrics/**"
  - "detection/**"
  - "core/detection/**"
  - "tests/core/**"
  - "tests/test_metrics_*.py"
  - "apps/web/lib/agp.ts"
  - "docs/decisions/**"
---

# Metrics and detection

- Each metric has exactly one definition, implemented once in `core/metrics/`. Other surfaces (web SQL, Streamlit, Telegram, the AI layer) consume or mirror it. They never re-derive it.
- A metric's implementation must match its record in `docs/decisions/`. If the code and the record disagree, stop and flag it. Don't silently pick one.
- Cite the source of a published metric (paper, consensus statement or Dexcom definition) in its docstring and decision record.
- Tests use small synthetic series with answers worked out by hand. Property tests (hypothesis) are welcome on top of those, never instead.
- Filter CGM sentinel and out-of-range values before any metric is computed. The cleaning rule lives in one place, and metrics never re-implement it.
- Report the data sufficiency (minutes or percentage of valid CGM) alongside every windowed metric. A metric over too little data returns None, never a number.
- Known open issues: MAGE in `core/metrics/variability.py` is a simplified variant with a weak test, and web AGP (`apps/web/lib/agp.ts`, hourly bins) doesn't match Python AGP (`core/metrics/agp.py`, 15-minute smoothed). Fix either one only through a decision record.
- Detection runs offline, as batch metrics over history. Anything that also runs in real time must use only a trailing window, with no future readings.
- `detection/` is DataFrame in, DataFrame out. No `ingestion/` imports.
