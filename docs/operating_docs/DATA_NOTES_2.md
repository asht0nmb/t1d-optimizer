# Data Notes 2

Domain knowledge from the user — session 4 (2026-03-24). Corrections and refinements from visual verification of pipeline output.

---

## 1. Backfilled CGM readings should use sensor time, not pump-received time

**Previous assumption (DATA_ISSUES #5):** Backfilled readings should use `eventTimestamp` (pump-received time) as the primary timestamp for consistency with how the system operated in real time, with `egvTimestamp` stored as a secondary column.

**Correction:** Backfilled readings (`cgmDataTypeRaw=2`) should use `egvTimestamp` (the actual sensor reading time) as their primary `timestamp`. The `eventTimestamp` (when the pump received the backfill burst) should be stored as a secondary column instead.

**Why:** When the pump is dead (e.g., battery shutdown 08:06–12:03), the sensor continues collecting readings every 5 minutes. On reconnection, all backfilled readings arrive with the same `eventTimestamp` (12:03). Plotting them all at 12:03 creates a false cluster and hides the real BG trajectory during the gap. The sensor times (spread across 08:06–12:03) are what actually happened — the BG rose continuously without insulin.

**How to apply:** In `build_cgm_df`, for backfilled readings (`cgmDataTypeRaw == 2`), use `egvTimestamp.datetime` as the primary `timestamp` column and store `eventTimestamp.datetime` as `pump_received_timestamp`. For live readings (`cgmDataTypeRaw == 1`), continue using `eventTimestamp.datetime` as `timestamp` (sensor_timestamp/pump_received_timestamp can be None or same).

**This only affects backfilled readings.** For all other CGM readings (live, cgmDataTypeRaw=1), the pump-received time IS the real time — the pump gets the reading within seconds of the sensor taking it. There is no meaningful difference between eventTimestamp and egvTimestamp for live readings.
