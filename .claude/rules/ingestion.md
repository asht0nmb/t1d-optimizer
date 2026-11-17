---
paths:
  - "ingestion/**"
  - "main.py"
  - "scripts/sync_tandem_to_supabase.py"
  - "scripts/sanity_check.py"
  - "scripts/daily_viz.py"
  - "scripts/doctor.py"
  - "tests/test_builders.py"
  - "tests/test_enrich.py"
  - "tests/test_fetch.py"
  - "tests/test_tconnectsync_field_contract.py"
---

# Ingestion and cleaning

- tconnectsync uses Tandem's undocumented API, which changes without notice (it broke on 2026-10-01). Verify field names against the installed tconnectsync classes, not memory. `tests/test_tconnectsync_field_contract.py` pins the field names `builders.py` relies on and has to keep passing.
- A sync that fetches nothing must fail loudly, never exit 0 with "no events". A silent success hid eight weeks of missing data in 2026.
- On-disk parquet is never mutated in place by view code. `ingestion/view_data.ensure_enriched` backfills enrichment in memory for `check` and `viz`.
- `--view original` hides enrichment so outputs can be compared against pre-enrichment behavior. `--view enriched` adds `bolus_category`, `override_delta`, `forced_by_alarm`, site-issue and `cgm_gaps` overlays.
- Read `docs/operating_docs/DATA_ISSUES.md` before changing a builder or adding a cleaning rule. Each known issue there needs its own decision record before code.
- Tandem CSV exports have three sections, each with its own header row, after a 6-line metadata header: EGV (CGM), manual BG (ignored), and bolus data. Parse the sections separately.
- Cross-pump overlaps are handled by fetching everything and then deduplicating. Record any dedup rule's timestamp tolerance in its decision record.
