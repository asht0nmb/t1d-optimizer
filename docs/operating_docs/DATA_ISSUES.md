# Data Issues & Observations

Findings from real-data verification against the Tandem t:connect app.

> **2026-04-20 update:** All 6 issues below are resolved in the current pipeline. See commit SHAs on each item. Issue #5's timestamp recommendation was superseded by `DATA_NOTES_2.md`.

---

## 1. Stale CGM readings on sensor reconnection

**Observed:** 2026-03-19 at 12:03 — two CGM readings logged 1 second apart after a ~4-hour gap (pump battery died at 08:06, suspension logged as `reason=alarm`):

```
2026-03-19 12:03:15  →  344 mg/dL  (real, current reading)
2026-03-19 12:03:16  →  136 mg/dL  (stale, cached value from before the gap)
```

**Cause:** When the pump loses power or CGM connection, it caches the last known BG value. On reconnection, it sometimes logs both the new reading and the stale cached value within 1 second. The Tandem app silently discards these; our pipeline does not.

**Impact:** The stale reading appears on the CGM trace as a false data point (136 plotted at noon when BG was actually 344). It also skews daily statistics (mean, TIR, SD).

**Severity:** This is not a rare edge case. Mar 18 alone had **5 stale readings** at 02:31, 04:51, 09:57, 10:01, and 16:01 — every CGM gap produced at least one. The stale values can be dramatically wrong (e.g., 355 → 126, a 229 mg/dL error). On a bad day with multiple gaps, this significantly corrupts aggregate statistics.

**When this can occur:**
- Pump battery dies (as in this case — suspension with `reason=alarm`)
- CGM sensor loses signal and reconnects
- Bluetooth connection drops between pump and sensor
- Control-IQ enters "pining" state (losing/regaining CGM signal)
- Any scenario where the pump has a data gap followed by reconnection

**Fix needed:** Filter CGM readings in `build_cgm_df` to drop entries that are <60 seconds apart from an adjacent reading, keeping the first. Current dedup is exact-timestamp only.

**Status: Resolved in `ee80fca`.** `build_cgm_df` now drops live readings that arrive <60 s after an adjacent reading (keeping the first); backfilled rows are exempt from the filter since they legitimately arrive out of order. The same commit also added `LidUsbConnected`/`LidUsbDisconnected` handling so pump connect/disconnect events no longer trigger unknown-event warnings.

---

## 2. Alarms and alerts are not surfaced in any DataFrame

**Observed:** 2026-03-19 had 27 alarm/alert events that are completely invisible in the pipeline output. The builder imports `LidAlarmActivated`, `LidAlarmCleared`, `LidAlertActivated`, `LidAlertCleared`, and the CGM alert variants, but only adds them to `_HANDLED_TYPES` to suppress warnings — they are never written to `events.parquet` or any other DataFrame.

**What's being lost (Mar 19 example):**

| Time | Type | Detail |
|------|------|--------|
| 07:34 | LowPowerAlert cleared → LowPowerAlert2 activated | Battery warning escalation |
| 08:06 | **BatteryShutdownAlarm** (id=12) | Pump died. Caused 227-min suspension |
| 08:06 | ResumePumpAlarm2 (id=23) | Paired with battery shutdown |
| 11:36 | PumpResetAlarm (id=3) | Pump rebooted after charging |
| 12:03 | CGM alert (dalertidRaw=2, unmapped) | G7 sensor reconnecting |
| 15:10 | IncompleteBolusAlert (id=11) | Bolus didn't fully deliver |
| 22:36 | **OcclusionAlarm** (id=2) | Caused 20-second suspension |

**Available data on alarm/alert events:**
- `LidAlarmActivated`: `alarmidRaw` (int), `alarmid` (enum name like `OcclusionAlarm`, `BatteryShutdownAlarm`), `param1`, `param2`, timestamp
- `LidAlertActivated`: `alertidRaw` (int), `alertid` (enum name like `LowPowerAlert`, `IncompleteBolusAlert`), `param1`, `param2`, timestamp
- `LidCgmAlertActivatedDex`: `dalertidRaw` (int), `dalertid` (enum — but dalertidRaw=2 is unmapped in tconnectsync, returns None), `sensortype`, timestamp
- All three have corresponding `Cleared` events for pairing activated→cleared duration

