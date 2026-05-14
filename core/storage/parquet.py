"""Parquet-backed :class:`core.storage.protocol.Storage` implementation.

Wraps the local-files I/O logic that used to live in
``ingestion/storage.py`` (which is now a thin compatibility shim that
delegates here). On-disk layout, dedup behavior, fetch-state JSON
shape, and pipeline-version sidecar shape are preserved verbatim so
``ingestion.version_guard`` and any out-of-band tooling sees an
unchanged file format.

New parquets introduced by the Storage Protocol:

* ``alerts_sent.parquet`` — mirrors the Postgres ``alerts_sent`` table.
* ``detection_results.parquet`` — mirrors the (forthcoming) Postgres
  ``detection_results`` table; structure follows the minimal record
  shape in :class:`core.storage.records.DetectionResult`.

These are written into the same root directory as the existing data
parquets (``data/processed/`` by default).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from core.schema import get_spec
from core.storage.memory import _require_tz_aware
from core.storage.records import (
    AlertRecord,
    DetectionResult,
    FetchState,
    UpsertResult,
)

logger = logging.getLogger(__name__)


# Logical-name → parquet filename. Kept identical to the names that
# used to live in ``ingestion.storage.PARQUET_FILES`` so the on-disk
# layout doesn't change. Re-exported from ``ingestion.storage`` for
# backward compatibility.
PARQUET_FILES: dict[str, str] = {
    "cgm": "cgm.parquet",
    "bolus": "bolus.parquet",
    "requests": "requests.parquet",
    "basal": "basal.parquet",
    "suspension": "suspension.parquet",
    "events": "events.parquet",
    "alarms": "alarms.parquet",
    "site_issues": "site_issues.parquet",
    "cgm_gaps": "cgm_gaps.parquet",
}

# Dedup keys per logical table; ORDER matters because the first key is
# the column we sort by after dedup (matches the legacy behavior).
# Kept identical to ``ingestion.storage.DEDUP_KEYS`` and re-exported
# for backward compatibility.
DEDUP_KEYS: dict[str, list[str]] = {
    "cgm": ["seqnum", "pump_serial"],
    "bolus": ["bolus_id", "pump_serial"],
    "requests": ["bolus_id", "pump_serial"],
    "basal": ["timestamp", "pump_serial"],
    "suspension": ["suspend_timestamp", "pump_serial"],
    "events": ["pump_serial", "seqnum"],
    "alarms": ["seqnum", "pump_serial"],
    "site_issues": ["first_occlusion_ts", "pump_serial"],
    "cgm_gaps": ["start_ts", "pump_serial"],
}

# Sidecar filenames (relative to root).
STATE_FILENAME = ".fetch_state.json"
PIPELINE_VERSION_FILENAME = ".pipeline_version.json"

# Parquet filenames for new (post-Protocol) tables.
ALERTS_FILENAME = "alerts_sent.parquet"
DETECTION_FILENAME = "detection_results.parquet"

_ALERT_COLS = ("id", "alert_kind", "event_ref", "sent_at", "payload")
_DETECTION_COLS = ("kind", "anchor_timestamp", "payload", "created_at")


class ParquetStorage:
    """Local-files :class:`Storage` implementation."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # ── path helpers ────────────────────────────────────────────────

    def _table_path(self, name: str) -> Path:
        if name not in PARQUET_FILES:
            get_spec(name)  # raises ValueError with the canonical message
        return self.root / PARQUET_FILES[name]

    def _state_path(self) -> Path:
        return self.root / STATE_FILENAME

    def _version_path(self) -> Path:
        return self.root / PIPELINE_VERSION_FILENAME

    def _alerts_path(self) -> Path:
        return self.root / ALERTS_FILENAME

    def _detections_path(self) -> Path:
        return self.root / DETECTION_FILENAME

    def _read_or_empty(self, path: Path) -> pd.DataFrame:
        if path.exists():
            return pd.read_parquet(path)
        return pd.DataFrame()

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
        path = self._table_path(name)
        if not path.exists():
            return pd.DataFrame()
        df = pd.read_parquet(path)
        if df.empty:
            return df
        time_col = spec.time_column
        out = df[(df[time_col] >= since) & (df[time_col] < until)]
        if pump_serial is not None and "pump_serial" in out.columns:
            out = out[out["pump_serial"] == pump_serial]
        return out.reset_index(drop=True)

    def read_all_table(self, name: str) -> pd.DataFrame:
        path = self._table_path(name)
        return self._read_or_empty(path).reset_index(drop=True)

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

        self.root.mkdir(parents=True, exist_ok=True)
        path = self._table_path(name)

        if path.exists():
            existing = pd.read_parquet(path)
            before = len(existing)
            combined = pd.concat([existing, df], ignore_index=True)
        else:
            before = 0
            combined = df.copy()

        dedup_cols = DEDUP_KEYS[name]
        combined = combined.drop_duplicates(subset=dedup_cols, keep="first")

        # Sort by the first dedup key (legacy parity); falls back to the
        # spec's time column for the new tables that aren't in DEDUP_KEYS.
        sort_col = dedup_cols[0] if dedup_cols else spec.time_column
        combined = combined.sort_values(sort_col).reset_index(drop=True)

        combined.to_parquet(path, index=False)
        logger.info(
            "Saved %s: %d rows → %s", name, len(combined), path
        )

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
        path = self._table_path(name)
        if not path.exists():
            return 0

        df = pd.read_parquet(path)
        if df.empty:
            return 0

        time_col = spec.time_column
        mask = pd.Series(True, index=df.index)
        if since is not None:
            mask &= df[time_col] >= since
        if until is not None:
            mask &= df[time_col] < until
        if pump_serial is not None and "pump_serial" in df.columns:
            mask &= df["pump_serial"] == pump_serial

        deleted = int(mask.sum())
        kept = df.loc[~mask].reset_index(drop=True)

        # Empty after delete → write the empty (but typed) frame back
        # rather than removing the file, so version_guard doesn't see
        # the table disappear.
        kept.to_parquet(path, index=False)
        return deleted

    # ── fetch state ─────────────────────────────────────────────────

    # On-disk format
    # --------------
    # `.fetch_state.json` is shared with the legacy
    # `ingestion.storage.load_fetch_state` dict-based shape:
    #     { "<arbitrary key>": <arbitrary value>, ... }
    # The Protocol layer maps this to FetchState records via the
    # convention: the legacy dict is stored as a single FetchState with
    # source_id="tandem" and the dict payload moved into FetchState.payload.
    # Multiple FetchState records are merged into a per-source mapping:
    #     {
    #       "<source_id>": {
    #         "last_cursor": ..., "last_fetched_at": ..., "payload": {...}
    #       },
    #       ...
    #     }
    # The shim in `ingestion/storage.py` preserves the legacy "flat dict"
    # interpretation by routing all writes through source_id="tandem".

    _LEGACY_SOURCE_ID = "tandem"

    def _read_state_file(self) -> dict[str, object]:
        path = self._state_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_state_file(self, payload: dict[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._state_path().write_text(json.dumps(payload, indent=2))
        logger.info("Fetch state saved → %s", self._state_path())

    @staticmethod
    def _decode_state(source_id: str, raw: object) -> FetchState:
        """Decode a per-source JSON value into a :class:`FetchState`."""
        if (
            isinstance(raw, dict)
            and "payload" in raw
            and (
                "last_cursor" in raw or "last_fetched_at" in raw
            )
        ):
            last_cursor = raw.get("last_cursor")
            last_fetched_at_str = raw.get("last_fetched_at")
            last_fetched_at: datetime | None
            if isinstance(last_fetched_at_str, str):
                last_fetched_at = datetime.fromisoformat(last_fetched_at_str)
            else:
                last_fetched_at = None
            return FetchState(
                source_id=source_id,
                last_cursor=last_cursor if isinstance(last_cursor, str) or last_cursor is None else str(last_cursor),
                last_fetched_at=last_fetched_at,
                payload=dict(raw.get("payload", {})) if isinstance(raw.get("payload"), dict) else {},
            )
        # Legacy flat dict shape: stored verbatim into payload, with the
        # other fields defaulted. Reached for the "tandem" source after
        # an old fetch wrote the JSON before the Protocol existed.
        payload = dict(raw) if isinstance(raw, dict) else {}
        return FetchState(
            source_id=source_id,
            last_cursor=None,
            last_fetched_at=None,
            payload=payload,
        )

    @staticmethod
    def _encode_state(state: FetchState) -> dict[str, object]:
        return {
            "last_cursor": state.last_cursor,
            "last_fetched_at": (
                state.last_fetched_at.isoformat()
                if state.last_fetched_at is not None
                else None
            ),
            "payload": dict(state.payload),
        }

    def _load_state_map(self) -> tuple[dict[str, FetchState], list[str], dict[str, object] | None]:
        """Return ``(records, source_order, legacy_payload)``.

        ``legacy_payload`` is the flat dict if the file is in the legacy
        shape (no per-source nesting) and ``None`` otherwise. The shim
        uses it to support the historical ``load_fetch_state() -> dict``
        return shape.
        """
        raw = self._read_state_file()
        # Heuristic: if the file's top-level dict has any value that is
        # itself a dict containing both "payload" and either of
        # "last_cursor"/"last_fetched_at", treat it as nested. Else
        # treat as legacy flat dict (everything for "tandem").
        nested = any(
            isinstance(v, dict)
            and "payload" in v
            and ("last_cursor" in v or "last_fetched_at" in v)
            for v in raw.values()
        )
        if not raw:
            return {}, [], None
        if not nested:
            return (
                {self._LEGACY_SOURCE_ID: self._decode_state(self._LEGACY_SOURCE_ID, raw)},
                [self._LEGACY_SOURCE_ID],
                dict(raw),
            )
        records: dict[str, FetchState] = {}
        order: list[str] = []
        for source_id, value in raw.items():
            records[source_id] = self._decode_state(source_id, value)
            order.append(source_id)
        return records, order, None

    def get_fetch_state(self, source_id: str) -> FetchState | None:
        records, _, _ = self._load_state_map()
        return records.get(source_id)

    def set_fetch_state(self, source_id: str, state: FetchState) -> None:
        records, order, _ = self._load_state_map()
        if source_id not in records:
            order.append(source_id)
        records[source_id] = state
        payload = {sid: self._encode_state(records[sid]) for sid in order}
        self._write_state_file(payload)

    def list_fetch_state(self) -> list[FetchState]:
        records, order, _ = self._load_state_map()
        return [records[sid] for sid in order]

    # Legacy shim helpers — used by `ingestion.storage` to preserve the
    # dict-based load_fetch_state / save_fetch_state signatures.
    def _read_legacy_fetch_state(self) -> dict[str, object]:
        """Return the legacy flat-dict view of fetch state.

        When the on-disk file is in nested per-source format, the
        ``tandem`` source's ``payload`` is returned (preserving the
        legacy callers' expectation). When the file is in legacy flat
        format, return the file's contents verbatim.
        """
        records, _, legacy = self._load_state_map()
        if legacy is not None:
            return legacy
        tandem = records.get(self._LEGACY_SOURCE_ID)
        if tandem is None:
            return {}
        return dict(tandem.payload)

    def _write_legacy_fetch_state(self, state: dict[str, object]) -> None:
        """Write the legacy flat-dict file shape verbatim.

        This intentionally bypasses the per-source FetchState
        serialization so the on-disk file shape stays identical to the
        pre-Protocol layout (a flat dict). Callers can keep parsing
        with `json.loads(...)` without changes.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        self._state_path().write_text(json.dumps(state, indent=2))
        logger.info("Fetch state saved → %s", self._state_path())

    # ── pipeline version ────────────────────────────────────────────

    def get_pipeline_version(self) -> int | None:
        path = self._version_path()
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        version = payload.get("version") if isinstance(payload, dict) else None
        if isinstance(version, int):
            return version
        return None

    def set_pipeline_version(self, version: int) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": int(version),
            "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._version_path().write_text(json.dumps(payload, indent=2))

    # ── alerts ──────────────────────────────────────────────────────

    def _load_alerts(self) -> list[AlertRecord]:
        path = self._alerts_path()
        if not path.exists():
            return []
        df = pd.read_parquet(path)
        records: list[AlertRecord] = []
        for r in df.to_dict(orient="records"):
            payload = r.get("payload") or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {}
            sent_at = r["sent_at"]
            if isinstance(sent_at, pd.Timestamp):
                sent_at = sent_at.to_pydatetime()
            records.append(
                AlertRecord(
                    id=r.get("id"),
                    alert_kind=r["alert_kind"],
                    event_ref=r.get("event_ref"),
                    sent_at=sent_at,
                    payload=dict(payload) if isinstance(payload, dict) else {},
                )
            )
        return records

    def _write_alerts(self, records: list[AlertRecord]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not records:
            empty = pd.DataFrame(columns=list(_ALERT_COLS))
            empty.to_parquet(self._alerts_path(), index=False)
            return
        rows = [
            {
                "id": r.id,
                "alert_kind": r.alert_kind,
                "event_ref": r.event_ref,
                "sent_at": r.sent_at,
                "payload": json.dumps(r.payload),
            }
            for r in records
        ]
        pd.DataFrame(rows).to_parquet(self._alerts_path(), index=False)

    def record_alert(self, alert: AlertRecord) -> AlertRecord:
        _require_tz_aware(alert.sent_at, "AlertRecord.sent_at")
        records = self._load_alerts()
        if alert.event_ref is not None:
            for r in records:
                if (
                    r.alert_kind == alert.alert_kind
                    and r.event_ref == alert.event_ref
                ):
                    return r
        rec = AlertRecord(
            id=alert.id if alert.id is not None else uuid.uuid4().hex,
            alert_kind=alert.alert_kind,
            event_ref=alert.event_ref,
            sent_at=alert.sent_at,
            payload=dict(alert.payload),
        )
        records.append(rec)
        self._write_alerts(records)
        return rec

    def find_alert(
        self, alert_kind: str, event_ref: str
    ) -> AlertRecord | None:
        matches = [
            r
            for r in self._load_alerts()
            if r.alert_kind == alert_kind and r.event_ref == event_ref
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda a: a.sent_at, reverse=True)[0]

    def recent_alerts(
        self, alert_kind: str, within: timedelta
    ) -> list[AlertRecord]:
        cutoff = datetime.now(tz=timezone.utc) - within
        matches = [
            r
            for r in self._load_alerts()
            if r.alert_kind == alert_kind and r.sent_at >= cutoff
        ]
        return sorted(matches, key=lambda a: a.sent_at, reverse=True)

    # ── detection results ───────────────────────────────────────────

    def _load_detections(self) -> list[DetectionResult]:
        path = self._detections_path()
        if not path.exists():
            return []
        df = pd.read_parquet(path)
        records: list[DetectionResult] = []
        for r in df.to_dict(orient="records"):
            payload = r.get("payload") or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {}
            anchor = r["anchor_timestamp"]
            if isinstance(anchor, pd.Timestamp):
                anchor = anchor.to_pydatetime()
            created = r["created_at"]
            if isinstance(created, pd.Timestamp):
                created = created.to_pydatetime()
            records.append(
                DetectionResult(
                    kind=r["kind"],
                    anchor_timestamp=anchor,
                    payload=dict(payload) if isinstance(payload, dict) else {},
                    created_at=created,
                )
            )
        return records

    def _write_detections(self, records: list[DetectionResult]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not records:
            empty = pd.DataFrame(columns=list(_DETECTION_COLS))
            empty.to_parquet(self._detections_path(), index=False)
            return
        rows = [
            {
                "kind": r.kind,
                "anchor_timestamp": r.anchor_timestamp,
                "payload": json.dumps(r.payload),
                "created_at": r.created_at,
            }
            for r in records
        ]
        pd.DataFrame(rows).to_parquet(self._detections_path(), index=False)

    def record_detection_result(self, result: DetectionResult) -> None:
        records = self._load_detections()
        records.append(result)
        self._write_detections(records)

    def list_detection_results(
        self,
        *,
        kind: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[DetectionResult]:
        out = self._load_detections()
        if kind is not None:
            out = [r for r in out if r.kind == kind]
        if since is not None:
            out = [r for r in out if r.created_at >= since]
        out.sort(key=lambda r: r.created_at, reverse=True)
        return out[:limit]

    # ── housekeeping ────────────────────────────────────────────────

    def clean_all(self) -> None:
        for filename in PARQUET_FILES.values():
            path = self.root / filename
            if path.exists():
                path.unlink()
                logger.info("Deleted %s", path)

        for path in (
            self._state_path(),
            self._version_path(),
            self._alerts_path(),
            self._detections_path(),
        ):
            if path.exists():
                path.unlink()
                logger.info("Deleted %s", path)
