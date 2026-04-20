"""Convert raw tconnectsync event lists into normalized pandas DataFrames."""

import json
import logging
from collections import defaultdict

import pandas as pd

from tconnectsync.eventparser.events import (
    LidCgmDataGxb, LidCgmDataG7, LidCgmDataFsl2,
    LidBolusCompleted,
    LidBolusRequestedMsg1, LidBolusRequestedMsg2, LidBolusRequestedMsg3,
    LidBasalDelivery,
    LidPumpingSuspended, LidPumpingResumed,
    LidCartridgeFilled, LidCannulaFilled, LidTubingFilled,
    LidCgmJoinSessionG7, LidCgmStopSessionG7,
    LidCgmJoinSessionGx, LidCgmStopSessionGx,
    LidCgmJoinSessionFsl2, LidCgmStopSessionFsl2,
    LidAaUserModeChange, LidAaPcmChange,
    LidNewDay, LidAaDailyStatus,
    LidBolusDelivery, LidBolusActivated,
    LidCgmAlertActivatedDex, LidCgmAlertClearedDex, LidCgmAlertAckDex,
    LidAlertActivated, LidAlertCleared,
    LidAlarmActivated, LidAlarmCleared,
    LidBgReadingTaken,
    LidVersionsA, LidVersionInfo, LidShelfMode, LidArmInit,
    LidDailyBasal, LidCarbsEntered,
    LidCgmStartSessionGx,
    LidUsbConnected, LidUsbDisconnected,
)
from tconnectsync.eventparser.raw_event import RawEvent

logger = logging.getLogger(__name__)

# All event types consumed by the specific builders (cgm through events).
# Used by build_all to detect unknown/unhandled types.
_HANDLED_TYPES = (
    # cgm
    LidCgmDataG7, LidCgmDataGxb, LidCgmDataFsl2,
    # bolus
    LidBolusCompleted,
    # request
    LidBolusRequestedMsg1, LidBolusRequestedMsg2, LidBolusRequestedMsg3,
    # basal
    LidBasalDelivery,
    # suspension
    LidPumpingSuspended, LidPumpingResumed,
    # events
    LidCartridgeFilled, LidCannulaFilled, LidTubingFilled,
    LidCgmJoinSessionG7, LidCgmStopSessionG7,
    LidCgmJoinSessionGx, LidCgmStopSessionGx,
    LidCgmJoinSessionFsl2, LidCgmStopSessionFsl2,
    LidAaUserModeChange, LidAaPcmChange,
    LidNewDay, LidAaDailyStatus,
    # known but not surfaced in a specific builder
    LidBolusDelivery, LidBolusActivated,
    LidCgmAlertActivatedDex, LidCgmAlertClearedDex, LidCgmAlertAckDex,
    LidAlertActivated, LidAlertCleared,
    LidAlarmActivated, LidAlarmCleared,
    LidBgReadingTaken,
    LidVersionsA, LidVersionInfo, LidShelfMode, LidArmInit,
    LidDailyBasal, LidCarbsEntered,
    LidCgmStartSessionGx,
    LidUsbConnected, LidUsbDisconnected,
    RawEvent,
)


# ---------------------------------------------------------------------------
# 1. CGM
# ---------------------------------------------------------------------------

