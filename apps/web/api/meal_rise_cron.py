"""Vercel Python cron entry for live missed-meal (fast-rise) alerting.

Invoked every five minutes by Vercel Cron (see apps/web/vercel.json).
Requires CRON_SECRET via Authorization: Bearer <secret>.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _unauthorized() -> dict[str, Any]:
    return {
        "statusCode": 401,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "unauthorized"}),
    }


def _verify_authorization(headers: dict[str, str]) -> bool:
    secret = os.environ.get("CRON_SECRET", "")
    if not secret:
        return False
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    return auth == f"Bearer {secret}"


def handler(request: Any) -> dict[str, Any]:
    """Vercel serverless entrypoint."""
    headers = getattr(request, "headers", None) or {}
    if isinstance(headers, dict):
        header_map = {str(k): str(v) for k, v in headers.items()}
    else:
        header_map = dict(headers) if headers else {}

    if not _verify_authorization(header_map):
        return _unauthorized()

    from apps.personal.cron.detect_meal_rise import run_cron

    exit_code = run_cron()
    return {
        "statusCode": 200 if exit_code == 0 else 500,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"exit_code": exit_code}),
    }