**Alarm ID map (from tconnectsync):**
- 0: CartridgeAlarm
- 2: OcclusionAlarm
- 3: PumpResetAlarm
- 7: AutoOffAlarm
- 8: EmptyCartridgeAlarm
- 10: TemperatureAlarm
- 12: BatteryShutdownAlarm
- 21: AltitudeAlarm
- 23: ResumePumpAlarm2
- 25: CartridgeRemovedAlarm
- 26: OcclusionAlarm2

**Include in pipeline: YES — critical.** Alarms and alerts directly affect insulin delivery (suspensions, basal reversion, incomplete boluses). Without them, the detection engine can't distinguish "pump chose not to deliver" from "pump was physically unable to deliver." Build into a new `alarms.parquet` with columns: timestamp, category (`alarm`/`alert`/`cgm_alert`), action (`activated`/`cleared`), alarm_id, alarm_name, param1, param2, pump_serial.

**Status: Resolved in `414432c`.** `alarms.parquet` is now built from `LidAlarmActivated/Cleared`, `LidAlertActivated/Cleared`, and `LidCgmAlertActivatedDex/ClearedDex` with the full schema described above (timestamp, category, action, alarm_id, alarm_name, param1, param2, pump_serial). Downstream viz (`c6320d8`) renders alarm and alert markers on the daily CGM trace.

---

## 3. Suspensions don't identify the specific alarm that caused them

**Observed:** `LidPumpingSuspended` only has `suspendreasonRaw` which maps to generic reasons: `user` (0), `alarm` (1), `malfunction` (2), `plgs_auto` (6). When a suspension is caused by an alarm, there is **no field on the suspension event** that identifies which specific alarm triggered it.

**Example from Mar 19:**
- 08:06 suspension: `reason=alarm` — actually caused by `BatteryShutdownAlarm`
- 22:36 suspension: `reason=alarm` — actually caused by `OcclusionAlarm`

Both show identically as `suspend_reason=alarm` in `suspension.parquet`. The critical clinical difference (battery death vs. occlusion) is invisible.

**Correlation strategy:** `LidAlarmActivated` events fire at the **exact same timestamp** as `LidPumpingSuspended` when the alarm causes the suspension. The specific alarm can be identified by matching on timestamp:

```
22:36:35  LidAlarmActivated  alarmidRaw=2  OcclusionAlarm
22:36:35  LidPumpingSuspended  reason=alarm
```

**Fix needed:** During `build_suspension_df`, cross-reference `LidAlarmActivated` events to enrich suspensions with the specific alarm name. Add columns:
- `alarm_id` (int or NaN): The `alarmidRaw` from the matched alarm event
- `alarm_name` (str or None): Human-readable name like `"occlusion"`, `"battery_shutdown"`, `"auto_off"`

This replaces the generic `suspend_reason=alarm` with actionable information like `suspend_reason=occlusion`.

**Include in pipeline: YES — enrich existing suspensions.** Not a new DataFrame, just additional columns on `suspension.parquet`. The alarm name turns a generic "alarm" suspension into clinically actionable data (occlusion vs battery death vs auto-off are very different situations).

**Status: Resolved in `ee80fca`.** `build_suspension_df` now cross-references `LidAlarmActivated` events at the same timestamp and populates `alarm_id` and `alarm_name` columns on `suspension.parquet`, so `reason=alarm` suspensions are distinguishable (e.g., `occlusion` vs `battery_shutdown`).

---

## 4. DefaultAlert50 and DefaultAlert51 are unmapped high/low BG alerts

tconnectsync maps alert IDs 0–49 to named enums but IDs 50+ fall through as `DefaultAlertNN`. Cross-referencing all activations against CGM readings reveals their meaning:

**Alert51 (param2=873) = Low BG alert**
Fires when BG is near or at ~70-80 mg/dL and dropping. Confirmed across 5 activations:
- BG at activation: 73, 136 (stale — actual was lower), 68, 82, 81 mg/dL
- Cleared when BG recovers above threshold

**Alert50 (param2=862) = High BG alert**
Fires when BG exceeds ~200 mg/dL. Confirmed across 19 activations:
- BG at activation: 201–315 mg/dL (every single one ≥200)
- Very frequent — fires 10+ times across a bad 5-day stretch

