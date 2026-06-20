"""Vercel Python webhook for Telegram commands (deterministic, no LLM).

Deploy with Vercel Root Directory: repository root (.), same project as
``api/index.py`` (the meal-rise cron worker). Reachable at ``/api/telegram``
— ``vercel.json`` already routes ``api/**/*.py`` with no rewrite needed.

Telegram must be configured (setWebhook) with a ``secret_token`` matching
the ``TELEGRAM_WEBHOOK_SECRET`` env var; the handler verifies it on every
request. Only the configured ``TELEGRAM_CHAT_ID`` gets replies.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger(__name__)


def _header_map(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    return {str(k): str(v) for k, v in headers.items()}


def handle_telegram_request(
    headers: dict[str, str], raw_body: bytes
) -> tuple[int, dict[str, Any]]:
    """Auth + parse + dispatch + send. Returns (HTTP status, JSON body)."""
    from apps.personal.cron.detect_meal_rise import (
        get_storage_connection,
        send_telegram_message,
    )
    from apps.personal.telegram.handler import process_webhook
    from detection.config import get_config

    try:
        body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (ValueError, UnicodeDecodeError):
        body = {}

    config = get_config()
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

    def _send(chat_id: str, text: str) -> bool:
        if not bot_token:
            logger.error("TELEGRAM_BOT_TOKEN unset; cannot reply")
            return False
        return send_telegram_message(bot_token, chat_id, text)

    # Pass the connection factory, not an open connection: process_webhook
    # only calls it after the secret + chat-allowlist checks pass, so
    # unauthenticated traffic never opens a pooler connection. It also
    # closes the connection when done.
    return process_webhook(
        body=body,
        headers=headers,
        storage_factory=get_storage_connection,
        config=config,
        send=_send,
    )


class handler(BaseHTTPRequestHandler):
    """Vercel Python entrypoint (class name must be ``handler``)."""

    def do_POST(self) -> None:
        self._serve()

    def do_GET(self) -> None:
        # Telegram only POSTs; a GET is a health probe.
        self._respond(200, {"ok": True, "service": "telegram-commands"})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _serve(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            status, body = handle_telegram_request(_header_map(self.headers), raw)
        except Exception as exc:  # pragma: no cover - defensive serverless guard
            logger.exception("telegram webhook crashed")
            status, body = 200, {"ok": False, "error": "internal"}
        self._respond(status, body)

    def _respond(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
