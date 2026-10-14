"""Argument handling for scripts/repair_nightly_sync_tz.py (no DB access)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts import repair_nightly_sync_tz as repair


@pytest.fixture(autouse=True)
def _no_env_serial(monkeypatch):
    monkeypatch.delenv("REPAIR_PUMP_SERIAL", raising=False)


def test_serial_is_required(capsys):
    with pytest.raises(SystemExit) as exc:
        repair.parse_args([])
    assert exc.value.code == 2
    assert "--serial" in capsys.readouterr().err


def test_serial_from_cli():
    args = repair.parse_args(["--serial", "ABC123456"])
    assert args.serial == "ABC123456"
    assert args.apply is False


def test_serial_from_env(monkeypatch):
    monkeypatch.setenv("REPAIR_PUMP_SERIAL", "ENV987654")
    assert repair.parse_args([]).serial == "ENV987654"


def test_missing_serial_fails_before_any_db_connection(monkeypatch):
    connect = MagicMock()
    monkeypatch.setattr(repair.psycopg2, "connect", connect)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://u@example.invalid/db")
    with pytest.raises(SystemExit):
        repair.main([])
    connect.assert_not_called()


def test_no_serial_constant_in_module():
    assert not hasattr(repair, "PUMP_SERIAL")


def test_serial_threaded_into_queries():
    cur = MagicMock()
    cur.rowcount = 0
    cur.fetchone.return_value = (0,)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur

    repair.repair(conn, apply=False, serial="SER-XYZ-1")

    params = [c.args[1] for c in cur.execute.call_args_list if len(c.args) > 1]
    assert params and all(p[0] == "SER-XYZ-1" for p in params)
