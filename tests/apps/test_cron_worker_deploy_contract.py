"""Deploy contract tests for the meal-rise Vercel cron worker (repo-root layout)."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
from http.server import BaseHTTPRequestHandler
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HANDLER_PATH = _REPO_ROOT / "api" / "index.py"
_ROOT_VERCEL = _REPO_ROOT / "vercel.json"
_WEB_VERCEL = _REPO_ROOT / "apps" / "web" / "vercel.json"
_LEGACY_HANDLER = _REPO_ROOT / "apps" / "cron_worker" / "api" / "meal_rise_cron.py"
_WEB_HANDLER = _REPO_ROOT / "apps" / "web" / "api" / "meal_rise_cron.py"
_NON_STANDARD_HANDLER = _REPO_ROOT / "api" / "meal_rise_cron.py"
_ROOT_REQUIREMENTS = _REPO_ROOT / "requirements.txt"
_VERCELIGNORE = _REPO_ROOT / ".vercelignore"

# Heavy deps the worker must NOT install on Vercel: they push the bundle past the
# 500 MB limit and nothing at runtime needs them (pure pandas/numpy + psycopg2 +
# pydexcom). The full set lands only if Vercel installs from uv.lock/pyproject.
_HEAVY_FORBIDDEN = (
    "pyarrow",
    "scipy",
    "scikit-learn",
    "jupyter",
    "matplotlib",
    "tconnectsync",
    "streamlit",
    "plotly",
)


def _requirement_names(text: str) -> list[str]:
    """Package names (lowercased, without version specifiers/comments)."""
    names = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.append(re.split(r"[<>=!~ \[]", line)[0].strip().lower())
    return names


def _load_cron_handler_module():
    spec = importlib.util.spec_from_file_location("meal_rise_cron_contract", _HANDLER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_api_index_exists_at_repo_root():
    assert _HANDLER_PATH.is_file()
    assert not _LEGACY_HANDLER.is_file()
    assert not _NON_STANDARD_HANDLER.is_file()


def test_root_vercel_json_functions_pattern_matches_handler():
    config = json.loads(_ROOT_VERCEL.read_text(encoding="utf-8"))
    functions = config.get("functions", {})
    assert "api/**/*.py" in functions
    api_py_files = list((_REPO_ROOT / "api").glob("**/*.py"))
    assert api_py_files, "expected at least one api/**/*.py file"


def test_root_vercel_json_rewrites_meal_rise_cron_to_index():
    config = json.loads(_ROOT_VERCEL.read_text(encoding="utf-8"))
    rewrites = config.get("rewrites", [])
    assert any(
        r.get("source") == "/api/meal_rise_cron" and r.get("destination") == "/api/index"
        for r in rewrites
    )


def test_pyproject_declares_vercel_entrypoint():
    text = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '[tool.vercel]' in text
    assert 'entrypoint = "api.index:handler"' in text


def test_root_vercel_json_pins_framework_to_other():
    # "Unmatched Function Pattern" surfaces when Vercel builds this repo with a
    # Next.js framework context: the Python `functions` globs are then never
    # matched. Pinning the framework to "Other" (JSON null) in vercel.json
    # overrides the dashboard Framework Preset and forces the Python builder,
    # so the worker deploy no longer depends on a manual dashboard setting.
    config = json.loads(_ROOT_VERCEL.read_text(encoding="utf-8"))
    assert "framework" in config, "vercel.json must pin a framework"
    assert config["framework"] is None, "framework must be null (= 'Other')"


def test_cron_handler_exports_handler_class():
    module = _load_cron_handler_module()
    assert isinstance(module.handler, type)
    assert issubclass(module.handler, BaseHTTPRequestHandler)
    assert not isinstance(module.handler, types.FunctionType)


def test_cron_handler_repo_root_on_sys_path():
    repo_root = str(_REPO_ROOT)
    sys.path[:] = [p for p in sys.path if p != repo_root]
    module = _load_cron_handler_module()
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


def test_root_requirements_exists_and_is_slim():
    # Vercel's Python builder installs the FULL project from uv.lock (~654 MB,
    # over the 500 MB limit) unless a root requirements.txt is present, in which
    # case it installs that. It must be slim — the heavy data/ML stack is not
    # used at runtime.
    assert _ROOT_REQUIREMENTS.is_file(), "root requirements.txt is required for the Vercel worker"
    names = _requirement_names(_ROOT_REQUIREMENTS.read_text(encoding="utf-8"))
    for pkg in _HEAVY_FORBIDDEN:
        assert pkg not in names, f"{pkg} must not be in the worker requirements.txt"
    joined = " ".join(names)
    for needed in ("pandas", "numpy", "psycopg2", "pydexcom"):
        assert needed in joined, f"{needed} missing from worker requirements.txt"


def test_vercelignore_hides_uv_lock_and_pyproject():
    # Without hiding these, Vercel installs from uv.lock/pyproject.toml (the full
    # project) instead of the slim requirements.txt.
    assert _VERCELIGNORE.is_file(), ".vercelignore is required to force the slim install"
    entries = {
        ln.strip()
        for ln in _VERCELIGNORE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }
    assert "uv.lock" in entries
    assert "pyproject.toml" in entries
