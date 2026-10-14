"""Webhook orchestration for the Telegram command surface.

Flow: verify secret header → parse update → enforce chat allowlist →
read storage → build a deterministic digest → send via Telegram.

The pure pieces (auth check, dispatch over an injected ``Storage``) are
unit-testable without network; the Vercel entrypoint in ``api/telegram.py``
wires in the real storage and the real ``send`` function.

LLM assistant (research/2026-08-16 exploration branch, code-complete but
UNWIRED): ``build_reply``/``process_webhook`` both take an optional
``llm_client`` parameter, defaulted to ``None``. When ``None`` (every
deploy today — no ``ANTHROPIC_API_KEY`` is configured anywhere), free-text
input still falls through to ``help_text()`` exactly as before this
parameter existed. Only when a caller explicitly passes a configured
``LLMClient`` (see ``apps/personal/telegram/llm_assistant.py`` for why
that's inert-by-default even in ``api/telegram.py``) does non-command text
route to the conversational assistant instead.
"""

from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from apps.personal.telegram.commands import ParsedCommand, parse_update
from apps.personal.telegram.digest import (
    build_day_digest,
    build_status_digest,
    build_trends_digest,
    help_text,
)
from apps.personal.telegram.llm_assistant import LLMClient, answer_query, build_context
from core.storage.protocol import Storage
from detection.config import AppConfig

logger = logging.getLogger(__name__)

SECRET_HEADER = "x-telegram-bot-api-secret-token"

# How far back to scan meal-rise alerts when counting a single day's alerts
# and when finding the most recent alert for /status.
_ALERT_SCAN = timedelta(days=3650)


def verify_secret(headers: dict[str, str], expected: str) -> bool:
    """Constant-time check of Telegram's secret-token header."""
    if not expected:
        return False
    provided = ""
    for key, value in headers.items():
        if key.lower() == SECRET_HEADER:
            provided = value or ""
            break
    return hmac.compare_digest(provided, expected)


def is_allowed_chat(chat_id: str | None, expected: str) -> bool:
    """True only when the update's chat matches the configured owner chat."""
    if not expected or chat_id is None:
        return False
    return hmac.compare_digest(str(chat_id), str(expected))


