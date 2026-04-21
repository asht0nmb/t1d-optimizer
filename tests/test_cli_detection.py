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


class TestEnsureEnriched:
    """Backfill enrichment on pre-enrichment parquets so CLI stays usable."""

    def test_enriches_requests_when_bolus_category_missing(self):
        import pandas as pd

        from detection.config import load_config
        from scripts.run_detection import _ensure_enriched

        config = load_config()
        raw_requests = pd.DataFrame(
            {
                "timestamp": [pd.Timestamp("2026-03-19 12:00", tz="UTC")],
                "bolus_id": [1],
                "carbs_g": [30.0],
                "bg_mgdl": [120],
                "iob": [0.0],
                "bolus_source": ["user"],
                "food_insulin": [2.0],
                "correction_insulin": [0.0],
                "total_requested": [2.0],
                "pump_serial": ["TEST"],
            }
        )
        out = _ensure_enriched({"requests": raw_requests}, config)
        assert "bolus_category" in out["requests"].columns
        assert "override_delta" in out["requests"].columns

    def test_skips_enrichment_when_column_already_present(self):
        import pandas as pd

        from detection.config import load_config
        from scripts.run_detection import _ensure_enriched

        config = load_config()
        already_enriched = pd.DataFrame(
            {
                "timestamp": [pd.Timestamp("2026-03-19 12:00", tz="UTC")],
                "bolus_id": [1],
                "carbs_g": [30.0],
                "bg_mgdl": [120],
                "iob": [0.0],
                "bolus_source": ["user"],
                "food_insulin": [2.0],
                "correction_insulin": [0.0],
                "total_requested": [2.0],
                "pump_serial": ["TEST"],
                "bolus_category": ["user_meal"],
                "override_delta": [0.0],
            }
        )
        out = _ensure_enriched({"requests": already_enriched}, config)
        assert out["requests"] is already_enriched

    def test_backfills_missing_site_issues_and_cgm_gaps(self):
        import pandas as pd

        from detection.config import load_config
        from scripts.run_detection import _ensure_enriched

        config = load_config()
        out = _ensure_enriched({"alarms": pd.DataFrame()}, config)
        assert "site_issues" in out
        assert "cgm_gaps" in out