def build_cgm_df(events: list, pump_serial: str) -> pd.DataFrame:
    """Build CGM readings DataFrame from G7, Gxb, and FSL2 events.

    For backfilled readings (cgmDataTypeRaw=2), uses the sensor reading time
    (egvTimestamp) as the primary timestamp since these readings weren't
    available to the pump in real time.
    """
    rows = []
    for e in events:
        if isinstance(e, (LidCgmDataG7, LidCgmDataGxb, LidCgmDataFsl2)):
            data_type_raw = getattr(e, 'cgmDataTypeRaw', 1)
            is_backfill = data_type_raw == 2

            egv_ts = getattr(e, 'egvTimestamp', None)
            if is_backfill and egv_ts is not None:
                # Backfilled: use actual sensor time, store pump-received time
                timestamp = egv_ts.datetime
                sensor_timestamp = e.eventTimestamp.datetime
            else:
                # Live: use pump-received time
                timestamp = e.eventTimestamp.datetime
                sensor_timestamp = None

            rows.append({
                "timestamp": timestamp,
                "bg_mgdl": int(e.currentglucosedisplayvalue),
                "backfilled": is_backfill,
                "sensor_timestamp": sensor_timestamp,
                "pump_serial": pump_serial,
                "seqnum": int(e.seqNum),
            })

    columns = ["timestamp", "bg_mgdl", "backfilled", "sensor_timestamp", "pump_serial", "seqnum"]
    df = pd.DataFrame(rows, columns=columns)
    if not df.empty:
        df = df.drop_duplicates(subset=["seqnum", "pump_serial"], keep="first")
        df = df.sort_values("timestamp").reset_index(drop=True)
    if not df.empty and len(df) > 1:
        time_diff = df["timestamp"].diff()
        mask = time_diff.isna() | (time_diff >= pd.Timedelta(seconds=60)) | df["backfilled"]
        df = df[mask].reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# 2. Bolus completed
# ---------------------------------------------------------------------------

def build_bolus_df(events: list, pump_serial: str) -> pd.DataFrame:
    """Build bolus delivery DataFrame from LidBolusCompleted events."""
    rows = []
    for e in events:
        if isinstance(e, LidBolusCompleted):
            rows.append({
                "timestamp": e.eventTimestamp.datetime,
                "insulin_units": float(e.insulindelivered),
                "bolus_id": int(e.bolusid),
                "pump_serial": pump_serial,
            })

    df = pd.DataFrame(rows, columns=["timestamp", "insulin_units", "bolus_id", "pump_serial"])
    if not df.empty:
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# 3. Bolus request (three-way join)
# ---------------------------------------------------------------------------

def _derive_bolus_source(msg2) -> str:
    """Derive bolus_source string from a LidBolusRequestedMsg2."""
    options_raw = msg2.optionsRaw
    if not isinstance(options_raw, int) or options_raw < 0 or options_raw > 7:
        logger.warning("Unexpected optionsRaw value: %r for bolusid %s", options_raw, msg2.bolusid)
        return "unknown"
    if options_raw in (3, 6):
        return "auto"
    if msg2.useroverrideRaw == 1:
        return "override"
    return "user"


def build_request_df(events: list, pump_serial: str) -> pd.DataFrame:
    """Build bolus request DataFrame by joining Msg1/Msg2/Msg3 on bolusid."""
    msg1_by_id: dict = {}
    msg2_by_id: dict = {}
    msg3_by_id: dict = {}

    for e in events:
        if isinstance(e, LidBolusRequestedMsg1):
            msg1_by_id[e.bolusid] = e
        elif isinstance(e, LidBolusRequestedMsg2):
            msg2_by_id[e.bolusid] = e
        elif isinstance(e, LidBolusRequestedMsg3):
            msg3_by_id[e.bolusid] = e

    # Warn about orphaned Msg2/Msg3 (no matching Msg1)
    for bid in msg2_by_id:
        if bid not in msg1_by_id:
            logger.warning("LidBolusRequestedMsg2 with bolusid=%s has no matching Msg1; discarding", bid)
    for bid in msg3_by_id:
        if bid not in msg1_by_id:
            logger.warning("LidBolusRequestedMsg3 with bolusid=%s has no matching Msg1; discarding", bid)

    rows = []
    for bid, m1 in msg1_by_id.items():
        m2 = msg2_by_id.get(bid)
        m3 = msg3_by_id.get(bid)

        if m2 is None:
            logger.warning("LidBolusRequestedMsg1 bolusid=%s has no matching Msg2", bid)
            bolus_source = "unknown"
        else:
            bolus_source = _derive_bolus_source(m2)

        rows.append({
            "timestamp": m1.eventTimestamp.datetime,
            "bolus_id": int(m1.bolusid),
            "carbs_g": int(m1.carbamount),
            "bg_mgdl": int(m1.BG),
            "iob": float(m1.IOB),
            "bolus_source": bolus_source,
            "food_insulin": float(m3.foodbolussize) if m3 is not None else float("nan"),
            "correction_insulin": float(m3.correctionbolussize) if m3 is not None else float("nan"),
            "total_requested": float(m3.totalbolussize) if m3 is not None else float("nan"),
            "pump_serial": pump_serial,
        })

    columns = [
        "timestamp", "bolus_id", "carbs_g", "bg_mgdl", "iob",
        "bolus_source", "food_insulin", "correction_insulin",
        "total_requested", "pump_serial",
    ]
    df = pd.DataFrame(rows, columns=columns)
    if not df.empty:
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# 4. Basal delivery
# ---------------------------------------------------------------------------

