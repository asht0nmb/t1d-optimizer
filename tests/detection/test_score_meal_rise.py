"""Tests for the M2 calibration runner (report building + orchestration)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import scripts.score_meal_rise as runner
from detection.calibration.meal_rise_scoring import ScoredInstance
from scripts.score_meal_rise import build_markdown_report, to_records


def _scored(label="uncovered", hour=12, resolution="none"):
    ts = datetime(2026, 6, 1, hour, 0, tzinfo=timezone.utc)
    return ScoredInstance(
        event_ref=f"meal_rise:{ts.isoformat(timespec='minutes')}",
        pump_serial="123",
        label=label,
        anchor_ts=ts,
        rise_start_ts=ts,
        rise_end_ts=ts,
        start_level=120,
        end_level=170,
        delta=50,
        slope_mgdl_per_min=2.0,
        hour_of_day=hour,
        matched_bolus_ts=None if label == "uncovered" else ts,
        matched_bolus_category=None if label == "uncovered" else "user_meal",
        matched_bolus_carbs=None if label == "uncovered" else 45,
        bolus_delay_min=None if label == "uncovered" else -10.0,
        resolution=resolution if label == "uncovered" else None,
        resolution_ts=None,
        resolution_delay_min=None,
    )


class TestToRecords:
    def test_serializes_datetimes_to_isoformat(self):
        records = to_records([_scored()])
        assert records[0]["anchor_ts"] == "2026-06-01T12:00:00+00:00"
        assert records[0]["label"] == "uncovered"
        assert records[0]["matched_bolus_ts"] is None

    def test_empty_list(self):
        assert to_records([]) == []


class TestBuildMarkdownReport:
    def test_contains_summary_counts_and_advisory_header(self):
        scored = [_scored("pre_bolused", 8), _scored("uncovered", 12),
                  _scored("late_bolused", 19)]
        md = build_markdown_report(
            scored,
            base_slope=1.8,
            sweep_rows=[{"base_slope": 1.8, "total": 3,
                         "pre_bolused": 1, "late_bolused": 1,
                         "uncovered": 1, "uncovered_rate": 1 / 3}],
            date_range=("2026-05-01", "2026-06-01"),
        )
        assert "advisory" in md.lower()
        assert "uncovered" in md
        assert "| 1.8 |" in md            # sweep table row
        assert "Hour" in md               # per-hour breakdown section

    def test_per_hour_breakdown_buckets_by_hour(self):
        md = build_markdown_report(
            [_scored("uncovered", 7), _scored("uncovered", 7),
             _scored("pre_bolused", 12)],
            base_slope=1.8, sweep_rows=[], date_range=(None, None),
        )
        assert "| 07 |" in md and "| 12 |" in md


def _synthetic_frames():
    # 3 hours of 5-min CGM with one sharp rise at 12:00–12:30 (~3 mg/dL/min).
    ts = pd.date_range("2026-06-01 10:00", periods=36, freq="5min", tz="UTC")
    bg = np.full(36, 110.0)
    rise = slice(24, 31)                      # 12:00 .. 12:30
    bg[rise] = [110, 125, 140, 155, 170, 185, 200]
    bg[31:] = 200.0
    cgm = pd.DataFrame({"timestamp": ts, "bg_mgdl": bg})
    requests = pd.DataFrame(
        {
            "timestamp": [pd.Timestamp("2026-06-01 13:00", tz="UTC")],
            "bolus_category": ["user_correction_only"],
            "carbs_g": [float("nan")],
            "pump_serial": ["881111"],
        }
    )
    return {"cgm": cgm, "requests": requests}


class TestRun:
    def test_run_scores_and_writes_reports(self, tmp_path, monkeypatch,
                                           default_config):
        monkeypatch.setattr(runner, "_load_enriched_frames",
                            lambda config: _synthetic_frames())
        result = runner.run(start=None, end=None, out_dir=tmp_path, sweep=[],
                            config=default_config)
        assert result["summary"]["total"] >= 1
        assert result["summary"]["counts"]["uncovered"] >= 1
        md_files = list(tmp_path.glob("meal_rise_scores_*.md"))
        json_files = list(tmp_path.glob("meal_rise_scores_*.json"))
        assert len(md_files) == 1 and len(json_files) == 1
        payload = json.loads(json_files[0].read_text())
        assert payload["summary"]["total"] == result["summary"]["total"]
        assert payload["records"][0]["label"]

    def test_date_filter_excludes_outside_range(self, tmp_path, monkeypatch,
                                                default_config):
        monkeypatch.setattr(runner, "_load_enriched_frames",
                            lambda config: _synthetic_frames())
        result = runner.run(start="2026-06-02", end=None,
                            out_dir=tmp_path, sweep=[],
                            config=default_config)
        assert result["summary"]["total"] == 0

    def test_sweep_produces_row_per_slope(self, tmp_path, monkeypatch,
                                          default_config):
        monkeypatch.setattr(runner, "_load_enriched_frames",
                            lambda config: _synthetic_frames())
        result = runner.run(start=None, end=None, out_dir=tmp_path,
                            sweep=[1.0, 5.0], config=default_config)
        slopes = [row["base_slope"] for row in result["sweep_rows"]]
        assert slopes == [1.0, 5.0]
        # a 5.0 mg/dL/min threshold should find fewer (likely zero) instances
        assert result["sweep_rows"][1]["total"] <= result["sweep_rows"][0]["total"]

    def test_empty_frames_yields_zero_total(self, tmp_path, monkeypatch,
                                            default_config):
        monkeypatch.setattr(runner, "_load_enriched_frames",
                            lambda config: {"cgm": pd.DataFrame(),
                                            "requests": pd.DataFrame()})
        result = runner.run(start=None, end=None, out_dir=tmp_path, sweep=[],
                            config=default_config)
        assert result["summary"]["total"] == 0
        assert result["summary"]["uncovered_rate"] == 0.0
        md_files = list(tmp_path.glob("meal_rise_scores_*.md"))
        json_files = list(tmp_path.glob("meal_rise_scores_*.json"))
        assert len(md_files) == 1 and len(json_files) == 1
