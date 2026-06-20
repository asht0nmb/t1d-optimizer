"""Live Missed-Meal (Fast-Rise) Alerting Cron Job.

Orchestrates real-time missed-meal detection: polls Dexcom G7/G6 readings
via pydexcom, maps to the windowing primitive, evaluates against the meal-rise
detector, dedupes using the alerts_sent registry, and dispatches Telegram notifications.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
import pandas as pd
import requests
from pydexcom import Dexcom, Region

from core.detection import Anchor, make_window, detect_meal_rise
from core.detection.meal_rise import MealRiseConfig, MealRiseDetection
from core.storage.protocol import Storage
from core.storage.records import AlertRecord, DetectionResult, FetchState

# Heartbeat source id for the live meal-rise worker. A fetch_state row under
# this id is rewritten on every completed cycle so the worker's liveness is
# observable (the /status page reads it); its absence/staleness signals the
# loop has stopped, which the "last detection" signal cannot (detections only
# fire on rises).
_LIVE_CRON_SOURCE = "live_cron"


def _write_live_heartbeat(storage: Any, when: datetime) -> None:
    """Record a liveness heartbeat for the live meal-rise worker."""
    storage.set_fetch_state(
        _LIVE_CRON_SOURCE,
        FetchState(
            source_id=_LIVE_CRON_SOURCE,
            last_cursor=None,
            last_fetched_at=when,
            source_kind="pydexcom",
        ),
    )
from detection.config import AppConfig, get_config

MealRiseAlertOutcome = Literal["sent", "suppressed", "failed_config", "partial_success"]

# Load environment
load_dotenv()

logger = logging.getLogger("meal_rise_cron")
DEFAULT_RETRY_LOOKBACK_HOURS = 24
DEFAULT_RETRY_BACKOFF_MINUTES = 15
DEFAULT_RETRY_MAX_ATTEMPTS = 3


def dexcom_max_count(
    window_minutes: int,
    buffer_minutes: int,
    interval_minutes: int,
    padding: int,
) -> int:
    """Compute how many Dexcom polls to request for a trailing window."""
    fetch_minutes = window_minutes + buffer_minutes
    return int(fetch_minutes // interval_minutes) + padding


def normalize_dexcom_readings(
    df: pd.DataFrame,
    *,
    interval_minutes: int,
) -> pd.DataFrame:
    """Keep one reading per sensor interval bucket (latest timestamp wins)."""
    if df.empty:
        return df
    out = df.copy()
    # Convert to UTC to compute floor buckets, avoiding DST AmbiguousTimeError/NonExistentTimeError
    utc_timestamps = out["timestamp"].dt.tz_convert("UTC")
    out["_bucket"] = utc_timestamps.dt.floor(f"{interval_minutes}min")
    out = (
        out.sort_values("timestamp")
        .groupby("_bucket", as_index=False)
        .last()
        .drop(columns=["_bucket"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    return out


def get_storage_connection() -> Any:
    """Instantiate a concrete Storage implementation based on environment."""
    db_url = os.environ.get("SUPABASE_DB_URL")
    if db_url:
        try:
            from core.storage.supabase import SupabaseStorage
            logger.info("Initializing SupabaseStorage connection via pooler...")
            storage = SupabaseStorage.from_pooler_url(db_url)
            return storage, storage
        except ImportError:
            logger.error("psycopg2 is not installed; cannot connect to Supabase.")
            raise
    else:
        allow_parquet = os.environ.get("MEAL_RISE_ALLOW_PARQUET_FALLBACK", "").lower()
        if allow_parquet not in {"1", "true", "yes"}:
            raise RuntimeError(
                "SUPABASE_DB_URL is required for cron execution. "
                "Set MEAL_RISE_ALLOW_PARQUET_FALLBACK=true only for local testing."
            )
        from core.storage.parquet import ParquetStorage
        from ingestion.storage import PROCESSED_DIR
        logger.info("Initializing ParquetStorage connection...")
        return ParquetStorage(PROCESSED_DIR), None


def fetch_dexcom_cgm(meal_rise_config: MealRiseConfig, tz_name: str) -> pd.DataFrame:
    """Fetch recent CGM display readings from Dexcom Share API.

    Returns a normalized DataFrame with ['timestamp', 'bg_mgdl'] columns,
    deduplicated and sorted ascending.
    """
    username = os.environ.get("DEXCOM_USERNAME") or os.environ.get("TCONNECT_EMAIL")
    password = os.environ.get("DEXCOM_PASSWORD") or os.environ.get("TCONNECT_PASSWORD")
    ous_raw = os.environ.get("DEXCOM_OUS", "false").lower()
    ous = ous_raw in ("true", "1", "yes")
    region = Region.OUS if ous else Region.US

    if not username or not password:
        raise ValueError(
            "Missing Dexcom credentials in environment (DEXCOM_USERNAME / DEXCOM_PASSWORD)"
        )

    logger.info("Connecting to Dexcom Share API (username: %s)...", username)
    dexcom = Dexcom(username=username, password=password, region=region)

    max_count = dexcom_max_count(
        meal_rise_config.window_minutes,
        meal_rise_config.fetch_buffer_minutes,
        meal_rise_config.expected_interval_minutes,
        meal_rise_config.fetch_readings_padding,
    )

    logger.info("Fetching last %d readings from Dexcom...", max_count)
    readings = dexcom.get_glucose_readings(max_count=max_count)
    if not readings:
        logger.warning("No glucose readings returned from Dexcom Share API.")
        return pd.DataFrame(columns=["timestamp", "bg_mgdl"])

    logger.info("Successfully fetched %d readings.", len(readings))

    normalized = []
    tz = ZoneInfo(tz_name)
    for r in readings:
        # r.datetime is typically in naive UTC from the pydexcom library
        # Let's ensure tz-aware representation in America/Los_Angeles (or config tz)
        dt_utc = r.datetime.replace(tzinfo=timezone.utc)
        dt_local = dt_utc.astimezone(tz)
        normalized.append({
            "timestamp": dt_local,
            "bg_mgdl": int(r.value)
        })

    df = pd.DataFrame(normalized)
    if df.empty:
        return df

    df = normalize_dexcom_readings(
        df,
        interval_minutes=meal_rise_config.expected_interval_minutes,
    )
    return df


def send_telegram_message(token: str, chat_id: str, text: str) -> bool:
    """Send a pre-templated message via the Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    logger.info("Sending alert to Telegram chat ID: %s...", chat_id[:5] + "...")
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("Telegram notification sent successfully.")
        return True
    except Exception as e:
        logger.error("Failed to deliver Telegram message: %s", e)
        return False


