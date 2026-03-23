# Diabetes Data Intelligence — Technical Spec & Agent Prompts 
Last updated: 3/21/26
 
## System Overview
 
A Python-based system that will ingest Type 1 diabetes device data (CGM + insulin pump), build event deterction for (missed meals, anomalies), use ML to cluster daily patterns and surface insights through a web dashboard. Using live data and detection models, Telegram will be used for notifications. Two ingestion modes: hitorical (using tconnectsync + CSVs (initially)) and live using pydexcom (for live anomaly detection). Detection engine is source-agnostic.
 
---

## Data Schema

### Source 1: Tandem Basal/Bolus/BG / Control-IQ (via tconnectsync)
- See [docs/DATA_CATALOG.md](docs/DATA_CATALOG.md) for complete field inventory and event type reference.


### Source 2: 'Backup' CSV BG/Bolus data
- Three data sets within each CSV (CGM BG (or EVP) data, manually entered BG data (should be ignored), and bolus data)
    - sets must be extracted individually

### Source 3: Dexcom CGM (pydexcom live)
- frequency: every 5 minutes


---
 
## Detection Logic (Heuristic Starting Points)
All to be determined:
### Meal Detection

### Anomaly Detection

### Daily Clustering

---
 
## Real-Time Detection Constraints
- Trailing window only (no future BG context)
- Confidence threshold must balance false positives (notification fatigue) vs. late alerts (not actionable)
- Telegram notifications will fire when confidence exceeds threshold

---
 
## Config Example (user_config.yaml)
```yaml
bg_targets:
  low: 70
  high: 180
  target: 110
 
meal_detection:
  rise_threshold_per_5min: 8        # mg/dL per interval to trigger
  sustained_intervals: 3             # how many consecutive rising intervals
  no_bolus_window_minutes: 30        # lookback for recent food bolus
  meal_windows:                      # weighted higher during these times
    - [6, 10]
    - [11, 14]
    - [17, 21]
 
anomaly_detection:
  spike_threshold: 180
  drop_threshold: 70
  flatline_tolerance: 2              # mg/dL variance over N readings = suspect
 
clustering:
  method: kmeans
  n_clusters: 5                      # starting point, evaluate and adjust
  feature_mode: aggregated           # or "raw_curve"
 
notifications:
  telegram_bot_token: ""
  telegram_chat_id: ""
  confidence_threshold: 0.75         # minimum confidence to send alert
  cooldown_minutes: 30               # don't re-alert within this window
```
 
---