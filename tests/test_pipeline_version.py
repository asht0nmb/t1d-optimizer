"""Tests for the pipeline version constant + changelog."""

from __future__ import annotations

from ingestion.pipeline_version import (
    PIPELINE_VERSION,
    PIPELINE_VERSION_CHANGELOG,
)


def test_pipeline_version_is_positive_int() -> None:
    assert isinstance(PIPELINE_VERSION, int)
    assert PIPELINE_VERSION >= 1


def test_changelog_has_entry_for_current_version() -> None:
    assert PIPELINE_VERSION in PIPELINE_VERSION_CHANGELOG
    entry = PIPELINE_VERSION_CHANGELOG[PIPELINE_VERSION]
    assert isinstance(entry, str) and entry.strip()


def test_changelog_keys_are_contiguous_starting_at_one() -> None:
    """No missing version numbers between 1 and PIPELINE_VERSION.

    Guards against dropping a changelog entry during a refactor. If someone
    bumps the constant to 4, they must also document versions 2 and 3.
    """
    assert sorted(PIPELINE_VERSION_CHANGELOG) == list(range(1, PIPELINE_VERSION + 1))


def test_changelog_entries_are_nonempty_strings() -> None:
    for version, entry in PIPELINE_VERSION_CHANGELOG.items():
        assert isinstance(entry, str), f"v{version} entry is not a string"
        assert entry.strip(), f"v{version} entry is empty"
