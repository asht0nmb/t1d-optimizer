import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import pandas as pd

from core.detection.meal_rise import detect_meal_rise
from core.detection.windowing import Anchor, make_window
from core.storage.memory import InMemoryStorage
from core.storage.records import AlertRecord, DetectionResult
from apps.personal.cron.detect_meal_rise import (
    dexcom_max_count,
    handle_detection_alert,
    normalize_dexcom_readings,
    retry_failed_alert_deliveries,
    run_cron,
    get_storage_connection,
)
from detection.config import get_config

TZ = timezone(timedelta(hours=-7), name="PDT")
UTC = timezone.utc


class MockGlucoseReading:
    """Mock structure for pydexcom.GlucoseReading."""
    def __init__(self, value: int, dt_utc: datetime):
        self.value = value
        self.datetime = dt_utc  # pydexcom datetimes are standard naive UTC datetime
        self.trend = 4


@pytest.fixture
def mock_env(monkeypatch):
    """Setup mock credentials in environment."""
    monkeypatch.setenv("DEXCOM_USERNAME", "test_user")
    monkeypatch.setenv("DEXCOM_PASSWORD", "test_pass")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "12345:mock_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "9876543")


@pytest.fixture
def memory_storage():
    return InMemoryStorage()


@pytest.fixture
def patch_cron_io(monkeypatch, memory_storage):
    """Patch Dexcom, Telegram, and DB connections to keep tests pure and in-memory."""
    # Mock storage connection to return our InMemoryStorage
    monkeypatch.setattr(
        "apps.personal.cron.detect_meal_rise.get_storage_connection",
        lambda: (memory_storage, None)
    )

    # Mock Telegram delivery
    telegram_mock = MagicMock(return_value=True)
    monkeypatch.setattr(
        "apps.personal.cron.detect_meal_rise.send_telegram_message",
        telegram_mock
    )

    return telegram_mock


@pytest.fixture
def cron_config():
    return get_config()


@pytest.fixture
def breakfast_detection():
    """Sharp rise detection at 8:00 AM PDT."""
    anchor_ts = datetime(2026, 5, 25, 8, 0, tzinfo=TZ)
    anchor = Anchor(anchor_ts, "live")
    bg_values = [100, 105, 112, 120, 130, 140, 150]
    timestamps = [
        anchor_ts - timedelta(minutes=30) + timedelta(minutes=5 * i) for i in range(7)
    ]
    cgm_df = pd.DataFrame({"timestamp": timestamps, "bg_mgdl": bg_values})
    window = make_window(cgm_df, anchor, pre=timedelta(minutes=30), post=timedelta(0))
    cfg = get_config()
    detection = detect_meal_rise(window, cfg.meal_rise)
    assert detection is not None
    return detection


def test_handle_detection_alert_same_anchor_second_run_suppressed(
    memory_storage, cron_config, breakfast_detection, mock_env
):
    send = MagicMock(return_value=True)
    latest_ts = breakfast_detection.anchor_timestamp

    assert (
        handle_detection_alert(
            memory_storage,
            cron_config,
            breakfast_detection,
            latest_ts=latest_ts,
            send_telegram=send,
        )
        == "sent"
    )
    assert (
        handle_detection_alert(
            memory_storage,
            cron_config,
            breakfast_detection,
            latest_ts=latest_ts,
            send_telegram=send,
        )
        == "suppressed"
    )

    assert send.call_count == 1
    assert len(memory_storage.list_detection_results(kind="meal_rise")) == 1


def test_handle_detection_alert_failed_delivery_is_partial_success(
    memory_storage, cron_config, breakfast_detection, mock_env
):
    send = MagicMock(return_value=False)
    latest_ts = breakfast_detection.anchor_timestamp

    assert (
        handle_detection_alert(
            memory_storage,
            cron_config,
            breakfast_detection,
            latest_ts=latest_ts,
            send_telegram=send,
        )
        == "partial_success"
    )
    detection_rows = memory_storage.list_detection_results(kind="meal_rise")
    assert len(detection_rows) == 1
    assert detection_rows[0].payload["telegram_sent"] is False
    assert detection_rows[0].payload["delivery_stage"] == "initial"
    assert detection_rows[0].payload["delivery_attempt"] == 1


