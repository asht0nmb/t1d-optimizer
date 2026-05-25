"""Postgres-backed :class:`core.storage.protocol.Storage` implementation.

Speaks to Supabase Postgres via psycopg2. Two constructor entry points:

* :class:`SupabaseStorage` ``(conn=...)`` — caller-managed connection.
  Used by the bootstrap script and the GitHub Action nightly sync. The
  storage instance does NOT close the connection on ``close()`` / on
  exiting the ``with`` block; the caller owns the lifecycle.
* :meth:`SupabaseStorage.from_pooler_url` — opens a fresh connection
  against Supabase's transaction-mode pooler URL
  (``aws-0-<region>.pooler.supabase.com:6543``). Used by Vercel cron
  functions, Telegram webhooks, dashboard API routes. The storage
  instance owns the connection and closes it on ``close()`` / context
  exit.

The implementation honours the same contract semantics as
:class:`core.storage.parquet.ParquetStorage` and
:class:`core.storage.memory.InMemoryStorage`; the parameterized
:mod:`tests.core.test_storage_contract` suite validates the three
implementations against one another.

Connection-pool ergonomics:

* Every method that mutates state commits before returning.
* Read-only methods (``read_table``, ``read_all_table``, ``get_*``,
  ``find_alert``, ``recent_alerts``, ``list_*``) do NOT commit — they
  don't mutate state.
* ``upsert_table`` commits per chunk (matches the bootstrap pattern;
  durable against a network blip mid-load).
* No long-lived transactions; the only "open" transaction is the one
  Postgres opens implicitly for the duration of a single SQL statement.

Type-precision trade-off: Postgres ``numeric(p,s)`` columns come back
as :class:`decimal.Decimal`; this implementation casts them to ``float``
in :func:`_normalize_value` so DataFrames compare uniformly across
backends (parquet stores native floats). The ``numeric(6,3)`` columns
used in this schema do not lose information under the float64 round
trip; if a future column demands wider precision, drop the cast.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pandas as pd

try:  # psycopg2 is a runtime dep; raise a clear error if it isn't installed.
    import psycopg2  # type: ignore[import-not-found]
    from psycopg2.extras import (  # type: ignore[import-not-found]
        Json,
        RealDictCursor,
        execute_values,
    )
except ImportError as exc:  # pragma: no cover - the dep is in pyproject.toml
    raise ImportError(
        "psycopg2 is required to use SupabaseStorage. Install via "
        "`uv add psycopg2-binary`."
    ) from exc

from core.schema import TABLES, get_spec
from core.storage._postgres_converters import COLUMN_SPECS, CONVERTERS
from core.storage.memory import _require_tz_aware
from core.storage.records import (
    AlertInsertResult,
    AlertRecord,
    DetectionResult,
    FetchState,
    UpsertResult,
)

# All known metadata tables. ``clean_all`` truncates these plus every
# table in ``core.schema.TABLES``.
_METADATA_TABLES: tuple[str, ...] = (
    "alerts_sent",
    "fetch_state",
    "detection_config",
    "detection_results",
)

# detection_config key under which the pipeline version is stored.
_PIPELINE_VERSION_KEY = "pipeline_version"

# Default chunk size for upsert_table's execute_values batching. Matches
# the bootstrap default and works well against both direct and pooler
# connections.
_DEFAULT_UPSERT_CHUNK = 5000

# Column list used in alerts_sent SELECT / RETURNING clauses. Constant so
# the four call sites stay in lockstep.
_ALERT_COLUMNS_SQL = "id, alert_kind, fired_at, pump_serial, event_ref, payload, delivery"


def _row_to_alert(row: dict[str, Any]) -> AlertRecord:
    """Convert a psycopg2 RealDictCursor row into an :class:`AlertRecord`."""
    payload = row.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    fired_at = row["fired_at"]
    if fired_at is not None and fired_at.tzinfo is None:
        # Postgres timestamptz always returns tz-aware, but pin the invariant.
        fired_at = fired_at.replace(tzinfo=timezone.utc)
    return AlertRecord(
        id=str(row["id"]) if row.get("id") is not None else None,
        alert_kind=row["alert_kind"],
        event_ref=row.get("event_ref"),
        sent_at=fired_at,
        payload=dict(payload) if isinstance(payload, dict) else {},
        pump_serial=row.get("pump_serial"),
        delivery=row.get("delivery", "pending"),
    )


def _normalize_value(value: Any) -> Any:
    """Convert Postgres-side types into the shape parquet round-trips produce.

    Three cases:

    * ``numeric`` columns come back as :class:`decimal.Decimal`; parquet
      returns ``float``. Cast to float so ``read_table`` comparisons
      against integer / float literals work uniformly across backends.
      See module docstring re: precision.
    * ``jsonb`` columns come back as already-parsed Python objects
      (``dict`` / ``list``) via psycopg2's default adapter; parquet
      stores them as JSON-encoded strings (the bootstrap writes them
      that way and detection / dashboard readers expect strings until
      they explicitly parse). Re-encode to JSON text so both backends
      return the same shape from ``read_table`` / ``read_all_table``.
      (Methods that own decoding internally — ``record_alert``,
      ``list_detection_results``, ``_row_to_fetch_state`` — are
      unaffected because they consume the dict directly before this
      normalisation runs.)
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


