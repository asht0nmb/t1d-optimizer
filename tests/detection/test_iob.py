"""Tests for detection/iob.py (Workstream A: algorithm-research phase).

Covers: the ported exponential curve's boundary/conservation properties,
the rolling-median baseline surrogate, suspension zeroing, dose-event
decomposition, and — the single most important correctness property per
the module's own docstring — the warm-up NaN policy.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from detection.iob import (
    IobCurveConfig,
    _activity_and_iob_fraction,
    _zero_during_suspensions,
    basal_baseline_rate,
    build_dose_events,
    compute_iob_activity,
)

TZ = timezone(timedelta(hours=-7), name="PDT")


def _ts(*, h, m=0, d=1):
    return datetime(2026, 8, d, h, m, tzinfo=TZ)


class TestActivityAndIobFraction:
    def test_iob_starts_near_full_at_t0(self):
        activity, iob = _activity_and_iob_fraction(np.array([0.0]), dia_hours=5.0, peak_minutes=75.0)
        assert iob[0] == pytest.approx(1.0, abs=1e-6)
        assert activity[0] == pytest.approx(0.0, abs=1e-6)

    def test_zero_outside_dia_window(self):
        end = 5.0 * 60
        activity, iob = _activity_and_iob_fraction(
            np.array([-1.0, end, end + 100]), dia_hours=5.0, peak_minutes=75.0
        )
        assert (activity == 0.0).all()
        assert (iob == 0.0).all()

    def test_iob_decays_monotonically(self):
        minutes = np.linspace(0, 5 * 60 - 1, 50)
        _, iob = _activity_and_iob_fraction(minutes, dia_hours=5.0, peak_minutes=75.0)
        assert np.all(np.diff(iob) <= 1e-9)

    def test_activity_integrates_to_full_dose(self):
        """Conservation: summing activity_frac * 1 minute over the whole DIA
        window should recover ~1.0 unit of the original dose (the curve is
        constructed so the activity integral over [0, end] equals the dose).
        """
        minutes = np.arange(0, 5 * 60, 1.0)
        activity, _ = _activity_and_iob_fraction(minutes, dia_hours=5.0, peak_minutes=75.0)
        total = activity.sum() * 1.0  # 1-minute steps
        assert total == pytest.approx(1.0, abs=0.02)

    def test_activity_peaks_near_configured_peak_minutes(self):
        minutes = np.arange(0, 5 * 60, 1.0)
        activity, _ = _activity_and_iob_fraction(minutes, dia_hours=5.0, peak_minutes=75.0)
        peak_idx = int(np.argmax(activity))
        assert abs(minutes[peak_idx] - 75.0) < 10


class TestBasalBaselineRate:
    def test_median_over_lookback_window(self):
        basal_df = pd.DataFrame(
            {
                "timestamp": [_ts(h=0), _ts(h=6), _ts(h=12), _ts(h=18)],
                "commanded_rate": [1.0, 1.0, 1.0, 3.0],
            }
        )
        # "at" = last timestamp: window includes all four rows -> median of
        # [1,1,1,3] = 1.0
        baseline = basal_baseline_rate(basal_df, at=_ts(h=18), lookback_days=7)
        assert baseline == pytest.approx(1.0)

    def test_none_when_no_history_in_window(self):
        basal_df = pd.DataFrame({"timestamp": [_ts(h=0)], "commanded_rate": [1.0]})
        baseline = basal_baseline_rate(basal_df, at=_ts(h=0, d=20), lookback_days=7)
        assert baseline is None

    def test_causal_excludes_future_rows(self):
        """A row timestamped after `at` must not influence the baseline —
        this is a nightly-batch primitive but must stay non-lookahead so it
        could, in principle, run live without modification."""
        basal_df = pd.DataFrame(
            {
                "timestamp": [_ts(h=0), _ts(h=12)],
                "commanded_rate": [1.0, 100.0],
            }
        )
        baseline = basal_baseline_rate(basal_df, at=_ts(h=1), lookback_days=7)
        assert baseline == pytest.approx(1.0)

    def test_empty_or_missing_column(self):
        assert basal_baseline_rate(pd.DataFrame(), at=_ts(h=0), lookback_days=7) is None
        assert basal_baseline_rate(None, at=_ts(h=0), lookback_days=7) is None


class TestZeroDuringSuspensions:
    def test_segment_split_around_suspension(self):
        segments = pd.DataFrame(
            [{"start": _ts(h=0), "end": _ts(h=4), "commanded_rate": 1.0}]
        )
        suspension_df = pd.DataFrame(
            [{"suspend_timestamp": _ts(h=1), "resume_timestamp": _ts(h=2)}]
        )
        out = _zero_during_suspensions(segments, suspension_df)
        # Expect 3 sub-segments: [0,1)=1.0, [1,2)=0.0, [2,4)=1.0
        assert len(out) == 3
        rates_by_start_hour = {row.start.hour: row.commanded_rate for row in out.itertuples()}
        assert rates_by_start_hour[0] == pytest.approx(1.0)
        assert rates_by_start_hour[1] == pytest.approx(0.0)
        assert rates_by_start_hour[2] == pytest.approx(1.0)

    def test_no_suspensions_passthrough(self):
        segments = pd.DataFrame(
            [{"start": _ts(h=0), "end": _ts(h=4), "commanded_rate": 1.0}]
        )
        out = _zero_during_suspensions(segments, None)
        pd.testing.assert_frame_equal(out, segments)

    def test_open_ended_suspension_zeros_to_segment_end(self):
        segments = pd.DataFrame(
            [{"start": _ts(h=0), "end": _ts(h=4), "commanded_rate": 1.0}]
        )
        suspension_df = pd.DataFrame(
            [{"suspend_timestamp": _ts(h=2), "resume_timestamp": pd.NaT}]
        )
        out = _zero_during_suspensions(segments, suspension_df)
        rates_by_start_hour = {row.start.hour: row.commanded_rate for row in out.itertuples()}
        assert rates_by_start_hour[0] == pytest.approx(1.0)
        assert rates_by_start_hour[2] == pytest.approx(0.0)


class TestBuildDoseEvents:
    def test_bolus_passthrough(self):
        bolus_df = pd.DataFrame({"timestamp": [_ts(h=8)], "insulin_units": [4.5]})
        out = build_dose_events(bolus_df, pd.DataFrame(), horizon_end=_ts(h=12))
        assert len(out) == 1
        assert out.iloc[0]["kind"] == "bolus"
        assert out.iloc[0]["units"] == pytest.approx(4.5)

    def test_basal_net_above_baseline_is_positive(self):
        # Steady 1.0 u/hr baseline, then a 2h boost to 2.0 u/hr.
        basal_df = pd.DataFrame(
            {
                "timestamp": [_ts(h=0), _ts(h=6), _ts(h=12), _ts(h=14)],
                "commanded_rate": [1.0, 1.0, 2.0, 1.0],
            }
        )
        out = build_dose_events(pd.DataFrame(), basal_df, horizon_end=_ts(h=18))
        boosted = out[(out["kind"] == "basal_net") & (out["timestamp"] == _ts(h=12))]
        assert len(boosted) == 1
        # baseline at h=12 is median([1,1,1,2]) = 1.0; segment is 2h @ +1.0u/hr = +2.0U
        assert boosted.iloc[0]["units"] == pytest.approx(2.0, abs=0.05)

    def test_segment_dropped_when_no_baseline_available(self):
        basal_df = pd.DataFrame({"timestamp": [_ts(h=0)], "commanded_rate": [1.0]})
        out = build_dose_events(
            pd.DataFrame(), basal_df, horizon_end=_ts(h=1), baseline_lookback_days=7
        )
        # Single segment, baseline computable from itself -> kept, not dropped.
        assert len(out) == 1

    def test_empty_inputs_return_empty_frame(self):
        out = build_dose_events(pd.DataFrame(), pd.DataFrame(), horizon_end=_ts(h=1))
        assert out.empty
        assert list(out.columns) == ["timestamp", "units", "kind"]


class TestComputeIobActivity:
    def test_single_bolus_warmed_up(self):
        bolus_df = pd.DataFrame({"timestamp": [_ts(h=0)], "insulin_units": [10.0]})
        dose_events = build_dose_events(bolus_df, pd.DataFrame(), horizon_end=_ts(h=6))
        config = IobCurveConfig(dia_hours=5.0, peak_minutes=75.0)
        eval_ts = pd.DatetimeIndex([_ts(h=0, m=5), _ts(h=1), _ts(h=6)])
        out = compute_iob_activity(eval_ts, dose_events, config, data_start=_ts(h=0))
        # All three eval points are >= data_start + dia_hours? No: dia=5h,
        # data_start=h0 -> warmup_cutoff = h5. h0:05 and h1 are NOT warmed up;
        # h6 is.
        assert not out.iloc[0]["warmed_up"]
        assert not out.iloc[1]["warmed_up"]
        assert out.iloc[2]["warmed_up"]
        assert np.isnan(out.iloc[0]["iob"])
        assert not np.isnan(out.iloc[2]["iob"])
        # 6h after a 10U dose with 5h DIA: fully metabolized.
        assert out.iloc[2]["iob"] == pytest.approx(0.0, abs=1e-6)

    def test_warmup_requires_full_dia_of_history(self):
        """The correctness-critical property: if data_start is recent
        relative to dia_hours, early eval timestamps must be NaN, not 0."""
        bolus_df = pd.DataFrame({"timestamp": [_ts(h=0)], "insulin_units": [10.0]})
        dose_events = build_dose_events(bolus_df, pd.DataFrame(), horizon_end=_ts(h=10))
        config = IobCurveConfig(dia_hours=5.0, peak_minutes=75.0)

        eval_ts = pd.DatetimeIndex([_ts(h=1), _ts(h=6)])
        out_short_history = compute_iob_activity(
            eval_ts, dose_events, config, data_start=_ts(h=0)
        )
        assert not out_short_history.iloc[0]["warmed_up"]

        # With a data_start far enough in the past, the same eval timestamp
        # (h=1) is warmed up.
        out_long_history = compute_iob_activity(
            eval_ts, dose_events, config, data_start=_ts(h=1) - timedelta(hours=6)
        )
        assert out_long_history.iloc[0]["warmed_up"]

    def test_empty_dose_events_all_nan_but_shaped(self):
        config = IobCurveConfig()
        eval_ts = pd.DatetimeIndex([_ts(h=10)])
        out = compute_iob_activity(
            eval_ts, pd.DataFrame(columns=["timestamp", "units", "kind"]), config, data_start=_ts(h=0)
        )
        assert len(out) == 1
        assert np.isnan(out.iloc[0]["iob"])