def test_handle_detection_alert_claim_lost_race_suppresses_telegram(
    memory_storage, cron_config, breakfast_detection, mock_env
):
    latest_ts = breakfast_detection.anchor_timestamp
    event_ref = f"meal_rise:{latest_ts.isoformat(timespec='minutes')}"
    memory_storage.record_alert(
        AlertRecord(
            id="existing",
            alert_kind="meal_rise",
            event_ref=event_ref,
            sent_at=datetime.now(UTC),
            payload={},
            delivery="pending",
        )
    )

    send = MagicMock(return_value=True)
    assert (
        handle_detection_alert(
            memory_storage,
            cron_config,
            breakfast_detection,
            latest_ts=latest_ts,
            send_telegram=send,
        )
        == "suppressed"
    )
    send.assert_not_called()
    assert len(memory_storage.list_detection_results(kind="meal_rise")) == 0


def test_cron_no_rise_detected(patch_cron_io, monkeypatch, memory_storage, mock_env):
    """Test that if glucose is flat, no alert is sent and no database records are written."""
    anchor_ts_utc = datetime(2026, 5, 25, 15, 0, tzinfo=timezone.utc)

    # Flat readings at 120 mg/dL (no rise)
    mock_readings = [
        MockGlucoseReading(120, anchor_ts_utc - timedelta(minutes=5 * i))
        for i in range(10)
    ]

    mock_dexcom_class = MagicMock()
    mock_dexcom_class.return_value.get_glucose_readings.return_value = mock_readings
    monkeypatch.setattr("apps.personal.cron.detect_meal_rise.Dexcom", mock_dexcom_class)

    exit_code = run_cron()
    assert exit_code == 0

    # Verify no alerts were recorded or sent
    assert len(memory_storage.recent_alerts("meal_rise", timedelta(hours=24))) == 0
    assert len(memory_storage.list_detection_results(kind="meal_rise")) == 0
    patch_cron_io.assert_not_called()


def test_cron_sharp_rise_breakfast_triggers_alert(patch_cron_io, monkeypatch, memory_storage, mock_env):
    """Test that a sharp rise during breakfast fires the detector, sends Telegram, and writes to database."""
    # 8:00 AM PDT corresponds to 3:00 PM UTC
    anchor_ts_utc = datetime(2026, 5, 25, 15, 0, tzinfo=timezone.utc)

    # Sharp rise from 100 to 150 over 30 mins
    bg_values = [150, 140, 130, 120, 112, 105, 100]
    mock_readings = [
        MockGlucoseReading(bg_values[i], anchor_ts_utc - timedelta(minutes=5 * i))
        for i in range(7)
    ]

    mock_dexcom_class = MagicMock()
    mock_dexcom_class.return_value.get_glucose_readings.return_value = mock_readings
    monkeypatch.setattr("apps.personal.cron.detect_meal_rise.Dexcom", mock_dexcom_class)

    exit_code = run_cron()
    assert exit_code == 0

    # 1. Assert Telegram message was sent
    patch_cron_io.assert_called_once()
    alert_text = patch_cron_io.call_args[0][2]
    assert "Fast glucose rise" in alert_text
    assert "100 to 150" in alert_text

    # 2. Assert detection result is saved in storage
    detections = memory_storage.list_detection_results(kind="meal_rise")
    assert len(detections) == 1
    assert detections[0].kind == "meal_rise"
    assert detections[0].payload["start_level"] == 100
    assert detections[0].payload["end_level"] == 150
    assert detections[0].payload["delta"] == 50

    # 3. Assert alert claim and detection persistence
    alerts = memory_storage.recent_alerts("meal_rise", timedelta(hours=24))
    assert len(alerts) == 1
    assert alerts[0].alert_kind == "meal_rise"
    assert alerts[0].event_ref is not None
    detections = memory_storage.list_detection_results(kind="meal_rise")
    assert detections[0].payload.get("telegram_sent") is True


