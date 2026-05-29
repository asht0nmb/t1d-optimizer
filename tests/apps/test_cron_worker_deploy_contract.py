"""Deploy contract tests for the meal-rise Vercel cron worker (repo-root layout)."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from http.server import BaseHTTPRequestHandler
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HANDLER_PATH = _REPO_ROOT / "api" / "meal_rise_cron.py"
_ROOT_VERCEL = _REPO_ROOT / "vercel.json"
_WEB_VERCEL = _REPO_ROOT / "apps" / "web" / "vercel.json"
_LEGACY_HANDLER = _REPO_ROOT / "apps" / "cron_worker" / "api" / "meal_rise_cron.py"
_WEB_HANDLER = _REPO_ROOT / "apps" / "web" / "api" / "meal_rise_cron.py"


def _load_meal_rise_cron_module():
    spec = importlib.util.spec_from_file_location("meal_rise_cron_contract", _HANDLER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_api_meal_rise_cron_exists_at_repo_root():
    assert _HANDLER_PATH.is_file()
    assert not _LEGACY_HANDLER.is_file()


def test_root_vercel_json_functions_pattern_matches_handler():
    config = json.loads(_ROOT_VERCEL.read_text(encoding="utf-8"))
    functions = config.get("functions", {})
    assert "api/**/*.py" in functions
    api_py_files = list((_REPO_ROOT / "api").glob("**/*.py"))
    assert api_py_files, "expected at least one api/**/*.py file"


def test_meal_rise_cron_exports_handler_class():
    module = _load_meal_rise_cron_module()
    assert isinstance(module.handler, type)
    assert issubclass(module.handler, BaseHTTPRequestHandler)
    assert not isinstance(module.handler, types.FunctionType)


def test_meal_rise_cron_repo_root_on_sys_path():
    repo_root = str(_REPO_ROOT)
    sys.path[:] = [p for p in sys.path if p != repo_root]
    module = _load_meal_rise_cron_module()
    assert repo_root in sys.path
    assert module._REPO_ROOT == _REPO_ROOT


def test_web_vercel_json_has_no_python_functions():
    config = json.loads(_WEB_VERCEL.read_text(encoding="utf-8"))
    functions = config.get("functions", {})
    for pattern in functions:
        assert "api/**/*.py" not in pattern
        assert not pattern.endswith(".py") or "pages/api" in pattern


def test_root_vercel_json_excludes_web_from_python_bundle():
    config = json.loads(_ROOT_VERCEL.read_text(encoding="utf-8"))
    fn_config = config["functions"]["api/**/*.py"]
    exclude = fn_config.get("excludeFiles", "")
    assert "apps/web/**" in exclude


def test_no_meal_rise_handler_under_apps_web():
    assert not _WEB_HANDLER.is_file()