**The param2 values (862, 873) are not mg/dL** — they appear to be pump-internal threshold encodings, constant across all activations of the same alert type.

**Also observed: CGM alert dalertidRaw=2 is unmapped**
`LidCgmAlertActivatedDex` with `dalertidRaw=2` fires frequently (tconnectsync only maps IDs 11, 13, 14, 20, 26, 27, 39, 40). Based on timing, it appears to be a **CGM high glucose alert** from the Dexcom G7 sensor itself (distinct from the pump-side Alert50). Its `param1` field contains the BG value at activation and `param2=180.0` which is likely the threshold.

**Full CGM alert map (dalertidRaw, all unmapped by tconnectsync):**
- dalertidRaw=1 → `"cgm_urgent_low"` — fires at BG ~49, threshold 55 (param2). Rare.
- dalertidRaw=2 → `"cgm_high"` — fires at BG ≥180, threshold 180 (param2). 29 activations in 5 days.
- dalertidRaw=3 → `"cgm_low"` — fires at BG ≤75, threshold 75 (param2). 8 activations.
- dalertidRaw=6 → `"cgm_rise_rate"` — rapid rise alert. param1 ~30-36 (rate?), param2=3.0 (mg/dL/min threshold).
- dalertidRaw=8 → `"cgm_fall_rate"` — rapid fall alert. param1 is uint overflow (negative rate), param2=3.0.
- dalertidRaw=14 → `"cgm_out_of_range"` — **CGM signal lost.** 37 activations in 5 days, durations 4–150 min. param1=20 (timeout minutes). Pump reverts to base basal rate during these windows.

**Include in pipeline: YES — all of the above.** These should be in the `alarms.parquet` alongside pump-side alarms/alerts. The CGM out-of-range events (dalertidRaw=14) are especially critical — they define windows where the pump reverts to profile basal and the detection engine must know this. The BG threshold alerts (1, 2, 3) duplicate pump-side Alert50/51 but carry the actual BG value at activation in param1, which is more useful. The rate alerts (6, 8) are the only source for rapid-change detection from the sensor side.

Map during building:
- alertidRaw=50 → `"high_bg_alert"` (pump-side)
- alertidRaw=51 → `"low_bg_alert"` (pump-side)
- All dalertidRaw values above → named CGM alerts with thresholds

**Status: Resolved in `414432c`.** The alarms builder applies the full name map above when writing `alarms.parquet`: pump-side `alertidRaw` 50/51 become `high_bg_alert`/`low_bg_alert`, and unmapped CGM `dalertidRaw` values (1, 2, 3, 6, 8, 14) are named `cgm_urgent_low`, `cgm_high`, `cgm_low`, `cgm_rise_rate`, `cgm_fall_rate`, and `cgm_out_of_range` respectively.

---

## 6. CGM out-of-range windows not tracked

**Observed:** `dalertidRaw=14` (OutOfRange) fires 37 times in a 5-day window, with durations from 4 to 150 minutes. During these windows, the pump has no CGM data and reverts to the programmed basal profile rate — Control-IQ cannot adjust.

This is distinct from the pump being dead (BatteryShutdownAlarm). During out-of-range:
- The pump is running and delivering insulin, but only at the base profile rate
- The sensor is still collecting readings (which get backfilled later as `cgmDataTypeRaw=2`)
- Control-IQ cannot issue auto corrections or adjust basal

**These windows are a key signal for the detection engine.** Any basal analysis during an out-of-range window should recognize that `rate_source=profile` is forced (not a user/algorithm choice), and any BG excursions during this window are happening without algorithmic intervention.

**Currently not surfaced** — dalertidRaw=14 is in `_HANDLED_TYPES` but never written to a DataFrame.

**Fix needed:** Surface CGM out-of-range episodes (activated→cleared pairs with duration) in the events or alarms DataFrame. These need to be cross-referenced with basal data to flag profile-rate entries that are forced by signal loss vs. intentional.

**Include in pipeline: YES — critical.** These episodes define windows where Control-IQ is blind. They belong in `alarms.parquet` as paired activated/cleared rows (same as other alarms), but the detection engine should also derive a convenience view of out-of-range windows with start/end/duration for cross-referencing against basal and CGM data.

