"""Memory-specific tests for :class:`core.storage.memory.InMemoryStorage`.

The contract test suite (:mod:`tests.core.test_storage_contract`) covers
every behavior shared with the other implementations. This file holds
the small handful of memory-specific invariants that aren't visible to
the Protocol.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from core.storage.memory import InMemoryStorage

UTC = timezone.utc


def test_unknown_table_name_raises_value_error():
    storage = InMemoryStorage()
    with pytest.raises(ValueError, match="unknown table"):
        storage.read_all_table("does_not_exist")


def test_separate_instances_do_not_share_state():
    """The implementation is purely instance-local — useful in tests."""
    a = InMemoryStorage()
    b = InMemoryStorage()
    ts = datetime(2026, 5, 13, tzinfo=UTC)
    df = pd.DataFrame(
        [{"pump_serial": "P", "seqnum": 1, "timestamp": ts,
          "bg_mgdl": 100, "backfilled": False, "sensor_timestamp": None}]
    )
    a.upsert_table("cgm", df)
    assert len(a.read_all_table("cgm")) == 1
    assert len(b.read_all_table("cgm")) == 0
