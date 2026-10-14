"""Live-loop observability: per-cycle outcome log, truthful heartbeat, and the
stale-CGM alert state machine.

The `live_cron` ``fetch_state`` row is the only place this lives (no schema
migration): ``last_synced_at`` is the last cycle that evaluated FRESH CGM, and
``meta`` (``FetchState.payload``) carries the rest. Everything stored is plain
JSON (ISO strings, local-date string keys) because ``SupabaseStorage`` writes
``meta`` through psycopg2 ``Json``.

Pure with respect to I/O: the caller injects the storage and the Telegram
sender, so this module imports nothing backend-specific and is the single
place both the cron worker and the Telegram ``/status`` command read.

Meta layout (all optional on legacy rows)::

    last_cycle_at         ISO ts of the most recent completed cycle
    last_outcome          one of OUTCOMES
    last_reading_at       ISO ts of the newest Dexcom reading ever seen
    last_reading_age_min  its age at ``last_cycle_at``
    outcome_counts        {"YYYY-MM-DD": {outcome: n}} local days, bounded
    stale_since           ISO ts the current blind episode started
    stale_alert_sent_at   ISO ts of the last stale alert sent this episode
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

from core.storage.records import FetchState

logger = logging.getLogger("meal_rise_cron")

LIVE_CRON_SOURCE = "live_cron"

# Closed vocabulary for ``last_outcome`` / ``outcome_counts`` keys. Keeping it
# closed bounds the size of the jsonb meta.
SUPPRESSION_GATES = ("refractory", "duplicate", "claim_lost")
OUTCOMES = (
    "no_data",
    "stale",
    "insufficient_window",
    "no_candidate",
    *(f"suppressed_{g}" for g in SUPPRESSION_GATES),
    "detected",
    "alert_sent",
    "error",
)

# What the cycle learned about the CGM feed:
#   fresh   newest reading passed the freshness guard
#   stale   readings exist but the newest is too old
#   none    Dexcom returned nothing / the fetch raised
#   unknown the cycle failed before the feed was checked (no feed verdict)
CgmState = Literal["fresh", "stale", "none", "unknown"]


@dataclass(frozen=True)
class LiveHealthConfig:
    """``live_health:`` block of ``config/user_config.yaml`` (all optional)."""

    cgm_stale_alert_minutes: int = 60
    cgm_stale_realert_hours: int = 12
    worker_cycle_stale_minutes: int = 15
    outcome_counter_days: int = 14

    @classmethod
    def from_raw(cls, block: dict[str, Any] | None) -> "LiveHealthConfig":
        block = block or {}
        defaults = cls()
        values: dict[str, int] = {}
        for f in (
            "cgm_stale_alert_minutes",
            "cgm_stale_realert_hours",
            "worker_cycle_stale_minutes",
            "outcome_counter_days",
        ):
            val = int(block.get(f, getattr(defaults, f)))
            if val <= 0:
                raise ValueError(f"live_health.{f}: must be > 0, got {val}")
            values[f] = val
        return cls(**values)

    @classmethod
    def from_app_config(cls, config: Any) -> "LiveHealthConfig":
        """Parse from ``AppConfig.raw``; fall back to defaults if malformed so
        a typo can never take the live loop down."""
        try:
            return cls.from_raw(getattr(config, "raw", {}).get("live_health"))
        except (TypeError, ValueError):
            logger.warning("Invalid live_health config; using defaults", exc_info=True)
            return cls()


@dataclass
class CycleReport:
    """What one cron cycle observed, filled in as the cycle progresses."""

    outcome: str = "error"
    cgm_state: CgmState = "unknown"
    last_reading_at: datetime | None = None


def classify_no_detection(window: Any, meal_rise_cfg: Any) -> str:
    """Why ``detect_meal_rise`` returned ``None``.

    Mirrors the detector's data-sufficiency guards (read-only; the detector is
    not modified): too few samples, low coverage, or a gap -> the window could
    not be evaluated; anything else -> evaluated, no candidate rise.
    """
    if (
        window.n_samples < meal_rise_cfg.min_samples
        or window.coverage < meal_rise_cfg.min_coverage
        or window.has_gap
    ):
        return "insufficient_window"
    return "no_candidate"


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _fmt_span(minutes: float) -> str:
    if minutes >= 180:
        return f"~{minutes / 60:.0f}h"
    return f"~{minutes:.0f}min"


def stale_alert_message(blind_minutes: float) -> str:
    return (
        f"No Dexcom readings for {_fmt_span(blind_minutes)} - is the G7 app "
        "connected and Share turned on? Live missed-meal alerts are blind "
        "until readings resume."
    )


def recovery_message(gap_minutes: float) -> str:
    return f"Dexcom readings are flowing again (gap {_fmt_span(gap_minutes)})."


def _safe_send(send: Callable[[str], bool], msg: str) -> bool:
    try:
        return bool(send(msg))
    except Exception:
        logger.warning("live_health Telegram send raised", exc_info=True)
        return False


def _bump_counts(
    payload: dict[str, Any], day_key: str, outcome: str, keep_days: int
) -> dict[str, dict[str, int]]:
    raw = payload.get("outcome_counts")
    counts: dict[str, dict[str, int]] = {}
    if isinstance(raw, dict):
        for day, per in raw.items():
            if isinstance(day, str) and isinstance(per, dict):
                counts[day] = {
                    str(k): int(v) for k, v in per.items() if isinstance(v, int)
                }
    day_counts = counts.setdefault(day_key, {})
    day_counts[outcome] = day_counts.get(outcome, 0) + 1
    for day in sorted(counts)[: max(0, len(counts) - keep_days)]:
        del counts[day]
    return counts


def build_next_state(
    prev: FetchState | None,
    report: CycleReport,
    *,
    now: datetime,
    tz_name: str,
    cfg: LiveHealthConfig,
    send: Callable[[str], bool],
) -> FetchState:
    """Fold one cycle into the heartbeat row (and fire stale/recovery alerts).

    ``last_fetched_at`` (-> ``last_synced_at``) advances only on a cycle that
    evaluated fresh CGM; otherwise the previous value is carried forward.
    """
    payload: dict[str, Any] = dict(prev.payload) if prev else {}
    payload.pop("last_cursor", None)  # reserved by SupabaseStorage
    last_fresh = prev.last_fetched_at if prev else None
    if report.cgm_state == "fresh":
        last_fresh = now

    # --- reading info (carried forward when this cycle saw none) -----------
    known = _parse_ts(payload.get("last_reading_at"))
    if report.last_reading_at is not None and (
        known is None or report.last_reading_at > known
    ):
        payload["last_reading_at"] = report.last_reading_at.isoformat()
    last_reading = _parse_ts(payload.get("last_reading_at"))
    payload.setdefault("last_reading_at", None)
    payload["last_reading_age_min"] = (
        round((now - last_reading).total_seconds() / 60.0, 1)
        if last_reading is not None
        else None
    )

    payload["last_cycle_at"] = now.isoformat()
    payload["last_outcome"] = report.outcome
    day_key = now.astimezone(ZoneInfo(tz_name)).date().isoformat()
    payload["outcome_counts"] = _bump_counts(
        payload, day_key, report.outcome, cfg.outcome_counter_days
    )

    # --- stale-CGM episode state machine -----------------------------------
    if report.cgm_state == "fresh":
        if payload.get("stale_alert_sent_at"):
            since = _parse_ts(payload.get("stale_since")) or now
            gap = (now - since).total_seconds() / 60.0
            if _safe_send(send, recovery_message(gap)):
                payload.pop("stale_alert_sent_at", None)
                payload.pop("stale_since", None)
            # else: keep the markers so the next fresh cycle retries
        else:
            payload.pop("stale_since", None)
    elif report.cgm_state in ("stale", "none"):
        since = _parse_ts(payload.get("stale_since"))
        if since is None:
            since = last_reading or now
            payload["stale_since"] = since.isoformat()
        # Age of the newest reading we know of; falls back to episode start
        # when Dexcom has never returned a reading.
        blind = (now - (last_reading or since)).total_seconds() / 60.0
        sent_at = _parse_ts(payload.get("stale_alert_sent_at"))
        due = sent_at is None or (
            (now - sent_at).total_seconds() / 3600.0 >= cfg.cgm_stale_realert_hours
        )
        if blind > cfg.cgm_stale_alert_minutes and due:
            if _safe_send(send, stale_alert_message(blind)):
                payload["stale_alert_sent_at"] = now.isoformat()

    return FetchState(
        source_id=LIVE_CRON_SOURCE,
        last_cursor=None,
        last_fetched_at=last_fresh,
        payload=payload,
        source_kind="pydexcom",
    )


def record_cycle(
    storage: Any,
    report: CycleReport,
    *,
    now: datetime,
    tz_name: str,
    cfg: LiveHealthConfig,
    send: Callable[[str], bool],
) -> None:
    """Read-modify-write the heartbeat row.

    If the prior state cannot be read, nothing is written: a blind write would
    wipe the counters and null ``last_synced_at``. Callers wrap this in
    try/except; it must never fail a cycle.
    """
    prev = storage.get_fetch_state(LIVE_CRON_SOURCE)
    state = build_next_state(
        prev, report, now=now, tz_name=tz_name, cfg=cfg, send=send
    )
    storage.set_fetch_state(LIVE_CRON_SOURCE, state)


# ── read side (Telegram /status) ─────────────────────────────────────────


@dataclass(frozen=True)
class LiveSummary:
    last_cycle_at: datetime | None = None
    last_outcome: str | None = None
    last_reading_at: datetime | None = None
    last_reading_age_min: float | None = None
    last_fresh_at: datetime | None = None
    today_counts: dict[str, int] = field(default_factory=dict)
    cycle_stale: bool = False
    reading_stale: bool = False

    @classmethod
    def empty(cls) -> "LiveSummary":
        return cls()


def summarize_live_state(
    state: FetchState | None,
    now: datetime,
    tz_name: str,
    cfg: LiveHealthConfig,
    *,
    reading_stale_minutes: float | None = None,
) -> LiveSummary | None:
    """Interpret the heartbeat row for display; tolerant of legacy/garbled meta."""
    if state is None:
        return None
    p = state.payload if isinstance(state.payload, dict) else {}
    # Legacy rows (pre-observability) only carry the heartbeat timestamp.
    last_cycle = _parse_ts(p.get("last_cycle_at")) or state.last_fetched_at
    last_reading = _parse_ts(p.get("last_reading_at"))
    outcome = p.get("last_outcome")
    age = (
        (now - last_reading).total_seconds() / 60.0 if last_reading is not None else None
    )

    today_counts: dict[str, int] = {}
    counts = p.get("outcome_counts")
    if isinstance(counts, dict):
        per = counts.get(now.astimezone(ZoneInfo(tz_name)).date().isoformat())
        if isinstance(per, dict):
            today_counts = {
                str(k): int(v) for k, v in per.items() if isinstance(v, int)
            }

    cycle_stale = last_cycle is None or (
        (now - last_cycle).total_seconds() / 60.0 > cfg.worker_cycle_stale_minutes
    )
    reading_stale = (
        reading_stale_minutes is not None
        and age is not None
        and age > reading_stale_minutes
    )
    return LiveSummary(
        last_cycle_at=last_cycle,
        last_outcome=outcome if isinstance(outcome, str) else None,
        last_reading_at=last_reading,
        last_reading_age_min=age,
        last_fresh_at=state.last_fetched_at,
        today_counts=today_counts,
        cycle_stale=cycle_stale,
        reading_stale=reading_stale,
    )
