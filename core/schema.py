"""Schema registry: single source of truth for table identity.

Owns the canonical (logical) table name, primary key columns, and time
column for every storage table. Does NOT own column type definitions —
Postgres types live canonically in `db/migrations/0001_init.sql`, and
parquet infers from pandas dtypes.

Both `ParquetStorage` and (the eventual) `SupabaseStorage` consume this
registry. `scripts/bootstrap_supabase.py` will migrate to importing
`TABLES` from here in a follow-up PR so its `TABLE_SPECS` list isn't a
second source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TableSpec:
    """Logical identity of a storage table.

    Attributes:
        name: Canonical (logical) name. Same on disk (e.g.
            ``cgm.parquet``) and in Postgres.
        primary_key: Columns whose combination uniquely identifies a
            row. Used by every backend for dedup-on-upsert and as the
            ``ON CONFLICT`` target in Postgres.
        time_column: Column used by ``Storage.read_table`` for the
            ``since`` / ``until`` window filter. Most tables use
            ``timestamp``; a few have a domain-specific time column
            (e.g. ``suspend_timestamp`` on the suspension table).
    """

    name: str
    primary_key: tuple[str, ...]
    time_column: str


TABLES: dict[str, TableSpec] = {
    "cgm":         TableSpec("cgm",         ("pump_serial", "seqnum"),            "timestamp"),
    "bolus":       TableSpec("bolus",       ("pump_serial", "bolus_id"),          "timestamp"),
    "requests":    TableSpec("requests",    ("pump_serial", "bolus_id"),          "timestamp"),
    "basal":       TableSpec("basal",       ("pump_serial", "timestamp"),         "timestamp"),
    "suspension":  TableSpec("suspension",  ("pump_serial", "suspend_timestamp"), "suspend_timestamp"),
    "events":      TableSpec("events",      ("pump_serial", "seqnum"),            "timestamp"),
    "alarms":      TableSpec("alarms",      ("pump_serial", "seqnum"),            "timestamp"),
    "site_issues": TableSpec("site_issues", ("pump_serial", "first_occlusion_ts"),"first_occlusion_ts"),
    "cgm_gaps":    TableSpec("cgm_gaps",    ("pump_serial", "start_ts"),          "start_ts"),
}


def get_spec(name: str) -> TableSpec:
    """Return the :class:`TableSpec` for ``name``.

    Raises:
        ValueError: ``name`` is not a known table; the message lists
            every known table to help callers fix typos.
    """
    if name not in TABLES:
        raise ValueError(f"unknown table {name!r}; known: {sorted(TABLES)}")
    return TABLES[name]