def _retry_settings(config: AppConfig) -> tuple[timedelta, timedelta, int]:
    meal_rise_raw = config.raw.get("meal_rise", {})
    lookback_hours = int(meal_rise_raw.get("delivery_retry_lookback_hours", DEFAULT_RETRY_LOOKBACK_HOURS))
    backoff_minutes = int(meal_rise_raw.get("delivery_retry_backoff_minutes", DEFAULT_RETRY_BACKOFF_MINUTES))
    max_attempts = int(meal_rise_raw.get("delivery_retry_max_attempts", DEFAULT_RETRY_MAX_ATTEMPTS))
    return timedelta(hours=lookback_hours), timedelta(minutes=backoff_minutes), max_attempts


def _anchor_from_payload(payload: dict[str, Any], fallback: datetime) -> datetime:
    raw_anchor = payload.get("anchor_timestamp")
    if isinstance(raw_anchor, str):
        try:
            anchor = datetime.fromisoformat(raw_anchor)
            if anchor.tzinfo is None:
                return anchor.replace(tzinfo=timezone.utc)
            return anchor
        except ValueError:
            logger.warning("Invalid anchor_timestamp in payload: %s", raw_anchor)
    return fallback


def _delivery_history_by_event_ref(
    storage: Storage,
    *,
    since: datetime,
) -> dict[str, list[DetectionResult]]:
    out: dict[str, list[DetectionResult]] = {}
    results = storage.list_detection_results(kind="meal_rise", since=since, limit=1000)
    for result in results:
        event_ref = result.payload.get("event_ref")
        if not isinstance(event_ref, str):
            continue
        out.setdefault(event_ref, []).append(result)
    return out


