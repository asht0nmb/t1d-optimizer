"""Vercel Python webhook for meal-rise cron (cron-job.org trigger).

Deploy with Vercel Root Directory: repository root (.).
Requires Authorization: Bearer <CRON_SECRET>.

Vercel discovers this file as a Serverless Function when it exports
``class handler(BaseHTTPRequestHandler)`` (not a plain ``def handler``).
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _header_map(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    if isinstance(headers, dict):
        return {str(k): str(v) for k, v in headers.items()}
    return {str(k): str(v) for k, v in headers.items()}


def _verify_authorization(headers: dict[str, str]) -> bool:
    secret = os.environ.get("CRON_SECRET", "")
    if not secret:
        return False
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    return auth == f"Bearer {secret}"


def handle_cron_request(headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
    """Run auth + cron pipeline; returns (HTTP status, JSON-serializable body)."""
    if not _verify_authorization(headers):
        return 401, {"error": "unauthorized"}

    from apps.personal.cron.detect_meal_rise import run_cron

    try:
        exit_code = run_cron()
    except Exception as exc:  # pragma: no cover - defensive serverless guard
        return 500, {"error": "cron_execution_failed", "detail": str(exc)}

    status = 200 if exit_code == 0 else 500
    return status, {"ok": exit_code == 0, "exit_code": exit_code}


class handler(BaseHTTPRequestHandler):
    """Vercel Python entrypoint (class name must be ``handler``)."""

    def do_GET(self) -> None:
        self._serve()

    def do_POST(self) -> None:
        self._serve()

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _serve(self) -> None:
        status, body = handle_cron_request(_header_map(self.headers))
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
