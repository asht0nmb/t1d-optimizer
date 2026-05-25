# 2026-05-17 — Interactive dashboard audit fixes

## Bugs fixed

- **Crash on Day view:** Plotly 6 removed `go.Figure.from_json`; use `plotly.io.from_json` instead.
- **X-axis / trace mismatch:** `day_xlim` now uses CGM timestamp timezone (e.g. `America/Los_Angeles`) so Plotly range matches data.
- **Performance:** CGM line drawing consolidated to one trace with gap breaks (257 → ~14 traces per day on real data).
- **Navigation:** Jump slider no longer overwrites date picker every rerun; removed duplicate sidebar date control.
- **Heatmap/TIR click-through:** Sync `page_radio` session state when jumping to Day view.
- **Caching:** Single `_cached_day_bundle` returns figure JSON + stats (avoids double `load_view_frames`).

## Follow-up polish (same date)

- **Drop figure caching** — `_cached_day_bundle` removed; figure rebuild is ~30 ms. Frames + windowed CGM are still cached (`_cached_frames`, `_cached_cgm_window`) keyed on `(view, on_disk_pipeline_version)` with a 5 min TTL.
- **Day chart UX:**
  - `hovermode="closest"` (was `"x unified"`) — events with distinct x no longer pile up in one tooltip.
  - Removed permanent text labels on bolus / carb / event / alarm markers; details live in hover.
  - Range bands drawn as soft filled rectangles (`add_hrect`) for low / target / high.
  - Cleaner alarm markers (triangle-down at the top edge), no text overlap.
  - Suspension / gap / site-issue windows use Plotly `add_vrect` labels (no invisible hover-marker dots).
  - X-axis: explicit 2-hour ticks, `%-I %p` formatting on all three rows.
  - Trace count per day: 14 → 6.
- **Heatmap:**
  - Y-axis reversed (00:00 at top).
  - Anchored colorscale with explicit stops at `low`, midpoint, `high`, 250 — hypos render blue, in-range green, highs orange/red.
  - Hover rounds mean BG to integer (`%{z:.0f}`).
  - Monday separator lines.
  - Height scales with hour count; small cell gaps for readability.
  - Fallback **Jump to date** selectbox + button below the chart for when Streamlit's Plotly selection events don't fire.
- **TIR trend:**
  - 70 % goal band + dotted goal line.
  - Markers colored by TIR band (green ≥ 70, amber 50–70, red < 50).
  - Y-axis ticks suffixed `%`; x-axis short month-day labels.
- **Navigation:** Single source of truth for `selected_day` — Prev / date picker / Next in the main toolbar; sidebar shows status only. Programmatic jumps from Heatmap/TIR set `page_radio` so the page tab follows.
- **Streamlit deprecations:** `use_container_width=True` replaced with `width="stretch"` on every `st.plotly_chart` and `st.button`.
- **Plotly toolbar:** removed `lasso2d` / `select2d` (the heatmap uses point clicks), hid Plotly logo, PNG export at 2× scale.
