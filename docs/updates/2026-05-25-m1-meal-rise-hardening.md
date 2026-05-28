# M1 Meal-Rise Hardening & Audit Resolution

**Date:** 2026-05-25

## Summary

Closed the Milestone M1 code-review gaps and successfully resolved the four critical production-blocking issues identified during the line-by-line code audit. This includes achieving idempotent live alerting (claim-before-send), config-driven Dexcom fetch depth, full DST-resilience, serverless connection pooling, correct Parquet fallback instantiation, restored HTML parse mode for Telegram alerts, and expanded test coverage.

## Changes

### 1. Storage & Protocol Invariants
- `record_alert` now returns `AlertInsertResult(record, inserted)` so callers can detect `ON CONFLICT` / deduplication without racing Telegram.

### 2. Live Cron Pipeline (`apps/personal/cron/detect_meal_rise.py`)
- **Idempotency & Race Protection:** `handle_detection_alert` implements: refractory → `find_alert` → claim (`record_alert` pending, `sent_at=now UTC`) → `record_detection_result` → Telegram. Exits cleanly without Telegram or detection write when `inserted=False`.
- **Magic Number Elimination:** Refactored `dexcom_max_count` and `normalize_dexcom_readings` (5-minute buckets) to derive completely from AppConfig.
- **ParquetStorage Fallback Fix:** Instantiating `ParquetStorage()` in the local fallback connection path failed with a `TypeError` due to a missing mandatory `root` parameter. Imported `PROCESSED_DIR` from `ingestion.storage` and correctly passed it: `ParquetStorage(PROCESSED_DIR)`.
- **DST Timezone Resilience:** Flooring localized datetime Series (e.g. `America/Los_Angeles`) directly raised `AmbiguousTimeError`/`NonExistentTimeError` during standard clock change transitions. Converted timestamps to timezone-invariant UTC to perform the 5-minute floor bucketing, keeping the logic 100% immune to DST clocks rolling back or springing forward.
- **Serverless Connection Pooling:** Ephemeral short-lived cron functions manually opening psycopg2 database connections violated pooler transaction-mode URL architecture and risked Supabase pool exhaustion. Refactored the DB connection connection initializer to use the designated transaction pooler entry point: `SupabaseStorage.from_pooler_url(db_url)`.
- **Telegram HTML Formatting:** Omission of parse mode caused inline HTML markup tags in the alert template to render as raw literal strings in user chats. Restored `"parse_mode": "HTML"` directly inside the `requests` HTTP payload structure in `send_telegram_message`.

### 3. Application Config
- `meal_rise.fetch_buffer_minutes`, `expected_interval_minutes`, `fetch_readings_padding` in `config/user_config.yaml`.
- `CONFIG_PATH` resolved from repo root (`detection/config.py`).
- Parser validates `min_coverage`, start-level ordering, refractory, and fetch fields.

### 4. Core & Test Verification
- `ONGOING_GAP_HORIZON` constant in `core/detection/windowing.py`.
- **Expanded Test Suite:** Added tests for `has_gap`, low coverage, slow drift, uneven spacing, cron idempotency, refractory with fixed `now`.
- **New Audit Tests:** Added `test_get_storage_connection_parquet`, `test_get_storage_connection_supabase`, and `test_normalize_dexcom_readings_dst_transition` simulating a clocks-fallback repeated hour (PDT to PST transition) to assert crash-free bucketing and correct connection initialization.

### 5. Vercel Cron Integration
- `apps/web/vercel.json` — cron `*/5 * * * *` → `/api/meal_rise_cron`.
- `apps/web/api/meal_rise_cron.py` — Python handler, `CRON_SECRET` Bearer auth.
- `apps/web/app/api/cron/meal-rise/route.ts` — manual health route with shared auth helper.

---

## Operational notes

- Meal-rise `meal_windows` use **inclusive** hours (`start_hour <= hour <= end_hour`). Legacy `meal_detection` uses half-open `[start, end)` pairs — do not compare priors directly in M2.
- `alerts_sent.delivery` stays `pending` on claim; Telegram outcome is in `detection_results.payload.telegram_sent`.

## Env (cron)

| Variable | Purpose |
|----------|---------|
| `CRON_SECRET` | Bearer token for cron handlers |
| `SUPABASE_DB_URL` | Postgres for `SupabaseStorage` |
| `DEXCOM_USERNAME` / `DEXCOM_PASSWORD` | Live CGM |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Alerts |

---

## Verification Results

- All new tests passed successfully.
- Ran the full engine verification suite:
  ```bash
  uv run pytest
  ```
  **Result:** `540 passed, 43 skipped, 47 deselected, 5 warnings in 1.96s`