_RATE_SOURCE_MAP = {
    0: "suspended",
    1: "profile",
    2: "temp_rate",
    3: "algorithm",
    4: "temp_rate_and_algorithm",
}


def build_basal_df(events: list, pump_serial: str) -> pd.DataFrame:
    """Build basal delivery DataFrame from LidBasalDelivery events."""
    rows = []
    for e in events:
        if isinstance(e, LidBasalDelivery):
            rows.append({
                "timestamp": e.eventTimestamp.datetime,
                "commanded_rate": float(e.commandedRate) / 1000.0,
                "rate_source": _RATE_SOURCE_MAP.get(e.commandedRateSourceRaw, "unknown"),
                "pump_serial": pump_serial,
            })

    df = pd.DataFrame(rows, columns=["timestamp", "commanded_rate", "rate_source", "pump_serial"])
    if not df.empty:
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# 5. Suspension episodes
# ---------------------------------------------------------------------------

_SUSPEND_REASON_MAP = {
    0: "user",
    1: "alarm",
    2: "malfunction",
    6: "plgs_auto",
}


def build_suspension_df(events: list, pump_serial: str) -> pd.DataFrame:
    """Build suspension episode DataFrame by pairing suspend/resume events."""
    # Build alarm lookup: timestamp → (alarmidRaw, alarm_name)
    alarm_lookup: dict = {}
    for e in events:
        if isinstance(e, LidAlarmActivated):
            name = e.alarmid.name if e.alarmid is not None else None
            alarm_lookup[e.eventTimestamp.datetime] = (e.alarmidRaw, name)

    # Gather and sort all suspend + resume events by timestamp
    sus_events = []
    for e in events:
        if isinstance(e, (LidPumpingSuspended, LidPumpingResumed)):
            sus_events.append(e)
    sus_events.sort(key=lambda e: e.eventTimestamp.datetime)

    def _alarm_fields(suspend_event):
        """Return alarm_id and alarm_name for a suspend event."""
        reason = _SUSPEND_REASON_MAP.get(suspend_event.suspendreasonRaw, "unknown")
        if reason == "alarm":
            ts = suspend_event.eventTimestamp.datetime
            if ts in alarm_lookup:
                aid, aname = alarm_lookup[ts]
                return aid, aname
        return float("nan"), None

    episodes: list[dict] = []
    current_suspend = None

    for e in sus_events:
        if isinstance(e, LidPumpingSuspended):
            if current_suspend is not None:
                # Double-suspend: close current episode at new suspend's timestamp
                suspend_ts = current_suspend.eventTimestamp.datetime
                resume_ts = e.eventTimestamp.datetime
                dur = (resume_ts - suspend_ts).total_seconds() / 60.0
                a_id, a_name = _alarm_fields(current_suspend)
                episodes.append({
                    "suspend_timestamp": suspend_ts,
                    "resume_timestamp": resume_ts,
                    "duration_minutes": dur,
                    "suspend_reason": _SUSPEND_REASON_MAP.get(
                        current_suspend.suspendreasonRaw, "unknown"
                    ),
                    "insulin_at_suspend": int(current_suspend.insulinamount),
                    "pairing_suspect": True,
                    "pump_serial": pump_serial,
                    "alarm_id": a_id,
                    "alarm_name": a_name,
                })
            current_suspend = e

        elif isinstance(e, LidPumpingResumed):
            if current_suspend is None:
                logger.warning("Unpaired LidPumpingResumed at %s; skipping", e.eventTimestamp.datetime)
                continue
            suspend_ts = current_suspend.eventTimestamp.datetime
            resume_ts = e.eventTimestamp.datetime
            dur = (resume_ts - suspend_ts).total_seconds() / 60.0
            pairing_suspect = dur > 1440
            a_id, a_name = _alarm_fields(current_suspend)
            episodes.append({
                "suspend_timestamp": suspend_ts,
                "resume_timestamp": resume_ts,
                "duration_minutes": dur,
                "suspend_reason": _SUSPEND_REASON_MAP.get(
                    current_suspend.suspendreasonRaw, "unknown"
                ),
                "insulin_at_suspend": int(current_suspend.insulinamount),
                "pairing_suspect": pairing_suspect,
                "pump_serial": pump_serial,
                "alarm_id": a_id,
                "alarm_name": a_name,
            })
            current_suspend = None

    # Unpaired suspend at end
    if current_suspend is not None:
        a_id, a_name = _alarm_fields(current_suspend)
        episodes.append({
            "suspend_timestamp": current_suspend.eventTimestamp.datetime,
            "resume_timestamp": pd.NaT,
            "duration_minutes": float("nan"),
            "suspend_reason": _SUSPEND_REASON_MAP.get(
                current_suspend.suspendreasonRaw, "unknown"
            ),
            "insulin_at_suspend": int(current_suspend.insulinamount),
            "pairing_suspect": False,
            "pump_serial": pump_serial,
            "alarm_id": a_id,
            "alarm_name": a_name,
        })

    columns = [
        "suspend_timestamp", "resume_timestamp", "duration_minutes",
        "suspend_reason", "insulin_at_suspend", "pairing_suspect", "pump_serial",
        "alarm_id", "alarm_name",
    ]
    df = pd.DataFrame(episodes, columns=columns)
    return df