def retry_failed_alert_deliveries(
    storage: Storage,
    config: AppConfig,
    *,
    send_telegram: Callable[[str], bool],
    now: datetime | None = None,
) -> dict[str, int]:
    """Retry failed Telegram deliveries for claimed meal-rise alerts."""
    now = now or datetime.now(timezone.utc)
    lookback, backoff, max_attempts = _retry_settings(config)
    history_by_ref = _delivery_history_by_event_ref(storage, since=now - lookback)
    retried = 0
    succeeded = 0

    # Query a wide alert window to avoid coupling retry logic to the storage backend's notion of "now".
    # We apply the actual lookback filter against the injected ``now`` below.
    for alert in storage.recent_alerts("meal_rise", timedelta(days=3650)):
        if alert.sent_at < now - lookback:
            continue
        event_ref = alert.event_ref
        if not event_ref:
            continue

        history = history_by_ref.get(event_ref, [])
        if any(item.payload.get("telegram_sent") is True for item in history):
            continue

        failed_history = [item for item in history if item.payload.get("telegram_sent") is False]
        if not failed_history:
            continue

        attempts = max(
            (
                int(item.payload.get("delivery_attempt", 0))
                for item in history
                if isinstance(item.payload.get("delivery_attempt"), int)
            ),
            default=len(failed_history),
        )
        if attempts >= max_attempts:
            continue

        latest_attempt_at = max(item.created_at for item in history)
        if now - latest_attempt_at < backoff:
            continue

        payload = dict(alert.payload or {})
        message = payload.get("alert_message")
        if not isinstance(message, str) or not message:
            logger.warning("Skipping retry for %s: missing alert_message", event_ref)
            continue

        telegram_sent = send_telegram(message)
        retried += 1
        if telegram_sent:
            succeeded += 1

        payload["event_ref"] = event_ref
        payload["telegram_sent"] = telegram_sent
        payload["delivery_stage"] = "retry"
        payload["delivery_attempt"] = attempts + 1
        if not telegram_sent:
            payload["telegram_error"] = "delivery_failed"
        else:
            payload.pop("telegram_error", None)

        storage.record_detection_result(
            DetectionResult(
                kind="meal_rise",
                anchor_timestamp=_anchor_from_payload(payload, fallback=now),
                payload=payload,
                created_at=now,
            )
        )

    return {"retried": retried, "succeeded": succeeded}


def handle_detection_alert(
    storage: Storage,
    config: AppConfig,
    detection: MealRiseDetection,
    *,
    latest_ts: datetime,
    send_telegram: Callable[[str], bool],
) -> MealRiseAlertOutcome:
    """Dedup, claim, persist detection, and send Telegram for one firing."""
    event_ref = f"meal_rise:{latest_ts.isoformat(timespec='minutes')}"
    refractory_window = timedelta(minutes=config.meal_rise.refractory_minutes)

    if storage.recent_alerts("meal_rise", refractory_window):
        logger.info("Suppressed: within refractory window")
        return "suppressed"

    if storage.find_alert("meal_rise", event_ref):
        logger.info("Suppressed: event_ref already exists: %s", event_ref)
        return "suppressed"

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or config.raw.get(
        "notifications", {}
    ).get("telegram_bot_token")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or config.raw.get(
        "notifications", {}
    ).get("telegram_chat_id")

    if not bot_token or not chat_id:
        logger.error("Missing Telegram configuration; cannot send alert.")
        return "failed_config"

    msg = config.meal_rise.alert_template.format(
        start=detection.start_level,
        end=detection.end_level,
        delta=detection.delta,
        minutes=int(detection.minutes_span),
    )

    claim_payload = dict(detection.to_payload())
    claim_payload["event_ref"] = event_ref
    claim_payload["alert_message"] = msg
    claim_result = storage.record_alert(
        AlertRecord(
            id=None,
            alert_kind="meal_rise",
            event_ref=event_ref,
            sent_at=datetime.now(timezone.utc),
            payload=claim_payload,
            pump_serial=None,
            delivery="pending",
        )
    )
    if not claim_result.inserted:
        logger.info("Suppressed: lost claim race for %s", event_ref)
        return "suppressed"

    telegram_sent = send_telegram(msg)
    result_payload = dict(detection.to_payload())
    result_payload["event_ref"] = event_ref
    result_payload["alert_message"] = msg
    result_payload["telegram_sent"] = telegram_sent
    result_payload["delivery_stage"] = "initial"
    result_payload["delivery_attempt"] = 1
    if not telegram_sent:
        result_payload["telegram_error"] = "delivery_failed"

    storage.record_detection_result(
        DetectionResult(
            kind="meal_rise",
            anchor_timestamp=detection.anchor_timestamp,
            payload=result_payload,
            created_at=datetime.now(timezone.utc),
        )
    )

    logger.info("Alert handled for %s (telegram_sent=%s)", event_ref, telegram_sent)
    if telegram_sent:
        return "sent"
    return "partial_success"


