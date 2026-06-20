"""Golden + property tests for core/metrics/risk_indices.py."""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from core.metrics.risk_indices import gri, hbgi, lbgi


def _f(g: float) -> float:
    return 1.509 * ((math.log(g) ** 1.084) - 5.381)


class TestLbgiHbgi:
    def test_risk_neutral_point(self):
        # g = 112.5 -> f ~= 0 -> both risk indices ~ 0
        arr = np.full(50, 112.5)
        assert lbgi(arr) == pytest.approx(0.0, abs=0.05)
        assert hbgi(arr) == pytest.approx(0.0, abs=0.05)

    def test_constant_low_frozen(self):
        g = 50.0
        f = _f(g)  # negative
        expected = 10.0 * f * f
        arr = np.full(20, g)
        assert lbgi(arr) == pytest.approx(expected, abs=1e-6)
        assert hbgi(arr) == pytest.approx(0.0, abs=1e-9)

    def test_constant_high_frozen(self):
        g = 300.0
        f = _f(g)  # positive
        expected = 10.0 * f * f
        arr = np.full(20, g)
        assert hbgi(arr) == pytest.approx(expected, abs=1e-6)
        assert lbgi(arr) == pytest.approx(0.0, abs=1e-9)

    def test_clamp_low(self):
        # Below 20 clamps to 20: same risk as a constant-20 array.
        f20 = _f(20.0)
        expected = 10.0 * f20 * f20
        assert lbgi(np.full(10, 5.0)) == pytest.approx(expected, abs=1e-6)

    def test_clamp_high(self):
        # Above 600 clamps to 600.
        f600 = _f(600.0)
        expected = 10.0 * f600 * f600
        assert hbgi(np.full(10, 900.0)) == pytest.approx(expected, abs=1e-6)

    def test_empty_is_zero(self):
        assert lbgi(np.array([], dtype=float)) == 0.0
        assert hbgi(np.array([], dtype=float)) == 0.0

    def test_nan_dropped(self):
        arr = np.array([112.5, np.nan, 112.5])
        assert lbgi(arr) == pytest.approx(0.0, abs=0.05)


class TestGri:
    def test_golden_mixed(self):
        out = gri(tbr2=2.0, tbr1=5.0, tar1=10.0, tar2=3.0)
        assert out["gri_hypo"] == pytest.approx(6.0)  # 2 + 0.8*5
        assert out["gri_hyper"] == pytest.approx(8.0)  # 3 + 0.5*10
        assert out["gri"] == pytest.approx(30.8)  # 3*6 + 1.6*8

    def test_all_in_range_is_zero(self):
        out = gri(tbr2=0.0, tbr1=0.0, tar1=0.0, tar2=0.0)
        assert out["gri"] == 0.0
        assert out["gri_hypo"] == 0.0
        assert out["gri_hyper"] == 0.0

    def test_clamp_to_100(self):
        out = gri(tbr2=100.0, tbr1=0.0, tar1=0.0, tar2=0.0)
        # 3*100 = 300 -> clamps to 100
        assert out["gri"] == 100.0


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

bg_arrays = st.lists(
    st.floats(min_value=10.0, max_value=700.0, allow_nan=False, allow_infinity=False),
    min_size=1,
    max_size=150,
).map(lambda xs: np.array(xs, dtype=float))


@settings(max_examples=300)
@given(bg_arrays)
def test_risk_indices_nonnegative(arr):
    assert lbgi(arr) >= -1e-9
    assert hbgi(arr) >= -1e-9


@settings(max_examples=200)
@given(
    bg_arrays,
    st.floats(min_value=1.0, max_value=200.0),
)
def test_shifting_up_monotone(arr, shift):
    # Shifting all readings up never decreases HBGI nor increases LBGI.
    base_l, base_h = lbgi(arr), hbgi(arr)
    up_l, up_h = lbgi(arr + shift), hbgi(arr + shift)
    assert up_h >= base_h - 1e-6
    assert up_l <= base_l + 1e-6


@settings(max_examples=200)
@given(
    st.floats(min_value=0.0, max_value=100.0),
    st.floats(min_value=0.0, max_value=100.0),
    st.floats(min_value=0.0, max_value=100.0),
    st.floats(min_value=0.0, max_value=100.0),
)
def test_gri_in_unit_range(tbr2, tbr1, tar1, tar2):
    out = gri(tbr2=tbr2, tbr1=tbr1, tar1=tar1, tar2=tar2)
    assert 0.0 <= out["gri"] <= 100.0
