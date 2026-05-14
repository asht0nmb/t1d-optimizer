detection/legacy
Reference implementation from detection v1.

Not maintained. No new features land here; bugs are not fixed.
Not imported from production code. Any module in ingestion/, scripts/, or the rest of detection/ that imports from detection.legacy.* is a review-blocking bug.
Preserved for v2 design reference. Read it, copy patterns, diff v2 outputs against it from notebooks. Delete when v2 fully supersedes its functionality.
Public surface at the time of quarantine:

detection.legacy.anomaly.detect_anomalies(cgm_df, AppConfig) -> DataFrame — spike/drop crossings + rolling-variance flatline.
detection.legacy.meal.detect_meals(cgm_df, requests_df, AppConfig) -> DataFrame — sustained-rise candidates suppressed by food-carrying boluses.
detection.legacy.clustering.cluster_days(features_df, AppConfig, retrain=False) -> DataFrame — StandardScaler + KMeans with persisted artefacts.
See docs/plans/2026-05-05-detection-rework-and-surfaces.md for why v1 is being replaced and what v2 is targeting.