def _rows_to_dataframe(
    rows: list[dict[str, Any]],
    *,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """Build a DataFrame from a list-of-dict result.

    Passing ``columns`` preserves the table's column list when the query
    returned zero rows — keeps callers from tripping on a missing column
    just because today's window happened to be empty.
    """
    if not rows:
        if columns:
            return pd.DataFrame(columns=columns)
        return pd.DataFrame()
    normalized = [
        {k: _normalize_value(v) for k, v in row.items()} for row in rows
    ]
    return pd.DataFrame(normalized)


class SupabaseStorage:
    """Postgres-backed :class:`Storage` implementation."""

    # ── construction / lifecycle ────────────────────────────────────

    def __init__(self, conn: Any) -> None:
        """Use an existing psycopg2 connection. Caller owns its lifecycle.

        ``__enter__`` returns ``self``; ``__exit__`` and ``close`` are
        no-ops. The bootstrap script and the GitHub Action nightly sync
        use this entry point.
        """
        if conn is None:
            raise ValueError(
                "conn must be a psycopg2 connection; "
                "use SupabaseStorage.from_pooler_url(url) if you want one "
                "opened for you."
            )
        self._conn = conn
        self._owns_conn = False

    @classmethod
    def from_pooler_url(cls, url: str) -> "SupabaseStorage":
        """Open a fresh connection against ``url``; the instance owns it.

        Use this from Vercel functions, Telegram webhooks, and dashboard
        API routes — short-lived callers that talk to Supabase's
        transaction-mode pooler.
        """
        conn = psycopg2.connect(url, connect_timeout=10)
        instance = cls(conn)
        instance._owns_conn = True
        return instance

    def __enter__(self) -> "SupabaseStorage":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self._owns_conn:
            self.close()

    def __del__(self) -> None:
        # Safety net for instances that own their conn but never reach
        # __exit__ / close() (e.g. an exception before the with-block
        # exit on a Vercel function). psycopg2.connection.close() is
        # idempotent, so this is harmless when close() already ran.
        try:
            if getattr(self, "_owns_conn", False) and getattr(self, "_conn", None) is not None:
                self._conn.close()
        except Exception:  # pragma: no cover - __del__ must never raise
            pass

    def close(self) -> None:
        """Close the underlying connection iff this instance owns it."""
        if self._owns_conn and self._conn is not None:
            self._conn.close()

    # ── data tables ─────────────────────────────────────────────────

    def read_table(
        self,
        name: str,
        *,
        since: datetime,
        until: datetime,
        pump_serial: str | None = None,
    ) -> pd.DataFrame:
        _require_tz_aware(since, "since")
        _require_tz_aware(until, "until")
        spec = get_spec(name)
        time_col = spec.time_column
        sql = (
            f"SELECT * FROM {name} "
            f"WHERE {time_col} >= %s AND {time_col} < %s"
        )
        params: list[Any] = [since, until]
        if pump_serial is not None:
            sql += " AND pump_serial = %s"
            params.append(pump_serial)
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            columns = (
                [d.name for d in cur.description] if cur.description else []
            )
        return _rows_to_dataframe(rows, columns=columns)

    def read_all_table(self, name: str) -> pd.DataFrame:
        get_spec(name)  # validate name
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SELECT * FROM {name}")
            rows = [dict(r) for r in cur.fetchall()]
            columns = (
                [d.name for d in cur.description] if cur.description else []
            )
        return _rows_to_dataframe(rows, columns=columns)

    def upsert_table(
        self,
        name: str,
        df: pd.DataFrame,
        *,
        chunk_size: int = _DEFAULT_UPSERT_CHUNK,
    ) -> UpsertResult:
        spec = get_spec(name)
        started = time.perf_counter()
        rows_received = len(df)
        if rows_received == 0:
            return UpsertResult(
                rows_received=0,
                rows_inserted=0,
                rows_skipped=0,
                elapsed_seconds=time.perf_counter() - started,
            )

        cols = COLUMN_SPECS[name]
        convert = CONVERTERS[name]
        pk_cols = list(spec.primary_key)

        sql = (
            f'INSERT INTO {name} ({", ".join(cols)}) VALUES %s '
            f'ON CONFLICT ({", ".join(pk_cols)}) DO NOTHING'
        )

        tuples = [convert(rec) for rec in df.to_dict(orient="records")]

        inserted = 0
        with self._conn.cursor() as cur:
            for start in range(0, len(tuples), chunk_size):
                chunk = tuples[start:start + chunk_size]
                execute_values(cur, sql, chunk, page_size=chunk_size)
                inserted += cur.rowcount
                self._conn.commit()

        skipped = rows_received - inserted
        return UpsertResult(
            rows_received=rows_received,
            rows_inserted=inserted,
            rows_skipped=skipped,
            elapsed_seconds=time.perf_counter() - started,
        )

    def delete_range(
        self,
        name: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        pump_serial: str | None = None,
    ) -> int:
        if since is None and until is None and pump_serial is None:
            raise ValueError(
                "delete_range requires at least one of since / until / pump_serial"
            )

        spec = get_spec(name)
        time_col = spec.time_column

        conditions: list[str] = []
        params: list[Any] = []
        if since is not None:
            _require_tz_aware(since, "since")
            conditions.append(f"{time_col} >= %s")
            params.append(since)
        if until is not None:
            _require_tz_aware(until, "until")
            conditions.append(f"{time_col} < %s")
            params.append(until)
        if pump_serial is not None:
            conditions.append("pump_serial = %s")
            params.append(pump_serial)

        sql = f"DELETE FROM {name} WHERE " + " AND ".join(conditions)

        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            deleted = cur.rowcount
        self._conn.commit()
        return int(deleted)

    # ── fetch state ─────────────────────────────────────────────────

    @staticmethod
    def _row_to_fetch_state(row: dict[str, Any]) -> FetchState:
        meta = row.get("meta") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        last_cursor_raw = meta.get("last_cursor")
        last_cursor = (
            last_cursor_raw if isinstance(last_cursor_raw, str) or last_cursor_raw is None
            else str(last_cursor_raw)
        )
        # The rest of meta (everything other than the reserved key) goes
        # into payload. Mirrors what set_fetch_state writes.
        payload = {k: v for k, v in meta.items() if k != "last_cursor"}
        last_fetched_at = row.get("last_synced_at")
        # ``or "unknown"`` would collapse empty strings; use a None-aware
        # default instead so callers that intentionally store ``""`` are
        # preserved verbatim.
        raw_kind = row.get("source_kind")
        source_kind = raw_kind if raw_kind is not None else "unknown"
        return FetchState(
            source_id=row["source_id"],
            last_cursor=last_cursor,
            last_fetched_at=last_fetched_at,
            payload=payload,
            source_kind=source_kind,
        )

    def get_fetch_state(self, source_id: str) -> FetchState | None:
        sql = (
            "SELECT source_id, source_kind, last_synced_at, meta "
            "FROM fetch_state WHERE source_id = %s"
        )
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (source_id,))
            row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_fetch_state(dict(row))

    def set_fetch_state(self, source_id: str, state: FetchState) -> None:
        # The dataclass field is authoritative — Protocol contract.
        canonical_source_id = state.source_id
        if "last_cursor" in state.payload:
            # ``last_cursor`` is a reserved meta key (we stash the dataclass
            # field there). Reject collisions loudly rather than silently
            # overwriting caller data on the round trip.
            raise ValueError(
                "FetchState.payload must not contain a 'last_cursor' key — "
                "set state.last_cursor instead."
            )
        meta = dict(state.payload)
        meta["last_cursor"] = state.last_cursor
        sql = (
            "INSERT INTO fetch_state (source_id, source_kind, last_synced_at, meta) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (source_id) DO UPDATE SET "
            "source_kind = EXCLUDED.source_kind, "
            "last_synced_at = EXCLUDED.last_synced_at, "
            "meta = EXCLUDED.meta, "
            "updated_at = now()"
        )
        with self._conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    canonical_source_id,
                    state.source_kind,
                    state.last_fetched_at,
                    Json(meta),
                ),
            )
        self._conn.commit()

    def list_fetch_state(self) -> list[FetchState]:
        # source_id breaks ties when multiple rows share the same
        # updated_at (e.g. seed data inserted in a single transaction);
        # keeps ordering deterministic across calls.
        sql = (
            "SELECT source_id, source_kind, last_synced_at, meta "
            "FROM fetch_state ORDER BY updated_at ASC, source_id ASC"
        )
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            rows = [dict(r) for r in cur.fetchall()]
        return [self._row_to_fetch_state(r) for r in rows]

    # ── pipeline version (detection_config row) ─────────────────────

    def get_pipeline_version(self) -> int | None:
        sql = "SELECT value FROM detection_config WHERE key = %s"
        with self._conn.cursor() as cur:
            cur.execute(sql, (_PIPELINE_VERSION_KEY,))
            row = cur.fetchone()
        if row is None:
            return None
        value = row[0]
        # psycopg2 decodes jsonb to native Python types automatically.
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, int) else None
        return None

    def set_pipeline_version(self, version: int) -> None:
        # Store as a JSON-encoded int so the column type matches the
        # other detection_config rows (jsonb).
        encoded = json.dumps(int(version))
        sql = (
            "INSERT INTO detection_config (key, value) "
            "VALUES (%s, %s::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET "
            "value = EXCLUDED.value, updated_at = now()"
        )
        with self._conn.cursor() as cur:
            cur.execute(sql, (_PIPELINE_VERSION_KEY, encoded))
        self._conn.commit()

    # ── alerts ──────────────────────────────────────────────────────

    def record_alert(self, alert: AlertRecord) -> AlertInsertResult:
        _require_tz_aware(alert.sent_at, "AlertRecord.sent_at")
        insert_sql = (
            "INSERT INTO alerts_sent "
            "(alert_kind, fired_at, pump_serial, event_ref, payload, delivery) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (alert_kind, event_ref) WHERE event_ref IS NOT NULL "
            "DO NOTHING "
            f"RETURNING {_ALERT_COLUMNS_SQL}"
        )
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                insert_sql,
                (
                    alert.alert_kind,
                    alert.sent_at,
                    alert.pump_serial,
                    alert.event_ref,
                    Json(dict(alert.payload)),
                    alert.delivery,
                ),
            )
            row = cur.fetchone()
        self._conn.commit()

        if row is not None:
            return AlertInsertResult(
                record=_row_to_alert(dict(row)),
                inserted=True,
            )

        # Conflict-skipped: fetch the existing row that won the dedup.
        # The partial unique index guarantees this is reachable only when
        # event_ref IS NOT NULL.
        assert alert.event_ref is not None, (
            "alerts_sent partial unique index only triggers when event_ref "
            "IS NOT NULL; reaching the conflict branch with a null event_ref "
            "indicates a logic error elsewhere."
        )
        # Retry briefly: a parallel writer may still be committing when our
        # find_alert reads. The window is sub-millisecond in practice, but
        # the loop makes the contract robust under pytest-xdist / cron
        # races without changing the success path.
        for _ in range(3):
            existing = self.find_alert(alert.alert_kind, alert.event_ref)
            if existing is not None:
                return AlertInsertResult(record=existing, inserted=False)
            time.sleep(0.01)
        raise RuntimeError(
            f"record_alert: ON CONFLICT skipped insert of "
            f"(alert_kind={alert.alert_kind!r}, event_ref={alert.event_ref!r}) "
            "but no matching row was found after retry; "
            "alerts_sent partial unique index may be missing or corrupt."
        )

    def find_alert(self, alert_kind: str, event_ref: str) -> AlertRecord | None:
        sql = (
            f"SELECT {_ALERT_COLUMNS_SQL} FROM alerts_sent "
            "WHERE alert_kind = %s AND event_ref = %s "
            "ORDER BY fired_at DESC LIMIT 1"
        )
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (alert_kind, event_ref))
            row = cur.fetchone()
        if row is None:
            return None
        return _row_to_alert(dict(row))

    def recent_alerts(
        self, alert_kind: str, within: timedelta
    ) -> list[AlertRecord]:
        sql = (
            f"SELECT {_ALERT_COLUMNS_SQL} FROM alerts_sent "
            "WHERE alert_kind = %s "
            "AND fired_at >= now() - make_interval(secs => %s) "
            "ORDER BY fired_at DESC"
        )
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (alert_kind, within.total_seconds()))
            rows = [dict(r) for r in cur.fetchall()]
        return [_row_to_alert(r) for r in rows]

    # ── detection results ──────────────────────────────────────────

    def record_detection_result(self, result: DetectionResult) -> None:
        sql = (
            "INSERT INTO detection_results "
            "(kind, anchor_timestamp, payload, created_at) "
            "VALUES (%s, %s, %s, %s)"
        )
        with self._conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    result.kind,
                    result.anchor_timestamp,
                    Json(dict(result.payload)),
                    result.created_at,
                ),
            )
        self._conn.commit()

    def list_detection_results(
        self,
        *,
        kind: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[DetectionResult]:
        conditions: list[str] = []
        params: list[Any] = []
        if kind is not None:
            conditions.append("kind = %s")
            params.append(kind)
        if since is not None:
            conditions.append("created_at >= %s")
            params.append(since)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = (
            "SELECT kind, anchor_timestamp, payload, created_at "
            f"FROM detection_results{where} "
            "ORDER BY created_at DESC LIMIT %s"
        )
        params.append(limit)
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
        out: list[DetectionResult] = []
        for r in rows:
            payload = r.get("payload") or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {}
            out.append(
                DetectionResult(
                    kind=r["kind"],
                    anchor_timestamp=r["anchor_timestamp"],
                    payload=dict(payload) if isinstance(payload, dict) else {},
                    created_at=r["created_at"],
                )
            )
        return out

    # ── housekeeping ───────────────────────────────────────────────

    def clean_all(self) -> None:
        """Truncate every data + metadata table. Resets bigserial identities."""
        tables = [*TABLES.keys(), *_METADATA_TABLES]
        sql = (
            f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY"
        )
        with self._conn.cursor() as cur:
            cur.execute(sql)
        self._conn.commit()
