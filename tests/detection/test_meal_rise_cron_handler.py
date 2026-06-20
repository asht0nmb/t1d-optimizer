"""Tests for the Vercel Python cron entry (auth + response mapping)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_CRON_MODULE_PATH = _REPO_ROOT / "api" / "index.py"
_spec = importlib.util.spec_from_file_location("meal_rise_cron", _CRON_MODULE_PATH)
assert _spec and _spec.loader
meal_rise_cron = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(meal_rise_cron)


def test_handler_class_is_base_http_request_handler():
    from http.server import BaseHTTPRequestHandler

    assert issubclass(meal_rise_cron.handler, BaseHTTPRequestHandler)


def test_verify_authorization_accepts_matching_bearer(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "expected")
    assert meal_rise_cron._verify_authorization({"authorization": "Bearer expected"})


def test_verify_authorization_rejects_wrong_bearer(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "expected")
    assert not meal_rise_cron._verify_authorization({"authorization": "Bearer wrong"})


def test_verify_authorization_rejects_empty_secret(monkeypatch):
    # Fail closed when no secret is configured, even with a Bearer header.
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert not meal_rise_cron._verify_authorization({"authorization": "Bearer anything"})


def test_handle_cron_request_rejects_missing_auth(monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    status, body = meal_rise_cron.handle_cron_request({})
    assert status == 401
    assert body == {"error": "unauthorized"}


def test_handle_cron_request_rejects_wrong_bearer(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "expected")
    status, body = meal_rise_cron.handle_cron_request(
        {"authorization": "Bearer wrong"}
    )
    assert status == 401
    assert body == {"error": "unauthorized"}


def test_handle_cron_request_accepts_valid_bearer(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "expected")
    monkeypatch.setattr(
        "apps.personal.cron.detect_meal_rise.run_cron",
        lambda: 0,
    )
    status, body = meal_rise_cron.handle_cron_request(
        {"Authorization": "Bearer expected"}
    )
    assert status == 200
    assert body["exit_code"] == 0
    assert body["ok"] is True


def test_handle_cron_request_returns_500_for_nonzero_exit(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "expected")
    monkeypatch.setattr(
        "apps.personal.cron.detect_meal_rise.run_cron",
        lambda: 2,
    )
    status, body = meal_rise_cron.handle_cron_request(
        {"Authorization": "Bearer expected"}
    )
    assert status == 500
    assert body["exit_code"] == 2


def test_handle_cron_request_returns_500_when_run_cron_raises(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "expected")

    def _boom() -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr("apps.personal.cron.detect_meal_rise.run_cron", _boom)
    status, body = meal_rise_cron.handle_cron_request(
        {"Authorization": "Bearer expected"}
    )
    assert status == 500
    assert body["error"] == "cron_execution_failed"
    assert "boom" in body["detail"]
