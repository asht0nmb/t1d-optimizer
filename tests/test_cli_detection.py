"""Smoke tests for the Task 2.6 detection CLI surface.

These stay intentionally cheap: they confirm the entry-point functions
are importable from `scripts.run_detection` and that `main.py --help`
registers the three new subcommands (with their documented flags).

No enriched parquet is required — the entry points are only imported,
and the subprocess calls use `--help` which exits before any I/O.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _main_help(*args: str) -> str:
    """Invoke `python main.py [...args] --help` and return combined stdout+stderr."""
    result = subprocess.run(
        [sys.executable, "main.py", *args, "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout + result.stderr


class TestRunDetectionImportable:
    def test_run_anomalies_importable(self):
        from scripts.run_detection import run_anomalies  # noqa: F401

    def test_run_meals_importable(self):
        from scripts.run_detection import run_meals  # noqa: F401

    def test_run_clustering_importable(self):
        from scripts.run_detection import run_clustering  # noqa: F401


class TestMainRegistersSubcommands:
    def test_main_py_registers_analyze_anomalies(self):
        assert "analyze-anomalies" in _main_help()

    def test_main_py_registers_analyze_meals(self):
        assert "analyze-meals" in _main_help()

    def test_main_py_registers_cluster_days(self):
        assert "cluster-days" in _main_help()

    def test_cluster_days_accepts_optional_args(self):
        help_text = _main_help("cluster-days")
        assert "--retrain" in help_text
        assert "--start" in help_text
        assert "--end" in help_text