# ---------------------------------------------------------------------------
# 6. Catch-all events
# ---------------------------------------------------------------------------

_USER_MODE_MAP = {0: "normal", 1: "sleeping", 2: "exercising", 3: "eating_soon"}
_PCM_MAP = {0: "no_control", 1: "open_loop", 2: "pining", 3: "closed_loop"}

# Event types handled by build_events_df
_EVENTS_TYPE_MAP: dict[type, tuple] = {}  # populated at module level below


def _build_event_row(e, pump_serial: str) -> dict | None:
    """Map a single event to a row dict for the events DataFrame, or None if not handled."""

    if isinstance(e, LidCartridgeFilled):
        return {
            "timestamp": e.eventTimestamp.datetime,
            "event_type": "site_change",
            "event_subtype": "cartridge",
            "previous_mode": None,
            "details": json.dumps({"insulin_volume": e.insulinvolume}),
            "seqnum": int(e.seqNum),
            "pump_serial": pump_serial,
        }

    if isinstance(e, LidCannulaFilled):
        return {
            "timestamp": e.eventTimestamp.datetime,
            "event_type": "site_change",
            "event_subtype": "cannula",
            "previous_mode": None,
            "details": json.dumps({"prime_size": e.primesize}),
            "seqnum": int(e.seqNum),
            "pump_serial": pump_serial,
        }

    if isinstance(e, LidTubingFilled):
        return {
            "timestamp": e.eventTimestamp.datetime,
            "event_type": "site_change",
            "event_subtype": "tubing",
            "previous_mode": None,
            "details": json.dumps({"prime_size": e.primesize}),
            "seqnum": int(e.seqNum),
            "pump_serial": pump_serial,
        }

    if isinstance(e, (LidCgmJoinSessionG7, LidCgmJoinSessionGx, LidCgmJoinSessionFsl2)):
        return {
            "timestamp": e.eventTimestamp.datetime,
            "event_type": "cgm_session",
            "event_subtype": "join",
            "previous_mode": None,
            "details": json.dumps({}),
            "seqnum": int(e.seqNum),
            "pump_serial": pump_serial,
        }

    if isinstance(e, (LidCgmStopSessionG7, LidCgmStopSessionGx, LidCgmStopSessionFsl2)):
        return {
            "timestamp": e.eventTimestamp.datetime,
            "event_type": "cgm_session",
            "event_subtype": "stop",
            "previous_mode": None,
            "details": json.dumps({}),
            "seqnum": int(e.seqNum),
            "pump_serial": pump_serial,
        }

    if isinstance(e, LidAaUserModeChange):
        current_raw = e.currentusermodeRaw
        previous_raw = e.previoususermodeRaw
        return {
            "timestamp": e.eventTimestamp.datetime,
            "event_type": "mode_change",
            "event_subtype": _USER_MODE_MAP.get(current_raw, f"unknown_{current_raw}"),
            "previous_mode": _USER_MODE_MAP.get(previous_raw, f"unknown_{previous_raw}"),
            "details": json.dumps({
                "requested_action": e.requestedactionRaw,
                "exercise_time": e.exercisetime,
            }),
            "seqnum": int(e.seqNum),
            "pump_serial": pump_serial,
        }

    if isinstance(e, LidAaPcmChange):
        current_raw = e.currentpcmRaw
        return {
            "timestamp": e.eventTimestamp.datetime,
            "event_type": "pcm_change",
            "event_subtype": _PCM_MAP.get(current_raw, f"unknown_{current_raw}"),
            "previous_mode": None,
            "details": json.dumps({"previous_pcm": e.previouspcmRaw}),
            "seqnum": int(e.seqNum),
            "pump_serial": pump_serial,
        }

    if isinstance(e, LidNewDay):
        return {
            "timestamp": e.eventTimestamp.datetime,
            "event_type": "daily_marker",
            "event_subtype": "new_day",
            "previous_mode": None,
            "details": json.dumps({"commanded_basal_rate": e.commandedbasalrate}),
            "seqnum": int(e.seqNum),
            "pump_serial": pump_serial,
        }

    if isinstance(e, LidAaDailyStatus):
        return {
            "timestamp": e.eventTimestamp.datetime,
            "event_type": "daily_marker",
            "event_subtype": "daily_status",
            "previous_mode": None,
            "details": json.dumps({
                "pump_control_state": e.pumpcontrolstateRaw,
                "user_mode": e.usermodeRaw,
            }),
            "seqnum": int(e.seqNum),
            "pump_serial": pump_serial,
        }

    return None


