"""Tests for the Vercel Python cron entry (auth only)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_CRON_MODULE_PATH = _REPO_ROOT / "apps" / "web" / "api" / "meal_rise_cron.py"
_spec = importlib.util.spec_from_file_location("meal_rise_cron", _CRON_MODULE_PATH)
assert _spec and _spec.loader
meal_rise_cron = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(meal_rise_cron)


def test_handler_rejects_missing_auth(monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    req = SimpleNamespace(headers={})
    out = meal_rise_cron.handler(req)
    assert out["statusCode"] == 401


def test_handler_rejects_wrong_bearer(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "expected")
    req = SimpleNamespace(headers={"authorization": "Bearer wrong"})
    out = meal_rise_cron.handler(req)
    assert out["statusCode"] == 401


def test_handler_accepts_valid_bearer(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "expected")
    monkeypatch.setattr(
        "apps.personal.cron.detect_meal_rise.run_cron",
        lambda: 0,
    )
    req = SimpleNamespace(headers={"Authorization": "Bearer expected"})
    out = meal_rise_cron.handler(req)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["exit_code"] == 0
