"""Golden + property tests for glycemic variability metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.metrics.variability import conga, j_index, mage, modd


# ── J-index ──────────────────────────────────────────────────────────────


def test_j_index_golden():
    # 0.001 * (mean + sd)^2 ; mean=120, sd=30 → 0.001*150^2 = 22.5
    assert j_index(120.0, 30.0) == pytest.approx(22.5)


def test_j_index_none_inputs():
    assert j_index(None, 30.0) is None
    assert j_index(120.0, None) is None


def test_j_index_constant_series_equivalent():
    # sd=0 → 0.001*mean^2
    assert j_index(100.0, 0.0) == pytest.approx(0.001 * 100.0**2)


# ── MODD ─────────────────────────────────────────────────────────────────


def _two_day_frame(offset: float, tz="UTC") -> pd.DataFrame:
    """Two consecutive local days, same times, day-2 = day-1 + offset."""
    base = pd.date_range("2026-04-13 00:00", periods=12, freq="2h", tz=tz)
    day1 = pd.DataFrame({"timestamp": base, "bg_mgdl": np.full(12, 120.0)})
    day2 = pd.DataFrame(
        {"timestamp": base + pd.Timedelta(days=1), "bg_mgdl": np.full(12, 120.0 + offset)}
    )
    return pd.concat([day1, day2], ignore_index=True)


def test_modd_constant_offset():
    # Matched times one day apart differ by exactly 20 → MODD = 20.
    assert modd(_two_day_frame(20.0), tz="UTC") == pytest.approx(20.0)


def test_modd_requires_two_days():
    one_day = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-04-13 00:00", periods=6, freq="2h", tz="UTC"),
            "bg_mgdl": np.full(6, 120.0),
        }
    )
    assert modd(one_day, tz="UTC") is None


def test_modd_empty_none():
    assert modd(pd.DataFrame(), tz="UTC") is None


# ── CONGA ────────────────────────────────────────────────────────────────


def test_conga_constant_series_zero():
    cgm = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-04-13 00:00", periods=48, freq="5min", tz="UTC"),
            "bg_mgdl": np.full(48, 120.0),
        }
    )
    # No variation n hours apart → SD of differences = 0.
    assert conga(cgm, n_hours=1, tz="UTC") == pytest.approx(0.0)


def test_conga_none_when_no_pairs():
    cgm = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-04-13 00:00", periods=3, freq="5min", tz="UTC"),
            "bg_mgdl": [100.0, 110.0, 120.0],
        }
    )
    # Only 15 min of data; no readings 2h apart → None.
    assert conga(cgm, n_hours=2, tz="UTC") is None


# ── MAGE ─────────────────────────────────────────────────────────────────


def test_mage_constant_series_zero():
    bg = np.full(20, 120.0)
    assert mage(bg) == pytest.approx(0.0)


def test_mage_single_big_excursion():
    # SD ~ large; one excursion of amplitude 100 (120→220→120) exceeds 1 SD.
    bg = np.array([120, 120, 220, 120, 120], dtype=float)
    result = mage(bg)
    assert result == pytest.approx(100.0, abs=1.0)


def test_mage_small_oscillations_below_sd_excluded():
    # Tiny ripples within 1 SD of a big excursion are excluded; MAGE tracks
    # the dominant excursion amplitude.
    bg = np.array([100, 102, 100, 102, 200, 100], dtype=float)
    result = mage(bg)
    assert result is None or result >= 50.0