def test_cron_refractory_suppresses_consecutive_alert(patch_cron_io, monkeypatch, memory_storage, mock_env):
    """Test that if an alert was already sent within 60 minutes, a second sharp rise is suppressed."""
    anchor_ts_utc = datetime(2026, 5, 25, 15, 0, tzinfo=timezone.utc)

    # Sharp rise readings
    bg_values = [150, 140, 130, 120, 112, 105, 100]
    mock_readings = [
        MockGlucoseReading(bg_values[i], anchor_ts_utc - timedelta(minutes=5 * i))
        for i in range(7)
    ]

    mock_dexcom_class = MagicMock()
    mock_dexcom_class.return_value.get_glucose_readings.return_value = mock_readings
    monkeypatch.setattr("apps.personal.cron.detect_meal_rise.Dexcom", mock_dexcom_class)

    fixed_now = datetime(2026, 5, 25, 16, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "core.storage.memory.datetime",
        type(
            "_FixedDatetime",
            (),
            {"now": staticmethod(lambda tz=None: fixed_now)},
        ),
    )

    pre_sent_ts = fixed_now - timedelta(minutes=20)
    memory_storage.record_alert(
        AlertRecord(
            id="existing_id",
            alert_kind="meal_rise",
            event_ref="meal_rise:some_prior_time",
            sent_at=pre_sent_ts,
            payload={},
            pump_serial=None,
            delivery="sent",
        )
    )

    exit_code = run_cron()
    assert exit_code == 0

    # Since it was sent within the 60-minute refractory cooldown, Telegram is NOT called
    patch_cron_io.assert_not_called()


def test_dexcom_max_count_derived_from_config():
    assert dexcom_max_count(30, 15, 5, 3) == 12


def test_normalize_dexcom_readings_one_per_bucket():
    ts = datetime(2026, 5, 25, 8, 0, tzinfo=TZ)
    df = pd.DataFrame(
        {
            "timestamp": [
                ts,
                ts + timedelta(minutes=2),
                ts + timedelta(minutes=4),
            ],
            "bg_mgdl": [100, 110, 120],
        }
    )
    out = normalize_dexcom_readings(df, interval_minutes=5)
    assert len(out) == 1
    assert out.iloc[0]["bg_mgdl"] == 120


def test_get_storage_connection_parquet(monkeypatch):
    """Test get_storage_connection falls back to ParquetStorage with correct root."""
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.setenv("MEAL_RISE_ALLOW_PARQUET_FALLBACK", "true")
    from core.storage.parquet import ParquetStorage
    
    storage, conn = get_storage_connection()
    assert isinstance(storage, ParquetStorage)
    assert conn is None
    # Verify the root is set to the canonical PROCESSED_DIR
    from ingestion.storage import PROCESSED_DIR
    assert storage.root == PROCESSED_DIR


