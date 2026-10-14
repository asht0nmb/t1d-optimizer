"""Tests for detection/deviation.py (Workstream A: algorithm-research phase)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from detection.deviation import (
    DeviationConfig,
    compute_bgi,
    compute_deviation_frame,
    compute_glucose_deltas,
)
from detection.iob import IobCurveConfig

TZ = timezone(timedelta(hours=-7), name="PDT")


def _ts(*, h, m=0, d=1):
    return datetime(2026, 8, d, h, m, tzinfo=TZ)


class TestComputeGlucoseDeltas:
    def test_regular_five_minute_spacing(self):
        timestamps = [_ts(h=10, m=5 * i) for i in range(6)]
        bg = [100, 105, 110, 108, 106, 104]
        cgm_df = pd.DataFrame({"timestamp": timestamps, "bg_mgdl": bg})
        out = compute_glucose_deltas(cgm_df)
        # delta at row i uses readings 2.5-7.5 min ago -> exactly the prior
        # 5-min reading at regular spacing, normalized by *5/5 = 1x.
        assert out.iloc[1]["delta"] == pytest.approx(5.0)
        assert out.iloc[2]["delta"] == pytest.approx(5.0)
        assert out.iloc[3]["delta"] == pytest.approx(-2.0)

    def test_first_row_has_no_delta(self):
        cgm_df = pd.DataFrame({"timestamp": [_ts(h=10)], "bg_mgdl": [100]})
        out = compute_glucose_deltas(cgm_df)
        assert np.isnan(out.iloc[0]["delta"])

    def test_irregular_backfilled_spacing_normalizes_correctly(self):
        """A 3-minute-spaced pair of backfilled readings (inside oref's
        (2.5, 7.5] "delta" bucket, but not grid-aligned to 5 minutes) should
        normalize by *actual* elapsed minutes, not row order — this is the
        whole point of not using `.diff()` on row order (see module
        docstring's cadence discussion). A naive row-order diff would
        report +6 mg/dL per step; the correct per-5-minute rate is +10.
        """
        timestamps = [_ts(h=10, m=0), _ts(h=10, m=3)]
        bg = [100, 106]  # +6 mg/dL in 3 minutes -> +10 mg/dL per 5 min
        cgm_df = pd.DataFrame({"timestamp": timestamps, "bg_mgdl": bg})
        out = compute_glucose_deltas(cgm_df)
        assert out.iloc[1]["delta"] == pytest.approx(10.0)

    def test_sub_2_5_minute_neighbor_is_not_a_delta_bucket_member(self):
        """oref merges readings <2.5 min apart into "now" rather than
        treating them as a delta sample (glucose-get-last.js's `-2 <
        minutesago <= 2.5` branch). We do not implement that merge (a
        documented simplification — see module docstring), so a neighbor
        this close simply contributes to no bucket and the delta is NaN,
        never a spuriously huge normalized rate."""
        timestamps = [_ts(h=10, m=0), _ts(h=10, m=1)]
        cgm_df = pd.DataFrame({"timestamp": timestamps, "bg_mgdl": [100, 102]})
        out = compute_glucose_deltas(cgm_df)
        assert np.isnan(out.iloc[1]["delta"])

    def test_gap_wider_than_42_5_minutes_yields_nan(self):
        timestamps = [_ts(h=10, m=0), _ts(h=11, m=0)]
        cgm_df = pd.DataFrame({"timestamp": timestamps, "bg_mgdl": [100, 150]})
        out = compute_glucose_deltas(cgm_df)
        assert np.isnan(out.iloc[1]["delta"])
        assert np.isnan(out.iloc[1]["short_avgdelta"])
        assert np.isnan(out.iloc[1]["long_avgdelta"])

    def test_low_bg_neighbor_excluded(self):
        timestamps = [_ts(h=10, m=0), _ts(h=10, m=5)]
        cgm_df = pd.DataFrame({"timestamp": timestamps, "bg_mgdl": [30, 100]})  # 30 < MIN_VALID_BG
        out = compute_glucose_deltas(cgm_df)
        assert np.isnan(out.iloc[1]["delta"])

    def test_empty_frame(self):
        cgm_df = pd.DataFrame(columns=["timestamp", "bg_mgdl"])
        out = compute_glucose_deltas(cgm_df)
        assert out.empty


class TestComputeBgi:
    def test_matches_oref_formula(self):
        activity = pd.Series([0.01, 0.0, -0.005])
        isf = 50.0
        bgi = compute_bgi(activity, isf)
        expected = -activity * isf * 5.0
        pd.testing.assert_series_equal(bgi, expected)

    def test_positive_activity_yields_negative_bgi(self):
        bgi = compute_bgi(pd.Series([0.02]), isf_mgdl_per_unit=50.0)
        assert bgi.iloc[0] < 0


class TestComputeDeviationFrame:
    def _synthetic_frames(self):
        # 6 hours of warm-up basal history (steady 1.0 u/hr, no boluses) so
        # the CGM readings starting at h=6 are fully warmed up under a 5h DIA.
        basal_df = pd.DataFrame(
            {
                "timestamp": [_ts(h=h) for h in range(0, 12)],
                "commanded_rate": [1.0] * 12,
            }
        )
        cgm_df = pd.DataFrame(
            {
                "timestamp": [_ts(h=6, m=5 * i) for i in range(6)],
                "bg_mgdl": [120, 122, 124, 126, 128, 130],
            }
        )
        bolus_df = pd.DataFrame(columns=["timestamp", "insulin_units"])
        return cgm_df, bolus_df, basal_df

    def test_deviation_equals_delta_minus_bgi_when_warmed_up(self):
        cgm_df, bolus_df, basal_df = self._synthetic_frames()
        config = DeviationConfig(
            isf_mgdl_per_unit=50.0, iob=IobCurveConfig(dia_hours=5.0, peak_minutes=75.0)
        )
        out = compute_deviation_frame(cgm_df, bolus_df, basal_df, config)
        warmed = out[out["warmed_up"]]
        assert len(warmed) > 0
        pd.testing.assert_series_equal(
            warmed["deviation_5m"], (warmed["delta"] - warmed["bgi"]), check_names=False
        )

    def test_steady_basal_at_baseline_yields_near_zero_bgi(self):
        """Basal that never deviates from its own trailing-median baseline
        contributes ~0 net dose, so activity/BGI should be ~0 — deviation
        should just track the raw delta."""
        cgm_df, bolus_df, basal_df = self._synthetic_frames()
        config = DeviationConfig(isf_mgdl_per_unit=50.0)
        out = compute_deviation_frame(cgm_df, bolus_df, basal_df, config)
        warmed = out[out["warmed_up"]]
        assert (warmed["bgi"].abs() < 1e-6).all()
        pd.testing.assert_series_equal(
            warmed["deviation_5m"], warmed["delta"], check_names=False
        )

    def test_bolus_with_flat_bg_drives_positive_deviation(self):
        """A big bolus, still near its activity peak, predicts a BG *fall*
        (bgi < 0). If observed BG stays flat instead of falling, deviation
        (= delta - bgi) comes out *positive* — glucose did not drop the way
        the insulin says it should have, which is exactly the "something
        else is pushing BG up against the insulin" signal deviation is
        supposed to surface (e.g. unaccounted carbs). This is the
        qualitative end-to-end sanity check that BGI's sign convention
        (`bgi = -activity * isf * 5`, negative while insulin acts) and
        deviation's sign convention compose the way the module docstring
        says they do."""
        basal_df = pd.DataFrame(
            {"timestamp": [_ts(h=0), _ts(h=6)], "commanded_rate": [1.0, 1.0]}
        )
        bolus_df = pd.DataFrame({"timestamp": [_ts(h=6)], "insulin_units": [10.0]})
        cgm_df = pd.DataFrame(
            {
                "timestamp": [_ts(h=7, m=5 * i) for i in range(4)],
                "bg_mgdl": [150, 150, 150, 150],  # flat despite a big bolus 1h earlier
            }
        )
        config = DeviationConfig(isf_mgdl_per_unit=50.0, iob=IobCurveConfig(dia_hours=5.0, peak_minutes=75.0))
        out = compute_deviation_frame(cgm_df, bolus_df, basal_df, config)
        # The very first CGM row never has a prior neighbor to diff against
        # (delta is NaN regardless of warm-up) — exclude it, not a warm-up
        # concern.
        warmed = out[out["warmed_up"]].dropna(subset=["deviation_5m"])
        assert len(warmed) > 0
        assert (warmed["bgi"] < 0).all()
        assert (warmed["deviation_5m"] > 0).all()

    def test_unwarmed_rows_are_nan_not_zero(self):
        cgm_df = pd.DataFrame(
            {"timestamp": [_ts(h=0, m=5 * i) for i in range(3)], "bg_mgdl": [100, 101, 102]}
        )
        bolus_df = pd.DataFrame({"timestamp": [_ts(h=0)], "insulin_units": [5.0]})
        basal_df = pd.DataFrame(columns=["timestamp", "commanded_rate"])
        config = DeviationConfig(isf_mgdl_per_unit=50.0, iob=IobCurveConfig(dia_hours=5.0, peak_minutes=75.0))
        out = compute_deviation_frame(cgm_df, bolus_df, basal_df, config)
        assert not out["warmed_up"].any()
        assert out["deviation_5m"].isna().all()
