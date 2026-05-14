"""Tests for `core.schema` table registry."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.schema import TABLES, TableSpec, get_spec


EXPECTED_TABLE_NAMES = {
    "cgm",
    "bolus",
    "requests",
    "basal",
    "suspension",
    "events",
    "alarms",
    "site_issues",
    "cgm_gaps",
}


# ---------------------------------------------------------------------------
# TableSpec dataclass
# ---------------------------------------------------------------------------


class TestTableSpec:
    def test_is_frozen(self):
        spec = TABLES["cgm"]
        with pytest.raises((AttributeError, Exception)):
            spec.name = "other"  # type: ignore[misc]

    def test_has_required_fields(self):
        spec = TABLES["cgm"]
        assert isinstance(spec, TableSpec)
        assert spec.name == "cgm"
        assert isinstance(spec.primary_key, tuple)
        assert all(isinstance(c, str) for c in spec.primary_key)
        assert isinstance(spec.time_column, str)


# ---------------------------------------------------------------------------
# TABLES registry
# ---------------------------------------------------------------------------


class TestTables:
    def test_has_nine_data_tables(self):
        assert set(TABLES.keys()) == EXPECTED_TABLE_NAMES

    @pytest.mark.parametrize("name", sorted(EXPECTED_TABLE_NAMES))
    def test_entry_key_matches_name(self, name):
        assert TABLES[name].name == name

    @pytest.mark.parametrize(
        "name,expected_pk,expected_time",
        [
            ("cgm",         ("pump_serial", "seqnum"),            "timestamp"),
            ("bolus",       ("pump_serial", "bolus_id"),          "timestamp"),
            ("requests",    ("pump_serial", "bolus_id"),          "timestamp"),
            ("basal",       ("pump_serial", "timestamp"),         "timestamp"),
            ("suspension",  ("pump_serial", "suspend_timestamp"), "suspend_timestamp"),
            ("events",      ("pump_serial", "seqnum"),            "timestamp"),
            ("alarms",      ("pump_serial", "seqnum"),            "timestamp"),
            ("site_issues", ("pump_serial", "first_occlusion_ts"),"first_occlusion_ts"),
            ("cgm_gaps",    ("pump_serial", "start_ts"),          "start_ts"),
        ],
    )
    def test_spec_shape_matches_plan(self, name, expected_pk, expected_time):
        spec = TABLES[name]
        assert spec.primary_key == expected_pk
        assert spec.time_column == expected_time


# ---------------------------------------------------------------------------
# get_spec()
# ---------------------------------------------------------------------------


class TestGetSpec:
    def test_returns_known_spec(self):
        assert get_spec("cgm") is TABLES["cgm"]

    def test_unknown_raises_value_error(self):
        with pytest.raises(ValueError, match="unknown table"):
            get_spec("does_not_exist")

    def test_unknown_lists_known_in_message(self):
        with pytest.raises(ValueError) as excinfo:
            get_spec("nope")
        msg = str(excinfo.value)
        # Message should mention at least a couple of known tables so a
        # caller hitting a typo can see the valid set.
        assert "cgm" in msg
        assert "bolus" in msg


# ---------------------------------------------------------------------------
# Registry consistency with db/migrations/0001_init.sql
# ---------------------------------------------------------------------------


def _read_migration_sql() -> str:
    sql_path = Path(__file__).resolve().parents[2] / "db" / "migrations" / "0001_init.sql"
    return sql_path.read_text()


def _primary_key_from_migration(table: str, sql_text: str) -> tuple[str, ...]:
    """Pull the PRIMARY KEY (a, b) line from the CREATE TABLE block for `table`."""
    create_re = re.compile(
        rf"CREATE TABLE IF NOT EXISTS {table} \((?P<body>.*?)^\);",
        re.DOTALL | re.MULTILINE,
    )
    match = create_re.search(sql_text)
    if not match:
        raise AssertionError(f"no CREATE TABLE block for {table!r} in migration SQL")
    body = match.group("body")
    pk_re = re.compile(r"PRIMARY KEY\s*\(([^)]+)\)", re.IGNORECASE)
    pk_match = pk_re.search(body)
    if not pk_match:
        raise AssertionError(f"no PRIMARY KEY clause for {table!r} in migration SQL")
    cols = tuple(c.strip() for c in pk_match.group(1).split(","))
    return cols


class TestMigrationConsistency:
    @pytest.mark.parametrize("name", sorted(EXPECTED_TABLE_NAMES))
    def test_primary_key_matches_migration(self, name):
        sql = _read_migration_sql()
        sql_pk = _primary_key_from_migration(name, sql)
        registry_pk = TABLES[name].primary_key
        assert set(sql_pk) == set(registry_pk), (
            f"{name}: registry PK {registry_pk} differs from migration PK {sql_pk}"
        )
