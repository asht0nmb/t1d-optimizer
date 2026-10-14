"""Static checks on the Tandem workflows: public-log hygiene and alert wiring."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
_TANDEM = ["tandem-nightly-sync.yml", "test-tandem-sync.yml"]


def _load(name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS / name).read_text())


def _steps(name: str) -> list[dict]:
    wf = _load(name)
    return [s for job in wf["jobs"].values() for s in job["steps"]]


@pytest.mark.parametrize("name", _TANDEM)
def test_identity_env_comes_from_secrets(name):
    for step in _steps(name):
        for key, value in (step.get("env") or {}).items():
            if key in {"TCONNECT_EMAIL", "TCONNECT_PASSWORD", "SUPABASE_DB_URL",
                       "TIMEZONE_NAME", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}:
                assert re.fullmatch(r"\$\{\{ secrets\.[A-Z_]+ \}\}", str(value)), (name, key)


@pytest.mark.parametrize("name", _TANDEM)
def test_no_verbose_or_debug(name):
    for step in _steps(name):
        run = step.get("run", "")
        assert not re.search(r"\s(-v|--verbose)\b", run), name
        assert "set -x" not in run, name
        for key, value in (step.get("env") or {}).items():
            assert "DEBUG" not in key.upper(), (name, key)
            assert "DEBUG" not in str(value).upper(), (name, key)


def test_nightly_passes_telegram_secrets_to_sync_step():
    sync = next(s for s in _steps("tandem-nightly-sync.yml") if "sync_tandem" in s.get("run", ""))
    assert sync["env"]["TELEGRAM_BOT_TOKEN"] == "${{ secrets.TELEGRAM_BOT_TOKEN }}"
    assert sync["env"]["TELEGRAM_CHAT_ID"] == "${{ secrets.TELEGRAM_CHAT_ID }}"


def test_nightly_generic_failure_step_remains_and_is_pii_free():
    step = next(s for s in _steps("tandem-nightly-sync.yml") if s.get("if") == "failure()")
    body = step["run"]
    assert "FAILED" in body
    assert "serial" not in body.lower() and "pumper" not in body.lower()