def build_events_df(events: list, pump_serial: str) -> pd.DataFrame:
    """Build catch-all events DataFrame for site changes, sessions, mode changes, etc."""
    rows = []
    for e in events:
        row = _build_event_row(e, pump_serial)
        if row is not None:
            rows.append(row)

    columns = [
        "timestamp", "event_type", "event_subtype",
        "previous_mode", "details", "seqnum", "pump_serial",
    ]
    df = pd.DataFrame(rows, columns=columns)
    if not df.empty:
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# 7. Alarms / alerts / CGM alerts
# ---------------------------------------------------------------------------

_ALERT_NAME_OVERRIDES: dict[int, str] = {
    50: "high_bg_alert",
    51: "low_bg_alert",
}

_CGM_DALERT_MAP: dict[int, str] = {
    1: "cgm_urgent_low",
    2: "cgm_high",
    3: "cgm_low",
    6: "cgm_rise_rate",
    8: "cgm_fall_rate",
    14: "cgm_out_of_range",
}


def build_alarm_df(events: list, pump_serial: str) -> pd.DataFrame:
    """Build unified alarm/alert/cgm_alert DataFrame."""
    rows: list[dict] = []

    for e in events:
        if isinstance(e, (LidAlarmActivated, LidAlarmCleared)):
            category = "alarm"
            action = "activated" if isinstance(e, LidAlarmActivated) else "cleared"
            alarm_id = int(e.alarmidRaw)
            alarm_name = e.alarmid.name if e.alarmid else f"alarm_{alarm_id}"
            p1 = float(e.param1) if hasattr(e, "param1") else float("nan")
            p2 = float(e.param2) if hasattr(e, "param2") else float("nan")

        elif isinstance(e, (LidAlertActivated, LidAlertCleared)):
            category = "alert"
            action = "activated" if isinstance(e, LidAlertActivated) else "cleared"
            alarm_id = int(e.alertidRaw)
            raw_name = e.alertid.name if e.alertid else f"alert_{alarm_id}"
            alarm_name = _ALERT_NAME_OVERRIDES.get(alarm_id, raw_name)
            p1 = float(e.param1) if hasattr(e, "param1") else float("nan")
            p2 = float(e.param2) if hasattr(e, "param2") else float("nan")

        elif isinstance(e, (LidCgmAlertActivatedDex, LidCgmAlertClearedDex, LidCgmAlertAckDex)):
            category = "cgm_alert"
            if isinstance(e, LidCgmAlertActivatedDex):
                action = "activated"
            elif isinstance(e, LidCgmAlertClearedDex):
                action = "cleared"
            else:
                action = "ack"
            alarm_id = int(e.dalertidRaw)
            alarm_name = _CGM_DALERT_MAP.get(alarm_id, str(e.dalertid) if e.dalertid is not None else f"unknown_{alarm_id}")
            p1 = float(e.param1) if hasattr(e, "param1") else float("nan")
            p2 = float(e.param2) if hasattr(e, "param2") else float("nan")

        else:
            continue

        rows.append({
            "timestamp": e.eventTimestamp.datetime,
            "category": category,
            "action": action,
            "alarm_id": alarm_id,
            "alarm_name": alarm_name,
            "param1": p1,
            "param2": p2,
            "seqnum": int(e.seqNum),
            "pump_serial": pump_serial,
        })

    columns = [
        "timestamp", "category", "action", "alarm_id", "alarm_name",
        "param1", "param2", "seqnum", "pump_serial",
    ]
    df = pd.DataFrame(rows, columns=columns)
    if not df.empty:
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# 8. Top-level routing
# ---------------------------------------------------------------------------

def build_all(events: list, pump_serial: str) -> dict[str, pd.DataFrame]:
    """Run all builders and return a dict of named DataFrames.

    Logs a warning for any event types not consumed by any builder.
    """
    result = {
        "cgm": build_cgm_df(events, pump_serial),
        "bolus": build_bolus_df(events, pump_serial),
        "requests": build_request_df(events, pump_serial),
        "basal": build_basal_df(events, pump_serial),
        "suspension": build_suspension_df(events, pump_serial),
        "events": build_events_df(events, pump_serial),
        "alarms": build_alarm_df(events, pump_serial),
    }

    # Detect unknown event types
    unknown_counts: dict[str, int] = defaultdict(int)
    for e in events:
        if not isinstance(e, _HANDLED_TYPES):
            unknown_counts[type(e).__name__] += 1

    for cls_name, count in sorted(unknown_counts.items()):
        logger.warning("Unhandled event type %s: %d occurrences", cls_name, count)

    return result
