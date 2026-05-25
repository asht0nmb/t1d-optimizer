# 2026-05-17 — Interactive Plotly local dashboard

## Summary

Replaced static `st.pyplot` matplotlib rendering in `apps/local/` with Plotly charts: hoverable CGM/bolus/basal/events, zoom/pan, prev/next day navigation, jump slider, interactive heatmap (click cell → day view), and daily TIR trend (click point → day view). Added `plotly` to `[dependency-groups] local`. CLI `main.py viz` remains matplotlib.

## Modules

- `apps/local/chart_prep.py` — day slicing (reuses `daily_viz` helpers)
- `apps/local/charts/{day_view,heatmap,tir_trend}.py`
- `apps/local/navigation.py`