def run_cron(*, now: datetime | None = None) -> int:
    """Run the live missed-meal alerting cron pipeline.

    Args:
        now: Reference "now" for the freshness guard and retry pass. Defaults
            to ``datetime.now(timezone.utc)``; injectable for tests.
    """
    # 1. Load config
    config = get_config()
    tz_name = config.timezone
    now = now or datetime.now(timezone.utc)

    # 2. Connect to storage
    try:
        storage, conn = get_storage_connection()
    except Exception as e:
        logger.exception("Failed to connect to storage: %s", e)
        return 1

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or config.raw.get(
        "notifications", {}
    ).get("telegram_bot_token")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or config.raw.get(
        "notifications", {}
    ).get("telegram_chat_id")

    def _send(msg: str) -> bool:
        if not bot_token or not chat_id:
            return False
        return send_telegram_message(bot_token, chat_id, msg)

    failed = False
    try:
        retry_summary = retry_failed_alert_deliveries(
            storage,
            config,
            send_telegram=_send,
            now=now,
        )
        if retry_summary["retried"] > 0:
            logger.info(
                "Retry delivery pass complete (retried=%d, succeeded=%d).",
                retry_summary["retried"],
                retry_summary["succeeded"],
            )

        # 3. Fetch recent CGM
        cgm_df = fetch_dexcom_cgm(config.meal_rise, tz_name)
        if cgm_df.empty:
            logger.warning("CGM data is empty; exiting.")
            return 0

        # Latest reading
        latest_reading = cgm_df.iloc[-1]
        latest_ts = latest_reading["timestamp"]
        latest_bg = latest_reading["bg_mgdl"]
        logger.info("Latest CGM Reading: %s @ %d mg/dL", latest_ts.isoformat(), latest_bg)

        # 3b. Freshness guard — refuse to act on stale CGM. Dexcom Share can
        # serve an hours-old window when no sensor session is active; without
        # this guard the detector could alert on a long-past rise.
        age_minutes = (now - latest_ts).total_seconds() / 60.0
        if age_minutes > config.meal_rise.max_reading_age_minutes:
            logger.warning(
                "Latest CGM reading is stale (%.1f min old > %d min max); "
                "skipping detection.",
                age_minutes,
                config.meal_rise.max_reading_age_minutes,
            )
            return 0

        # 4. Construct Anchor and Window
        anchor = Anchor(timestamp=latest_ts, kind="live")
        window = make_window(
            cgm_df=cgm_df,
            anchor=anchor,
            pre=timedelta(minutes=config.meal_rise.window_minutes),
            post=timedelta(0)
        )

        logger.info(
            "Window constructed. samples=%d expected_coverage=%.2f gaps=%s",
            window.n_samples,
            window.coverage,
            window.has_gap
        )

        # 5. Evaluate detector
        detection = detect_meal_rise(window, config.meal_rise)
        if detection is None:
            logger.info("No fast rise detected; exiting cleanly.")
            return 0

        logger.info(
            "★ sharp rise detected! slope=%.2f mg/dL/min, delta=+%d (threshold=%.2f)",
            detection.slope_mgdl_per_min,
            detection.delta,
            detection.threshold_used
        )

        outcome = handle_detection_alert(
            storage,
            config,
            detection,
            latest_ts=latest_ts,
            send_telegram=_send,
        )
        if outcome == "failed_config":
            return 1

        logger.info("Cron loop completed (outcome=%s).", outcome)
        return 0

    except Exception as e:
        failed = True
        logger.exception("Meal-rise cron cycle failed: %s", e)
        try:
            _send(
                f"⚠️ Meal-rise cron worker error: {type(e).__name__}. "
                "Live missed-meal alerts may be paused until this clears."
            )
        except Exception:
            logger.warning("Failed to send worker-failure alert", exc_info=True)
        return 1

    finally:
        # Heartbeat on any completed cycle (success or clean no-op exit), but
        # NOT when the cycle raised — the absence of a fresh heartbeat is the
        # liveness signal the /status page watches for.
        if not failed:
            try:
                _write_live_heartbeat(storage, now)
            except Exception:
                logger.warning("live_cron heartbeat write failed", exc_info=True)
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    sys.exit(run_cron())