def test_get_storage_connection_requires_db_url_or_explicit_parquet_flag(monkeypatch):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.delenv("MEAL_RISE_ALLOW_PARQUET_FALLBACK", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_DB_URL is required"):
        get_storage_connection()


def test_get_storage_connection_supabase(monkeypatch):
    """Test get_storage_connection uses SupabaseStorage.from_pooler_url when db_url is set."""
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://user:pass@localhost:5432/db")
    
    mock_from_pooler = MagicMock()
    monkeypatch.setattr("core.storage.supabase.SupabaseStorage.from_pooler_url", mock_from_pooler)
    
    get_storage_connection()
    mock_from_pooler.assert_called_once_with("postgresql://user:pass@localhost:5432/db")


def test_normalize_dexcom_readings_dst_transition():
    """Verify that normalize_dexcom_readings does not raise AmbiguousTimeError/NonExistentTimeError on DST fallback/spring forward."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Los_Angeles")
    
    # Fallback transition (repeated hour): 2026-11-01 01:30:00 PDT and PST
    # We create timestamps that cross or land exactly in the ambiguous local hour.
    # In America/Los_Angeles, clocks fall back on Nov 1, 2026 at 2:00 AM.
    # 2026-11-01 01:30:00 PDT corresponds to 08:30:00 UTC
    # 2026-11-01 01:30:00 PST corresponds to 09:30:00 UTC
    ts1 = datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc).astimezone(tz)
    ts2 = datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc).astimezone(tz)
    
    df = pd.DataFrame({
        "timestamp": [ts1, ts1 + timedelta(minutes=2), ts2, ts2 + timedelta(minutes=2)],
        "bg_mgdl": [100, 110, 120, 130]
    })
    
    # This should not raise AmbiguousTimeError or NonExistentTimeError!
    out = normalize_dexcom_readings(df, interval_minutes=5)
    assert not out.empty


def test_retry_failed_alert_deliveries_retries_after_backoff(
    memory_storage, cron_config, breakfast_detection
):
    latest_ts = breakfast_detection.anchor_timestamp
    event_ref = f"meal_rise:{latest_ts.isoformat(timespec='minutes')}"
    now = datetime(2026, 5, 25, 16, 0, tzinfo=UTC)

    memory_storage.record_alert(
        AlertRecord(
            id=None,
            alert_kind="meal_rise",
            event_ref=event_ref,
            sent_at=now - timedelta(minutes=30),
            payload={
                **breakfast_detection.to_payload(),
                "event_ref": event_ref,
                "alert_message": "retry me",
            },
            pump_serial=None,
            delivery="pending",
        )
    )
    memory_storage.record_detection_result(
        DetectionResult(
            kind="meal_rise",
            anchor_timestamp=latest_ts,
            payload={
                **breakfast_detection.to_payload(),
                "event_ref": event_ref,
                "telegram_sent": False,
                "telegram_error": "delivery_failed",
                "delivery_stage": "initial",
                "delivery_attempt": 1,
            },
            created_at=now - timedelta(minutes=20),
        )
    )
    send = MagicMock(return_value=True)

    summary = retry_failed_alert_deliveries(
        memory_storage,
        cron_config,
        send_telegram=send,
        now=now,
    )
    assert summary == {"retried": 1, "succeeded": 1}
    send.assert_called_once_with("retry me")
    results = memory_storage.list_detection_results(kind="meal_rise")
    assert results[0].payload["delivery_stage"] == "retry"
    assert results[0].payload["delivery_attempt"] == 2
    assert results[0].payload["telegram_sent"] is True


def test_retry_failed_alert_deliveries_respects_backoff(
    memory_storage, cron_config, breakfast_detection
):
    latest_ts = breakfast_detection.anchor_timestamp
    event_ref = f"meal_rise:{latest_ts.isoformat(timespec='minutes')}"
    now = datetime(2026, 5, 25, 16, 0, tzinfo=UTC)

    memory_storage.record_alert(
        AlertRecord(
            id=None,
            alert_kind="meal_rise",
            event_ref=event_ref,
            sent_at=now - timedelta(minutes=2),
            payload={
                **breakfast_detection.to_payload(),
                "event_ref": event_ref,
                "alert_message": "too soon",
            },
            pump_serial=None,
            delivery="pending",
        )
    )
    memory_storage.record_detection_result(
        DetectionResult(
            kind="meal_rise",
            anchor_timestamp=latest_ts,
            payload={
                "event_ref": event_ref,
                "telegram_sent": False,
                "delivery_stage": "initial",
                "delivery_attempt": 1,
            },
            created_at=now - timedelta(minutes=1),
        )
    )

    send = MagicMock(return_value=True)
    summary = retry_failed_alert_deliveries(
        memory_storage,
        cron_config,
        send_telegram=send,
        now=now,
    )
    assert summary == {"retried": 0, "succeeded": 0}
    send.assert_not_called()

