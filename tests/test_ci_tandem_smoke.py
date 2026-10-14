"""Tests for scripts/ci_tandem_smoke.py exit-code semantics and log hygiene."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ingestion import client as ingestion_client
from ingestion import redaction
from ingestion.client import FetchResult
from scripts import ci_tandem_smoke as smoke

SERIAL = "SMOKE-SERIAL-98765432"


@pytest.fixture
def patched_smoke(monkeypatch):
    import logging

    monkeypatch.setenv("TCONNECT_EMAIL", "t@example.com")
    monkeypatch.setenv("TCONNECT_PASSWORD", "pw")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    # configure_logging replaces root handlers; restore afterwards.
    root = logging.getLogger()
    old_handlers, old_level, old_hook = root.handlers[:], root.level, sys.excepthook
    redaction.clear_secrets()

    c = MagicMock()
    c.fetch = MagicMock(
        return_value=FetchResult([MagicMock()], "2026-08-22", chunks_total=1)
    )
    monkeypatch.setattr(ingestion_client, "get_api", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(
        ingestion_client,
        "get_pump_metadata",
        MagicMock(
            return_value=[
                {
                    "serialNumber": SERIAL,
                    "tconnectDeviceId": "dev-id-uuid-0001",
                    "maxDateWithEvents": "2026-08-22T00:00:00",
                }
            ]
        ),
    )
    monkeypatch.setattr(ingestion_client, "fetch_pump_events", c.fetch)
    import ingestion.builders as builders

    monkeypatch.setattr(
        builders,
        "build_all",
        MagicMock(return_value={"cgm": pd.DataFrame({"a": [1]})}),
    )
    yield c
    root.handlers = old_handlers
    root.setLevel(old_level)
    sys.excepthook = old_hook
    redaction.clear_secrets()


def test_success_exits_zero(patched_smoke):
    assert smoke.main() == 0


def test_zero_events_exits_nonzero(patched_smoke):
    patched_smoke.fetch.return_value = FetchResult([], None, chunks_total=1)
    assert smoke.main() != 0


def test_failed_chunk_exits_nonzero_even_with_events(patched_smoke):
    patched_smoke.fetch.return_value = FetchResult(
        [MagicMock()], None, chunks_total=2, chunks_failed=1
    )
    assert smoke.main() != 0


def test_output_never_contains_full_serial(patched_smoke, capsys):
    smoke.main()
    captured = capsys.readouterr()
    assert SERIAL not in captured.out
    assert SERIAL not in captured.err
    assert "…5432" in captured.out


def test_add_mask_only_in_actions(patched_smoke, monkeypatch, capsys):
    smoke.main()
    assert "::add-mask::" not in capsys.readouterr().out

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    smoke.main()
    assert f"::add-mask::{SERIAL}" in capsys.readouterr().out