**Status: Resolved in `414432c` (capture) and `c6320d8` (viz).** `alarms.parquet` now contains paired activated/cleared rows for `dalertidRaw=14` with `alarm_name="cgm_out_of_range"`, and `daily_viz` renders these windows as gray spans on the CGM trace. **Future enhancement:** a dedicated out-of-range episode view (start/end/duration derived from activated→cleared pairs) is not yet built; the detection engine currently has to derive episodes from the paired rows itself.

---

## 5. Backfilled CGM readings are present but dropped by the builder

**Observed:** When the pump loses connection to the CGM sensor (battery death, out of range, etc.), the Dexcom G7 sensor continues collecting BG readings independently. When the connection restores, the sensor backfills up to 3 hours of stored readings in a single burst. These readings are in the API response but our pipeline discards them.

**How it works in the raw data:**
- **Live readings**: `cgmDataTypeRaw=1`, `eventTimestamp` is the real reading time, `interval=0`
- **Backfilled readings**: `cgmDataTypeRaw=2`, `eventTimestamp` is the reconnection time (all identical), `egvTimestamp` is the **actual sensor reading time**, `interval` = number of 5-min periods ago

**Example from Mar 18** (pump dead 05:06–09:57):
- At 09:57:18, 36 backfilled readings arrived all at once with `cgmDataTypeRaw=2`
- Their `egvTimestamp` values decode to actual times from 06:56 to 09:51
- BG during dead period: 168→349 mg/dL (rose continuously without insulin)
- Our builder uses `eventTimestamp` for all CGM events, so all 36 readings get timestamp 09:57:18 and dedup to 1

**This is the data the Tandem app shows with the "out of range" visual styling** — the readings are real sensor data, just not available to the pump in real time.

**Timestamp resolution:** Although `egvTimestamp` reflects when the sensor actually took the reading, `eventTimestamp` is when the pump received it — and that's what the pump bases decisions on. The pipeline should resolve to `eventTimestamp` for consistency with how the system operated in real time. The `egvTimestamp` should be stored as a secondary column (e.g., `sensor_timestamp`) for analysis purposes, but `timestamp` should remain `eventTimestamp`.

**Only two cgmDataTypeRaw values exist:** `1` (live) and `2` (backfill). No other values observed across 5 days of data (1971 total CGM events).

**Scale of impact:** In this 5-day sample, **596 of 1971 CGM readings (30%) are backfilled**. This is not an edge case — it's a major data source. Without capturing these, any day with a pump disconnection loses up to 3 hours of CGM data, distorting daily statistics and creating false gaps in the CGM trace.

**Fix needed in `build_cgm_df`:**
1. Check `cgmDataTypeRaw` on each CGM event
2. For backfilled readings (`cgmDataTypeRaw == 2`), still use `eventTimestamp` as the primary `timestamp` but store the decoded `egvTimestamp` as `sensor_timestamp`
3. Add a `backfilled` boolean column (True for type 2) so downstream consumers know these readings weren't available to the pump in real time
4. Backfilled readings currently all share the same `eventTimestamp` (the reconnection moment), so they dedup to 1 row. The dedup key must include `seqNum` or `egvTimestamp` to preserve them

**Include in pipeline: YES — 30% of all CGM data.** These readings are real sensor measurements. Dropping them creates false gaps and distorts daily statistics.

**Status: Resolved in `35041db` (supersedes `ec6d18f`).** `build_cgm_df` now preserves `cgmDataTypeRaw=2` rows (dedup key includes `seqNum`/`egvTimestamp`) and adds a `backfilled` boolean column; backfilled readings are also exempt from the <60 s stale filter from Issue #1.

**Correction:** The timestamp recommendation in the box above (use `eventTimestamp` as primary, store `egvTimestamp` as `sensor_timestamp`) was **reversed** by `35041db`. See `docs/operating_docs/DATA_NOTES_2.md` for the rationale. In the shipped pipeline, backfilled readings use `egvTimestamp` (sensor time, when the reading was actually taken) as the primary `timestamp`, and `sensor_timestamp` stores `eventTimestamp` (the pump-received time). Live readings (`cgmDataTypeRaw=1`) are unchanged — they still use `eventTimestamp`.
