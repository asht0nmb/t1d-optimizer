"""Tests for core/metrics/report.py — CgmReport orchestrator."""

from __future__ import annotations

import dataclasses
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from core.metrics import CgmReport, ReportWindow, compute_cgm_report

TZ = "America/Los_Angeles"


@dataclasses.dataclass(frozen=True)
class _BgTargets:
    low: int = 70
    high: int = 180
    target: int = 110


@dataclasses.dataclass(frozen=True)
class _Config:
    bg_targets: _BgTargets = dataclasses.field(default_factory=_BgTargets)
    timezone: str = TZ


def _frame_for(end_date: dt.date, days: int, *, mean=120.0, jitter=20.0, freq="5min"):
    """Dense synthetic CGM frame spanning ``days`` local dates ending on end_date."""
    start = end_date - dt.timedelta(days=days - 1)
    since = pd.Timestamp(dt.datetime(start.year, start.month, start.day), tz=TZ)
    until = pd.Timestamp(
        dt.datetime(end_date.year, end_date.month, end_date.day), tz=TZ
    ) + pd.Timedelta(days=1)
    ts = pd.date_range(since, until, freq=freq, inclusive="left")
    rng = np.random.default_rng(0)
    bg = mean + rng.uniform(-jitter, jitter, size=len(ts))
    return pd.DataFrame({"timestamp": ts.tz_convert("UTC"), "bg_mgdl": bg})


class TestSufficientReport:
    def test_headline_fields_present(self):
        end = dt.date(2026, 6, 16)
        df = _frame_for(end, 14, mean=120.0, jitter=10.0)
        window = ReportWindow(end_date=end, days=14, tz=TZ)
        rep = compute_cgm_report(df, config=_Config(), window=window)

        assert isinstance(rep, CgmReport)
        assert rep.n_readings > 14 * 250
        assert rep.meets_sufficiency is True
        assert rep.mean_bg == pytest.approx(120.0, abs=2.0)
        assert rep.sd_bg is not None
        assert rep.cv_pct is not None
        assert rep.gmi is not None
        assert rep.ea1c is not None
        assert rep.gri is not None
        assert rep.lbgi is not None
        assert rep.hbgi is not None
        # partition still sums to 100
        total = rep.tbr2 + rep.tbr1 + rep.tir + rep.tar1 + rep.tar2
        assert total == pytest.approx(100.0)

    def test_variability_fields_populated(self):
        end = dt.date(2026, 6, 16)
        df = _frame_for(end, 14, mean=120.0, jitter=30.0)
        rep = compute_cgm_report(
            df, config=_Config(), window=ReportWindow(end_date=end, days=14, tz=TZ)
        )
        # J-index, MODD, CONGA, MAGE all defined for a dense multi-day window.
        assert rep.j_index is not None and rep.j_index > 0
        assert rep.modd is not None and rep.modd >= 0
        assert rep.conga is not None and rep.conga >= 0
        assert rep.mage is not None

    def test_frozen(self):
        end = dt.date(2026, 6, 16)
        df = _frame_for(end, 14)
        rep = compute_cgm_report(
            df, config=_Config(), window=ReportWindow(end_date=end, days=14, tz=TZ)
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            rep.mean_bg = 1.0  # type: ignore[misc]

    def test_config_band_tir_uses_custom_targets(self):
        end = dt.date(2026, 6, 16)
        df = _frame_for(end, 14, mean=120.0, jitter=10.0)
        cfg = _Config(bg_targets=_BgTargets(low=70, high=140))
        rep = compute_cgm_report(
            df, config=cfg, window=ReportWindow(end_date=end, days=14, tz=TZ)
        )
        # narrower band -> config TIR <= consensus TIR
        assert rep.tir_config <= rep.tir + 1e-9


class TestEmptyFrame:
    def test_all_none(self):
        end = dt.date(2026, 6, 16)
        df = pd.DataFrame({"timestamp": pd.to_datetime([]), "bg_mgdl": []})
        rep = compute_cgm_report(
            df, config=_Config(), window=ReportWindow(end_date=end, days=14, tz=TZ)
        )
        assert rep.n_readings == 0
        assert rep.meets_sufficiency is False
        assert rep.mean_bg is None
        assert rep.sd_bg is None
        assert rep.gmi is None
        assert rep.gri is None
        assert rep.lbgi is None
        assert rep.hbgi is None


class TestSingleReading:
    def test_mean_defined_sd_cv_gmi_gri_none(self):
        end = dt.date(2026, 6, 16)
        ts = pd.Timestamp(dt.datetime(2026, 6, 16, 12), tz=TZ).tz_convert("UTC")
        df = pd.DataFrame({"timestamp": [ts], "bg_mgdl": [120.0]})
        rep = compute_cgm_report(
            df, config=_Config(), window=ReportWindow(end_date=end, days=1, tz=TZ)
        )
        assert rep.n_readings == 1
        assert rep.mean_bg == pytest.approx(120.0)
        assert rep.sd_bg is None
        assert rep.cv_pct is None
        assert rep.gmi is None  # insufficient
        assert rep.gri is None  # insufficient
        # lbgi/hbgi are defined for a single reading
        assert rep.lbgi is not None
        assert rep.hbgi is not None


class TestShortWindow:
    def test_under_14_days_gates_gmi_gri_but_keeps_lbgi(self):
        end = dt.date(2026, 6, 16)
        df = _frame_for(end, 7, mean=120.0, jitter=10.0)
        rep = compute_cgm_report(
            df, config=_Config(), window=ReportWindow(end_date=end, days=7, tz=TZ)
        )
        assert rep.meets_sufficiency is False
        assert rep.gmi is None
        assert rep.gri is None
        assert rep.mean_bg is not None
        assert rep.sd_bg is not None
        assert rep.lbgi is not None
        assert rep.hbgi is not None


class TestDstDay:
    def test_dst_transition_active_pct_sane(self):
        # 14-day window ending the day after spring-forward.
        end = dt.date(2026, 3, 9)
        df = _frame_for(end, 14, mean=120.0, jitter=10.0)
        rep = compute_cgm_report(
            df, config=_Config(), window=ReportWindow(end_date=end, days=14, tz=TZ)
        )
        # Dense data should yield ~100% active even with a 23h day in the window.
        assert 95.0 <= rep.active_pct <= 100.5
        assert rep.meets_sufficiency is True
