"""In-memory :class:`Storage` implementation.

Stores DataFrames in a per-table dict and metadata records in plain
Python lists/dicts. Used by the contract test suite so the parquet and
(future) Supabase implementations are validated against the same set
of expected behaviors. Also useful for one-shot scripts and prototypes
that don't want to touch disk.

Trivial by construction: this implementation IS the reference for the
Protocol's behavior — read it when in doubt about what each method
should do.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from core.schema import get_spec
from core.storage.records import (
    AlertInsertResult,
    AlertRecord,
    DetectionResult,
    FetchState,
    UpsertResult,
)


def _require_tz_aware(value: datetime, field_name: str) -> None:
    """Reject tz-naive datetimes at the source.

    ``recent_alerts`` compares against tz-aware "now"; a naive
    ``sent_at`` would raise ``TypeError`` at compare time. We force
    the failure at insert so the offending caller sees a clear error
    message instead of an obscure stack trace on a later read.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            f"{field_name} must be tz-aware (got naive datetime {value!r})"
        )


class InMemoryStorage:
    """Reference :class:`core.storage.protocol.Storage` implementation."""

    def __init__(self) -> None:
        self._tables: dict[str, pd.DataFrame] = {}
        self._fetch_state: dict[str, FetchState] = {}
        self._fetch_state_order: list[str] = []
        self._pipeline_version: int | None = None
        self._alerts: list[AlertRecord] = []
        self._detections: list[DetectionResult] = []

    # ── helpers ─────────────────────────────────────────────────────

    def _get_or_empty(self, name: str) -> pd.DataFrame:
        """Return the stored DataFrame for ``name`` (empty if missing)."""
        get_spec(name)  # validate name, raise ValueError for typos
        return self._tables.get(name, pd.DataFrame()).copy()

    # ── data tables ─────────────────────────────────────────────────

    def read_table(
        self,
        name: str,
        *,
        since: datetime,
        until: datetime,
        pump_serial: str | None = None,
    ) -> pd.DataFrame:
        spec = get_spec(name)
        df = self._tables.get(name)
        if df is None or df.empty:
            return pd.DataFrame()
        out = df
        time_col = spec.time_column
        out = out[(out[time_col] >= since) & (out[time_col] < until)]
        if pump_serial is not None and "pump_serial" in out.columns:
            out = out[out["pump_serial"] == pump_serial]
        return out.reset_index(drop=True).copy()

    def read_all_table(self, name: str) -> pd.DataFrame:
        return self._get_or_empty(name)

    def upsert_table(self, name: str, df: pd.DataFrame) -> UpsertResult:
        spec = get_spec(name)
        started = time.perf_counter()
        rows_received = len(df)

        if df.empty:
            return UpsertResult(
                rows_received=0,
                rows_inserted=0,
                rows_skipped=0,
                elapsed_seconds=time.perf_counter() - started,
            )

        existing = self._tables.get(name)
        before = 0 if existing is None else len(existing)

        combined = (
            df.copy()
            if existing is None or existing.empty
            else pd.concat([existing, df], ignore_index=True)
        )

        pk_cols = list(spec.primary_key)
        combined = combined.drop_duplicates(subset=pk_cols, keep="first")
        combined = combined.sort_values(spec.time_column).reset_index(drop=True)

        self._tables[name] = combined
        inserted = len(combined) - before
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
        df = self._tables.get(name)
        if df is None or df.empty:
            return 0

        time_col = spec.time_column
        mask = pd.Series(True, index=df.index)
        if since is not None:
            mask &= df[time_col] >= since
        if until is not None:
            mask &= df[time_col] < until
        if pump_serial is not None and "pump_serial" in df.columns:
            mask &= df["pump_serial"] == pump_serial

        to_delete = int(mask.sum())
        self._tables[name] = df.loc[~mask].reset_index(drop=True)
        return to_delete

    # ── fetch state ─────────────────────────────────────────────────

    def get_fetch_state(self, source_id: str) -> FetchState | None:
        return self._fetch_state.get(source_id)

    def set_fetch_state(self, source_id: str, state: FetchState) -> None:
        if source_id not in self._fetch_state:
            self._fetch_state_order.append(source_id)
        self._fetch_state[source_id] = state

    def list_fetch_state(self) -> list[FetchState]:
        return [self._fetch_state[sid] for sid in self._fetch_state_order]

    # ── pipeline version ────────────────────────────────────────────

    def get_pipeline_version(self) -> int | None:
        return self._pipeline_version

    def set_pipeline_version(self, version: int) -> None:
        self._pipeline_version = int(version)

    # ── alerts ──────────────────────────────────────────────────────

    def record_alert(self, alert: AlertRecord) -> AlertInsertResult:
        _require_tz_aware(alert.sent_at, "AlertRecord.sent_at")
        if alert.event_ref is not None:
            existing = self.find_alert(alert.alert_kind, alert.event_ref)
            if existing is not None:
                return AlertInsertResult(record=existing, inserted=False)
        rec = AlertRecord(
            id=alert.id if alert.id is not None else uuid.uuid4().hex,
            alert_kind=alert.alert_kind,
            event_ref=alert.event_ref,
            sent_at=alert.sent_at,
            payload=dict(alert.payload),
            pump_serial=alert.pump_serial,
            delivery=alert.delivery,
        )
        self._alerts.append(rec)
        return AlertInsertResult(record=rec, inserted=True)

    def find_alert(
        self, alert_kind: str, event_ref: str
    ) -> AlertRecord | None:
        matches = [
            a
            for a in self._alerts
            if a.alert_kind == alert_kind and a.event_ref == event_ref
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda a: a.sent_at, reverse=True)[0]

    def recent_alerts(
        self, alert_kind: str, within: timedelta
    ) -> list[AlertRecord]:
        if not self._alerts:
            return []
        # "Now" is the wall clock at the time of the call; tz-aware to
        # compare against tz-aware sent_at values used everywhere else.
        now = datetime.now(tz=timezone.utc)
        cutoff = now - within
        matches = [
            a
            for a in self._alerts
            if a.alert_kind == alert_kind and a.sent_at >= cutoff
        ]
        return sorted(matches, key=lambda a: a.sent_at, reverse=True)

    # ── detection results ───────────────────────────────────────────

    def record_detection_result(self, result: DetectionResult) -> None:
        self._detections.append(result)

    def list_detection_results(
        self,
        *,
        kind: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[DetectionResult]:
        out = list(self._detections)
        if kind is not None:
            out = [r for r in out if r.kind == kind]
        if since is not None:
            out = [r for r in out if r.created_at >= since]
        out.sort(key=lambda r: r.created_at, reverse=True)
        return out[:limit]

    # ── housekeeping ────────────────────────────────────────────────

    def clean_all(self) -> None:
        self._tables.clear()
        self._fetch_state.clear()
        self._fetch_state_order.clear()
        self._pipeline_version = None
        self._alerts.clear()
        self._detections.clear()
