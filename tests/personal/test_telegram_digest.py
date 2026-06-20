"""Tests for the pure digest builders."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from apps.personal.telegram.digest import (
    DISCLAIMER,
    build_day_digest,
    build_status_digest,
    build_trends_digest,
    compute_tir,
    help_text,
)


def test_compute_tir_basic():
    bg = pd.Series([70.0, 100.0, 180.0, 200.0])
    assert compute_tir(bg, low=70, high=180) == 75.0


def test_compute_tir_empty_is_none():
    assert compute_tir(pd.Series(dtype=float), low=70, high=180) is None


def test_day_digest_totals_and_disclaimer():
    cgm = pd.DataFrame({"bg_mgdl": [100.0, 120.0, 200.0, 140.0]})
    bolus = pd.DataFrame({"insulin_units": [2.5, 3.0]})
    requests = pd.DataFrame(
        {
            "carbs_g": [45.0, 10.0, float("nan")],
            "bolus_category": ["user_meal", "user_correction_only", "user_meal"],
        }
    )
    text = build_day_digest(
        label="Today",
        day=date(2026, 4, 14),
        cgm=cgm,
        bolus=bolus,
        requests=requests,
        alert_count=2,
        low=70,
        high=180,
    )
    assert "Today" in text and "2026-04-14" in text
    assert "75%" in text  # 3 of 4 readings in range
    assert "5.5 U" in text  # bolus total
    assert "45 g" in text  # only food-carrying carbs counted
    assert "Meal-rise alerts: 2" in text
    assert DISCLAIMER in text


def test_day_digest_empty_day():
    empty = pd.DataFrame()
    text = build_day_digest(
        label="Today",
        day=date(2026, 4, 14),
        cgm=empty,
        bolus=empty,
        requests=empty,
        alert_count=0,
        low=70,
        high=180,
    )
    assert "—" in text  # TIR/mean unavailable
    assert "0.0 U" in text
    assert DISCLAIMER in text


def test_trends_digest_lists_windows():
    text = build_trends_digest({7: 80.0, 14: None, 30: 65.0})
    assert "7-day: <b>80%</b>" in text
    assert "14-day: <b>—</b>" in text
    assert "30-day: <b>65%</b>" in text
    assert DISCLAIMER in text


def test_status_digest_handles_none():
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    text = build_status_digest(
        latest_cgm_ts=datetime(2026, 4, 14, 11, 30, tzinfo=timezone.utc),
        latest_detection_ts=None,
        latest_alert_ts=None,
        latest_alert_delivery=None,
        now=now,
    )
    assert "Latest CGM" in text
    assert "30 min ago" in text
    assert "Last detection: — (none recorded)" in text
    assert "none recorded" in text


def test_help_lists_all_commands():
    text = help_text()
    for cmd in ("/today", "/yesterday", "/trends", "/status", "/help"):
        assert cmd in text
