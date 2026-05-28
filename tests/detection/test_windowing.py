import pytest
from datetime import datetime, timedelta, timezone
import pandas as pd
from core.detection.windowing import Anchor, make_window, DEFAULT_INTERVAL

# Fixed timezone standard for test repeatability
TZ = timezone(timedelta(hours=-7), name="PDT")


def test_anchor_validation():
    # Naive timestamp should raise ValueError
    with pytest.raises(ValueError, match="timestamp must be timezone-aware"):
        Anchor(datetime(2026, 5, 25, 12, 0), "live")

    # Correct tz-aware timestamp should succeed
    anchor = Anchor(datetime(2026, 5, 25, 12, 0, tzinfo=TZ), "live")
    assert anchor.timestamp.tzinfo is not None
    assert anchor.kind == "live"


def test_make_window_slicing():
    anchor_ts = datetime(2026, 5, 25, 12, 0, tzinfo=TZ)
    anchor = Anchor(anchor_ts, "live")

    # Generate 5-minute interval CGM data from 11:30 to 12:30
    timestamps = [
        anchor_ts - timedelta(minutes=30) + timedelta(minutes=5 * i)
        for i in range(13)
    ]
    bg_values = [100 + 5 * i for i in range(13)]

    cgm_df = pd.DataFrame({
        "timestamp": timestamps,
        "bg_mgdl": bg_values
    })

    # Test pre=30min, post=0min (typical M1 live trailing window)
    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0))

    assert window.start == anchor_ts - timedelta(minutes=30)
    assert window.end == anchor_ts
    assert window.n_samples == 7  # 11:30, 11:35, 11:40, 11:45, 11:50, 11:55, 12:00
    assert window.samples["timestamp"].iloc[0] == window.start
    assert window.samples["timestamp"].iloc[-1] == window.end
    assert window.coverage == 1.0  # (30 / 5) + 1 = 7 expected, 7 present

    # Test pre=30min, post=15min (retrospective window)
    retro_window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(minutes=15))
    assert retro_window.start == anchor_ts - timedelta(minutes=30)
    assert retro_window.end == anchor_ts + timedelta(minutes=15)
    assert retro_window.n_samples == 10  # 11:30 to 12:15 inclusive (7 pre + 3 post)
    # Expected: (45 / 5) + 1 = 10 expected, 10 present
    assert retro_window.coverage == 1.0


def test_make_window_sparse_and_empty():
    anchor_ts = datetime(2026, 5, 25, 12, 0, tzinfo=TZ)
    anchor = Anchor(anchor_ts, "live")

    # Empty CGM data
    cgm_df = pd.DataFrame(columns=["timestamp", "bg_mgdl"])

    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0))
    assert window.n_samples == 0
    assert window.coverage == 0.0
    assert window.samples.empty

    # Sparse data (only 3 of 7 expected are present)
    timestamps = [
        anchor_ts - timedelta(minutes=30),
        anchor_ts - timedelta(minutes=15),
        anchor_ts
    ]
    cgm_df = pd.DataFrame({
        "timestamp": timestamps,
        "bg_mgdl": [100, 110, 120]
    })
    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0))
    assert window.n_samples == 3
    assert window.coverage == float(3 / 7)


def test_make_window_gaps():
    anchor_ts = datetime(2026, 5, 25, 12, 0, tzinfo=TZ)
    anchor = Anchor(anchor_ts, "live")

    timestamps = [anchor_ts - timedelta(minutes=30) + timedelta(minutes=5 * i) for i in range(7)]
    cgm_df = pd.DataFrame({
        "timestamp": timestamps,
        "bg_mgdl": [120] * 7
    })

    # Case A: no gaps df
    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0), gaps_df=None)
    assert not window.has_gap

    # Case B: gaps df provided but no overlap
    gaps_df = pd.DataFrame({
        "start_ts": [anchor_ts - timedelta(minutes=60)],
        "end_ts": [anchor_ts - timedelta(minutes=45)],
        "ongoing": [False]
    })
    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0), gaps_df=gaps_df)
    assert not window.has_gap

    # Case C: gaps df overlaps window start
    gaps_df = pd.DataFrame({
        "start_ts": [anchor_ts - timedelta(minutes=45)],
        "end_ts": [anchor_ts - timedelta(minutes=25)],
        "ongoing": [False]
    })
    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0), gaps_df=gaps_df)
    assert window.has_gap

    # Case D: ongoing gap overlapping
    gaps_df = pd.DataFrame({
        "start_ts": [anchor_ts - timedelta(minutes=10)],
        "end_ts": [pd.NaT],
        "ongoing": [True]
    })
    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0), gaps_df=gaps_df)
    assert window.has_gap


def test_make_window_uneven_spacing_coverage():
    anchor_ts = datetime(2026, 5, 25, 12, 0, tzinfo=TZ)
    anchor = Anchor(anchor_ts, "live")
    timestamps = [
        anchor_ts - timedelta(minutes=30),
        anchor_ts - timedelta(minutes=22),
        anchor_ts - timedelta(minutes=14),
        anchor_ts - timedelta(minutes=6),
        anchor_ts,
    ]
    cgm_df = pd.DataFrame({"timestamp": timestamps, "bg_mgdl": [100, 105, 110, 115, 120]})
    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0))
    assert window.n_samples == 5
    assert window.coverage == pytest.approx(5 / 7, rel=1e-6)
