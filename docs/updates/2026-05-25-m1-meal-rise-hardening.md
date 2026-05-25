# M1 meal-rise hardening

**Date:** 2026-05-25

## Summary

Closed the M1 code-review gaps: idempotent live alerting (claim-before-send), config-driven Dexcom fetch depth, expanded detector/cron tests, repo-root config resolution, and Vercel five-minute cron wiring.

## Changes

### Storage

- `record_alert` now returns `AlertInsertResult(record, inserted)` so callers can detect ON CONFLICT / dedup without racing Telegram.

### Live cron (`apps/personal/cron/detect_meal_rise.py`)

- `handle_detection_alert`: refractory → `find_alert` → claim (`record_alert` pending, `sent_at=now UTC`) → `record_detection_result` → Telegram.
- Exits without Telegram or detection write when `inserted=False`.
- `dexcom_max_count` and `normalize_dexcom_readings` (5-minute buckets) remove magic numbers.
- Telegram payload uses plain text (no HTML `parse_mode`).

### Config

- `meal_rise.fetch_buffer_minutes`, `expected_interval_minutes`, `fetch_readings_padding` in `config/user_config.yaml`.
- `CONFIG_PATH` resolved from repo root (`detection/config.py`).
- Parser validates `min_coverage`, start-level ordering, refractory, and fetch fields.

### Core / tests

- `ONGOING_GAP_HORIZON` constant in `core/detection/windowing.py`.
- Tests: `has_gap`, low coverage, slow drift, uneven spacing, cron idempotency, refractory with fixed `now`.

### Vercel

- `apps/web/vercel.json` — cron `*/5 * * * *` → `/api/meal_rise_cron`.
- `apps/web/api/meal_rise_cron.py` — Python handler, `CRON_SECRET` Bearer auth.
- `apps/web/app/api/cron/meal-rise/route.ts` — manual health route with shared auth helper.

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
