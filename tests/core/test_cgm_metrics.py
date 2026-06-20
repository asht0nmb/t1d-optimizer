"""Golden + property tests for core/metrics/cgm_metrics.py."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from core.metrics.cgm_metrics import (
    cv_pct,
    cv_stable,
    ea1c,
    gmi,
    mean_bg,
    median_bg,
    sd_bg,
    time_in_bands,
    time_in_range,
)


class TestGmi:
    def test_golden_150(self):
        assert gmi(150) == pytest.approx(6.898, abs=1e-3)

    def test_golden_100(self):
        assert gmi(100) == pytest.approx(5.702, abs=1e-3)

    def test_golden_200(self):
        assert gmi(200) == pytest.approx(8.094, abs=1e-3)

    def test_none_passthrough(self):
        assert gmi(None) is None


class TestEa1c:
    def test_golden_150(self):
        assert ea1c(150) == pytest.approx(6.8537, abs=1e-3)

    def test_none_passthrough(self):
        assert ea1c(None) is None


class TestCentralTendency:
    def test_mean_median(self):
        arr = np.array([100, 110, 120, 130, 140], dtype=float)
        assert mean_bg(arr) == pytest.approx(120.0)
        assert median_bg(arr) == pytest.approx(120.0)

    def test_sd_ddof1(self):
        arr = np.array([100, 110, 120, 130, 140], dtype=float)
        assert sd_bg(arr) == pytest.approx(np.std(arr, ddof=1))

    def test_sd_requires_n_ge_2(self):
        assert sd_bg(np.array([120.0])) is None
        assert sd_bg(np.array([], dtype=float)) is None

    def test_mean_empty_is_none(self):
        assert mean_bg(np.array([], dtype=float)) is None
        assert median_bg(np.array([], dtype=float)) is None

    def test_nan_dropped(self):
        arr = np.array([100.0, np.nan, 140.0])
        assert mean_bg(arr) == pytest.approx(120.0)


class TestCv:
    def test_cv_golden(self):
        arr = np.array([100, 110, 120, 130, 140], dtype=float)
        expected = np.std(arr, ddof=1) / np.mean(arr) * 100
        assert cv_pct(arr) == pytest.approx(expected)

    def test_cv_none_when_n_lt_2(self):
        assert cv_pct(np.array([120.0])) is None

    def test_cv_stable_boundary_at_36(self):
        # cv exactly 36 → stable (<= 36)
        assert cv_stable(36.0) is True
        assert cv_stable(36.0001) is False
        assert cv_stable(35.999) is True

    def test_cv_stable_none_passthrough(self):
        assert cv_stable(None) is None


class TestTimeInBands:
    def test_boundary_array_partition(self):
        # One reading landing in each band region plus exact boundaries.
        # 53 -> tbr2; 54,69 -> tbr1; 70,140,180 -> tir; 181,250 -> tar1; 251 -> tar2
        arr = np.array([53, 54, 69, 70, 140, 180, 181, 250, 251], dtype=float)
        b = time_in_bands(arr)
        n = len(arr)
        assert b["tbr2"] == pytest.approx(100 * 1 / n)  # 53
        assert b["tbr1"] == pytest.approx(100 * 2 / n)  # 54, 69
        assert b["tir"] == pytest.approx(100 * 3 / n)  # 70, 140, 180
        assert b["tar1"] == pytest.approx(100 * 2 / n)  # 181, 250
        assert b["tar2"] == pytest.approx(100 * 1 / n)  # 251
        # partition sums to 100
        total = b["tbr2"] + b["tbr1"] + b["tir"] + b["tar1"] + b["tar2"]
        assert total == pytest.approx(100.0)

    def test_titr_overlaps_tir(self):
        # 70..140 inclusive in TITR; 141..180 in TIR but not TITR
        arr = np.array([70, 140, 141, 180], dtype=float)
        b = time_in_bands(arr)
        assert b["titr"] == pytest.approx(50.0)  # 70, 140
        assert b["tir"] == pytest.approx(100.0)  # all four in consensus range

    def test_aggregate_totals(self):
        arr = np.array([40, 60, 100, 200, 300], dtype=float)
        b = time_in_bands(arr)
        assert b["tbr_total"] == pytest.approx(b["tbr2"] + b["tbr1"])
        assert b["tar_total"] == pytest.approx(b["tar1"] + b["tar2"])

    def test_empty_returns_zeros(self):
        b = time_in_bands(np.array([], dtype=float))
        for key in ("tbr2", "tbr1", "tir", "tar1", "tar2", "tar_total", "tbr_total", "titr"):
            assert b[key] == 0.0

    def test_nan_dropped(self):
        arr = np.array([100.0, np.nan, 100.0])
        b = time_in_bands(arr)
        assert b["tir"] == pytest.approx(100.0)


class TestTimeInRange:
    def test_default_band(self):
        arr = np.array([60, 70, 120, 180, 200], dtype=float)
        # 70,120,180 in [70,180] -> 60%
        assert time_in_range(arr, 70, 180) == pytest.approx(60.0)

    def test_custom_band_inclusive(self):
        arr = np.array([70, 140, 141], dtype=float)
        assert time_in_range(arr, 70, 140) == pytest.approx(200 / 3)

    def test_empty_is_zero(self):
        assert time_in_range(np.array([], dtype=float), 70, 180) == 0.0


# ---------------------------------------------------------------------------
# Property tests (hypothesis)
# ---------------------------------------------------------------------------

bg_arrays = st.lists(
    st.floats(min_value=1.0, max_value=600.0, allow_nan=False, allow_infinity=False),
    min_size=1,
    max_size=200,
).map(lambda xs: np.array(xs, dtype=float))


@settings(max_examples=300)
@given(bg_arrays)
def test_partition_sums_to_100(arr):
    b = time_in_bands(arr)
    total = b["tbr2"] + b["tbr1"] + b["tir"] + b["tar1"] + b["tar2"]
    assert abs(total - 100.0) <= 1e-9


@settings(max_examples=300)
@given(bg_arrays)
def test_all_percentages_in_unit_range(arr):
    b = time_in_bands(arr)
    for v in b.values():
        assert -1e-9 <= v <= 100.0 + 1e-9


@given(
    st.floats(min_value=40.0, max_value=400.0),
    st.floats(min_value=0.1, max_value=50.0),
)
def test_gmi_monotonic_in_mean(m, delta):
    assert gmi(m + delta) > gmi(m)
