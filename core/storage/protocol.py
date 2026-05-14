"""Backend-agnostic data layer Protocol.

Every caller in `core/` and the downstream shells takes a
:class:`Storage` and the deployment shell decides which concrete
implementation to instantiate at startup. Three implementations exist
(or are planned):

* :class:`core.storage.parquet.ParquetStorage` — local files; today's
  default and the OSS shell day-one impl.
* :class:`core.storage.memory.InMemoryStorage` — in-process dicts and
  DataFrames; used by tests.
* ``core.storage.supabase.SupabaseStorage`` — Postgres via psycopg2;
  *implementation pending* in a follow-up PR (Phase 3 of the plan).

Concrete implementations are checked against
:mod:`tests.core.test_storage_contract` — every behavior described in
the docstrings below has at least one parameterized test.

Design notes
------------
* **Time bounds are required for ``read_table``.** This prevents
  accidental whole-table reads (especially over Supabase, where
  ``SELECT *`` against ``cgm`` would return 700K+ rows). Callers that
  genuinely want every row use :meth:`Storage.read_all_table`, which
  is explicit by name.
* **``delete_range`` requires at least one scope.** A ``DELETE``
  without ``WHERE`` is too easy a footgun. At least one of
  ``since`` / ``until`` / ``pump_serial`` must be passed; implementations
  raise :class:`ValueError` otherwise.
* **``upsert_table`` is idempotent by primary key.** Re-running the
  same upsert is a no-op modulo the returned :class:`UpsertResult`
  counters. Backends that can collapse conflicts at write time (parquet
  via concat + drop_duplicates; Postgres via ``ON CONFLICT DO
  NOTHING``) do so.
* **``record_alert`` deduplicates on ``(alert_kind, event_ref)`` when
  ``event_ref`` is non-``None``.** A second call with the same
  ``(alert_kind, event_ref)`` returns the existing record without
  inserting; this mirrors the partial unique index on the Postgres
  ``alerts_sent`` table (see ``db/migrations/0001_init.sql``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

import pandas as pd

from core.storage.records import (
    AlertRecord,
    DetectionResult,
    FetchState,
    UpsertResult,
)


@runtime_checkable
class Storage(Protocol):
    """Backend-agnostic data layer.

    Implementations: :class:`ParquetStorage` (local files),
    ``SupabaseStorage`` (Postgres, pending), :class:`InMemoryStorage`
    (tests). Callers never reference a concrete impl; they accept a
    :class:`Storage` and the shell decides.
    """

    # ── data tables ──────────────────────────────────────────────────

    def read_table(
        self,
        name: str,
        *,
        since: datetime,
        until: datetime,
        pump_serial: str | None = None,
    ) -> pd.DataFrame:
        """Read rows from ``name`` whose time-column is in [``since``,
        ``until``), optionally scoped to ``pump_serial``.

        Time bounds are REQUIRED to prevent accidental whole-table
        reads — use :meth:`read_all_table` for unbounded reads.
        Implementations raise :class:`TypeError` if either bound is
        omitted (the keyword-only signature pins this where Python can
        enforce it).

        Args:
            name: Logical table name; must be in :data:`core.schema.TABLES`.
            since: Inclusive lower bound on the table's time column.
            until: Exclusive upper bound on the table's time column.
            pump_serial: Optional pump-serial filter (most tables
                carry a ``pump_serial`` column; the filter is a no-op
                when ``None``).

        Returns:
            A DataFrame with the table's full column set when rows
            exist on the bounds. A bare (column-less, length-zero)
            DataFrame at cold-start, before the table has ever been
            written — the Protocol does NOT guarantee a typed empty
            frame, because parquet's column list is materialised on
            first write. Callers that need a typed empty frame
            should ``.reindex`` against the spec's known columns
            themselves.
        """
        ...

    def read_all_table(self, name: str) -> pd.DataFrame:
        """Read every row in ``name``.

        Used by batch jobs (bootstrap, full-historical calibration
        sweeps). Explicit by name so it isn't reached for accidentally
        — most callers want :meth:`read_table` with bounds.

        Args:
            name: Logical table name; must be in :data:`core.schema.TABLES`.

        Returns:
            A DataFrame with the table's full column set when rows
            exist on disk. A bare (column-less, length-zero)
            DataFrame at cold-start — see :meth:`read_table` for the
            same caveat.
        """
        ...

    def upsert_table(self, name: str, df: pd.DataFrame) -> UpsertResult:
        """Insert rows into ``name``; conflicts on the table's primary
        key are silently skipped.

        Idempotent: re-upserting identical rows is a no-op modulo the
        returned :class:`UpsertResult` counters.

        Args:
            name: Logical table name; must be in :data:`core.schema.TABLES`.
            df: Rows to upsert. May be empty (a no-op that still
                returns a zero-filled :class:`UpsertResult`).

        Returns:
            Per-call counts: ``rows_received`` (rows the caller passed
            in), ``rows_inserted`` (rows actually written),
            ``rows_skipped`` (collapsed by PK conflict),
            ``elapsed_seconds``.
        """
        ...

    def delete_range(
        self,
        name: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        pump_serial: str | None = None,
    ) -> int:
        """Delete rows from ``name`` whose time-column falls in
        [``since``, ``until``), optionally scoped to ``pump_serial``.

        At least one of ``since`` / ``until`` / ``pump_serial`` MUST
        be non-``None``; implementations raise :class:`ValueError`
        otherwise. This prevents the obvious footgun of a ``DELETE``
        with no ``WHERE`` clause.

        Used by the Tandem sync to clear a pre-window CGM range
        before backfilling fresh rows.

        Args:
            name: Logical table name; must be in :data:`core.schema.TABLES`.
            since: Inclusive lower bound on the table's time column.
            until: Exclusive upper bound on the table's time column.
            pump_serial: Optional pump-serial filter.

        Returns:
            Count of rows actually deleted.
        """
        ...

    # ── fetch state (per-source sync bookmarks) ──────────────────────

    def get_fetch_state(self, source_id: str) -> FetchState | None:
        """Return the :class:`FetchState` for ``source_id``, or
        ``None`` if the source has not yet been recorded."""
        ...

    def set_fetch_state(self, source_id: str, state: FetchState) -> None:
        """Replace the :class:`FetchState` for ``source_id``.

        The ``state.source_id`` field is authoritative — implementations
        store the record under that key regardless of the ``source_id``
        argument (which is kept for symmetry with :meth:`get_fetch_state`
        and to make callers' intent explicit).
        """
        ...

    def list_fetch_state(self) -> list[FetchState]:
        """Return every recorded :class:`FetchState` in insertion order."""
        ...

    # ── pipeline version (schema-drift guard) ────────────────────────

    def get_pipeline_version(self) -> int | None:
        """Return the currently-recorded pipeline version, or
        ``None`` if none has been written yet."""
        ...

    def set_pipeline_version(self, version: int) -> None:
        """Stamp the storage with ``version`` so the schema-drift
        guard (``ingestion.version_guard``) can detect mismatches."""
        ...

    # ── alerts (live-path dedup) ─────────────────────────────────────

    def record_alert(self, alert: AlertRecord) -> AlertRecord:
        """Insert an alert record.

        If ``alert.event_ref`` is non-``None`` and an alert with the
        same ``(alert_kind, event_ref)`` already exists, return the
        existing record unchanged WITHOUT inserting a new one. This
        mirrors the partial unique index on the Postgres
        ``alerts_sent`` table.

        When the input ``alert.id`` is ``None`` and a new row is
        inserted, the implementation fabricates an id (Postgres:
        ``RETURNING id`` from the ``bigserial`` column; parquet:
        ``uuid4``).

        Raises :class:`ValueError` if ``alert.sent_at`` is
        timezone-naive — :meth:`recent_alerts` compares against
        tz-aware "now", so naive timestamps would crash at compare
        time. The validation forces callers to supply tz-aware values
        at the source.
        """
        ...

    def find_alert(
        self, alert_kind: str, event_ref: str
    ) -> AlertRecord | None:
        """Return the most recently-recorded alert with the given
        ``(alert_kind, event_ref)``, or ``None`` if none exists."""
        ...

    def recent_alerts(
        self, alert_kind: str, within: timedelta
    ) -> list[AlertRecord]:
        """Return every alert with ``alert_kind`` whose ``sent_at``
        is within ``within`` of "now" (storage's notion of now),
        ordered most-recent-first."""
        ...

    # ── detection results (triggered analysis log) ───────────────────

    def record_detection_result(self, result: DetectionResult) -> None:
        """Append a :class:`DetectionResult`. No dedup; every call
        produces a new row (callers do their own idempotency)."""
        ...

    def list_detection_results(
        self,
        *,
        kind: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[DetectionResult]:
        """Return detection results, most recent first.

        Args:
            kind: Optional filter on :attr:`DetectionResult.kind`.
            since: Optional inclusive lower bound on
                :attr:`DetectionResult.created_at`.
            limit: Maximum number of results to return (the most
                recent ones if more are recorded).
        """
        ...

    # ── housekeeping ─────────────────────────────────────────────────

    def clean_all(self) -> None:
        """Delete every row from every table (data, metadata, alerts,
        detection results, fetch state, pipeline version).

        Destructive. Used by ``fetch --clean`` and dev workflows.
        """
        ...
