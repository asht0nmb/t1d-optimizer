"""Typed metadata records used by the Storage Protocol.

These records describe the rows of the metadata tables where a DataFrame
is overkill: alerts the live loop has sent, fetch-state bookmarks per
ingestion source, the pipeline-version sidecar, and the structured
output of each detection trigger.

Design choice: every record is a `@dataclass(frozen=True)`. We chose
stdlib dataclasses over `pydantic.BaseModel` because:

* The Storage Protocol is in `core/`, which restricts third-party deps.
  Pydantic is allowed by the boundary rules, but adding it for these
  four record types isn't justified — they carry plain JSON-shaped data
  with no validation logic.
* All four records are immutable identity-by-value records, which is
  exactly what `frozen=True` dataclasses provide.
* Backends (parquet, Postgres) convert to/from the underlying storage
  in their own files; records don't need their own (de)serialisers.

If a future record needs validation (e.g. enum constraints on
``alert_kind`` or ``DetectionResult.kind``), promote that single record
to a pydantic model and document the asymmetry here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class UpsertResult:
    """Outcome of a single :meth:`Storage.upsert_table` call.

    Attributes:
        rows_received: Rows in the DataFrame the caller passed in
            (before any backend-side dedup or PK conflict handling).
        rows_inserted: Rows the backend actually wrote (parquet:
            count after concat/dedup minus the prior on-disk row
            count; Postgres: ``cur.rowcount`` after ``ON CONFLICT DO
            NOTHING``).
        rows_skipped: ``rows_received - rows_inserted``; the count
            collapsed by PK conflict.
        elapsed_seconds: Wall-clock time spent inside the call;
            populated for diagnostics, not invariants.
    """

    rows_received: int
    rows_inserted: int
    rows_skipped: int
    elapsed_seconds: float


@dataclass(frozen=True)
class FetchState:
    """Per-source incremental-sync bookmark.

    Mirrors the ``fetch_state`` Postgres table (see migration 0001) but
    keeps source-specific extras out of the typed shape so that the
    record doesn't grow a column every time a new ingestion source
    lands.

    Attributes:
        source_id: Source identifier (e.g. a pump serial for
            tconnectsync, the literal string ``pydexcom`` for the live
            CGM connector). Primary key.
        last_cursor: Opaque cursor string the source-specific
            connector advances each fetch. ``None`` when the source
            uses a date window instead.
        last_fetched_at: Wall-clock of the most recent successful
            fetch; ``None`` before any fetch has completed.
        payload: Source-specific extras (per-event-type cursors,
            actual date ranges, etc.). The existing
            ``ingestion.storage.load_fetch_state`` dict shape maps
            directly into this field for the ``"tandem"`` source.
        source_kind: Connector kind (e.g. ``"tconnectsync"``,
            ``"pydexcom"``). Mirrors the ``fetch_state.source_kind``
            Postgres column (NOT NULL). Defaults to ``"unknown"`` for
            callers that pre-date the field; the connectors populate the
            real value when they sync.
    """

    source_id: str
    last_cursor: str | None
    last_fetched_at: datetime | None
    payload: dict[str, Any] = field(default_factory=dict)
    source_kind: str = "unknown"


@dataclass(frozen=True)
class AlertRecord:
    """A single delivered (or about-to-be-delivered) alert.

    Mirrors the ``alerts_sent`` Postgres table (see migration 0001).
    ``id`` is ``None`` until insertion populates it; the parquet impl
    fabricates a UUID at insert time, the Postgres impl receives the
    ``bigserial`` value back from the ``RETURNING`` clause.

    Attributes:
        id: Storage-assigned identifier. ``None`` for not-yet-inserted
            records; non-``None`` after :meth:`Storage.record_alert`.
        alert_kind: Alert kind identifier (e.g. ``anomaly_spike``,
            ``missed_meal``, ``site_failure``).
        event_ref: Opaque per-event dedup key owned by the detector
            that produced the alert. When non-``None``, the partial
            unique index on ``(alert_kind, event_ref)`` enforces "send
            this alert at most once per event".
        sent_at: Wall-clock of the (attempted) send. **MUST be
            timezone-aware** — every implementation compares against
            tz-aware "now" in :meth:`Storage.recent_alerts`; passing a
            naive ``datetime`` causes a ``TypeError`` at compare time.
            :meth:`Storage.record_alert` rejects naive values with a
            :class:`ValueError` so the failure surfaces at the source.
        payload: JSON-shaped extras carried with the alert (e.g.
            current BG, trend, message text).
        pump_serial: Pump that produced the alert, or ``None`` for
            non-pump-scoped alert kinds (the Postgres column is
            nullable, so ``None`` is a valid persistent value, not just
            a sentinel for "not set yet").
        delivery: Delivery status — typically ``"pending"`` (default,
            mirrors the Postgres column default), ``"sent"``, or
            ``"failed"``. The live alert loop advances this column as
            it ships the alert downstream.
    """

    id: str | None
    alert_kind: str
    event_ref: str | None
    sent_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    pump_serial: str | None = None
    delivery: str = "pending"


@dataclass(frozen=True)
class DetectionResult:
    """A single triggered detection — the minimal record shape.

    Generic by design (see the plan's "Detection result schema" open
    question). When episodes or patterns demand more structure, we add
    columns or split into a discriminated table per the user's earlier
    guidance.

    Attributes:
        kind: Detection family identifier (e.g. ``missed_meal``,
            ``anomaly_spike``, ``occlusion_cluster``).
        anchor_timestamp: The data point in the time-series that this
            detection is anchored to (typically the meal start, spike
            apex, or alarm time).
        payload: JSON-shaped per-detection details.
        created_at: Wall-clock when the detection ran.
    """

    kind: str
    anchor_timestamp: datetime
    payload: dict[str, Any]
    created_at: datetime
