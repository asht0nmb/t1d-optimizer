"""The live cron must never log the Dexcom account identifier.

The Dexcom username is usually an email address. CLAUDE.md forbids logging
account identifiers because the repo and its Actions logs are public.
Synthetic username only; nothing touches Dexcom.
"""

from __future__ import annotations

import logging

import pytest

from apps.personal.cron import detect_meal_rise as cron
from detection.config import load_config

SYNTHETIC_USERNAME = "synthetic.user@example.invalid"


class _FakeDexcom:
    def __init__(self, username, password, region):
        pass

    def get_glucose_readings(self, max_count):
        return []


@pytest.mark.parametrize("env_var", ["DEXCOM_USERNAME", "TCONNECT_EMAIL"])
def test_connect_message_does_not_contain_username(monkeypatch, caplog, capsys, env_var):
    for var in ("DEXCOM_USERNAME", "TCONNECT_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(env_var, SYNTHETIC_USERNAME)
    monkeypatch.setenv("DEXCOM_PASSWORD", "synthetic-password")
    monkeypatch.setattr(cron, "Dexcom", _FakeDexcom)
    config = load_config()

    with caplog.at_level(logging.DEBUG):
        cron.fetch_dexcom_cgm(config.meal_rise, config.timezone)

    connect = [r for r in caplog.records if "Connecting to Dexcom" in r.getMessage()]
    assert connect, "expected the connect log line to be emitted"
    for record in caplog.records:
        assert SYNTHETIC_USERNAME not in record.getMessage()
        assert "synthetic.user" not in record.getMessage()
    out = capsys.readouterr()
    assert SYNTHETIC_USERNAME not in out.out + out.err
