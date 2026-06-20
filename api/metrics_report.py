"""Vercel Python webhook serving the clinical CGM report (single source of truth).

Deploy with Vercel Root Directory: repository root (.), same project as
``api/index.py`` (the meal-rise cron worker) and ``api/telegram.py``. Reachable
at ``/api/metrics_report`` — ``vercel.json`` already routes ``api/**/*.py`` with
no rewrite needed.

Requires ``Authorization: Bearer <CRON_SECRET>`` (constant-time check, same as
``api/index.py``). Computes the report with :func:`core.metrics.compute_cgm_report`
so the web dashboard and the local Streamlit "Report" page surface identical
numbers — the formulas live in ``core/metrics`` and are never re-derived here.

Query params (GET): ``days`` (default 14), optional ``pump_serial`` and
``end_date`` (ISO ``YYYY-MM-DD``; default today in the configured timezone).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _header_map(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    return {str(k): str(v) for k, v in headers.items()}


def _verify_authorization(headers: dict[str, str]) -> bool:
    secret = os.environ.get("CRON_SECRET", "")
    if not secret:
        return False
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    return hmac.compare_digest(auth, f"Bearer {secret}")


def _json_default(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _parse_params(query: str) -> dict[str, str]:
    parsed = parse_qs(query)
    return {k: v[0] for k, v in parsed.items() if v}


def compute_report_payload(params: dict[str, str]) -> dict[str, Any]:
    """Run config + storage + compute_cgm_report; return a JSON-able dict.

    Pure of HTTP concerns so tests can drive it directly. ``params`` accepts
    ``days``, ``pump_serial`` and ``end_date`` as strings.
    """
    from core.metrics import ReportWindow, compute_cgm_report
    from core.metrics.windows import window_bounds
    from detection.config import get_config

    # Imported here (not at module top) so tests can monkeypatch the symbol
    # before it is resolved, mirroring api/telegram.py's lazy imports.
    from apps.personal.cron.detect_meal_rise import get_storage_connection

    config = get_config()
    tz = config.timezone

    try:
        days = int(params.get("days", "14"))
    except ValueError:
        days = 14
    if days < 1:
        days = 14

    pump_serial = params.get("pump_serial") or None

    end_date_raw = params.get("end_date")
    if end_date_raw:
        end_date = dt.date.fromisoformat(end_date_raw)
    else:
        end_date = dt.datetime.now(ZoneInfo(tz)).date()

    window = ReportWindow(end_date=end_date, days=days, tz=tz)
    since, until = window_bounds(end_date, days, tz=tz)

    storage, closeable = get_storage_connection()
    try:
        cgm = storage.read_table(
            "cgm", since=since, until=until, pump_serial=pump_serial
        )
        report = compute_cgm_report(cgm, config=config, window=window)
    finally:
        if closeable is not None and hasattr(closeable, "close"):
            closeable.close()

    return dataclasses.asdict(report)


def handle_report_request(
    headers: dict[str, str], query: str
) -> tuple[int, dict[str, Any]]:
    """Auth + compute. Returns (HTTP status, JSON-serializable body)."""
    if not _verify_authorization(headers):
        return 401, {"error": "unauthorized"}

    try:
        payload = compute_report_payload(_parse_params(query))
    except Exception as exc:  # pragma: no cover - defensive serverless guard
        return 500, {"error": "report_computation_failed", "detail": str(exc)}

    return 200, payload


class handler(BaseHTTPRequestHandler):
    """Vercel Python entrypoint (class name must be ``handler``)."""

    def do_GET(self) -> None:
        self._serve()

    def do_POST(self) -> None:
        self._serve()

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _serve(self) -> None:
        query = urlparse(self.path).query
        status, body = handle_report_request(_header_map(self.headers), query)
        payload = json.dumps(body, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
