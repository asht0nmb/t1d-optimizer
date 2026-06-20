"""TIR convergence guard: every caller delegates to ``core.metrics``.

After the 4× TIR de-duplication, the local dashboard metric, the local
chart-prep stats, the Telegram digest, and the detection feature aggregator
must all agree, to the last decimal, with the shared
``core.metrics.cgm_metrics.time_in_range``. This test pins that agreement on a
shared fixture so any future drift in one caller fails loudly.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from apps.local.chart_prep import _compute_stats
from apps.local.metrics import compute_tir_percent
from apps.personal.telegram.digest import compute_tir
from core.metrics.cgm_metrics import time_in_range
from detection.features import daily_features

# Shared fixture: hand-picked BGs straddling 70 and 180 so the in-band count
# is unambiguous. 4 of 8 readings lie in [70, 180] inclusive (70, 100, 150,
# 180) → 50.0%. 55 is below; 181, 200, 260 are above.
_LOW = 70.0
_HIGH = 180.0
_BG = [55.0, 70.0, 100.0, 150.0, 180.0, 181.0, 200.0, 260.0]
_EXPECTED_PCT = 50.0


def test_all_tir_callers_agree_with_core(default_config):
    bg = pd.Series(_BG)
    shared = time_in_range(bg, _LOW, _HIGH)
    assert shared == pytest.approx(_EXPECTED_PCT)

    # apps/local/metrics.py — percent float, empty → 0.0
    assert compute_tir_percent(bg, low=_LOW, high=_HIGH) == pytest.approx(shared)

    # apps/personal/telegram/digest.py — percent float, empty → None
    assert compute_tir(bg, low=_LOW, high=_HIGH) == pytest.approx(shared)

    # apps/local/chart_prep.py — DayStats.tir_pct via _compute_stats
    cgm = pd.DataFrame({"bg_mgdl": _BG})
    empty = pd.DataFrame()
    stats = _compute_stats(cgm, empty, empty, empty, low=_LOW, high=_HIGH)
    assert stats.tir_pct == pytest.approx(shared)

    # detection/features.py — returns the SAME value as a fraction (0–1).
    # daily_features slices by the config timezone, so build the fixture
    # timestamps in that tz at midday to keep them on the target local day.
    day = date(2026, 6, 1)
    ts = pd.date_range(
        start=f"{day.isoformat()} 12:00",
        periods=len(_BG),
        freq="5min",
        tz=default_config.timezone,
    )
    frames = {"cgm": pd.DataFrame({"timestamp": ts, "bg_mgdl": _BG})}
    assert default_config.bg_targets.low == _LOW
    assert default_config.bg_targets.high == _HIGH
    feats = daily_features(frames, day, default_config)
    assert feats["tir_70_180"] * 100.0 == pytest.approx(shared)
