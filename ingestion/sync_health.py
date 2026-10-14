"""Data-staleness checks for the nightly Tandem sync.

A green sync run only proves the *job* worked, not that fresh data arrived.
Two upstream conditions have silently starved the dashboards before:

* **Upload staleness** — Tandem has not received a pump upload (the t:connect
  mobile app was closed), so every nightly run re-fetches the same window.
* **Pump CGM gap** — the pump keeps logging basal delivery but no CGM readings
  (e.g. the G7 sensor got unpaired from the pump).

This module holds the pure checks, the typed ``sync_health`` config block, and
a tiny Telegram sender. Alert text deliberately carries no serials, UUIDs or
names: it is built only from durations.

Thresholds come from ``config/user_config.yaml`` (``sync_health:``); the
defaults below only apply when the block is absent so older configs keep
working. ``SyncHealthConfig`` is defined in ``detection.config`` (typed
``AppConfig.sync_health``) and re-exported here.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
import requests

from detection.config import SyncHealthConfig
from ingestion import redaction

logger = logging.getLogger(__name__)

__all__ = [
    "HealthAlert",
    "SyncHealthConfig",
    "check_pump_cgm_gap",
    "check_upload_staleness",
    "parse_upload_time",
    "send_telegram",
]

_TELEGRAM_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class HealthAlert:
    """A data-staleness finding. ``message`` is safe to send and to log."""

    kind: str  # "upload_stale" | "pump_cgm_gap"
    message: str


def parse_upload_time(value: object) -> datetime | None:
    """Parse Tandem's ``lastUploadDate`` defensively; naive values are UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def check_upload_staleness(
    last_upload_date: object, now: datetime, max_age_hours: float
) -> HealthAlert | None:
    """Alert when Tandem's last pump upload is older than ``max_age_hours``.

    An absent / unparseable ``lastUploadDate`` is "unknown", not "stale": it
    logs a warning but does not page.
    """
    uploaded = parse_upload_time(last_upload_date)
    if uploaded is None:
        logger.warning("Active pump has no usable lastUploadDate; upload staleness unknown")
        return None
    age_hours = (now - uploaded).total_seconds() / 3600
    if age_hours < max_age_hours:
        return None
    return HealthAlert(
        kind="upload_stale",
        message=(
            f"Tandem hasn't received a pump upload in ~{round(age_hours)}h "
            "— is the t:connect app open on your phone?"
        ),
    )


def check_pump_cgm_gap(
    cgm: pd.DataFrame | None, basal: pd.DataFrame | None, max_gap_hours: float
) -> HealthAlert | None:
    """Alert when the pump logged basal delivery but no CGM for ``max_gap_hours``.

    Looks at the *trailing* stretch of the fetched window: the time between the
    newest CGM reading (or the start of basal data, if there is none) and the
    newest basal delivery. A gap in the middle that later resumed is already
    captured by the ``cgm_gaps`` table and must not re-page every night.
    """
    if basal is None or basal.empty or "timestamp" not in basal.columns:
        return None
    basal_ts = pd.to_datetime(basal["timestamp"], utc=True).dropna()
    if basal_ts.empty:
        return None
    basal_first, basal_last = basal_ts.min(), basal_ts.max()

    reference = basal_first
    if cgm is not None and not cgm.empty and "timestamp" in cgm.columns:
        cgm_ts = pd.to_datetime(cgm["timestamp"], utc=True).dropna()
        if not cgm_ts.empty:
            reference = max(cgm_ts.max(), basal_first)

    gap_hours = (basal_last - reference).total_seconds() / 3600
    if gap_hours < max_gap_hours:
        return None
    return HealthAlert(
        kind="pump_cgm_gap",
        message=(
            f"Pump logged insulin delivery but no CGM readings for ~{round(gap_hours)}h "
            "— check your G7 is paired to the pump."
        ),
    )


def send_telegram(text: str, *, config: dict | None = None) -> bool:
    """Send ``text`` to the owner's Telegram chat. Returns True on success.

    Credentials come from ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID`` (the
    nightly workflow's secrets), falling back to ``notifications`` in the
    config. Failures log the exception *type* only: a ``requests`` error
    message embeds the request URL, which contains the bot token.
    """
    notifications = (config or {}).get("notifications") or {}
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or notifications.get("telegram_bot_token")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or notifications.get("telegram_chat_id")
    if not token or not chat_id:
        logger.error("Telegram credentials not configured; cannot send health alert")
        return False
    redaction.register_secret(token)
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": str(chat_id), "text": text},
            timeout=_TELEGRAM_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - type-only log, see docstring
        logger.error("Telegram health alert failed to send: %s", type(exc).__name__)
        return False
    logger.info("Telegram health alert sent")
    return True
