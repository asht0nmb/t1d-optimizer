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
from pydexcom import Dexcom

from core.detection import Anchor, make_window, detect_meal_rise
from core.detection.meal_rise import MealRiseConfig, MealRiseDetection
from core.storage.protocol import Storage
from core.storage.records import AlertRecord, DetectionResult
from detection.config import AppConfig, get_config

MealRiseAlertOutcome = Literal["sent", "suppressed", "failed_config"]

# Load environment
load_dotenv()

logger = logging.getLogger("meal_rise_cron")


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
    out["_bucket"] = out["timestamp"].dt.floor(f"{interval_minutes}min")
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
            import psycopg2
            from core.storage.supabase import SupabaseStorage
            logger.info("Initializing SupabaseStorage connection...")
            conn = psycopg2.connect(db_url, connect_timeout=10)
            return SupabaseStorage(conn=conn), conn
        except ImportError:
            logger.error("psycopg2 is not installed; cannot connect to Supabase.")
            raise
    else:
        from core.storage.parquet import ParquetStorage
        logger.info("Initializing ParquetStorage connection...")
        return ParquetStorage(), None


def fetch_dexcom_cgm(meal_rise_config: MealRiseConfig, tz_name: str) -> pd.DataFrame:
    """Fetch recent CGM display readings from Dexcom Share API.

    Returns a normalized DataFrame with ['timestamp', 'bg_mgdl'] columns,
    deduplicated and sorted ascending.
    """
    username = os.environ.get("DEXCOM_USERNAME") or os.environ.get("TCONNECT_EMAIL")
    password = os.environ.get("DEXCOM_PASSWORD") or os.environ.get("TCONNECT_PASSWORD")
    ous_raw = os.environ.get("DEXCOM_OUS", "false").lower()
    ous = ous_raw in ("true", "1", "yes")

    if not username or not password:
        raise ValueError(
            "Missing Dexcom credentials in environment (DEXCOM_USERNAME / DEXCOM_PASSWORD)"
        )

    logger.info("Connecting to Dexcom Share API (username: %s)...", username)
    dexcom = Dexcom(username, password, ous=ous)

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

    claim_payload = dict(detection.to_payload())
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

    msg = config.meal_rise.alert_template.format(
        start=detection.start_level,
        end=detection.end_level,
        delta=detection.delta,
        minutes=int(detection.minutes_span),
    )

    telegram_sent = send_telegram(msg)
    result_payload = dict(detection.to_payload())
    result_payload["telegram_sent"] = telegram_sent
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
    return "sent"


def run_cron() -> int:
    """Run the live missed-meal alerting cron pipeline."""
    # 1. Load config
    config = get_config()
    tz_name = config.timezone

    # 2. Connect to storage
    try:
        storage, conn = get_storage_connection()
    except Exception as e:
        logger.exception("Failed to connect to storage: %s", e)
        return 1

    try:
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

    finally:
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
