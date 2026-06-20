"""Tests for the Vercel Python metrics-report entry (auth + report JSON).

Mirrors tests/detection/test_meal_rise_cron_handler.py: drives the handler's
request-handling function with a bearer header and an InMemoryStorage
(get_storage_connection monkeypatched), and checks the 200/401 contract.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.storage.memory import InMemoryStorage

_MODULE_PATH = _REPO_ROOT / "api" / "metrics_report.py"
_spec = importlib.util.spec_from_file_location("metrics_report", _MODULE_PATH)
assert _spec and _spec.loader
metrics_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(metrics_report)


def _storage_with_full_window(end_date: dt.date, days: int) -> InMemoryStorage:
    """Build an InMemoryStorage with a dense CGM window meeting sufficiency.

    One reading every 5 minutes across ``days`` UTC days ending on ``end_date``
    — enough coverage for GMI/GRI to be reportable.
    """
    storage = InMemoryStorage()
    start = dt.datetime.combine(
        end_date - dt.timedelta(days=days - 1),
        dt.time(0, 0),
        tzinfo=dt.timezone.utc,
    )
    rows = []
    total_minutes = days * 24 * 60
    for i, minute in enumerate(range(0, total_minutes, 5)):
        ts = start + dt.timedelta(minutes=minute)
        rows.append(
            {
                "pump_serial": "PUMP1",
                "seqnum": i,
                "timestamp": ts,
                "bg_mgdl": 120.0,
            }
        )
    storage.upsert_table("cgm", pd.DataFrame(rows))
    return storage


def test_handler_class_is_base_http_request_handler():
    from http.server import BaseHTTPRequestHandler

    assert issubclass(metrics_report.handler, BaseHTTPRequestHandler)


def test_handle_report_request_rejects_missing_auth(monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    status, body = metrics_report.handle_report_request({}, "days=14")
    assert status == 401
    assert body == {"error": "unauthorized"}


def test_handle_report_request_rejects_wrong_bearer(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "expected")
    status, body = metrics_report.handle_report_request(
        {"authorization": "Bearer wrong"}, "days=14"
    )
    assert status == 401
    assert body == {"error": "unauthorized"}


def test_handle_report_request_returns_report_json(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "expected")
    end_date = dt.date(2025, 6, 1)
    storage = _storage_with_full_window(end_date, days=14)

    monkeypatch.setattr(
        "apps.personal.cron.detect_meal_rise.get_storage_connection",
        lambda: (storage, None),
    )

    status, body = metrics_report.handle_report_request(
        {"Authorization": "Bearer expected"},
        f"days=14&end_date={end_date.isoformat()}",
    )

    assert status == 200
    # Core report fields present.
    for key in ("gri", "gmi", "tir", "tbr2", "tar2", "mean_bg", "lbgi", "hbgi"):
        assert key in body
    assert body["days"] == 14
    # handle_report_request returns the raw dataclass dict; date → ISO happens
    # at JSON-encode time in _serve (covered by the serialization test below).
    assert body["end_date"] == end_date
    # A dense, in-range window meets sufficiency, so GMI/GRI are reportable.
    assert body["meets_sufficiency"] is True
    assert body["gmi"] is not None
    assert body["gri"] is not None
    assert body["tir"] == 100.0


def test_compute_report_payload_serializes_dates(monkeypatch):
    """end_date must round-trip to an ISO string (dataclasses.asdict + default)."""
    import json

    monkeypatch.setenv("CRON_SECRET", "expected")
    end_date = dt.date(2025, 6, 1)
    storage = _storage_with_full_window(end_date, days=14)
    monkeypatch.setattr(
        "apps.personal.cron.detect_meal_rise.get_storage_connection",
        lambda: (storage, None),
    )

    payload = metrics_report.compute_report_payload(
        {"days": "14", "end_date": end_date.isoformat()}
    )
    # The whole payload must be JSON-encodable via the handler's default hook.
    encoded = json.dumps(payload, default=metrics_report._json_default)
    assert end_date.isoformat() in encoded