def _day_bounds(day, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    return start, start + timedelta(days=1)


def _read_window(storage: Storage, name: str, since, until):
    try:
        return storage.read_table(name, since=since, until=until)
    except Exception:  # cold-start / missing table → empty frame
        logger.warning(
            "telegram: read_table(%s) failed; returning empty frame",
            name,
            exc_info=True,
        )
        import pandas as pd

        return pd.DataFrame()


def _alerts_within(storage: Storage):
    try:
        return storage.recent_alerts("meal_rise", _ALERT_SCAN)
    except Exception:
        logger.warning(
            "telegram: recent_alerts(meal_rise) failed; returning none",
            exc_info=True,
        )
        return []


def _day_reply(
    label: str, day, *, storage: Storage, config: AppConfig, tz: ZoneInfo
) -> str:
    since, until = _day_bounds(day, tz)
    cgm = _read_window(storage, "cgm", since, until)
    bolus = _read_window(storage, "bolus", since, until)
    requests = _read_window(storage, "requests", since, until)
    alerts = _alerts_within(storage)
    alert_count = sum(
        1
        for a in alerts
        if a.sent_at is not None and a.sent_at.astimezone(tz).date() == day
    )
    return build_day_digest(
        label=label,
        day=day,
        cgm=cgm,
        bolus=bolus,
        requests=requests,
        alert_count=alert_count,
        low=config.bg_targets.low,
        high=config.bg_targets.high,
    )


def _trends_reply(
    *, storage: Storage, config: AppConfig, tz: ZoneInfo, now: datetime
) -> str:
    today = now.astimezone(tz).date()
    from apps.personal.telegram.digest import compute_tir

    tir_by_window: dict[int, float | None] = {}
    for window in (7, 14, 30):
        since = datetime(today.year, today.month, today.day, tzinfo=tz) - timedelta(
            days=window - 1
        )
        until = datetime(today.year, today.month, today.day, tzinfo=tz) + timedelta(
            days=1
        )
        cgm = _read_window(storage, "cgm", since, until)
        bg = cgm["bg_mgdl"] if "bg_mgdl" in cgm.columns else None
        tir_by_window[window] = (
            compute_tir(bg, low=config.bg_targets.low, high=config.bg_targets.high)
            if bg is not None
            else None
        )
    return build_trends_digest(tir_by_window)


def _status_reply(*, storage: Storage, now: datetime) -> str:
    try:
        cgm = storage.read_all_table("cgm")
        latest_cgm = (
            cgm["timestamp"].max()
            if "timestamp" in cgm.columns and not cgm.empty
            else None
        )
    except Exception:
        logger.warning(
            "telegram: read_all_table(cgm) failed for /status", exc_info=True
        )
        latest_cgm = None
    latest_cgm_ts = latest_cgm.to_pydatetime() if latest_cgm is not None else None

    detections = []
    try:
        detections = storage.list_detection_results(kind="meal_rise", limit=1)
    except Exception:
        logger.warning(
            "telegram: list_detection_results(meal_rise) failed for /status",
            exc_info=True,
        )
        detections = []
    latest_detection_ts = detections[0].created_at if detections else None

    alerts = _alerts_within(storage)
    latest_alert = max(alerts, key=lambda a: a.sent_at, default=None)
    return build_status_digest(
        latest_cgm_ts=latest_cgm_ts,
        latest_detection_ts=latest_detection_ts,
        latest_alert_ts=latest_alert.sent_at if latest_alert else None,
        latest_alert_delivery=latest_alert.delivery if latest_alert else None,
        now=now,
    )


def _llm_reply(parsed: ParsedCommand, *, storage: Storage, config: AppConfig, now: datetime, llm_client: LLMClient) -> str:
    """Route free-text (non-command) input to the LLM assistant.

    Never lets an LLM-path failure crash the webhook — a network error, a
    missing/invalid API key, or a malformed response from the model all
    fall back to `help_text()` rather than surfacing an exception to the
    Telegram chat or (worse) to `process_webhook`'s generic 200/internal-
    error path. See `apps/personal/telegram/llm_assistant.py`'s module
    docstring for the context-assembly and Protocol design this calls into.
    """
    try:
        context = build_context(storage=storage, config=config, now=now)
        return answer_query(parsed.raw_text or "", context=context, client=llm_client)
    except Exception:
        logger.warning("telegram: LLM assistant path failed; falling back to help", exc_info=True)
        return help_text()


def build_reply(
    parsed: ParsedCommand,
    *,
    storage: Storage,
    config: AppConfig,
    now: datetime,
    llm_client: LLMClient | None = None,
) -> str:
    """Map a known command to its reply text.

    Unknown/free-text input → the LLM assistant when `llm_client` is
    provided (research branch, inert by default — see module docstring);
    otherwise → `help_text()`, exactly as before that feature existed.
    """
    tz = ZoneInfo(config.timezone)
    today = now.astimezone(tz).date()
    if parsed.command == "today":
        return _day_reply("Today", today, storage=storage, config=config, tz=tz)
    if parsed.command == "yesterday":
        return _day_reply(
            "Yesterday", today - timedelta(days=1), storage=storage,
            config=config, tz=tz,
        )
    if parsed.command == "trends":
        return _trends_reply(storage=storage, config=config, tz=tz, now=now)
    if parsed.command == "status":
        return _status_reply(storage=storage, now=now)

    # parsed.command is None here: either no text, an unrecognized
    # /command (still help — we never treat a typo'd command as a
    # conversational question), or genuine free text. Only the last case,
    # with an LLM client actually configured, goes to the assistant.
    is_free_text = (
        parsed.raw_text is not None
        and parsed.raw_text.strip() != ""
        and not parsed.raw_text.strip().startswith("/")
    )
    if is_free_text and llm_client is not None:
        return _llm_reply(parsed, storage=storage, config=config, now=now, llm_client=llm_client)
    return help_text()


def process_webhook(
    *,
    body: dict,
    headers: dict[str, str],
    storage_factory: Callable[[], Storage],
    config: AppConfig,
    send: Callable[[str, str], bool],
    now: datetime | None = None,
    llm_client: LLMClient | None = None,
) -> tuple[int, dict[str, Any]]:
    """Full request handling. Returns ``(http_status, json_body)``.

    Bad secret → 401. Wrong/unknown chat → 200 with no reply (we never
    leak that the bot exists). Both auth checks run BEFORE
    ``storage_factory`` is called, so unauthenticated traffic never opens
    a database connection. The factory's result is closed (if it exposes
    ``close``) before returning.

    ``llm_client``: optional, defaults to ``None`` (research branch,
    inert by default — see module docstring and
    ``apps/personal/telegram/llm_assistant.py``). Passed straight through
    to ``build_reply``.
    """
    expected_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not verify_secret(headers, expected_secret):
        return 401, {"error": "unauthorized"}

    parsed = parse_update(body)
    allowed_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not is_allowed_chat(parsed.chat_id, allowed_chat):
        # Silently accept so unknown chats learn nothing.
        return 200, {"ok": True, "replied": False}

    now = now or datetime.now(ZoneInfo(config.timezone))
    storage = storage_factory()
    try:
        reply = build_reply(
            parsed, storage=storage, config=config, now=now, llm_client=llm_client
        )
    except Exception:  # never leak internals to the chat
        logger.exception("telegram command failed")
        return 200, {"ok": False, "replied": False, "error": "internal"}
    finally:
        close = getattr(storage, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass

    sent = send(str(parsed.chat_id), reply)
    return 200, {"ok": True, "replied": bool(sent), "command": parsed.command}
