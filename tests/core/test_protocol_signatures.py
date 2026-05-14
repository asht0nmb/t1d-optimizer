"""Smoke tests for the Storage Protocol surface.

The Protocol itself has no runtime tests — its behavior is exercised by
:mod:`tests.core.test_storage_contract` against each implementation.
These tests catch typos in the method-name surface so a stray rename
doesn't silently turn a Protocol method into a non-Protocol attribute.
"""

from __future__ import annotations

from core.storage.protocol import Storage


EXPECTED_METHODS = {
    # data tables
    "read_table",
    "read_all_table",
    "upsert_table",
    "delete_range",
    # fetch state
    "get_fetch_state",
    "set_fetch_state",
    "list_fetch_state",
    # pipeline version
    "get_pipeline_version",
    "set_pipeline_version",
    # alerts
    "record_alert",
    "find_alert",
    "recent_alerts",
    # detection results
    "record_detection_result",
    "list_detection_results",
    # housekeeping
    "clean_all",
}


def test_storage_protocol_exposes_expected_methods():
    actual = {name for name in dir(Storage) if not name.startswith("_")}
    missing = EXPECTED_METHODS - actual
    assert not missing, f"Storage Protocol missing methods: {sorted(missing)}"
